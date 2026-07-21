from datetime import UTC, datetime
from uuid import uuid4

from tradehub_data.models import CollectionGroup, CollectionGroupPage, CollectionOccurrence


def _group(*, sequence: int | None = None) -> CollectionGroup:
    now = datetime.now(UTC)
    return CollectionGroup(
        id=uuid4(),
        group_sequence=sequence,
        source_id=uuid4(),
        exchange_id=uuid4(),
        ingestion_run_id=uuid4(),
        dataset_code="sqlite_identity_test",
        collection_mode="replay",
        group_purpose="validation",
        page_limit=1,
        started_at=now,
        collection_status="running",
        pagination_complete=None,
        completion_evidence_kind="none",
        coverage_status="unknown",
    )


def _transport_occurrence(
    group: CollectionGroup,
    *,
    sequence: int | None = None,
) -> tuple[CollectionGroupPage, CollectionOccurrence]:
    now = datetime.now(UTC)
    page = CollectionGroupPage(
        id=uuid4(),
        group_id=group.id,
        source_id=group.source_id,
        ingestion_run_id=group.ingestion_run_id,
        page_limit=1,
        logical_page_number=1,
        page_offset=0,
        page_role="unknown",
        collection_page_outcome="pending",
    )
    occurrence = CollectionOccurrence(
        id=uuid4(),
        occurrence_sequence=sequence,
        source_id=group.source_id,
        ingestion_run_id=group.ingestion_run_id,
        group_page_id=page.id,
        raw_payload_id=None,
        request_sequence=1,
        attempt_number=1,
        redirect_hop=0,
        logical_request_url="https://www.casablanca-bourse.com/prices",
        requested_url="https://www.casablanca-bourse.com/prices",
        response_url=None,
        request_profile="sqlite-test-v1",
        requested_at=now,
        response_received_at=None,
        finished_at=now,
        http_status=None,
        body_length=None,
        outcome="transport_failure",
        safe_error_code="network_error",
        safe_response_headers={},
        dropped_response_header_name_count=0,
        response_headers_overflow=False,
        response_headers_policy_version="sqlite-test-v1",
    )
    return page, occurrence


def test_sqlite_fixture_emulates_non_pk_audit_identity_columns(db_session):
    explicit_group = _group(sequence=10)
    automatic_group = _group()
    explicit_page, explicit_occurrence = _transport_occurrence(
        explicit_group,
        sequence=20,
    )
    automatic_page, automatic_occurrence = _transport_occurrence(automatic_group)

    db_session.add_all(
        [
            explicit_group,
            automatic_group,
            explicit_page,
            automatic_page,
            explicit_occurrence,
            automatic_occurrence,
        ]
    )
    db_session.flush()

    assert explicit_group.group_sequence == 10
    assert automatic_group.group_sequence == 11
    assert explicit_occurrence.occurrence_sequence == 20
    assert automatic_occurrence.occurrence_sequence == 21
