from collections.abc import Iterable
from typing import Any

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection, Engine

from tests.postgres.harness import (
    COLLECTION_AUDIT_ALL_MIGRATED_TABLES,
    COLLECTION_AUDIT_REVISION,
    COLLECTION_AUDIT_TABLES,
    FOUNDATION_ALL_MIGRATED_TABLES,
    FOUNDATION_REVISION,
    LegacyFoundationBaseline,
    database_revision,
    downgrade_database,
    make_database_engine,
    public_table_names,
    seed_legacy_raw_evidence_variants,
    upgrade_database,
)


pytestmark = pytest.mark.postgres


RUN_ADDED_COLUMNS = {"run_role", "parent_run_id", "safe_error_code"}
RAW_ADDED_COLUMNS = {
    "entity_body",
    "entity_body_sha256",
    "entity_body_length",
    "content_evidence_kind",
    "entity_hash_algorithm",
    "storage_status",
    "legacy_hash_algorithm",
}
COLLECTION_GROUP_COLUMNS = {
    "id",
    "group_sequence",
    "source_id",
    "exchange_id",
    "ingestion_run_id",
    "dataset_code",
    "collection_mode",
    "group_purpose",
    "external_group_key",
    "page_limit",
    "started_at",
    "collection_completed_at",
    "collection_status",
    "pagination_complete",
    "completion_evidence_kind",
    "expected_pages",
    "selected_data_pages",
    "terminal_page_present",
    "coverage_status",
    "expected_instrument_count",
    "observed_instrument_count",
    "collection_stop_reason",
    "safe_diagnostic_codes",
    "finalized_at",
    "created_at",
}
COLLECTION_GROUP_PAGE_COLUMNS = {
    "id",
    "group_id",
    "source_id",
    "ingestion_run_id",
    "page_limit",
    "logical_page_number",
    "page_offset",
    "page_role",
    "collection_page_outcome",
    "structural_reason_code",
    "created_at",
    "finalized_at",
}
COLLECTION_OCCURRENCE_COLUMNS = {
    "id",
    "occurrence_sequence",
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
    "created_at",
}

