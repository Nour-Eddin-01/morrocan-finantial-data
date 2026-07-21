from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text

from tradehub_data.models import (
    CollectionGroup,
    CollectionGroupPage,
    CollectionOccurrence,
    CollectionPageSelection,
    DataSource,
    Exchange,
    IngestionRun,
    ProcessingAttempt,
    RawPayload,
)
from tradehub_data.repositories.collection_audit import (
    create_collection_group,
    create_collection_group_page,
    finalize_collection_group_and_run,
    finalize_page_failure,
    finalize_page_with_selection,
    record_fixture_occurrence,
    record_response_occurrence,
    record_transport_failure_occurrence,
)
from tradehub_data.repositories.raw_contents import (
    EXACT_COMPATIBILITY_HASH_ALGORITHM,
    EXACT_CONTENT_EVIDENCE_KIND,
    ExactRawCompatibilityContext,
    exact_compatibility_payload_hash,
    exact_entity_body_sha256,
    fill_exact_raw_content_text_cache,
    insert_or_get_exact_raw_content,
)
from tradehub_data.repositories.raw_payloads import (
    update_raw_payload_metadata,
    update_raw_payload_status,
)
from tradehub_data.repositories.sources import create_ingestion_run


STARTED_AT = datetime(2026, 7, 21, 10, 0, tzinfo=UTC)
RESPONSE_AT = STARTED_AT + timedelta(seconds=1)
FINISHED_AT = RESPONSE_AT + timedelta(milliseconds=100)
SELECTED_AT = FINISHED_AT + timedelta(milliseconds=100)
FINALIZED_AT = SELECTED_AT + timedelta(milliseconds=100)
ENTITY_BODY = b'{"data":{"data":[{"symbol":"TST"}]}}'


def _source_and_exchange(db_session) -> tuple[DataSource, Exchange]:
    source = DataSource(
        code="bvc_prices_repository_test",
        name="Synthetic BVC prices",
        source_type="exchange",
        base_url="https://www.casablanca-bourse.com",
        country_code="MA",
    )
    exchange = Exchange(
        code="BVC",
        name="Synthetic BVC",
        country_code="MA",
        currency_code="MAD",
        timezone="Africa/Casablanca",
    )
    db_session.add_all((source, exchange))
    db_session.commit()
    return source, exchange


def _run_group_page(
    db_session,
    *,
    source: DataSource,
    exchange: Exchange,
    started_at: datetime,
    collector_name: str,
    collection_mode: str = "live_json",
    run_role: str = "acquisition",
) -> tuple[IngestionRun, CollectionGroup, CollectionGroupPage]:
    run = create_ingestion_run(
        db_session,
        source_id=source.id,
        collector_name=collector_name,
        run_type="manual",
        run_role=run_role,
        started_at=started_at,
    )
    group = create_collection_group(
        db_session,
        source_id=source.id,
        exchange_id=exchange.id,
        ingestion_run_id=run.id,
        dataset_code="bvc_equity_prices",
        collection_mode=collection_mode,
        group_purpose="validation",
        page_limit=80 if collection_mode != "manual_fixture" else 1,
        started_at=started_at,
    )
    db_session.commit()
    page = create_collection_group_page(
        db_session,
        group_id=group.id,
        logical_page_number=1,
    )
    db_session.commit()
    return run, group, page


def _response_context(run: IngestionRun, *, response_at: datetime, response_url: str):
    return ExactRawCompatibilityContext(
        ingestion_run_id=run.id,
        collected_at=response_at,
        source_url=response_url,
        source_endpoint="bvc_json_market_data",
        http_status=200,
        content_type="application/json",
    )


