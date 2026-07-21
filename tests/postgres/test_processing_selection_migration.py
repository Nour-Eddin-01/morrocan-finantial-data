from collections.abc import Iterable
from typing import Any
import warnings

from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
import pytest
from sqlalchemy import Text, inspect, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import SAWarning

from tradehub_data.models import Base
from tests.postgres.harness import (
    ALL_MIGRATED_TABLES,
    COLLECTION_AUDIT_ALL_MIGRATED_TABLES,
    COLLECTION_AUDIT_REVISION,
    CURRENT_HEAD_REVISION,
    PROCESSING_SELECTION_REVISION,
    CollectionAuditBaseline,
    database_revision,
    downgrade_database,
    make_database_engine,
    public_table_names,
    upgrade_database,
)


pytestmark = pytest.mark.postgres


PROCESSING_ATTEMPT_COLUMNS = {
    "id",
    "processing_attempt_sequence",
    "source_id",
    "ingestion_run_id",
    "group_id",
    "group_page_id",
    "collection_occurrence_id",
    "raw_payload_id",
    "processing_stage",
    "component_version",
    "rule_version",
    "input_fingerprint_algorithm",
    "input_fingerprint",
    "status",
    "started_at",
    "completed_at",
    "rows_found",
    "rows_usable",
    "rows_failed",
    "errors_count",
    "selected_pages_evaluated",
    "duplicate_symbol_count",
    "blocking_conflict_count",
    "staged_revision_count",
    "pagination_complete_evaluated",
    "coverage_status_evaluated",
    "acceptance_eligibility",
    "eligibility_reason_codes",
    "output_fingerprint_algorithm",
    "output_fingerprint",
    "safe_diagnostic_codes",
    "created_at",
}
PAGE_SELECTION_COLUMNS = {
    "group_page_id",
    "occurrence_id",
    "selected_at",
    "selection_reason",
    "selected_by_processing_attempt_id",
    "created_at",
}

EXPECTED_UNIQUES = {
    "processing_attempts": {
        "uq_processing_attempts_processing_attempt_sequence": (
            "processing_attempt_sequence",
        ),
        "uq_processing_attempts_id_group": ("id", "group_id"),
        "uq_processing_attempts_id_occurrence_group": (
            "id",
            "collection_occurrence_id",
            "group_id",
        ),
        "uq_processing_attempts_id_source_raw": (
            "id",
            "source_id",
            "raw_payload_id",
        ),
        "uq_processing_attempts_id_source_raw_occurrence": (
            "id",
            "source_id",
            "raw_payload_id",
            "collection_occurrence_id",
        ),
    },
    "collection_page_selections": {
        "uq_collection_page_selections_occurrence_id": ("occurrence_id",),
    },
}

EXPECTED_CHECK_NAMES = {
    "processing_attempts": {
        "ck_processing_attempts_processing_stage",
        "ck_processing_attempts_status",
        "ck_processing_attempts_coverage_status_evaluated",
        "ck_processing_attempts_acceptance_eligibility",
        "ck_processing_attempts_sequence_positive",
        "ck_processing_attempts_rows_found_nonnegative",
        "ck_processing_attempts_rows_usable_nonnegative",
        "ck_processing_attempts_rows_failed_nonnegative",
        "ck_processing_attempts_errors_count_nonnegative",
        "ck_processing_attempts_selected_pages_nonnegative",
        "ck_processing_attempts_duplicate_symbols_nonnegative",
        "ck_processing_attempts_blocking_conflicts_nonnegative",
        "ck_processing_attempts_staged_revisions_nonnegative",
        "ck_processing_attempts_completed_at_order",
        "ck_processing_attempts_status_completed_at",
        "ck_processing_attempts_input_fingerprint_format",
        "ck_processing_attempts_output_fingerprint_pair",
        "ck_processing_attempts_output_fingerprint_format",
        "ck_processing_attempts_safe_diagnostic_codes_array",
        "ck_processing_attempts_eligibility_reason_codes_no_nulls",
        "ck_processing_attempts_context_shape",
        "ck_processing_attempts_evaluation_fields_stage",
    },
    "collection_page_selections": {
        "ck_collection_page_selections_selection_reason",
    },
}