EXPECTED_UNIQUES = {
    "ingestion_runs": {"uq_ingestion_runs_id_source_id": ("id", "source_id")},
    "raw_payloads": {"uq_raw_payloads_id_source_id": ("id", "source_id")},
    "collection_groups": {
        "uq_collection_groups_group_sequence": ("group_sequence",),
        "uq_collection_groups_id_source_id": ("id", "source_id"),
        "uq_collection_groups_id_source_id_ingestion_run_id": (
            "id",
            "source_id",
            "ingestion_run_id",
        ),
        "uq_collection_groups_id_source_run_page_limit": (
            "id",
            "source_id",
            "ingestion_run_id",
            "page_limit",
        ),
    },
    "collection_group_pages": {
        "uq_collection_group_pages_group_page_number": (
            "group_id",
            "logical_page_number",
        ),
        "uq_collection_group_pages_group_page_offset": ("group_id", "page_offset"),
        "uq_collection_group_pages_id_source_run": (
            "id",
            "source_id",
            "ingestion_run_id",
        ),
        "uq_collection_group_pages_id_source_group": ("id", "source_id", "group_id"),
    },
    "collection_occurrences": {
        "uq_collection_occurrences_occurrence_sequence": ("occurrence_sequence",),
        "uq_collection_occurrences_run_request_attempt_redirect": (
            "ingestion_run_id",
            "request_sequence",
            "attempt_number",
            "redirect_hop",
        ),
        "uq_collection_occurrences_id_group_page": ("id", "group_page_id"),
        "uq_collection_occurrences_id_source_raw": (
            "id",
            "source_id",
            "raw_payload_id",
        ),
        "uq_collection_occurrences_id_source_raw_group_page": (
            "id",
            "source_id",
            "raw_payload_id",
            "group_page_id",
        ),
    },
}
EXPECTED_CHECK_NAMES = {
    "ingestion_runs": {
        "ck_ingestion_runs_run_role",
        "ck_ingestion_runs_status",
        "ck_ingestion_runs_records_collected_nonnegative",
        "ck_ingestion_runs_records_inserted_nonnegative",
        "ck_ingestion_runs_records_updated_nonnegative",
        "ck_ingestion_runs_records_failed_nonnegative",
        "ck_ingestion_runs_status_finished_at",
        "ck_ingestion_runs_finished_at_order",
    },
    "raw_payloads": {
        "ck_raw_payloads_content_evidence_kind",
        "ck_raw_payloads_storage_status",
        "ck_raw_payloads_entity_body_length_nonnegative",
        "ck_raw_payloads_exact_fields_present",
        "ck_raw_payloads_exact_hash_format",
        "ck_raw_payloads_exact_hash_algorithm",
        "ck_raw_payloads_exact_body_length",
        "ck_raw_payloads_nonexact_entity_algorithm",
    },
    "collection_groups": {
        "ck_collection_groups_collection_mode",
        "ck_collection_groups_group_purpose",
        "ck_collection_groups_collection_status",
        "ck_collection_groups_coverage_status",
        "ck_collection_groups_completion_evidence_kind",
        "ck_collection_groups_page_limit_positive",
        "ck_collection_groups_expected_pages_nonnegative",
        "ck_collection_groups_selected_data_pages_nonnegative",
        "ck_collection_groups_expected_instrument_count_nonnegative",
        "ck_collection_groups_observed_instrument_count_nonnegative",
        "ck_collection_groups_collection_completed_at_order",
        "ck_collection_groups_finalized_at_order",
        "ck_collection_groups_status_finalized_at",
        "ck_collection_groups_success_pagination_complete",
        "ck_collection_groups_production_not_manual_fixture",
    },
    "collection_group_pages": {
        "ck_collection_group_pages_page_role",
        "ck_collection_group_pages_outcome",
        "ck_collection_group_pages_page_number_positive",
        "ck_collection_group_pages_page_offset_nonnegative",
        "ck_collection_group_pages_page_limit_positive",
        "ck_collection_group_pages_offset_formula",
        "ck_collection_group_pages_outcome_finalized_at",
    },
    "collection_occurrences": {
        "ck_collection_occurrences_outcome",
        "ck_collection_occurrences_request_sequence_positive",
        "ck_collection_occurrences_attempt_number_positive",
        "ck_collection_occurrences_redirect_hop_nonnegative",
        "ck_collection_occurrences_requested_finished_order",
        "ck_collection_occurrences_response_time_order",
        "ck_collection_occurrences_http_status_range",
        "ck_collection_occurrences_body_length_nonnegative",
        "ck_collection_occurrences_dropped_header_count_nonnegative",
        "ck_collection_occurrences_safe_headers_object",
        "ck_collection_occurrences_outcome_evidence",
    },
}
EXPECTED_FOREIGN_KEYS = {
    "ingestion_runs": {
        "fk_ingestion_runs_parent_run_id_ingestion_runs": (
            ("parent_run_id",),
            "ingestion_runs",
            ("id",),
        ),
    },
    "collection_groups": {
        "fk_collection_groups_source_id_data_sources": (
            ("source_id",),
            "data_sources",
            ("id",),
        ),
        "fk_collection_groups_exchange_id_exchanges": (
            ("exchange_id",),
            "exchanges",
            ("id",),
        ),
        "fk_collection_groups_ingestion_run_source": (
            ("ingestion_run_id", "source_id"),
            "ingestion_runs",
            ("id", "source_id"),
        ),
    },
    "collection_group_pages": {
        "fk_collection_group_pages_group_source_run_limit": (
            ("group_id", "source_id", "ingestion_run_id", "page_limit"),
            "collection_groups",
            ("id", "source_id", "ingestion_run_id", "page_limit"),
        ),
    },
    "collection_occurrences": {
        "fk_collection_occurrences_ingestion_run_source": (
            ("ingestion_run_id", "source_id"),
            "ingestion_runs",
            ("id", "source_id"),
        ),
        "fk_collection_occurrences_group_page_source_run": (
            ("group_page_id", "source_id", "ingestion_run_id"),
            "collection_group_pages",
            ("id", "source_id", "ingestion_run_id"),
        ),
        "fk_collection_occurrences_raw_payload_source": (
            ("raw_payload_id", "source_id"),
            "raw_payloads",
            ("id", "source_id"),
        ),
    },
}
EXPECTED_INDEXES = {
    "raw_payloads": {
        "uq_raw_payloads_source_entity_sha256": ("source_id", "entity_body_sha256"),
    },
    "collection_groups": {
        "ix_collection_groups_source_sequence": ("source_id", "group_sequence"),
        "ix_collection_groups_exchange_dataset_purpose_sequence": (
            "exchange_id",
            "dataset_code",
            "group_purpose",
            "group_sequence",
        ),
        "ix_collection_groups_status_sequence": ("collection_status", "group_sequence"),
        "ix_collection_groups_pagination_sequence": (
            "pagination_complete",
            "group_sequence",
        ),
    },
    "collection_group_pages": {
        "ix_collection_group_pages_outcome_page_number": (
            "group_id",
            "collection_page_outcome",
            "logical_page_number",
        ),
    },
    "collection_occurrences": {
        "ix_collection_occurrences_source_sequence": ("source_id", "occurrence_sequence"),
        "ix_collection_occurrences_ingestion_run_sequence": (
            "ingestion_run_id",
            "occurrence_sequence",
        ),
        "ix_collection_occurrences_group_page_sequence": (
            "group_page_id",
            "occurrence_sequence",
        ),
        "ix_collection_occurrences_raw_payload_sequence": (
            "raw_payload_id",
            "occurrence_sequence",
        ),
        "ix_collection_occurrences_outcome_sequence": ("outcome", "occurrence_sequence"),
    },
}

