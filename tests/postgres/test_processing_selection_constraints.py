from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import Table, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from tradehub_data.models import Base
from tests.postgres.harness import (
    PROCESSING_SELECTION_REVISION,
    CollectionAuditBaseline,
    make_database_engine,
    upgrade_database,
)


pytestmark = pytest.mark.postgres


STARTED_AT = datetime(2026, 7, 21, 11, 0, tzinfo=UTC)
COMPLETED_AT = STARTED_AT + timedelta(seconds=1)
PAGE_LIMIT = 80
VALID_INPUT_FINGERPRINT = hashlib.sha256(b"synthetic processing input").hexdigest()
VALID_OUTPUT_FINGERPRINT = hashlib.sha256(b"synthetic processing output").hexdigest()


@dataclass(frozen=True)
class ProcessingSelectionDatabase:
    engine: Engine
    tables: dict[str, Table]
    ids: dict[str, uuid.UUID]


@pytest.fixture()
def processing_selection_database(
    collection_audit_baseline_database: CollectionAuditBaseline,
) -> ProcessingSelectionDatabase:
    upgrade_database(
        collection_audit_baseline_database.database_url,
        PROCESSING_SELECTION_REVISION,
    )
    engine = make_database_engine(collection_audit_baseline_database.database_url)
    table_names = (
        "data_sources",
        "ingestion_runs",
        "raw_payloads",
        "collection_groups",
        "collection_group_pages",
        "collection_occurrences",
        "processing_attempts",
        "collection_page_selections",
    )
    tables = {
        table_name: Base.metadata.tables[table_name]
        for table_name in table_names
    }
    ids = dict(collection_audit_baseline_database.ids)
    ids.update(_new_support_ids())
    database = ProcessingSelectionDatabase(engine=engine, tables=tables, ids=ids)
    try:
        _seed_support_graphs(database)
        yield database
    finally:
        engine.dispose()


def _new_support_ids() -> dict[str, uuid.UUID]:
    return {
        name: uuid.uuid4()
        for name in (
            "processing_run",
            "raw_payload_2",
            "page_2",
            "occurrence_2",
            "retry_occurrence",
            "fixture_page",
            "fixture_occurrence",
            "other_group",
            "other_page",
            "other_occurrence",
            "alternate_source",
            "alternate_run",
            "alternate_raw_payload",
            "alternate_group",
            "alternate_page",
            "alternate_occurrence",
        )
    }


def _run_values(
    run_id: uuid.UUID,
    source_id: uuid.UUID,
    *,
    collector_name: str = "synthetic_processing_test",
) -> dict[str, Any]:
    return {
        "id": run_id,
        "source_id": source_id,
        "collector_name": collector_name,
        "run_type": "manual",
        "run_role": "validation",
        "status": "success",
        "started_at": STARTED_AT,
        "finished_at": COMPLETED_AT,
        "records_collected": 1,
        "records_inserted": 0,
        "records_updated": 0,
        "records_failed": 0,
    }


def _raw_values(
    raw_payload_id: uuid.UUID,
    source_id: uuid.UUID,
    ingestion_run_id: uuid.UUID,
    *,
    label: str,
) -> dict[str, Any]:
    body = f'{{"synthetic":"{label}"}}'.encode()
    return {
        "id": raw_payload_id,
        "source_id": source_id,
        "ingestion_run_id": ingestion_run_id,
        "source_url": f"https://example.test/{label}",
        "source_endpoint": f"/{label}",
        "payload_type": "json",
        "payload_text": body.decode(),
        "payload_hash": hashlib.sha256(f"legacy:{label}".encode()).hexdigest(),
        "entity_body": body,
        "entity_body_sha256": hashlib.sha256(body).hexdigest(),
        "entity_body_length": len(body),
        "content_evidence_kind": "exact_entity_bytes",
        "entity_hash_algorithm": "sha256_entity_body_v1",
        "storage_status": "stored",
        "legacy_hash_algorithm": None,
        "http_status": 200,
        "content_type": "application/json",
        "collected_at": COMPLETED_AT,
        "status": "collected",
    }