EXPECTED_FOREIGN_KEYS = {
    "processing_attempts": {
        "fk_processing_attempts_ingestion_run_source": (
            ("ingestion_run_id", "source_id"),
            "ingestion_runs",
            ("id", "source_id"),
        ),
        "fk_processing_attempts_group_source": (
            ("group_id", "source_id"),
            "collection_groups",
            ("id", "source_id"),
        ),
        "fk_processing_attempts_group_page_source_group": (
            ("group_page_id", "source_id", "group_id"),
            "collection_group_pages",
            ("id", "source_id", "group_id"),
        ),
        "fk_processing_attempts_raw_payload_source": (
            ("raw_payload_id", "source_id"),
            "raw_payloads",
            ("id", "source_id"),
        ),
        "fk_processing_attempts_occurrence_source_raw_group_page": (
            (
                "collection_occurrence_id",
                "source_id",
                "raw_payload_id",
                "group_page_id",
            ),
            "collection_occurrences",
            ("id", "source_id", "raw_payload_id", "group_page_id"),
        ),
    },
    "collection_page_selections": {
        "fk_collection_page_selections_group_page": (
            ("group_page_id",),
            "collection_group_pages",
            ("id",),
        ),
        "fk_collection_page_selections_occurrence_page": (
            ("occurrence_id", "group_page_id"),
            "collection_occurrences",
            ("id", "group_page_id"),
        ),
        "fk_collection_page_selections_selected_by_attempt": (
            ("selected_by_processing_attempt_id",),
            "processing_attempts",
            ("id",),
        ),
    },
}

EXPECTED_INDEXES = {
    "processing_attempts": {
        "ix_processing_attempts_group_sequence": (
            "group_id",
            "processing_attempt_sequence",
        ),
        "ix_processing_attempts_group_page_sequence": (
            "group_page_id",
            "processing_attempt_sequence",
        ),
        "ix_processing_attempts_raw_rule_sequence": (
            "raw_payload_id",
            "rule_version",
            "processing_attempt_sequence",
        ),
        "ix_processing_attempts_status_stage_sequence": (
            "status",
            "processing_stage",
            "processing_attempt_sequence",
        ),
        "ix_processing_attempts_completed_sequence": (
            "completed_at",
            "processing_attempt_sequence",
        ),
    },
}

BASELINE_TABLES = (
    "exchanges",
    "data_sources",
    "ingestion_runs",
    "raw_payloads",
    "collection_groups",
    "collection_group_pages",
    "collection_occurrences",
)


def _constraint_names(items: Iterable[dict[str, Any]]) -> set[str]:
    return {item["name"] for item in items if item.get("name") is not None}


def _snapshot_baseline(connection: Connection) -> dict[str, list[dict[str, Any]]]:
    return {
        table_name: [
            dict(row)
            for row in connection.execute(
                text(f'SELECT * FROM "{table_name}" ORDER BY id')
            ).mappings()
        ]
        for table_name in BASELINE_TABLES
    }


def _assert_processing_selection_catalog(engine: Engine) -> None:
    inspector = inspect(engine)
    assert {
        column["name"]
        for column in inspector.get_columns("processing_attempts", schema="public")
    } == PROCESSING_ATTEMPT_COLUMNS
    assert {
        column["name"]
        for column in inspector.get_columns(
            "collection_page_selections",
            schema="public",
        )
    } == PAGE_SELECTION_COLUMNS

    primary_keys = {
        table_name: inspector.get_pk_constraint(table_name, schema="public")
        for table_name in ("processing_attempts", "collection_page_selections")
    }
    assert {
        table_name: (item["name"], tuple(item["constrained_columns"]))
        for table_name, item in primary_keys.items()
    } == {
        "processing_attempts": ("pk_processing_attempts", ("id",)),
        "collection_page_selections": (
            "pk_collection_page_selections",
            ("group_page_id",),
        ),
    }

    for table_name, expected_constraints in EXPECTED_UNIQUES.items():
        actual_constraints = {
            item["name"]: tuple(item["column_names"])
            for item in inspector.get_unique_constraints(table_name, schema="public")
        }
        for name, columns in expected_constraints.items():
            assert actual_constraints[name] == columns

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
        actual_options = {
            item["name"]: item.get("options", {})
            for item in inspector.get_foreign_keys(table_name, schema="public")
        }
        for name, signature in expected_constraints.items():
            assert actual_constraints[name] == signature
            assert actual_options[name].get("ondelete") == "RESTRICT"

    for table_name, expected_indexes in EXPECTED_INDEXES.items():
        actual_indexes = {
            item["name"]: tuple(item["column_names"])
            for item in inspector.get_indexes(table_name, schema="public")
        }
        for name, columns in expected_indexes.items():
            assert actual_indexes[name] == columns

    columns = {
        column["name"]: column
        for column in inspector.get_columns("processing_attempts", schema="public")
    }
    assert columns["processing_attempt_sequence"]["identity"]["always"] is True
    assert isinstance(columns["eligibility_reason_codes"]["type"], postgresql.ARRAY)
    assert isinstance(
        columns["eligibility_reason_codes"]["type"].item_type,
        Text,
    )
    assert isinstance(columns["safe_diagnostic_codes"]["type"], postgresql.JSONB)

    with engine.connect() as connection:
        partial_index = connection.execute(
            text(
                "SELECT index_info.indisunique, "
                "pg_get_expr(index_info.indpred, index_info.indrelid) AS predicate "
                "FROM pg_index AS index_info "
                "JOIN pg_class AS index_class ON index_class.oid = index_info.indexrelid "
                "WHERE index_class.relname = 'ix_processing_attempts_completed_sequence'"
            )
        ).one()
    assert partial_index.indisunique is False
    assert "completed_at IS NOT NULL" in partial_index.predicate