LEGACY_COLUMNS = {
    "ingestion_runs": (
        "id",
        "source_id",
        "collector_name",
        "run_type",
        "status",
        "started_at",
        "finished_at",
        "records_collected",
        "records_inserted",
        "records_updated",
        "records_failed",
        "error_message",
        "metadata",
        "created_at",
    ),
    "raw_payloads": (
        "id",
        "source_id",
        "ingestion_run_id",
        "source_url",
        "source_endpoint",
        "payload_type",
        "payload",
        "payload_text",
        "payload_hash",
        "http_status",
        "content_type",
        "collected_at",
        "source_published_at",
        "status",
        "error_message",
        "metadata",
        "created_at",
    ),
    "latest_prices": (
        "id",
        "instrument_id",
        "price",
        "open_price",
        "high_price",
        "low_price",
        "previous_close",
        "change_value",
        "change_percent",
        "volume",
        "traded_value",
        "market_cap",
        "price_timestamp",
        "trading_date",
        "source_id",
        "raw_payload_id",
        "data_quality_status",
        "metadata",
        "created_at",
        "updated_at",
    ),
    "price_bars": (
        "id",
        "instrument_id",
        "timeframe",
        "bar_timestamp",
        "trading_date",
        "open_price",
        "high_price",
        "low_price",
        "close_price",
        "volume",
        "traded_value",
        "number_of_trades",
        "source_id",
        "raw_payload_id",
        "is_adjusted",
        "data_quality_status",
        "metadata",
        "created_at",
        "updated_at",
    ),
    "normalization_errors": (
        "id",
        "raw_payload_id",
        "ingestion_run_id",
        "source_id",
        "entity_type",
        "error_type",
        "error_message",
        "raw_fragment",
        "status",
        "created_at",
        "resolved_at",
    ),
}

BASELINE_ID_TABLES = (
    ("exchanges", "exchange"),
    ("data_sources", "source"),
    ("ingestion_runs", "run"),
    ("raw_payloads", "raw_payload"),
    ("instruments", "instrument"),
    ("latest_prices", "latest_price"),
    ("price_bars", "price_bar"),
    ("normalization_errors", "normalization_error"),
    ("sync_states", "sync_state"),
)


def _column_names(inspector, table_name: str) -> set[str]:
    return {column["name"] for column in inspector.get_columns(table_name, schema="public")}


def _constraint_names(items: Iterable[dict[str, Any]]) -> set[str]:
    return {item["name"] for item in items if item.get("name") is not None}


def _snapshot_legacy_rows(connection: Connection) -> dict[str, list[dict[str, Any]]]:
    snapshot: dict[str, list[dict[str, Any]]] = {}
    for table_name, column_names in LEGACY_COLUMNS.items():
        selected_columns = ", ".join(f'"{name}"' for name in column_names)
        statement = text(
            f'SELECT {selected_columns} FROM "{table_name}" ORDER BY "id"'
        )
        snapshot[table_name] = [
            dict(row) for row in connection.execute(statement).mappings()
        ]
    return snapshot


def _assert_baseline_ids(
    connection: Connection,
    baseline: LegacyFoundationBaseline,
) -> None:
    for table_name, id_key in BASELINE_ID_TABLES:
        persisted_id = connection.execute(
            text(f'SELECT id FROM "{table_name}" WHERE id = :expected_id'),
            {"expected_id": baseline.ids[id_key]},
        ).scalar_one()
        assert persisted_id == baseline.ids[id_key]