def test_response_audit_selection_and_group_finalization_are_coherent(db_session):
    source, exchange = _source_and_exchange(db_session)
    run, group, page = _run_group_page(
        db_session,
        source=source,
        exchange=exchange,
        started_at=STARTED_AT,
        collector_name="response_repository_test",
    )
    response_url = "https://www.casablanca-bourse.com/api/prices?page%5Boffset%5D=0"

    result = record_response_occurrence(
        db_session,
        group_id=group.id,
        group_page_id=page.id,
        source_id=source.id,
        ingestion_run_id=run.id,
        entity_body=ENTITY_BODY,
        compatibility_context=_response_context(
            run,
            response_at=RESPONSE_AT,
            response_url=response_url,
        ),
        request_sequence=1,
        attempt_number=1,
        redirect_hop=0,
        logical_request_url=response_url,
        requested_url=response_url,
        response_url=response_url,
        source_endpoint="bvc_json_market_data",
        request_profile="bvc-json-safe-v1",
        requested_at=STARTED_AT,
        response_received_at=RESPONSE_AT,
        finished_at=FINISHED_AT,
        http_status=200,
        outcome="success_response",
        content_type="application/json",
        safe_response_headers={"content-type": ["application/json"]},
        dropped_response_header_name_count=2,
        response_headers_overflow=False,
        response_headers_policy_version="bvc-safe-response-headers-v1",
    )
    db_session.commit()

    assert result.raw_content_inserted is True
    assert result.raw_payload is not None
    assert result.raw_payload.entity_body == ENTITY_BODY
    assert result.raw_payload.entity_body_sha256 == exact_entity_body_sha256(ENTITY_BODY)
    assert result.raw_payload.payload_text is None
    assert db_session.execute(
        text(
            "SELECT payload IS NULL AND metadata IS NULL "
            "FROM raw_payloads WHERE id = :raw_payload_id"
        ),
        {"raw_payload_id": result.raw_payload.id.hex},
    ).scalar_one()
    assert result.occurrence.safe_response_headers == {
        "content-type": ["application/json"]
    }

    assert fill_exact_raw_content_text_cache(
        db_session,
        raw_payload_id=result.raw_payload.id,
        source_id=source.id,
        first_ingestion_run_id=run.id,
        payload_text=ENTITY_BODY.decode("utf-8"),
    )
    db_session.commit()

    selection = finalize_page_with_selection(
        db_session,
        group_id=group.id,
        group_page_id=page.id,
        occurrence_id=result.occurrence.id,
        page_role="data",
        selected_at=SELECTED_AT,
        selection_reason="first_qualifying_success",
    )
    finalized_group = finalize_collection_group_and_run(
        db_session,
        group_id=group.id,
        collection_status="success",
        pagination_complete=True,
        completion_evidence_kind="short_page",
        finalized_at=FINALIZED_AT,
        collection_stop_reason="short_page",
        records_collected=1,
        records_inserted=1,
        records_updated=0,
        records_failed=0,
    )
    db_session.commit()

    assert selection.group_page_id == page.id
    assert finalized_group.selected_data_pages == 1
    # SQLite's unit-test adapter returns TIMESTAMPTZ values without tzinfo;
    # PostgreSQL integration tests exercise the authoritative aware value.
    assert finalized_group.collection_completed_at == RESPONSE_AT.replace(tzinfo=None)
    assert finalized_group.coverage_status == "unknown"
    assert run.run_role == "acquisition"
    assert run.status == "success"
    assert db_session.query(ProcessingAttempt).count() == 0


def test_exact_text_cache_requires_first_run_occurrence_evidence(db_session):
    source, _ = _source_and_exchange(db_session)
    run = create_ingestion_run(
        db_session,
        source_id=source.id,
        collector_name="raw_without_occurrence_test",
        run_type="manual",
        run_role="validation",
        started_at=STARTED_AT,
    )
    raw_payload = insert_or_get_exact_raw_content(
        db_session,
        source_id=source.id,
        entity_body=ENTITY_BODY,
        compatibility_context=ExactRawCompatibilityContext(
            ingestion_run_id=run.id,
            collected_at=RESPONSE_AT,
            source_url="https://www.casablanca-bourse.com/api/prices",
            source_endpoint="bvc_json_market_data",
            http_status=200,
            content_type="application/json",
        ),
    )

    assert not fill_exact_raw_content_text_cache(
        db_session,
        raw_payload_id=raw_payload.id,
        source_id=source.id,
        first_ingestion_run_id=run.id,
        payload_text=ENTITY_BODY.decode("utf-8"),
    )
    assert raw_payload.payload_text is None