def _group_values(
    database: ProcessingSelectionDatabase,
    *,
    group_id: uuid.UUID,
    source_id: uuid.UUID,
    ingestion_run_id: uuid.UUID,
    dataset_code: str,
) -> dict[str, Any]:
    return {
        "id": group_id,
        "source_id": source_id,
        "exchange_id": database.ids["exchange"],
        "ingestion_run_id": ingestion_run_id,
        "dataset_code": dataset_code,
        "collection_mode": "live_json",
        "group_purpose": "validation",
        "page_limit": PAGE_LIMIT,
        "started_at": STARTED_AT,
        "collection_completed_at": COMPLETED_AT,
        "collection_status": "success",
        "pagination_complete": True,
        "completion_evidence_kind": "short_page",
        "expected_pages": 1,
        "selected_data_pages": 1,
        "terminal_page_present": False,
        "coverage_status": "proven",
        "expected_instrument_count": 1,
        "observed_instrument_count": 1,
        "safe_diagnostic_codes": [],
        "finalized_at": COMPLETED_AT,
    }


def _page_values(
    *,
    page_id: uuid.UUID,
    group_id: uuid.UUID,
    source_id: uuid.UUID,
    ingestion_run_id: uuid.UUID,
    logical_page_number: int,
) -> dict[str, Any]:
    return {
        "id": page_id,
        "group_id": group_id,
        "source_id": source_id,
        "ingestion_run_id": ingestion_run_id,
        "page_limit": PAGE_LIMIT,
        "logical_page_number": logical_page_number,
        "page_offset": (logical_page_number - 1) * PAGE_LIMIT,
        "page_role": "data",
        "collection_page_outcome": "success",
        "finalized_at": COMPLETED_AT,
    }


def _response_occurrence_values(
    *,
    occurrence_id: uuid.UUID,
    source_id: uuid.UUID,
    ingestion_run_id: uuid.UUID,
    group_page_id: uuid.UUID,
    raw_payload_id: uuid.UUID,
    request_sequence: int,
    attempt_number: int = 1,
) -> dict[str, Any]:
    url = f"https://example.test/bvc/prices?request={request_sequence}"
    return {
        "id": occurrence_id,
        "source_id": source_id,
        "ingestion_run_id": ingestion_run_id,
        "group_page_id": group_page_id,
        "raw_payload_id": raw_payload_id,
        "request_sequence": request_sequence,
        "attempt_number": attempt_number,
        "redirect_hop": 0,
        "logical_request_url": url,
        "requested_url": url,
        "response_url": url,
        "source_endpoint": "/bvc/prices",
        "request_profile": "synthetic_processing_test_v1",
        "requested_at": STARTED_AT,
        "response_received_at": COMPLETED_AT,
        "finished_at": COMPLETED_AT,
        "http_status": 200,
        "content_type": "application/json",
        "body_length": 1,
        "outcome": "success_response",
        "safe_response_headers": {},
        "dropped_response_header_name_count": 0,
        "response_headers_overflow": False,
        "response_headers_policy_version": "synthetic_allowlist_v1",
    }


def _fixture_occurrence_values(database: ProcessingSelectionDatabase) -> dict[str, Any]:
    return {
        "id": database.ids["fixture_occurrence"],
        "source_id": database.ids["source"],
        "ingestion_run_id": database.ids["run"],
        "group_page_id": database.ids["fixture_page"],
        "raw_payload_id": database.ids["raw_payload"],
        "request_sequence": 3,
        "attempt_number": 1,
        "redirect_hop": 0,
        "logical_request_url": "fixture://synthetic/page-3",
        "requested_url": None,
        "response_url": None,
        "source_endpoint": "synthetic/page-3",
        "request_profile": "synthetic_fixture_v1",
        "requested_at": STARTED_AT,
        "response_received_at": None,
        "finished_at": COMPLETED_AT,
        "http_status": None,
        "content_type": "application/json",
        "body_length": 1,
        "outcome": "fixture_loaded",
        "safe_response_headers": {},
        "dropped_response_header_name_count": 0,
        "response_headers_overflow": False,
        "response_headers_policy_version": "synthetic_allowlist_v1",
    }