def _assert_audit_catalog(engine: Engine) -> None:
    inspector = inspect(engine)
    assert _column_names(inspector, "ingestion_runs").issuperset(RUN_ADDED_COLUMNS)
    assert _column_names(inspector, "raw_payloads").issuperset(RAW_ADDED_COLUMNS)
    assert _column_names(inspector, "collection_groups") == COLLECTION_GROUP_COLUMNS
    assert _column_names(inspector, "collection_group_pages") == COLLECTION_GROUP_PAGE_COLUMNS
    assert _column_names(inspector, "collection_occurrences") == COLLECTION_OCCURRENCE_COLUMNS

    for table_name, expected_constraints in EXPECTED_UNIQUES.items():
        actual_constraints = {
            constraint["name"]: tuple(constraint["column_names"])
            for constraint in inspector.get_unique_constraints(table_name, schema="public")
        }
        for constraint_name, expected_columns in expected_constraints.items():
            assert actual_constraints[constraint_name] == expected_columns

    for table_name, expected_names in EXPECTED_CHECK_NAMES.items():
        actual_names = _constraint_names(
            inspector.get_check_constraints(table_name, schema="public")
        )
        assert actual_names.issuperset(expected_names)

    for table_name, expected_constraints in EXPECTED_FOREIGN_KEYS.items():
        actual_constraints = {
            item["name"]: (
                tuple(item["constrained_columns"]),
                item["referred_table"],
                tuple(item["referred_columns"]),
            )
            for item in inspector.get_foreign_keys(table_name, schema="public")
        }
        for name, signature in expected_constraints.items():
            assert actual_constraints[name] == signature

    for table_name, expected_indexes in EXPECTED_INDEXES.items():
        actual_indexes = {
            index["name"]: tuple(index["column_names"])
            for index in inspector.get_indexes(table_name, schema="public")
        }
        for index_name, expected_columns in expected_indexes.items():
            assert actual_indexes[index_name] == expected_columns

    for table_name, sequence_column in (
        ("collection_groups", "group_sequence"),
        ("collection_occurrences", "occurrence_sequence"),
    ):
        columns = {
            column["name"]: column
            for column in inspector.get_columns(table_name, schema="public")
        }
        assert columns[sequence_column]["identity"]["always"] is True

    alembic_columns = {
        column["name"]: column
        for column in inspector.get_columns("alembic_version", schema="public")
    }
    assert alembic_columns["version_num"]["type"].length == 64

    with engine.connect() as connection:
        partial_indexes = {
            row.name: (row.is_unique, row.predicate)
            for row in connection.execute(
                text(
                    "SELECT index_class.relname AS name, index_info.indisunique AS is_unique, "
                    "pg_get_expr(index_info.indpred, index_info.indrelid) AS predicate "
                    "FROM pg_index AS index_info "
                    "JOIN pg_class AS index_class ON index_class.oid = index_info.indexrelid "
                    "JOIN pg_class AS table_class ON table_class.oid = index_info.indrelid "
                    "WHERE table_class.relname IN ('raw_payloads', 'collection_occurrences') "
                    "AND index_class.relname IN "
                    "('uq_raw_payloads_source_entity_sha256', "
                    "'ix_collection_occurrences_raw_payload_sequence')"
                )
            )
        }
    raw_unique, raw_predicate = partial_indexes["uq_raw_payloads_source_entity_sha256"]
    assert raw_unique is True
    assert "exact_entity_bytes" in raw_predicate
    occurrence_unique, occurrence_predicate = partial_indexes[
        "ix_collection_occurrences_raw_payload_sequence"
    ]
    assert occurrence_unique is False
    assert "raw_payload_id IS NOT NULL" in occurrence_predicate


def test_fresh_upgrade_has_collection_audit_catalog(empty_postgres_database_url):
    upgrade_database(empty_postgres_database_url, COLLECTION_AUDIT_REVISION)

    assert database_revision(empty_postgres_database_url) == COLLECTION_AUDIT_REVISION
    assert public_table_names(empty_postgres_database_url) == (
        COLLECTION_AUDIT_ALL_MIGRATED_TABLES
    )

    engine = make_database_engine(empty_postgres_database_url)
    try:
        _assert_audit_catalog(engine)
    finally:
        engine.dispose()


