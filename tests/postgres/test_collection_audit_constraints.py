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
    COLLECTION_AUDIT_REVISION,
    make_database_engine,
    upgrade_database,
)


pytestmark = pytest.mark.postgres


STARTED_AT = datetime(2026, 7, 21, 9, 0, tzinfo=UTC)
FINISHED_AT = STARTED_AT + timedelta(seconds=1)
PAGE_LIMIT = 80


@dataclass(frozen=True)
class AuditDatabase:
    engine: Engine
    tables: dict[str, Table]
    ids: dict[str, uuid.UUID]


@pytest.fixture()
def audit_database(empty_postgres_database_url) -> AuditDatabase:
    """Migrate a disposable database and seed only synthetic prerequisite rows."""

    upgrade_database(empty_postgres_database_url, COLLECTION_AUDIT_REVISION)
    engine = make_database_engine(empty_postgres_database_url)
    table_names = (
        "data_sources",
        "exchanges",
        "ingestion_runs",
        "raw_payloads",
        "collection_groups",
        "collection_group_pages",
        "collection_occurrences",
    )
    tables = {
        table_name: Base.metadata.tables[table_name]
        for table_name in table_names
    }
    database = AuditDatabase(engine=engine, tables=tables, ids=_new_graph_ids())
    try:
        _seed_valid_graph(database)
        yield database
    finally:
        engine.dispose()


def _new_graph_ids() -> dict[str, uuid.UUID]:
    return {
        name: uuid.uuid4()
        for name in (
            "exchange",
            "source",
            "run",
            "raw_payload",
            "group",
            "page",
            "alternate_source",
            "alternate_run",
        )
    }


def _seed_valid_graph(database: AuditDatabase) -> None:
    tables = database.tables
    ids = database.ids
    body = b'{"synthetic":true}'
    entity_digest = hashlib.sha256(body).hexdigest()

    with database.engine.begin() as connection:
        connection.execute(
            tables["exchanges"].insert(),
            {
                "id": ids["exchange"],
                "code": "BVC-AUDIT-TEST",
                "name": "Synthetic BVC audit exchange",
                "country_code": "MA",
                "currency_code": "MAD",
                "timezone": "Africa/Casablanca",
            },
        )
        connection.execute(
            tables["data_sources"].insert(),
            [
                {
                    "id": ids["source"],
                    "code": "bvc_audit_test",
                    "name": "Synthetic BVC audit source",
                    "source_type": "exchange",
                    "base_url": "https://example.test",
                    "country_code": "MA",
                },
                {
                    "id": ids["alternate_source"],
                    "code": "bvc_audit_test_alternate",
                    "name": "Synthetic alternate audit source",
                    "source_type": "exchange",
                    "base_url": "https://alternate.example.test",
                    "country_code": "MA",
                },
            ],
        )
        connection.execute(
            tables["ingestion_runs"].insert(),
            [
                _run_values(ids["run"], ids["source"]),
                _run_values(ids["alternate_run"], ids["alternate_source"]),
            ],
        )
        connection.execute(
            tables["raw_payloads"].insert(),
            _raw_values(
                ids["raw_payload"],
                ids["source"],
                ids["run"],
                body=body,
                entity_digest=entity_digest,
            ),
        )
        connection.execute(
            tables["collection_groups"].insert(),
            _group_values(database, group_id=ids["group"]),
        )
        connection.execute(
            tables["collection_group_pages"].insert(),
            _page_values(database, page_id=ids["page"], logical_page_number=1),
        )


def _run_values(
    run_id: uuid.UUID,
    source_id: uuid.UUID,
    **overrides: Any,
) -> dict[str, Any]:
    values: dict[str, Any] = {
        "id": run_id,
        "source_id": source_id,
        "collector_name": "synthetic_constraint_test",
        "run_type": "manual",
        "status": "success",
        "started_at": STARTED_AT,
        "finished_at": FINISHED_AT,
        "records_collected": 1,
        "records_inserted": 0,
        "records_updated": 0,
        "records_failed": 0,
        "run_role": "acquisition",
    }
    values.update(overrides)
    return values