def _seed_support_graphs(database: ProcessingSelectionDatabase) -> None:
    tables = database.tables
    ids = database.ids
    with database.engine.begin() as connection:
        connection.execute(
            tables["data_sources"].insert(),
            {
                "id": ids["alternate_source"],
                "code": "bvc_processing_alternate",
                "name": "Synthetic alternate processing source",
                "source_type": "exchange",
                "base_url": "https://alternate.example.test",
                "country_code": "MA",
            },
        )
        connection.execute(
            tables["ingestion_runs"].insert(),
            [
                _run_values(
                    ids["processing_run"],
                    ids["source"],
                    collector_name="synthetic_processing_pipeline",
                ),
                _run_values(
                    ids["alternate_run"],
                    ids["alternate_source"],
                    collector_name="synthetic_alternate_pipeline",
                ),
            ],
        )
        connection.execute(
            tables["raw_payloads"].insert(),
            [
                _raw_values(
                    ids["raw_payload_2"],
                    ids["source"],
                    ids["run"],
                    label="primary-second-content",
                ),
                _raw_values(
                    ids["alternate_raw_payload"],
                    ids["alternate_source"],
                    ids["alternate_run"],
                    label="alternate-content",
                ),
            ],
        )
        connection.execute(
            tables["collection_groups"].insert(),
            [
                _group_values(
                    database,
                    group_id=ids["other_group"],
                    source_id=ids["source"],
                    ingestion_run_id=ids["run"],
                    dataset_code="bvc_prices_other_group",
                ),
                _group_values(
                    database,
                    group_id=ids["alternate_group"],
                    source_id=ids["alternate_source"],
                    ingestion_run_id=ids["alternate_run"],
                    dataset_code="bvc_prices_alternate",
                ),
            ],
        )
        connection.execute(
            tables["collection_group_pages"].insert(),
            [
                _page_values(
                    page_id=ids["page_2"],
                    group_id=ids["group"],
                    source_id=ids["source"],
                    ingestion_run_id=ids["run"],
                    logical_page_number=2,
                ),
                _page_values(
                    page_id=ids["fixture_page"],
                    group_id=ids["group"],
                    source_id=ids["source"],
                    ingestion_run_id=ids["run"],
                    logical_page_number=3,
                ),
                _page_values(
                    page_id=ids["other_page"],
                    group_id=ids["other_group"],
                    source_id=ids["source"],
                    ingestion_run_id=ids["run"],
                    logical_page_number=1,
                ),
                _page_values(
                    page_id=ids["alternate_page"],
                    group_id=ids["alternate_group"],
                    source_id=ids["alternate_source"],
                    ingestion_run_id=ids["alternate_run"],
                    logical_page_number=1,
                ),
            ],
        )
        connection.execute(
            tables["collection_occurrences"].insert(),
            [
                _response_occurrence_values(
                    occurrence_id=ids["retry_occurrence"],
                    source_id=ids["source"],
                    ingestion_run_id=ids["run"],
                    group_page_id=ids["page"],
                    raw_payload_id=ids["raw_payload"],
                    request_sequence=1,
                    attempt_number=2,
                ),
                _response_occurrence_values(
                    occurrence_id=ids["occurrence_2"],
                    source_id=ids["source"],
                    ingestion_run_id=ids["run"],
                    group_page_id=ids["page_2"],
                    raw_payload_id=ids["raw_payload_2"],
                    request_sequence=2,
                ),
                _fixture_occurrence_values(database),
                _response_occurrence_values(
                    occurrence_id=ids["other_occurrence"],
                    source_id=ids["source"],
                    ingestion_run_id=ids["run"],
                    group_page_id=ids["other_page"],
                    raw_payload_id=ids["raw_payload_2"],
                    request_sequence=4,
                ),
                _response_occurrence_values(
                    occurrence_id=ids["alternate_occurrence"],
                    source_id=ids["alternate_source"],
                    ingestion_run_id=ids["alternate_run"],
                    group_page_id=ids["alternate_page"],
                    raw_payload_id=ids["alternate_raw_payload"],
                    request_sequence=1,
                ),
            ],
        )