def test_upgrade_from_0001_preserves_legacy_rows_and_classifies_evidence(
    legacy_foundation_database: LegacyFoundationBaseline,
):
    variant_ids = seed_legacy_raw_evidence_variants(legacy_foundation_database)
    engine = make_database_engine(legacy_foundation_database.database_url)
    try:
        with engine.connect() as connection:
            before = _snapshot_legacy_rows(connection)

        upgrade_database(legacy_foundation_database.database_url, COLLECTION_AUDIT_REVISION)

        assert database_revision(legacy_foundation_database.database_url) == COLLECTION_AUDIT_REVISION
        with engine.connect() as connection:
            assert _snapshot_legacy_rows(connection) == before
            _assert_baseline_ids(connection, legacy_foundation_database)

            run_role = connection.execute(
                text("SELECT run_role FROM ingestion_runs WHERE id = :run_id"),
                {"run_id": legacy_foundation_database.ids["run"]},
            ).scalar_one()
            assert run_role == "legacy_unclassified"

            raw_rows = {
                row.id: row
                for row in connection.execute(
                    text(
                        "SELECT id, content_evidence_kind, storage_status, legacy_hash_algorithm, "
                        "entity_body, entity_body_sha256, entity_body_length, entity_hash_algorithm "
                        "FROM raw_payloads"
                    )
                )
            }
            assert raw_rows[legacy_foundation_database.ids["raw_payload"]].content_evidence_kind == (
                "legacy_decoded_text"
            )
            assert raw_rows[variant_ids["jsonb_only"]].content_evidence_kind == "legacy_jsonb_only"
            assert raw_rows[variant_ids["body_missing"]].content_evidence_kind == (
                "legacy_body_missing"
            )
            for row in raw_rows.values():
                assert row.storage_status == "stored"
                assert row.legacy_hash_algorithm == "unknown_legacy"
                assert row.entity_body is None
                assert row.entity_body_sha256 is None
                assert row.entity_body_length is None
                assert row.entity_hash_algorithm is None

            for table_name in COLLECTION_AUDIT_TABLES:
                assert connection.execute(
                    text(f'SELECT count(*) FROM "{table_name}"')
                ).scalar_one() == 0

            run_check_validation = {
                row.conname: row.convalidated
                for row in connection.execute(
                    text(
                        "SELECT conname, convalidated FROM pg_constraint "
                        "WHERE conrelid = 'ingestion_runs'::regclass AND contype = 'c'"
                    )
                )
            }
            assert run_check_validation["ck_ingestion_runs_run_role"] is True
            legacy_not_valid_checks = EXPECTED_CHECK_NAMES["ingestion_runs"] - {
                "ck_ingestion_runs_run_role"
            }
            assert all(
                run_check_validation[constraint_name] is False
                for constraint_name in legacy_not_valid_checks
            )
    finally:
        engine.dispose()


def test_downgrade_to_0001_preserves_legacy_rows_and_reupgrades(
    legacy_foundation_database: LegacyFoundationBaseline,
):
    seed_legacy_raw_evidence_variants(legacy_foundation_database)
    engine = make_database_engine(legacy_foundation_database.database_url)
    try:
        with engine.connect() as connection:
            before = _snapshot_legacy_rows(connection)

        upgrade_database(legacy_foundation_database.database_url, COLLECTION_AUDIT_REVISION)
        downgrade_database(legacy_foundation_database.database_url, FOUNDATION_REVISION)

        assert database_revision(legacy_foundation_database.database_url) == FOUNDATION_REVISION
        assert public_table_names(legacy_foundation_database.database_url) == (
            FOUNDATION_ALL_MIGRATED_TABLES
        )
        inspector = inspect(engine)
        assert _column_names(inspector, "ingestion_runs").isdisjoint(RUN_ADDED_COLUMNS)
        assert _column_names(inspector, "raw_payloads").isdisjoint(RAW_ADDED_COLUMNS)
        with engine.connect() as connection:
            assert _snapshot_legacy_rows(connection) == before
            _assert_baseline_ids(connection, legacy_foundation_database)

        upgrade_database(
            legacy_foundation_database.database_url,
            COLLECTION_AUDIT_REVISION,
        )
        assert database_revision(legacy_foundation_database.database_url) == (
            COLLECTION_AUDIT_REVISION
        )
        assert public_table_names(legacy_foundation_database.database_url) == (
            COLLECTION_AUDIT_ALL_MIGRATED_TABLES
        )
        _assert_audit_catalog(engine)
    finally:
        engine.dispose()