def _raw_values(
    raw_payload_id: uuid.UUID,
    source_id: uuid.UUID,
    ingestion_run_id: uuid.UUID,
    *,
    body: bytes = b"synthetic exact entity body",
    entity_digest: str | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    digest = entity_digest or hashlib.sha256(body).hexdigest()
    values: dict[str, Any] = {
        "id": raw_payload_id,
        "source_id": source_id,
        "ingestion_run_id": ingestion_run_id,
        "source_url": "https://example.test/bvc/prices",
        "source_endpoint": "/bvc/prices",
        "payload_type": "json",
        "payload": None,
        "payload_text": None,
        # This remains the independent legacy hash identity.
        "payload_hash": hashlib.sha256(f"legacy:{raw_payload_id}".encode()).hexdigest(),
        "http_status": 200,
        "content_type": "application/json",
        "collected_at": STARTED_AT,
        "status": "collected",
        "entity_body": body,
        "entity_body_sha256": digest,
        "entity_body_length": len(body),
        "content_evidence_kind": "exact_entity_bytes",
        "entity_hash_algorithm": "sha256_entity_body_v1",
        "storage_status": "stored",
        "legacy_hash_algorithm": None,
    }
    values.update(overrides)
    return values


def _group_values(
    database: AuditDatabase,
    *,
    group_id: uuid.UUID | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    values: dict[str, Any] = {
        "id": group_id or uuid.uuid4(),
        "source_id": database.ids["source"],
        "exchange_id": database.ids["exchange"],
        "ingestion_run_id": database.ids["run"],
        "dataset_code": "bvc_prices",
        "collection_mode": "live_json",
        "group_purpose": "validation",
        "page_limit": PAGE_LIMIT,
        "started_at": STARTED_AT,
        "collection_completed_at": FINISHED_AT,
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
        "finalized_at": FINISHED_AT,
    }
    values.update(overrides)
    return values


def _page_values(
    database: AuditDatabase,
    *,
    page_id: uuid.UUID | None = None,
    logical_page_number: int = 2,
    **overrides: Any,
) -> dict[str, Any]:
    values: dict[str, Any] = {
        "id": page_id or uuid.uuid4(),
        "group_id": database.ids["group"],
        "source_id": database.ids["source"],
        "ingestion_run_id": database.ids["run"],
        "page_limit": PAGE_LIMIT,
        "logical_page_number": logical_page_number,
        "page_offset": (logical_page_number - 1) * PAGE_LIMIT,
        "page_role": "data",
        "collection_page_outcome": "success",
        "finalized_at": FINISHED_AT,
    }
    values.update(overrides)
    return values


def _occurrence_values(
    database: AuditDatabase,
    *,
    outcome: str = "success_response",
    request_sequence: int = 100,
    **overrides: Any,
) -> dict[str, Any]:
    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "source_id": database.ids["source"],
        "ingestion_run_id": database.ids["run"],
        "group_page_id": database.ids["page"],
        "raw_payload_id": database.ids["raw_payload"],
        "request_sequence": request_sequence,
        "attempt_number": 1,
        "redirect_hop": 0,
        "logical_request_url": "https://example.test/bvc/prices",
        "requested_url": "https://example.test/bvc/prices",
        "response_url": "https://example.test/bvc/prices",
        "request_profile": "synthetic_test_v1",
        "requested_at": STARTED_AT,
        "response_received_at": FINISHED_AT,
        "finished_at": FINISHED_AT,
        "http_status": 200,
        "content_type": "application/json",
        "body_length": 18,
        "outcome": outcome,
        "safe_error_code": None,
        "safe_response_headers": {"content-type": "application/json"},
        "dropped_response_header_name_count": 0,
        "response_headers_overflow": False,
        "response_headers_policy_version": "allowlist_v1",
    }
    values.update(overrides)
    return values


def _valid_occurrence_values(
    database: AuditDatabase,
    outcome: str,
    request_sequence: int,
) -> dict[str, Any]:
    common = {"outcome": outcome, "request_sequence": request_sequence}
    if outcome == "success_response":
        return _occurrence_values(database, http_status=200, **common)
    if outcome == "redirect_response":
        return _occurrence_values(database, http_status=302, redirect_hop=1, **common)
    if outcome == "http_error_response":
        return _occurrence_values(database, http_status=503, **common)
    if outcome == "transport_failure":
        return _occurrence_values(
            database,
            raw_payload_id=None,
            response_url=None,
            response_received_at=None,
            http_status=None,
            body_length=None,
            safe_error_code="connect_timeout",
            **common,
        )
    if outcome == "fixture_loaded":
        return _occurrence_values(
            database,
            response_url=None,
            response_received_at=None,
            http_status=None,
            **common,
        )
    raise AssertionError(f"unhandled synthetic occurrence outcome: {outcome}")


def _assert_rejected(
    database: AuditDatabase,
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


def test_allowed_run_roles_and_status_lifecycles_succeed(audit_database: AuditDatabase):
    role_statuses = (
        ("acquisition", "running"),
        ("authoritative_pipeline", "success"),
        ("validation", "partial_success"),
        ("backfill", "failed"),
        ("publication_retry", "success"),
        ("legacy_unclassified", "success"),
    )
    rows = []
    for run_role, status in role_statuses:
        rows.append(
            _run_values(
                uuid.uuid4(),
                audit_database.ids["source"],
                run_role=run_role,
                status=status,
                finished_at=None if status == "running" else FINISHED_AT,
            )
        )

    with audit_database.engine.begin() as connection:
        connection.execute(audit_database.tables["ingestion_runs"].insert(), rows)


def test_run_role_lifecycle_and_counter_constraints_reject_invalid_rows(
    audit_database: AuditDatabase,
):
    source_id = audit_database.ids["source"]
    invalid_cases = (
        (
            {"run_role": "unknown"},
            "ck_ingestion_runs_run_role",
        ),
        (
            {"status": "running", "finished_at": FINISHED_AT},
            "ck_ingestion_runs_status_finished_at",
        ),
        (
            {"status": "failed", "finished_at": None},
            "ck_ingestion_runs_status_finished_at",
        ),
        (
            {"started_at": FINISHED_AT, "finished_at": STARTED_AT},
            "ck_ingestion_runs_finished_at_order",
        ),
        (
            {"records_collected": -1},
            "ck_ingestion_runs_records_collected_nonnegative",
        ),
        (
            {"records_inserted": -1},
            "ck_ingestion_runs_records_inserted_nonnegative",
        ),
        (
            {"records_updated": -1},
            "ck_ingestion_runs_records_updated_nonnegative",
        ),
        (
            {"records_failed": -1},
            "ck_ingestion_runs_records_failed_nonnegative",
        ),
    )
    for overrides, expected_constraint in invalid_cases:
        _assert_rejected(
            audit_database,
            "ingestion_runs",
            _run_values(uuid.uuid4(), source_id, **overrides),
            expected_constraint,
        )

    # An unknown status violates both the vocabulary and lifecycle expression;
    # the important invariant is rejection, independent of PostgreSQL check order.
    _assert_rejected(
        audit_database,
        "ingestion_runs",
        _run_values(uuid.uuid4(), source_id, status="unknown"),
    )


def test_exact_raw_content_constraints_and_deduplication(
    audit_database: AuditDatabase,
):
    ids = audit_database.ids
    for missing_field in (
        "entity_body",
        "entity_body_sha256",
        "entity_body_length",
        "entity_hash_algorithm",
    ):
        _assert_rejected(
            audit_database,
            "raw_payloads",
            _raw_values(
                uuid.uuid4(),
                ids["source"],
                ids["run"],
                **{missing_field: None},
            ),
            "ck_raw_payloads_exact_fields_present",
        )

    _assert_rejected(
        audit_database,
        "raw_payloads",
        _raw_values(
            uuid.uuid4(),
            ids["source"],
            ids["run"],
            entity_body_sha256="A" * 64,
        ),
        "ck_raw_payloads_exact_hash_format",
    )
    _assert_rejected(
        audit_database,
        "raw_payloads",
        _raw_values(
            uuid.uuid4(),
            ids["source"],
            ids["run"],
            entity_hash_algorithm="sha256",
        ),
        "ck_raw_payloads_exact_hash_algorithm",
    )
    _assert_rejected(
        audit_database,
        "raw_payloads",
        _raw_values(
            uuid.uuid4(),
            ids["source"],
            ids["run"],
            entity_body_length=999,
        ),
        "ck_raw_payloads_exact_body_length",
    )

    existing_entity_hash = hashlib.sha256(b'{"synthetic":true}').hexdigest()
    _assert_rejected(
        audit_database,
        "raw_payloads",
        _raw_values(
            uuid.uuid4(),
            ids["source"],
            ids["run"],
            body=b"different bytes are deliberately not digest-checked here",
            entity_digest=existing_entity_hash,
        ),
        "uq_raw_payloads_source_entity_sha256",
    )


def test_raw_content_vocabularies_and_legacy_algorithm_guard(
    audit_database: AuditDatabase,
):
    ids = audit_database.ids
    base = _raw_values(uuid.uuid4(), ids["source"], ids["run"])
    _assert_rejected(
        audit_database,
        "raw_payloads",
        {**base, "content_evidence_kind": "unknown"},
        "ck_raw_payloads_content_evidence_kind",
    )
    _assert_rejected(
        audit_database,
        "raw_payloads",
        {**base, "storage_status": "missing"},
        "ck_raw_payloads_storage_status",
    )
    _assert_rejected(
        audit_database,
        "raw_payloads",
        {
            **base,
            "content_evidence_kind": "legacy_decoded_text",
            "entity_body": None,
            "entity_body_sha256": None,
            "entity_body_length": None,
            "entity_hash_algorithm": "sha256_entity_body_v1",
        },
        "ck_raw_payloads_nonexact_entity_algorithm",
    )
    _assert_rejected(
        audit_database,
        "raw_payloads",
        {
            **base,
            "content_evidence_kind": "legacy_decoded_text",
            "entity_body": None,
            "entity_body_sha256": None,
            "entity_body_length": -1,
            "entity_hash_algorithm": None,
        },
        "ck_raw_payloads_entity_body_length_nonnegative",
    )


def test_transitional_raw_model_default_matches_jsonb_null_storage(
    audit_database: AuditDatabase,
):
    raw_payload_id = uuid.uuid4()
    with audit_database.engine.begin() as connection:
        connection.execute(
            audit_database.tables["raw_payloads"].insert(),
            {
                "id": raw_payload_id,
                "source_id": audit_database.ids["source"],
                "ingestion_run_id": audit_database.ids["run"],
                "payload_type": "json",
                "payload": None,
                "payload_text": None,
                "payload_hash": hashlib.sha256(b"legacy-json-null").hexdigest(),
                "collected_at": STARTED_AT,
                "status": "collected",
            },
        )
        row = connection.execute(
            text(
                "SELECT payload IS NULL AS payload_is_sql_null, content_evidence_kind, "
                "storage_status, legacy_hash_algorithm "
                "FROM raw_payloads WHERE id = :raw_payload_id"
            ),
            {"raw_payload_id": raw_payload_id},
        ).one()

    assert row.payload_is_sql_null is False
    assert row.content_evidence_kind == "legacy_jsonb_only"
    assert row.storage_status == "stored"
    assert row.legacy_hash_algorithm == "unknown_legacy"


def test_group_controlled_values_and_state_constraints_reject_invalid_rows(
    audit_database: AuditDatabase,
):
    invalid_cases = (
        ({"collection_mode": "unknown"}, "ck_collection_groups_collection_mode"),
        ({"group_purpose": "unknown"}, "ck_collection_groups_group_purpose"),
        ({"coverage_status": "partial"}, "ck_collection_groups_coverage_status"),
        (
            {"completion_evidence_kind": "guessed"},
            "ck_collection_groups_completion_evidence_kind",
        ),
        ({"page_limit": 0}, "ck_collection_groups_page_limit_positive"),
        ({"expected_pages": -1}, "ck_collection_groups_expected_pages_nonnegative"),
        (
            {"selected_data_pages": -1},
            "ck_collection_groups_selected_data_pages_nonnegative",
        ),
        (
            {"expected_instrument_count": -1},
            "ck_collection_groups_expected_instrument_count_nonnegative",
        ),
        (
            {"observed_instrument_count": -1},
            "ck_collection_groups_observed_instrument_count_nonnegative",
        ),
        (
            {"collection_completed_at": STARTED_AT - timedelta(seconds=1)},
            "ck_collection_groups_collection_completed_at_order",
        ),
        (
            {"finalized_at": STARTED_AT - timedelta(seconds=1)},
            "ck_collection_groups_finalized_at_order",
        ),
        (
            {"collection_status": "running", "finalized_at": FINISHED_AT},
            "ck_collection_groups_status_finalized_at",
        ),
        (
            {"collection_status": "failed", "finalized_at": None},
            "ck_collection_groups_status_finalized_at",
        ),
        (
            {"pagination_complete": False},
            "ck_collection_groups_success_pagination_complete",
        ),
        (
            {"group_purpose": "production", "collection_mode": "manual_fixture"},
            "ck_collection_groups_production_not_manual_fixture",
        ),
    )
    for overrides, expected_constraint in invalid_cases:
        _assert_rejected(
            audit_database,
            "collection_groups",
            _group_values(audit_database, **overrides),
            expected_constraint,
        )

    # Unknown status also contradicts the lifecycle check; rejection proves the
    # controlled vocabulary without relying on constraint evaluation order.
    _assert_rejected(
        audit_database,
        "collection_groups",
        _group_values(audit_database, collection_status="unknown"),
    )


def test_page_controlled_values_lifecycle_and_offset_constraints(
    audit_database: AuditDatabase,
):
    invalid_cases = (
        {"page_role": "unknown_role"},
        {"collection_page_outcome": "unknown_outcome"},
        {"logical_page_number": 0, "page_offset": 0},
        {"page_offset": -1},
        {"page_limit": 0, "page_offset": 0},
        {"logical_page_number": 2, "page_offset": 1},
        {"collection_page_outcome": "pending", "finalized_at": FINISHED_AT},
        {"collection_page_outcome": "failed", "finalized_at": None},
    )
    for overrides in invalid_cases:
        _assert_rejected(
            audit_database,
            "collection_group_pages",
            _page_values(audit_database, **overrides),
        )

    coherence_mismatches = (
        {
            "source_id": audit_database.ids["alternate_source"],
            "ingestion_run_id": audit_database.ids["alternate_run"],
        },
        {"ingestion_run_id": audit_database.ids["alternate_run"]},
        {"page_limit": PAGE_LIMIT // 2, "page_offset": PAGE_LIMIT // 2},
    )
    for mismatch in coherence_mismatches:
        _assert_rejected(
            audit_database,
            "collection_group_pages",
            _page_values(audit_database, **mismatch),
            "fk_collection_group_pages_group_source_run_limit",
        )


def test_all_valid_occurrence_evidence_shapes_succeed(audit_database: AuditDatabase):
    outcomes = (
        "success_response",
        "redirect_response",
        "http_error_response",
        "transport_failure",
        "fixture_loaded",
    )
    rows = [
        _valid_occurrence_values(audit_database, outcome, sequence)
        for sequence, outcome in enumerate(outcomes, start=1)
    ]
    with audit_database.engine.begin() as connection:
        connection.execute(audit_database.tables["collection_occurrences"].insert(), rows)


def test_contradictory_occurrence_evidence_shapes_are_rejected(
    audit_database: AuditDatabase,
):
    invalid_rows = []
    for outcome in ("success_response", "redirect_response", "http_error_response"):
        valid_response = _valid_occurrence_values(audit_database, outcome, 200)
        for required_field in (
            "raw_payload_id",
            "response_url",
            "response_received_at",
            "http_status",
        ):
            invalid_rows.append({**valid_response, required_field: None})

    invalid_rows.extend(
        (
            {
                **_valid_occurrence_values(audit_database, "success_response", 200),
                "http_status": 302,
            },
            {
                **_valid_occurrence_values(audit_database, "redirect_response", 200),
                "http_status": 200,
            },
            {
                **_valid_occurrence_values(audit_database, "http_error_response", 200),
                "http_status": 302,
            },
        )
    )

    valid_transport = _valid_occurrence_values(audit_database, "transport_failure", 200)
    invalid_rows.extend(
        (
            {**valid_transport, "raw_payload_id": audit_database.ids["raw_payload"]},
            {**valid_transport, "response_url": "https://example.test/response"},
            {**valid_transport, "response_received_at": FINISHED_AT},
            {**valid_transport, "http_status": 503},
            {**valid_transport, "safe_error_code": None},
        )
    )

    valid_fixture = _valid_occurrence_values(audit_database, "fixture_loaded", 200)
    invalid_rows.extend(
        (
            {**valid_fixture, "raw_payload_id": None},
            {**valid_fixture, "response_url": "https://example.test/fixture"},
            {**valid_fixture, "response_received_at": FINISHED_AT},
            {**valid_fixture, "http_status": 200},
        )
    )

    for values in invalid_rows:
        values["id"] = uuid.uuid4()
        _assert_rejected(
            audit_database,
            "collection_occurrences",
            values,
            "ck_collection_occurrences_outcome_evidence",
        )


def test_occurrence_basic_constraints_reject_invalid_rows(audit_database: AuditDatabase):
    base = _valid_occurrence_values(audit_database, "success_response", 300)
    invalid_cases = (
        {"outcome": "unknown"},
        {"request_sequence": 0},
        {"attempt_number": 0},
        {"redirect_hop": -1},
        {"requested_at": FINISHED_AT + timedelta(seconds=1)},
        {"response_received_at": STARTED_AT - timedelta(seconds=1)},
        {"http_status": 99},
        {"body_length": -1},
        {"dropped_response_header_name_count": -1},
        {"safe_response_headers": []},
    )
    for overrides in invalid_cases:
        _assert_rejected(
            audit_database,
            "collection_occurrences",
            {**base, "id": uuid.uuid4(), **overrides},
        )


def test_occurrence_source_run_page_mismatch_uses_composite_fk(
    audit_database: AuditDatabase,
):
    values = _valid_occurrence_values(audit_database, "transport_failure", 400)
    values.update(
        {
            "source_id": audit_database.ids["alternate_source"],
            "ingestion_run_id": audit_database.ids["alternate_run"],
        }
    )
    _assert_rejected(
        audit_database,
        "collection_occurrences",
        values,
        "fk_collection_occurrences_group_page_source_run",
    )