def _processing_values(
    database: ProcessingSelectionDatabase,
    *,
    processing_stage: str = "diagnostics",
    context: str = "occurrence",
    status: str = "success",
    attempt_id: uuid.UUID | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    values: dict[str, Any] = {
        "id": attempt_id or uuid.uuid4(),
        "source_id": database.ids["source"],
        # A processing run may differ from the acquisition run that owns the page.
        "ingestion_run_id": database.ids["processing_run"],
        "group_id": database.ids["group"],
        "group_page_id": database.ids["page"],
        "collection_occurrence_id": database.ids["occurrence"],
        "raw_payload_id": database.ids["raw_payload"],
        "processing_stage": processing_stage,
        "component_version": "synthetic-component-v1",
        "rule_version": "synthetic-rule-v1",
        "input_fingerprint_algorithm": "sha256_processing_input_v1",
        "input_fingerprint": VALID_INPUT_FINGERPRINT,
        "status": status,
        "started_at": STARTED_AT,
        "completed_at": None if status == "running" else COMPLETED_AT,
        "rows_found": 1,
        "rows_usable": 1,
        "rows_failed": 0,
        "errors_count": 0,
        "safe_diagnostic_codes": [],
    }
    if context == "raw_only":
        values.update(
            {
                "group_id": None,
                "group_page_id": None,
                "collection_occurrence_id": None,
            }
        )
    elif context == "group":
        values.update(
            {
                "group_page_id": None,
                "collection_occurrence_id": None,
                "raw_payload_id": None,
            }
        )
    elif context != "occurrence":
        raise AssertionError(f"unsupported synthetic processing context: {context}")
    values.update(overrides)
    return values


def _selection_values(
    database: ProcessingSelectionDatabase,
    *,
    group_page_id: uuid.UUID | None = None,
    occurrence_id: uuid.UUID | None = None,
    selection_reason: str = "first_qualifying_success",
    selected_by_processing_attempt_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    return {
        "group_page_id": group_page_id or database.ids["page"],
        "occurrence_id": occurrence_id or database.ids["occurrence"],
        "selected_at": COMPLETED_AT,
        "selection_reason": selection_reason,
        "selected_by_processing_attempt_id": selected_by_processing_attempt_id,
    }


def _assert_rejected(
    database: ProcessingSelectionDatabase,
    table_name: str,
    values: dict[str, Any],
    expected_constraint: str | None = None,
) -> IntegrityError:
    with pytest.raises(IntegrityError) as caught:
        with database.engine.begin() as connection:
            connection.execute(database.tables[table_name].insert(), values)

    if expected_constraint is not None:
        diagnostic = getattr(caught.value.orig, "diag", None)
        assert diagnostic is not None
        assert diagnostic.constraint_name == expected_constraint
    return caught.value


def _assert_delete_rejected(
    database: ProcessingSelectionDatabase,
    statement: str,
    parameters: dict[str, Any],
) -> None:
    with pytest.raises(IntegrityError):
        with database.engine.begin() as connection:
            connection.execute(text(statement), parameters)


@pytest.mark.parametrize("processing_stage", ("diagnostics", "parser", "normalizer"))
def test_valid_occurrence_backed_page_attempts_succeed(
    processing_selection_database: ProcessingSelectionDatabase,
    processing_stage: str,
):
    with processing_selection_database.engine.begin() as connection:
        connection.execute(
            processing_selection_database.tables["processing_attempts"].insert(),
            _processing_values(
                processing_selection_database,
                processing_stage=processing_stage,
            ),
        )


def test_valid_raw_only_reprocessing_attempt_succeeds(
    processing_selection_database: ProcessingSelectionDatabase,
):
    with processing_selection_database.engine.begin() as connection:
        connection.execute(
            processing_selection_database.tables["processing_attempts"].insert(),
            _processing_values(
                processing_selection_database,
                processing_stage="repository_validation",
                context="raw_only",
            ),
        )


@pytest.mark.parametrize(
    "processing_stage",
    ("group_evaluation", "publication_staging"),
)
def test_valid_group_attempts_succeed(
    processing_selection_database: ProcessingSelectionDatabase,
    processing_stage: str,
):
    with processing_selection_database.engine.begin() as connection:
        connection.execute(
            processing_selection_database.tables["processing_attempts"].insert(),
            _processing_values(
                processing_selection_database,
                processing_stage=processing_stage,
                context="group",
                selected_pages_evaluated=1,
                duplicate_symbol_count=0,
                blocking_conflict_count=0,
                staged_revision_count=1,
                pagination_complete_evaluated=True,
                coverage_status_evaluated="proven",
                acceptance_eligibility="eligible",
                eligibility_reason_codes=[],
                output_fingerprint_algorithm="sha256_processing_output_v1",
                output_fingerprint=VALID_OUTPUT_FINGERPRINT,
            ),
        )


@pytest.mark.parametrize(
    ("status", "completed_at"),
    (
        ("running", None),
        ("success", COMPLETED_AT),
        ("partial_success", COMPLETED_AT),
        ("failed", COMPLETED_AT),
        ("skipped", COMPLETED_AT),
    ),
)
def test_valid_attempt_lifecycle_shapes_succeed(
    processing_selection_database: ProcessingSelectionDatabase,
    status: str,
    completed_at: datetime | None,
):
    with processing_selection_database.engine.begin() as connection:
        connection.execute(
            processing_selection_database.tables["processing_attempts"].insert(),
            _processing_values(
                processing_selection_database,
                status=status,
                completed_at=completed_at,
            ),
        )


def test_attempt_uuid_is_the_database_idempotency_key(
    processing_selection_database: ProcessingSelectionDatabase,
):
    attempt_id = uuid.uuid4()
    values = _processing_values(
        processing_selection_database,
        attempt_id=attempt_id,
    )
    with processing_selection_database.engine.begin() as connection:
        connection.execute(
            processing_selection_database.tables["processing_attempts"].insert(),
            values,
        )

    _assert_rejected(
        processing_selection_database,
        "processing_attempts",
        values,
        "pk_processing_attempts",
    )
    with processing_selection_database.engine.connect() as connection:
        assert connection.execute(
            text("SELECT count(*) FROM processing_attempts WHERE id = :attempt_id"),
            {"attempt_id": attempt_id},
        ).scalar_one() == 1


def test_invalid_page_and_group_context_shapes_are_rejected(
    processing_selection_database: ProcessingSelectionDatabase,
):
    page_base = _processing_values(processing_selection_database)
    invalid_page_contexts = (
        {"raw_payload_id": None},
        {"group_page_id": None},
        {"collection_occurrence_id": None},
        {"group_id": None},
        # A page without an occurrence is not a raw-only reprocessing context.
        {"collection_occurrence_id": None, "group_page_id": uuid.uuid4()},
    )
    for overrides in invalid_page_contexts:
        _assert_rejected(
            processing_selection_database,
            "processing_attempts",
            {**page_base, "id": uuid.uuid4(), **overrides},
            "ck_processing_attempts_context_shape",
        )

    group_base = _processing_values(
        processing_selection_database,
        processing_stage="group_evaluation",
        context="group",
    )
    invalid_group_contexts = (
        {"group_id": None},
        {"raw_payload_id": processing_selection_database.ids["raw_payload"]},
        {"group_page_id": processing_selection_database.ids["page"]},
        {
            "collection_occurrence_id": processing_selection_database.ids[
                "occurrence"
            ]
        },
    )
    for overrides in invalid_group_contexts:
        _assert_rejected(
            processing_selection_database,
            "processing_attempts",
            {**group_base, "id": uuid.uuid4(), **overrides},
            "ck_processing_attempts_context_shape",
        )


def test_attempt_vocabularies_and_lifecycle_constraints_reject_invalid_rows(
    processing_selection_database: ProcessingSelectionDatabase,
):
    base = _processing_values(processing_selection_database)
    _assert_rejected(
        processing_selection_database,
        "processing_attempts",
        {**base, "id": uuid.uuid4(), "processing_stage": "unknown"},
    )
    _assert_rejected(
        processing_selection_database,
        "processing_attempts",
        {**base, "id": uuid.uuid4(), "status": "unknown"},
    )
    _assert_rejected(
        processing_selection_database,
        "processing_attempts",
        {**base, "id": uuid.uuid4(), "status": "running"},
        "ck_processing_attempts_status_completed_at",
    )
    _assert_rejected(
        processing_selection_database,
        "processing_attempts",
        {**base, "id": uuid.uuid4(), "completed_at": None},
        "ck_processing_attempts_status_completed_at",
    )
    _assert_rejected(
        processing_selection_database,
        "processing_attempts",
        {
            **base,
            "id": uuid.uuid4(),
            "started_at": COMPLETED_AT,
            "completed_at": STARTED_AT,
        },
        "ck_processing_attempts_completed_at_order",
    )


@pytest.mark.parametrize(
    ("field_name", "expected_constraint"),
    (
        ("rows_found", "ck_processing_attempts_rows_found_nonnegative"),
        ("rows_usable", "ck_processing_attempts_rows_usable_nonnegative"),
        ("rows_failed", "ck_processing_attempts_rows_failed_nonnegative"),
        ("errors_count", "ck_processing_attempts_errors_count_nonnegative"),
        (
            "selected_pages_evaluated",
            "ck_processing_attempts_selected_pages_nonnegative",
        ),
        (
            "duplicate_symbol_count",
            "ck_processing_attempts_duplicate_symbols_nonnegative",
        ),
        (
            "blocking_conflict_count",
            "ck_processing_attempts_blocking_conflicts_nonnegative",
        ),
        (
            "staged_revision_count",
            "ck_processing_attempts_staged_revisions_nonnegative",
        ),
    ),
)
def test_negative_attempt_counts_are_rejected(
    processing_selection_database: ProcessingSelectionDatabase,
    field_name: str,
    expected_constraint: str,
):
    values = _processing_values(
        processing_selection_database,
        processing_stage="group_evaluation",
        context="group",
        **{field_name: -1},
    )
    _assert_rejected(
        processing_selection_database,
        "processing_attempts",
        values,
        expected_constraint,
    )


def test_attempt_fingerprint_and_json_shapes_are_enforced(
    processing_selection_database: ProcessingSelectionDatabase,
):
    base = _processing_values(processing_selection_database)
    invalid_cases = (
        (
            {"input_fingerprint": "A" * 64},
            "ck_processing_attempts_input_fingerprint_format",
        ),
        (
            {"input_fingerprint": "a" * 63},
            "ck_processing_attempts_input_fingerprint_format",
        ),
        (
            {"output_fingerprint_algorithm": "sha256_processing_output_v1"},
            "ck_processing_attempts_output_fingerprint_pair",
        ),
        (
            {"output_fingerprint": VALID_OUTPUT_FINGERPRINT},
            "ck_processing_attempts_output_fingerprint_pair",
        ),
        (
            {
                "output_fingerprint_algorithm": "sha256_processing_output_v1",
                "output_fingerprint": "Z" * 64,
            },
            "ck_processing_attempts_output_fingerprint_format",
        ),
        (
            {"safe_diagnostic_codes": {}},
            "ck_processing_attempts_safe_diagnostic_codes_array",
        ),
    )
    for overrides, expected_constraint in invalid_cases:
        _assert_rejected(
            processing_selection_database,
            "processing_attempts",
            {**base, "id": uuid.uuid4(), **overrides},
            expected_constraint,
        )

    group_base = _processing_values(
        processing_selection_database,
        processing_stage="group_evaluation",
        context="group",
    )
    _assert_rejected(
        processing_selection_database,
        "processing_attempts",
        {
            **group_base,
            "id": uuid.uuid4(),
            "eligibility_reason_codes": ["synthetic_reason", None],
        },
        "ck_processing_attempts_eligibility_reason_codes_no_nulls",
    )


def test_group_evaluation_fields_and_vocabularies_are_stage_scoped(
    processing_selection_database: ProcessingSelectionDatabase,
):
    page_base = _processing_values(processing_selection_database)
    _assert_rejected(
        processing_selection_database,
        "processing_attempts",
        {**page_base, "selected_pages_evaluated": 1},
        "ck_processing_attempts_evaluation_fields_stage",
    )

    group_base = _processing_values(
        processing_selection_database,
        processing_stage="group_evaluation",
        context="group",
    )
    _assert_rejected(
        processing_selection_database,
        "processing_attempts",
        {**group_base, "coverage_status_evaluated": "violated"},
        "ck_processing_attempts_coverage_status_evaluated",
    )
    _assert_rejected(
        processing_selection_database,
        "processing_attempts",
        {**group_base, "acceptance_eligibility": "published"},
        "ck_processing_attempts_acceptance_eligibility",
    )


def test_attempt_cannot_cross_group_page_or_occurrence_ownership(
    processing_selection_database: ProcessingSelectionDatabase,
):
    base = _processing_values(processing_selection_database)
    _assert_rejected(
        processing_selection_database,
        "processing_attempts",
        {
            **base,
            "id": uuid.uuid4(),
            "group_id": processing_selection_database.ids["other_group"],
        },
        "fk_processing_attempts_group_page_source_group",
    )
    _assert_rejected(
        processing_selection_database,
        "processing_attempts",
        {
            **base,
            "id": uuid.uuid4(),
            "raw_payload_id": processing_selection_database.ids["raw_payload_2"],
        },
        "fk_processing_attempts_occurrence_source_raw_group_page",
    )
    _assert_rejected(
        processing_selection_database,
        "processing_attempts",
        {
            **base,
            "id": uuid.uuid4(),
            "group_page_id": processing_selection_database.ids["page_2"],
        },
        "fk_processing_attempts_occurrence_source_raw_group_page",
    )


def test_attempt_cannot_combine_source_and_raw_from_different_tuples(
    processing_selection_database: ProcessingSelectionDatabase,
):
    raw_only = _processing_values(
        processing_selection_database,
        processing_stage="repository_validation",
        context="raw_only",
    )
    _assert_rejected(
        processing_selection_database,
        "processing_attempts",
        {
            **raw_only,
            "id": uuid.uuid4(),
            "ingestion_run_id": processing_selection_database.ids["alternate_run"],
        },
        "fk_processing_attempts_ingestion_run_source",
    )
    _assert_rejected(
        processing_selection_database,
        "processing_attempts",
        {
            **raw_only,
            "id": uuid.uuid4(),
            "raw_payload_id": processing_selection_database.ids[
                "alternate_raw_payload"
            ],
        },
        "fk_processing_attempts_raw_payload_source",
    )

    group_only = _processing_values(
        processing_selection_database,
        processing_stage="group_evaluation",
        context="group",
    )
    _assert_rejected(
        processing_selection_database,
        "processing_attempts",
        {
            **group_only,
            "id": uuid.uuid4(),
            "source_id": processing_selection_database.ids["alternate_source"],
            "ingestion_run_id": processing_selection_database.ids["alternate_run"],
        },
        "fk_processing_attempts_group_source",
    )


@pytest.mark.parametrize(
    ("selection_reason", "page_key", "occurrence_key"),
    (
        ("first_qualifying_success", "page", "occurrence"),
        ("fixture_selected", "fixture_page", "fixture_occurrence"),
        ("legacy_validation_selection", "page_2", "occurrence_2"),
    ),
)
def test_valid_page_selection_reasons_succeed(
    processing_selection_database: ProcessingSelectionDatabase,
    selection_reason: str,
    page_key: str,
    occurrence_key: str,
):
    with processing_selection_database.engine.begin() as connection:
        connection.execute(
            processing_selection_database.tables[
                "collection_page_selections"
            ].insert(),
            _selection_values(
                processing_selection_database,
                group_page_id=processing_selection_database.ids[page_key],
                occurrence_id=processing_selection_database.ids[occurrence_key],
                selection_reason=selection_reason,
            ),
        )


def test_second_selection_for_same_page_is_rejected(
    processing_selection_database: ProcessingSelectionDatabase,
):
    with processing_selection_database.engine.begin() as connection:
        connection.execute(
            processing_selection_database.tables[
                "collection_page_selections"
            ].insert(),
            _selection_values(processing_selection_database),
        )

    _assert_rejected(
        processing_selection_database,
        "collection_page_selections",
        _selection_values(
            processing_selection_database,
            occurrence_id=processing_selection_database.ids["retry_occurrence"],
        ),
        "pk_collection_page_selections",
    )


def test_same_occurrence_for_another_page_is_rejected_by_cardinality_or_ownership(
    processing_selection_database: ProcessingSelectionDatabase,
):
    with processing_selection_database.engine.begin() as connection:
        connection.execute(
            processing_selection_database.tables[
                "collection_page_selections"
            ].insert(),
            _selection_values(processing_selection_database),
        )

    # An occurrence owns exactly one page, so a second-page reuse necessarily
    # violates both the occurrence unique key and the composite ownership FK.
    # The catalog test independently proves the unique-key signature.
    _assert_rejected(
        processing_selection_database,
        "collection_page_selections",
        _selection_values(
            processing_selection_database,
            group_page_id=processing_selection_database.ids["page_2"],
        ),
    )


def test_occurrence_cannot_be_selected_for_a_page_it_does_not_own(
    processing_selection_database: ProcessingSelectionDatabase,
):
    _assert_rejected(
        processing_selection_database,
        "collection_page_selections",
        _selection_values(
            processing_selection_database,
            occurrence_id=processing_selection_database.ids["occurrence_2"],
        ),
        "fk_collection_page_selections_occurrence_page",
    )


def test_invalid_selection_reason_and_selector_attempt_are_rejected(
    processing_selection_database: ProcessingSelectionDatabase,
):
    _assert_rejected(
        processing_selection_database,
        "collection_page_selections",
        _selection_values(
            processing_selection_database,
            selection_reason="latest_success",
        ),
        "ck_collection_page_selections_selection_reason",
    )
    _assert_rejected(
        processing_selection_database,
        "collection_page_selections",
        _selection_values(
            processing_selection_database,
            selected_by_processing_attempt_id=uuid.uuid4(),
        ),
        "fk_collection_page_selections_selected_by_attempt",
    )


def test_selected_evidence_graph_restricts_deletion_of_referenced_evidence(
    processing_selection_database: ProcessingSelectionDatabase,
):
    selector_attempt_id = uuid.uuid4()
    with processing_selection_database.engine.begin() as connection:
        connection.execute(
            processing_selection_database.tables["processing_attempts"].insert(),
            _processing_values(
                processing_selection_database,
                processing_stage="group_evaluation",
                context="group",
                attempt_id=selector_attempt_id,
            ),
        )
        connection.execute(
            processing_selection_database.tables[
                "collection_page_selections"
            ].insert(),
            _selection_values(
                processing_selection_database,
                selected_by_processing_attempt_id=selector_attempt_id,
            ),
        )

    _assert_delete_rejected(
        processing_selection_database,
        "DELETE FROM collection_occurrences WHERE id = :row_id",
        {"row_id": processing_selection_database.ids["occurrence"]},
    )
    _assert_delete_rejected(
        processing_selection_database,
        "DELETE FROM processing_attempts WHERE id = :row_id",
        {"row_id": selector_attempt_id},
    )
    # The selected page is also owned by its occurrence, so both the 0002
    # occurrence FK and the new direct selection FK protect this delete. The
    # catalog test independently proves the new FK and its RESTRICT action.
    _assert_delete_rejected(
        processing_selection_database,
        "DELETE FROM collection_group_pages WHERE id = :row_id",
        {"row_id": processing_selection_database.ids["page"]},
    )