def test_duplicate_exact_content_reuses_row_and_freezes_first_context(db_session):
    source, exchange = _source_and_exchange(db_session)
    run_1, group_1, page_1 = _run_group_page(
        db_session,
        source=source,
        exchange=exchange,
        started_at=STARTED_AT,
        collector_name="duplicate_first",
    )
    first_url = "https://www.casablanca-bourse.com/api/prices?page%5Boffset%5D=0"
    first = record_response_occurrence(
        db_session,
        group_id=group_1.id,
        group_page_id=page_1.id,
        source_id=source.id,
        ingestion_run_id=run_1.id,
        entity_body=ENTITY_BODY,
        compatibility_context=_response_context(
            run_1,
            response_at=RESPONSE_AT,
            response_url=first_url,
        ),
        request_sequence=1,
        attempt_number=1,
        redirect_hop=0,
        logical_request_url=first_url,
        requested_url=first_url,
        response_url=first_url,
        source_endpoint="bvc_json_market_data",
        request_profile="bvc-json-safe-v1",
        requested_at=STARTED_AT,
        response_received_at=RESPONSE_AT,
        finished_at=FINISHED_AT,
        http_status=200,
        outcome="success_response",
        content_type="application/json",
        safe_response_headers={},
        dropped_response_header_name_count=0,
        response_headers_overflow=False,
        response_headers_policy_version="bvc-safe-response-headers-v1",
    )
    db_session.commit()
    assert first.raw_payload is not None
    first_raw_id = first.raw_payload.id
    first_snapshot = (
        first.raw_payload.ingestion_run_id,
        first.raw_payload.source_url,
        first.raw_payload.collected_at,
        first.raw_payload.http_status,
        first.raw_payload.metadata_,
    )

    later_at = RESPONSE_AT + timedelta(minutes=5)
    run_2, group_2, page_2 = _run_group_page(
        db_session,
        source=source,
        exchange=exchange,
        started_at=later_at - timedelta(seconds=1),
        collector_name="duplicate_second",
    )
    second_url = "https://www.casablanca-bourse.com/api/prices?page%5Boffset%5D=80"
    second = record_response_occurrence(
        db_session,
        group_id=group_2.id,
        group_page_id=page_2.id,
        source_id=source.id,
        ingestion_run_id=run_2.id,
        entity_body=ENTITY_BODY,
        compatibility_context=_response_context(
            run_2,
            response_at=later_at,
            response_url=second_url,
        ),
        request_sequence=1,
        attempt_number=1,
        redirect_hop=0,
        logical_request_url=second_url,
        requested_url=second_url,
        response_url=second_url,
        source_endpoint="bvc_json_market_data",
        request_profile="bvc-json-safe-v1",
        requested_at=later_at - timedelta(seconds=1),
        response_received_at=later_at,
        finished_at=later_at + timedelta(milliseconds=100),
        http_status=200,
        outcome="success_response",
        content_type="application/json",
        safe_response_headers={},
        dropped_response_header_name_count=0,
        response_headers_overflow=False,
        response_headers_policy_version="bvc-safe-response-headers-v1",
    )
    db_session.commit()

    assert second.raw_content_inserted is False
    assert second.raw_payload is not None
    assert second.raw_payload.id == first_raw_id
    assert db_session.query(RawPayload).count() == 1
    assert db_session.query(CollectionOccurrence).count() == 2
    assert (
        second.raw_payload.ingestion_run_id,
        second.raw_payload.source_url,
        second.raw_payload.collected_at,
        second.raw_payload.http_status,
        second.raw_payload.metadata_,
    ) == first_snapshot
    assert second.raw_payload.legacy_hash_algorithm == EXACT_COMPATIBILITY_HASH_ALGORITHM
    assert second.raw_payload.payload_hash == exact_compatibility_payload_hash(
        source_id=source.id,
        entity_body_sha256=exact_entity_body_sha256(ENTITY_BODY),
    )
    assert not fill_exact_raw_content_text_cache(
        db_session,
        raw_payload_id=first_raw_id,
        source_id=source.id,
        first_ingestion_run_id=run_2.id,
        payload_text="later context must not win",
    )

    update_raw_payload_metadata(db_session, second.raw_payload, {"page": 99})
    update_raw_payload_status(db_session, second.raw_payload, status="normalized")
    assert second.raw_payload.metadata_ is None
    assert second.raw_payload.status == "collected"


