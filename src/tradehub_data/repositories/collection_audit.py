"""Persistence boundary for BVC collection audit evidence.

These helpers deliberately contain no network, parsing, diagnostics, or
normalization behavior.  They flush but never commit.  Callers must commit one
response/attempt transaction before inspecting its body.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from tradehub_data.models import (
    CollectionGroup,
    CollectionGroupPage,
    CollectionOccurrence,
    CollectionPageSelection,
    IngestionRun,
    RawPayload,
)
from tradehub_data.repositories.raw_contents import (
    ExactRawCompatibilityContext,
    ExactRawContentResult,
    insert_or_get_exact_raw_content_result,
)
from tradehub_data.repositories.sources import finish_ingestion_run


ResponseOutcome = Literal["success_response", "redirect_response", "http_error_response"]
PageRole = Literal["data", "terminal_sentinel"]


class CollectionAuditError(RuntimeError):
    """Base class for safe collection-audit repository failures."""


class CollectionAuditStateError(CollectionAuditError):
    """Raised when a write contradicts the current audit lifecycle."""


class CollectionAuditConflictError(CollectionAuditError):
    """Raised when an immutable identity is replayed with different evidence."""


@dataclass(frozen=True, slots=True)
class OccurrenceWriteResult:
    occurrence: CollectionOccurrence
    raw_payload: RawPayload | None
    raw_content_inserted: bool


def create_collection_group(
    db: Session,
    *,
    source_id: uuid.UUID,
    exchange_id: uuid.UUID,
    ingestion_run_id: uuid.UUID,
    dataset_code: str,
    collection_mode: str,
    group_purpose: str,
    page_limit: int,
    started_at: datetime,
    external_group_key: str | None = None,
) -> CollectionGroup:
    """Create the one running acquisition group for a collector execution."""

    run = db.get(IngestionRun, ingestion_run_id)
    if run is None or run.source_id != source_id:
        raise CollectionAuditStateError("collection group run/source context does not exist")
    if run.status != "running":
        raise CollectionAuditStateError("collection group requires a running ingestion run")

    group = CollectionGroup(
        source_id=source_id,
        exchange_id=exchange_id,
        ingestion_run_id=ingestion_run_id,
        dataset_code=dataset_code,
        collection_mode=collection_mode,
        group_purpose=group_purpose,
        external_group_key=external_group_key,
        page_limit=page_limit,
        started_at=started_at,
        collection_completed_at=None,
        collection_status="running",
        pagination_complete=None,
        completion_evidence_kind="none",
        expected_pages=None,
        selected_data_pages=0,
        terminal_page_present=False,
        coverage_status="unknown",
        expected_instrument_count=None,
        observed_instrument_count=None,
        collection_stop_reason=None,
        safe_diagnostic_codes=[],
        finalized_at=None,
    )
    db.add(group)
    db.flush()
    return group


def create_collection_group_page(
    db: Session,
    *,
    group_id: uuid.UUID,
    logical_page_number: int,
) -> CollectionGroupPage:
    """Create or safely reuse one pending logical page in a running group."""

    group = _lock_group(db, group_id)
    if group.collection_status != "running":
        raise CollectionAuditStateError("cannot create a page in a finalized collection group")
    if logical_page_number <= 0:
        raise CollectionAuditStateError("logical page numbers are one-based")

    page_offset = (logical_page_number - 1) * group.page_limit
    existing = db.scalar(
        select(CollectionGroupPage)
        .where(
            CollectionGroupPage.group_id == group.id,
            CollectionGroupPage.logical_page_number == logical_page_number,
        )
        .with_for_update()
    )
    if existing is not None:
        if (
            existing.source_id != group.source_id
            or existing.ingestion_run_id != group.ingestion_run_id
            or existing.page_limit != group.page_limit
            or existing.page_offset != page_offset
        ):
            raise CollectionAuditConflictError("existing logical page contradicts its group")
        if existing.collection_page_outcome != "pending":
            raise CollectionAuditStateError("cannot reopen a finalized logical page")
        return existing

    page = CollectionGroupPage(
        group_id=group.id,
        source_id=group.source_id,
        ingestion_run_id=group.ingestion_run_id,
        page_limit=group.page_limit,
        logical_page_number=logical_page_number,
        page_offset=page_offset,
        page_role="unknown",
        collection_page_outcome="pending",
        structural_reason_code=None,
        finalized_at=None,
    )
    db.add(page)
    db.flush()
    return page


def record_response_occurrence(
    db: Session,
    *,
    group_id: uuid.UUID,
    group_page_id: uuid.UUID,
    source_id: uuid.UUID,
    ingestion_run_id: uuid.UUID,
    entity_body: bytes,
    compatibility_context: ExactRawCompatibilityContext,
    request_sequence: int,
    attempt_number: int,
    redirect_hop: int,
    logical_request_url: str,
    requested_url: str | None,
    response_url: str,
    source_endpoint: str | None,
    request_profile: str,
    requested_at: datetime,
    response_received_at: datetime,
    finished_at: datetime,
    http_status: int,
    outcome: ResponseOutcome,
    content_type: str | None,
    safe_response_headers: dict[str, list[str]],
    dropped_response_header_name_count: int,
    response_headers_overflow: bool,
    response_headers_policy_version: str,
    source_published_at: datetime | None = None,
    safe_error_code: str | None = None,
    safe_error_message: str | None = None,
    occurrence_id: uuid.UUID | None = None,
) -> OccurrenceWriteResult:
    """Atomically insert/reuse exact bytes and record one HTTP response."""

    if outcome not in {"success_response", "redirect_response", "http_error_response"}:
        raise CollectionAuditStateError("response occurrence has an invalid response outcome")
    if compatibility_context.ingestion_run_id != ingestion_run_id:
        raise CollectionAuditStateError("raw compatibility run differs from occurrence run")
    if compatibility_context.collected_at != response_received_at:
        raise CollectionAuditStateError("raw compatibility time must be the first response time")
    if compatibility_context.http_status != http_status:
        raise CollectionAuditStateError("raw compatibility status differs from response status")
    if compatibility_context.content_type != content_type:
        raise CollectionAuditStateError("raw compatibility content type differs from response")
    if compatibility_context.source_published_at != source_published_at:
        raise CollectionAuditStateError("raw compatibility source time differs from response")

    _lock_running_group_and_pending_page(
        db,
        group_id=group_id,
        group_page_id=group_page_id,
        source_id=source_id,
        ingestion_run_id=ingestion_run_id,
    )
    _validate_safe_headers(safe_response_headers)

    raw_result = insert_or_get_exact_raw_content_result(
        db,
        source_id=source_id,
        entity_body=entity_body,
        compatibility_context=compatibility_context,
    )
    occurrence = _insert_or_get_occurrence(
        db,
        id=occurrence_id or uuid.uuid4(),
        source_id=source_id,
        ingestion_run_id=ingestion_run_id,
        group_page_id=group_page_id,
        raw_payload_id=raw_result.raw_payload.id,
        request_sequence=request_sequence,
        attempt_number=attempt_number,
        redirect_hop=redirect_hop,
        logical_request_url=logical_request_url,
        requested_url=requested_url,
        response_url=response_url,
        source_endpoint=source_endpoint,
        request_profile=request_profile,
        requested_at=requested_at,
        response_received_at=response_received_at,
        finished_at=finished_at,
        source_published_at=source_published_at,
        http_status=http_status,
        content_type=content_type,
        body_length=len(entity_body),
        outcome=outcome,
        safe_error_code=safe_error_code,
        safe_error_message=safe_error_message,
        safe_response_headers=safe_response_headers,
        dropped_response_header_name_count=dropped_response_header_name_count,
        response_headers_overflow=response_headers_overflow,
        response_headers_policy_version=response_headers_policy_version,
    )
    return OccurrenceWriteResult(
        occurrence=occurrence,
        raw_payload=raw_result.raw_payload,
        raw_content_inserted=raw_result.inserted,
    )


def record_transport_failure_occurrence(
    db: Session,
    *,
    group_id: uuid.UUID,
    group_page_id: uuid.UUID,
    source_id: uuid.UUID,
    ingestion_run_id: uuid.UUID,
    request_sequence: int,
    attempt_number: int,
    redirect_hop: int,
    logical_request_url: str,
    requested_url: str | None,
    source_endpoint: str | None,
    request_profile: str,
    requested_at: datetime,
    finished_at: datetime,
    safe_error_code: str,
    response_headers_policy_version: str,
    safe_error_message: str | None = None,
    occurrence_id: uuid.UUID | None = None,
) -> OccurrenceWriteResult:
    """Record one no-response transport attempt without creating raw content."""

    _lock_running_group_and_pending_page(
        db,
        group_id=group_id,
        group_page_id=group_page_id,
        source_id=source_id,
        ingestion_run_id=ingestion_run_id,
    )
    occurrence = _insert_or_get_occurrence(
        db,
        id=occurrence_id or uuid.uuid4(),
        source_id=source_id,
        ingestion_run_id=ingestion_run_id,
        group_page_id=group_page_id,
        raw_payload_id=None,
        request_sequence=request_sequence,
        attempt_number=attempt_number,
        redirect_hop=redirect_hop,
        logical_request_url=logical_request_url,
        requested_url=requested_url,
        response_url=None,
        source_endpoint=source_endpoint,
        request_profile=request_profile,
        requested_at=requested_at,
        response_received_at=None,
        finished_at=finished_at,
        source_published_at=None,
        http_status=None,
        content_type=None,
        body_length=None,
        outcome="transport_failure",
        safe_error_code=safe_error_code,
        safe_error_message=safe_error_message,
        safe_response_headers={},
        dropped_response_header_name_count=0,
        response_headers_overflow=False,
        response_headers_policy_version=response_headers_policy_version,
    )
    return OccurrenceWriteResult(
        occurrence=occurrence,
        raw_payload=None,
        raw_content_inserted=False,
    )


def record_fixture_occurrence(
    db: Session,
    *,
    group_id: uuid.UUID,
    group_page_id: uuid.UUID,
    source_id: uuid.UUID,
    ingestion_run_id: uuid.UUID,
    entity_body: bytes,
    compatibility_context: ExactRawCompatibilityContext,
    request_sequence: int,
    logical_request_url: str,
    source_endpoint: str | None,
    request_profile: str,
    requested_at: datetime,
    finished_at: datetime,
    response_headers_policy_version: str,
    occurrence_id: uuid.UUID | None = None,
) -> OccurrenceWriteResult:
    """Store exact fixture bytes and one non-HTTP fixture occurrence."""

    if compatibility_context.ingestion_run_id != ingestion_run_id:
        raise CollectionAuditStateError("fixture compatibility run differs from occurrence run")
    if compatibility_context.collected_at != finished_at:
        raise CollectionAuditStateError("fixture compatibility time must be its load finish time")
    if compatibility_context.http_status is not None:
        raise CollectionAuditStateError("fixture compatibility context cannot claim HTTP status")

    _lock_running_group_and_pending_page(
        db,
        group_id=group_id,
        group_page_id=group_page_id,
        source_id=source_id,
        ingestion_run_id=ingestion_run_id,
    )
    raw_result = insert_or_get_exact_raw_content_result(
        db,
        source_id=source_id,
        entity_body=entity_body,
        compatibility_context=compatibility_context,
    )
    occurrence = _insert_or_get_occurrence(
        db,
        id=occurrence_id or uuid.uuid4(),
        source_id=source_id,
        ingestion_run_id=ingestion_run_id,
        group_page_id=group_page_id,
        raw_payload_id=raw_result.raw_payload.id,
        request_sequence=request_sequence,
        attempt_number=1,
        redirect_hop=0,
        logical_request_url=logical_request_url,
        requested_url=None,
        response_url=None,
        source_endpoint=source_endpoint,
        request_profile=request_profile,
        requested_at=requested_at,
        response_received_at=None,
        finished_at=finished_at,
        source_published_at=None,
        http_status=None,
        content_type=compatibility_context.content_type,
        body_length=len(entity_body),
        outcome="fixture_loaded",
        safe_error_code=None,
        safe_error_message=None,
        safe_response_headers={},
        dropped_response_header_name_count=0,
        response_headers_overflow=False,
        response_headers_policy_version=response_headers_policy_version,
    )
    return OccurrenceWriteResult(
        occurrence=occurrence,
        raw_payload=raw_result.raw_payload,
        raw_content_inserted=raw_result.inserted,
    )


def finalize_page_with_selection(
    db: Session,
    *,
    group_id: uuid.UUID,
    group_page_id: uuid.UUID,
    occurrence_id: uuid.UUID,
    page_role: PageRole,
    selected_at: datetime,
    selection_reason: str,
    structural_reason_code: str | None = None,
) -> CollectionPageSelection:
    """Atomically finalize a qualifying page and make its immutable selection."""

    group, page = _lock_group_and_page(db, group_id=group_id, group_page_id=group_page_id)
    existing = db.get(CollectionPageSelection, group_page_id)
    if existing is not None:
        if (
            existing.occurrence_id != occurrence_id
            or existing.selection_reason != selection_reason
            or page.page_role != page_role
            or page.collection_page_outcome != "success"
        ):
            raise CollectionAuditConflictError("page selection is immutable")
        return existing

    if group.collection_status != "running" or page.collection_page_outcome != "pending":
        raise CollectionAuditStateError("only a pending page in a running group can be selected")

    occurrence = db.get(CollectionOccurrence, occurrence_id)
    if occurrence is None or occurrence.group_page_id != page.id:
        raise CollectionAuditStateError("selected occurrence does not own the logical page")
    allowed_outcomes = {
        "first_qualifying_success": {"success_response"},
        "fixture_selected": {"fixture_loaded"},
        "legacy_validation_selection": {"success_response", "fixture_loaded"},
    }
    if selection_reason not in allowed_outcomes:
        raise CollectionAuditStateError("unsupported page-selection reason")
    if occurrence.outcome not in allowed_outcomes[selection_reason]:
        raise CollectionAuditStateError("selection reason contradicts occurrence outcome")
    if _timestamp_order_key(selected_at) < _timestamp_order_key(occurrence.finished_at):
        raise CollectionAuditStateError("selection time cannot precede occurrence completion")
    if page_role not in {"data", "terminal_sentinel"}:
        raise CollectionAuditStateError("a selected page must have a known structural role")

    if occurrence.outcome == "success_response":
        successful = list(
            db.scalars(
                select(CollectionOccurrence)
                .where(
                    CollectionOccurrence.group_page_id == page.id,
                    CollectionOccurrence.outcome == "success_response",
                )
                .order_by(CollectionOccurrence.occurrence_sequence)
            )
        )
        successful_raw_ids = {candidate.raw_payload_id for candidate in successful}
        if len(successful_raw_ids) > 1:
            raise CollectionAuditConflictError("logical page has different successful response bodies")
        if successful and successful[0].id != occurrence.id:
            raise CollectionAuditConflictError("selection must use the earliest equivalent success")

    page.page_role = page_role
    page.collection_page_outcome = "success"
    page.structural_reason_code = structural_reason_code
    page.finalized_at = selected_at
    selection = CollectionPageSelection(
        group_page_id=page.id,
        occurrence_id=occurrence.id,
        selected_at=selected_at,
        selection_reason=selection_reason,
        selected_by_processing_attempt_id=None,
    )
    db.add(selection)
    db.flush()
    return selection


def finalize_page_failure(
    db: Session,
    *,
    group_id: uuid.UUID,
    group_page_id: uuid.UUID,
    finalized_at: datetime,
    structural_reason_code: str,
) -> CollectionGroupPage:
    """Finalize one exhausted/malformed logical page without a selection."""

    group, page = _lock_group_and_page(db, group_id=group_id, group_page_id=group_page_id)
    if page.collection_page_outcome == "failed":
        if page.structural_reason_code != structural_reason_code:
            raise CollectionAuditConflictError("failed page evidence is immutable")
        return page
    if group.collection_status != "running" or page.collection_page_outcome != "pending":
        raise CollectionAuditStateError("only a pending page in a running group can fail")
    if db.get(CollectionPageSelection, page.id) is not None:
        raise CollectionAuditConflictError("a selected page cannot be finalized as failed")

    page.page_role = "unknown"
    page.collection_page_outcome = "failed"
    page.structural_reason_code = structural_reason_code
    page.finalized_at = finalized_at
    db.flush()
    return page


def finalize_collection_group_and_run(
    db: Session,
    *,
    group_id: uuid.UUID,
    collection_status: Literal["success", "partial_success", "failed"],
    pagination_complete: bool,
    completion_evidence_kind: str,
    finalized_at: datetime,
    collection_stop_reason: str,
    safe_diagnostic_codes: list[str] | tuple[str, ...] = (),
    expected_pages: int | None = None,
    expected_instrument_count: int | None = None,
    observed_instrument_count: int | None = None,
    records_collected: int | None = None,
    records_inserted: int | None = None,
    records_updated: int | None = None,
    records_failed: int | None = None,
    safe_error_code: str | None = None,
    error_message: str | None = None,
    run_metadata: dict[str, Any] | None = None,
) -> CollectionGroup:
    """Atomically finalize a group and its acquisition/validation run."""

    group = _lock_group(db, group_id)
    if group.collection_status != "running":
        if (
            group.collection_status == collection_status
            and group.pagination_complete is pagination_complete
            and group.completion_evidence_kind == completion_evidence_kind
            and group.collection_stop_reason == collection_stop_reason
        ):
            return group
        raise CollectionAuditConflictError("collection group finalization is immutable")

    pages = list(
        db.scalars(
            select(CollectionGroupPage)
            .where(CollectionGroupPage.group_id == group.id)
            .order_by(CollectionGroupPage.logical_page_number)
            .with_for_update()
        )
    )
    if any(page.collection_page_outcome == "pending" for page in pages):
        raise CollectionAuditStateError("cannot finalize a group with pending logical pages")

    page_ids = [page.id for page in pages]
    selections = (
        list(
            db.scalars(
                select(CollectionPageSelection).where(
                    CollectionPageSelection.group_page_id.in_(page_ids)
                )
            )
        )
        if page_ids
        else []
    )
    selections_by_page = {selection.group_page_id: selection for selection in selections}
    successful_pages = [page for page in pages if page.collection_page_outcome == "success"]
    if any(page.id not in selections_by_page for page in successful_pages):
        raise CollectionAuditStateError("every successful logical page requires a selection")
    if any(page.id in selections_by_page for page in pages if page.collection_page_outcome == "failed"):
        raise CollectionAuditStateError("a failed logical page cannot have a selection")

    selected_data_pages = sum(
        page.page_role == "data" and page.id in selections_by_page for page in successful_pages
    )
    terminal_page_present = any(
        page.page_role == "terminal_sentinel" and page.id in selections_by_page
        for page in successful_pages
    )
    if collection_status == "success":
        page_numbers = [page.logical_page_number for page in pages]
        expected_contiguous_numbers = list(range(1, len(pages) + 1))
        terminal_pages = [page for page in pages if page.page_role == "terminal_sentinel"]
        if (
            not pagination_complete
            or not pages
            or selected_data_pages == 0
            or len(successful_pages) != len(pages)
            or page_numbers != expected_contiguous_numbers
            or len(terminal_pages) > 1
            or (terminal_pages and terminal_pages[0].id != pages[-1].id)
        ):
            raise CollectionAuditStateError("successful group requires complete successful pages")
    if collection_status == "partial_success" and selected_data_pages == 0:
        raise CollectionAuditStateError("partial group requires at least one selected data page")
    if pagination_complete and completion_evidence_kind == "none":
        raise CollectionAuditStateError("complete pagination requires positive evidence")
    if not pagination_complete and completion_evidence_kind != "none":
        raise CollectionAuditStateError("incomplete pagination cannot claim completion evidence")

    selected_occurrences = (
        list(
            db.scalars(
                select(CollectionOccurrence).where(
                    CollectionOccurrence.id.in_(
                        [selection.occurrence_id for selection in selections]
                    )
                )
            )
        )
        if selections
        else []
    )
    collection_completed_at = max(
        (
            occurrence.response_received_at or occurrence.finished_at
            for occurrence in selected_occurrences
        ),
        default=None,
    )

    run = db.scalar(
        select(IngestionRun)
        .where(
            IngestionRun.id == group.ingestion_run_id,
            IngestionRun.source_id == group.source_id,
        )
        .with_for_update()
    )
    if run is None or run.status != "running":
        raise CollectionAuditStateError("collection group requires its running coherent run")

    group.collection_completed_at = collection_completed_at
    group.collection_status = collection_status
    group.pagination_complete = pagination_complete
    group.completion_evidence_kind = completion_evidence_kind
    group.expected_pages = expected_pages
    group.selected_data_pages = selected_data_pages
    group.terminal_page_present = terminal_page_present
    group.coverage_status = "unknown"
    group.expected_instrument_count = expected_instrument_count
    group.observed_instrument_count = observed_instrument_count
    group.collection_stop_reason = collection_stop_reason
    group.safe_diagnostic_codes = list(safe_diagnostic_codes)
    group.finalized_at = finalized_at
    finish_ingestion_run(
        db,
        run,
        status=collection_status,
        finished_at=finalized_at,
        records_collected=records_collected,
        records_inserted=records_inserted,
        records_updated=records_updated,
        records_failed=records_failed,
        safe_error_code=safe_error_code,
        error_message=error_message,
        metadata=run_metadata,
    )
    db.flush()
    return group


def _lock_group(db: Session, group_id: uuid.UUID) -> CollectionGroup:
    group = db.scalar(
        select(CollectionGroup).where(CollectionGroup.id == group_id).with_for_update()
    )
    if group is None:
        raise CollectionAuditStateError("collection group does not exist")
    return group


def _lock_group_and_page(
    db: Session,
    *,
    group_id: uuid.UUID,
    group_page_id: uuid.UUID,
) -> tuple[CollectionGroup, CollectionGroupPage]:
    # Every write path uses the same group -> page lock order.
    group = _lock_group(db, group_id)
    page = db.scalar(
        select(CollectionGroupPage)
        .where(
            CollectionGroupPage.id == group_page_id,
            CollectionGroupPage.group_id == group.id,
        )
        .with_for_update()
    )
    if page is None:
        raise CollectionAuditStateError("logical page does not belong to collection group")
    return group, page


def _lock_running_group_and_pending_page(
    db: Session,
    *,
    group_id: uuid.UUID,
    group_page_id: uuid.UUID,
    source_id: uuid.UUID,
    ingestion_run_id: uuid.UUID,
) -> tuple[CollectionGroup, CollectionGroupPage]:
    group, page = _lock_group_and_page(
        db,
        group_id=group_id,
        group_page_id=group_page_id,
    )
    if (
        group.source_id != source_id
        or group.ingestion_run_id != ingestion_run_id
        or page.source_id != source_id
        or page.ingestion_run_id != ingestion_run_id
    ):
        raise CollectionAuditStateError("occurrence source/run/page ownership is incoherent")
    if group.collection_status != "running" or page.collection_page_outcome != "pending":
        raise CollectionAuditStateError("new occurrences require a pending page in a running group")
    return group, page


def _insert_or_get_occurrence(db: Session, **values: Any) -> CollectionOccurrence:
    dialect_name = db.get_bind().dialect.name
    occurrences = CollectionOccurrence.__table__
    if dialect_name == "postgresql":
        statement = postgresql_insert(occurrences).values(**values)
    elif dialect_name == "sqlite":
        # SQLite ignores Identity on non-primary-key columns, and this Core
        # insert intentionally bypasses the ORM before_flush test fixture.  A
        # sequential value keeps the non-authoritative in-memory fallback
        # usable; PostgreSQL remains the concurrency authority.
        values = {
            **values,
            "occurrence_sequence": (
                db.scalar(select(func.max(CollectionOccurrence.occurrence_sequence))) or 0
            )
            + 1,
        }
        statement = sqlite_insert(occurrences).values(**values)
    else:  # pragma: no cover - supported project databases are above
        raise CollectionAuditError(f"unsupported occurrence database dialect: {dialect_name}")
    statement = statement.on_conflict_do_nothing(
        index_elements=[
            occurrences.c.ingestion_run_id,
            occurrences.c.request_sequence,
            occurrences.c.attempt_number,
            occurrences.c.redirect_hop,
        ]
    ).returning(occurrences.c.id)
    inserted_id = db.execute(statement).scalar_one_or_none()

    occurrence = db.scalar(
        select(CollectionOccurrence).where(
            CollectionOccurrence.ingestion_run_id == values["ingestion_run_id"],
            CollectionOccurrence.request_sequence == values["request_sequence"],
            CollectionOccurrence.attempt_number == values["attempt_number"],
            CollectionOccurrence.redirect_hop == values["redirect_hop"],
        )
    )
    if occurrence is None:
        raise CollectionAuditConflictError("occurrence conflict did not resolve to stored evidence")
    if inserted_id is not None and occurrence.id != inserted_id:
        raise CollectionAuditConflictError("inserted occurrence resolved to an unexpected identity")
    _assert_occurrence_matches(occurrence, values)
    return occurrence


def _assert_occurrence_matches(occurrence: CollectionOccurrence, values: dict[str, Any]) -> None:
    compared_fields = (
        "id",
        "source_id",
        "ingestion_run_id",
        "group_page_id",
        "raw_payload_id",
        "request_sequence",
        "attempt_number",
        "redirect_hop",
        "logical_request_url",
        "requested_url",
        "response_url",
        "source_endpoint",
        "request_profile",
        "requested_at",
        "response_received_at",
        "finished_at",
        "source_published_at",
        "http_status",
        "content_type",
        "body_length",
        "outcome",
        "safe_error_code",
        "safe_error_message",
        "safe_response_headers",
        "dropped_response_header_name_count",
        "response_headers_overflow",
        "response_headers_policy_version",
    )
    if any(
        not _audit_values_equal(getattr(occurrence, field), values[field])
        for field in compared_fields
    ):
        raise CollectionAuditConflictError("occurrence identity was replayed with different evidence")


def _audit_values_equal(stored: Any, expected: Any) -> bool:
    # SQLite drops timezone offsets for TIMESTAMP values.  Its repository path
    # is unit-test-only; compare identical wall-clock values when exactly one
    # side is naive.  PostgreSQL retains aware instants and uses normal equality.
    if isinstance(stored, datetime) and isinstance(expected, datetime):
        if (stored.tzinfo is None) != (expected.tzinfo is None):
            return stored.replace(tzinfo=None) == expected.replace(tzinfo=None)
    return stored == expected


def _timestamp_order_key(value: datetime) -> datetime:
    """Normalize timestamps for SQLite/PostgreSQL-neutral lifecycle checks.

    PostgreSQL preserves ``TIMESTAMPTZ`` offsets while SQLite's test adapter
    returns the same values without ``tzinfo``.  The project writes UTC audit
    times, so treating SQLite's naive representation as UTC gives both
    dialects the same ordering without weakening the PostgreSQL constraints.
    """

    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _validate_safe_headers(headers: dict[str, list[str]]) -> None:
    if not isinstance(headers, dict):
        raise CollectionAuditStateError("safe response headers must be an object")
    for name, values in headers.items():
        if not isinstance(name, str) or name != name.lower():
            raise CollectionAuditStateError("safe response header names must be lowercase strings")
        if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
            raise CollectionAuditStateError("safe response header values must be string arrays")