def test_fresh_upgrade_has_processing_selection_catalog(
    empty_postgres_database_url,
):
    upgrade_database(empty_postgres_database_url, "head")

    assert CURRENT_HEAD_REVISION == PROCESSING_SELECTION_REVISION
    assert database_revision(empty_postgres_database_url) == PROCESSING_SELECTION_REVISION
    assert public_table_names(empty_postgres_database_url) == ALL_MIGRATED_TABLES

    engine = make_database_engine(empty_postgres_database_url)
    try:
        _assert_processing_selection_catalog(engine)
    finally:
        engine.dispose()


def test_upgrade_from_0002_preserves_collection_evidence_without_synthesis(
    collection_audit_baseline_database: CollectionAuditBaseline,
):
    engine = make_database_engine(collection_audit_baseline_database.database_url)
    try:
        with engine.connect() as connection:
            before = _snapshot_baseline(connection)

        upgrade_database(
            collection_audit_baseline_database.database_url,
            PROCESSING_SELECTION_REVISION,
        )

        assert database_revision(collection_audit_baseline_database.database_url) == (
            PROCESSING_SELECTION_REVISION
        )
        with engine.connect() as connection:
            assert _snapshot_baseline(connection) == before
            for table_name in (
                "processing_attempts",
                "collection_page_selections",
            ):
                assert connection.execute(
                    text(f'SELECT count(*) FROM "{table_name}"')
                ).scalar_one() == 0

            raw_row = connection.execute(
                text(
                    "SELECT id, entity_body, entity_body_sha256, entity_body_length, "
                    "entity_hash_algorithm FROM raw_payloads WHERE id = :raw_payload_id"
                ),
                {
                    "raw_payload_id": collection_audit_baseline_database.ids[
                        "raw_payload"
                    ]
                },
            ).one()
            assert raw_row.id == collection_audit_baseline_database.ids["raw_payload"]
            assert raw_row.entity_body is not None
            assert len(raw_row.entity_body) == raw_row.entity_body_length
            assert raw_row.entity_body_sha256 is not None
            assert raw_row.entity_hash_algorithm == "sha256_entity_body_v1"
    finally:
        engine.dispose()


def test_migrated_head_matches_sqlalchemy_metadata(empty_postgres_database_url):
    upgrade_database(empty_postgres_database_url, "head")
    engine = make_database_engine(empty_postgres_database_url)
    try:
        with engine.connect() as connection:
            context = MigrationContext.configure(
                connection,
                opts={"compare_type": True, "compare_server_default": True},
            )
            # SQLAlchemy 2.0 reflects PostgreSQL NOT VALID check options from
            # 0002 under a generic dialect_options key that its own
            # CheckConstraint constructor warns about. Suppress only that
            # known reflection warning; every reported schema diff remains a
            # hard failure.
            with warnings.catch_warnings():
                warnings.filterwarnings(
                    "ignore",
                    message="Can't validate argument 'dialect_options'.*",
                    category=SAWarning,
                )
                assert compare_metadata(context, Base.metadata) == []
    finally:
        engine.dispose()


def test_downgrade_to_0002_preserves_baseline_and_reupgrades(
    collection_audit_baseline_database: CollectionAuditBaseline,
):
    engine = make_database_engine(collection_audit_baseline_database.database_url)
    try:
        with engine.connect() as connection:
            before = _snapshot_baseline(connection)

        upgrade_database(
            collection_audit_baseline_database.database_url,
            PROCESSING_SELECTION_REVISION,
        )
        downgrade_database(
            collection_audit_baseline_database.database_url,
            COLLECTION_AUDIT_REVISION,
        )

        assert database_revision(collection_audit_baseline_database.database_url) == (
            COLLECTION_AUDIT_REVISION
        )
        assert public_table_names(collection_audit_baseline_database.database_url) == (
            COLLECTION_AUDIT_ALL_MIGRATED_TABLES
        )
        with engine.connect() as connection:
            assert _snapshot_baseline(connection) == before

        upgrade_database(
            collection_audit_baseline_database.database_url,
            PROCESSING_SELECTION_REVISION,
        )
        assert database_revision(collection_audit_baseline_database.database_url) == (
            PROCESSING_SELECTION_REVISION
        )
        assert public_table_names(collection_audit_baseline_database.database_url) == (
            ALL_MIGRATED_TABLES
        )
        _assert_processing_selection_catalog(engine)
        with engine.connect() as connection:
            assert _snapshot_baseline(connection) == before
    finally:
        engine.dispose()