def test_transport_failure_has_no_raw_content_and_finalizes_failed(db_session):
    source, exchange = _source_and_exchange(db_session)
    run, group, page = _run_group_page(
        db_session,
        source=source,
        exchange=exchange,
        started_at=STARTED_AT,
        collector_name="transport_failure_test",
    )
    request_url = "https://www.casablanca-bourse.com/api/prices?page%5Boffset%5D=0"

    result = record_transport_failure_occurrence(
        db_session,
        group_id=group.id,
        group_page_id=page.id,
        source_id=source.id,
        ingestion_run_id=run.id,
        request_sequence=1,
        attempt_number=1,
        redirect_hop=0,
        logical_request_url=request_url,
        requested_url=request_url,
        source_endpoint="bvc_json_market_data",
        request_profile="bvc-json-safe-v1",
        requested_at=STARTED_AT,
        finished_at=FINISHED_AT,
        safe_error_code="timeout",
        response_headers_policy_version="bvc-safe-response-headers-v1",
    )
    db_session.commit()

    assert result.raw_payload is None
    assert result.occurrence.outcome == "transport_failure"
    assert result.occurrence.response_url is None
    assert db_session.query(RawPayload).count() == 0

    finalize_page_failure(
        db_session,
        group_id=group.id,
        group_page_id=page.id,
        finalized_at=SELECTED_AT,
        structural_reason_code="transport_failure",
    )
    finalize_collection_group_and_run(
        db_session,
        group_id=group.id,
        collection_status="failed",
        pagination_complete=False,
        completion_evidence_kind="none",
        finalized_at=FINALIZED_AT,
        collection_stop_reason="transport_failure",
        records_collected=0,
        records_inserted=0,
        records_updated=0,
        records_failed=1,
        safe_error_code="timeout",
    )
    db_session.commit()

    assert run.status == "failed"
    assert run.safe_error_code == "timeout"
    assert group.collection_completed_at is None
    assert db_session.query(CollectionPageSelection).count() == 0


def test_fixture_occurrence_uses_exact_bytes_without_http_evidence(db_session):
    source, exchange = _source_and_exchange(db_session)
    run, group, page = _run_group_page(
        db_session,
        source=source,
        exchange=exchange,
        started_at=STARTED_AT,
        collector_name="fixture_repository_test",
        collection_mode="manual_fixture",
        run_role="validation",
    )
    fixture_body = b"<html><body>synthetic fixture</body></html>"
    fixture_identifier = "manual-fixture://bvc_prices/local"
    context = ExactRawCompatibilityContext(
        ingestion_run_id=run.id,
        collected_at=FINISHED_AT,
        source_url=fixture_identifier,
        source_endpoint="manual_fixture",
        http_status=None,
        content_type="text/html",
        source_published_at=None,
    )

    result = record_fixture_occurrence(
        db_session,
        group_id=group.id,
        group_page_id=page.id,
        source_id=source.id,
        ingestion_run_id=run.id,
        entity_body=fixture_body,
        compatibility_context=context,
        request_sequence=1,
        logical_request_url=fixture_identifier,
        source_endpoint="manual_fixture",
        request_profile="bvc-fixture-safe-v1",
        requested_at=STARTED_AT,
        finished_at=FINISHED_AT,
        response_headers_policy_version="bvc-safe-response-headers-v1",
    )
    db_session.commit()

    assert result.raw_payload is not None
    assert result.raw_payload.content_evidence_kind == EXACT_CONTENT_EVIDENCE_KIND
    assert result.occurrence.outcome == "fixture_loaded"
    assert result.occurrence.response_url is None
    assert result.occurrence.response_received_at is None
    assert result.occurrence.http_status is None
    assert result.occurrence.source_published_at is None

    finalize_page_with_selection(
        db_session,
        group_id=group.id,
        group_page_id=page.id,
        occurrence_id=result.occurrence.id,
        page_role="data",
        selected_at=SELECTED_AT,
        selection_reason="fixture_selected",
    )
    finalize_collection_group_and_run(
        db_session,
        group_id=group.id,
        collection_status="success",
        pagination_complete=True,
        completion_evidence_kind="declared_fixture_scope",
        finalized_at=FINALIZED_AT,
        collection_stop_reason="declared_fixture_scope_complete",
        expected_pages=1,
        records_collected=1,
        records_inserted=1,
        records_updated=0,
        records_failed=0,
    )
    db_session.commit()

    assert group.collection_completed_at == FINISHED_AT.replace(tzinfo=None)
    assert group.collection_status == "success"
    assert run.run_role == "validation"
    assert run.status == "success"
    assert db_session.scalar(select(CollectionPageSelection.occurrence_id)) == result.occurrence.id
