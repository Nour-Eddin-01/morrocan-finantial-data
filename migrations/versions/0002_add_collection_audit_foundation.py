"""add collection audit foundation

Revision ID: 0002_add_collection_audit_foundation
Revises: 0001_initial_foundation
Create Date: 2026-07-21
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0002_add_collection_audit_foundation"
down_revision = "0001_initial_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Alembic creates this bookkeeping column as VARCHAR(32), while the approved
    # revision identifier above is 36 characters long.
    op.alter_column(
        "alembic_version",
        "version_num",
        existing_type=sa.String(length=32),
        type_=sa.String(length=64),
        existing_nullable=False,
    )

    _upgrade_ingestion_runs()
    _upgrade_raw_payloads()
    _create_collection_groups()
    _create_collection_group_pages()
    _create_collection_occurrences()


def _upgrade_ingestion_runs() -> None:
    op.add_column(
        "ingestion_runs",
        sa.Column(
            "run_role",
            sa.String(length=40),
            server_default=sa.text("'legacy_unclassified'"),
            nullable=False,
        ),
    )
    op.add_column(
        "ingestion_runs",
        sa.Column("parent_run_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "ingestion_runs",
        sa.Column("safe_error_code", sa.String(length=80), nullable=True),
    )

    op.create_foreign_key(
        op.f("fk_ingestion_runs_parent_run_id_ingestion_runs"),
        "ingestion_runs",
        "ingestion_runs",
        ["parent_run_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_unique_constraint(
        op.f("uq_ingestion_runs_id_source_id"),
        "ingestion_runs",
        ["id", "source_id"],
    )
    op.create_check_constraint(
        op.f("ck_ingestion_runs_run_role"),
        "ingestion_runs",
        "run_role IN ('acquisition', 'authoritative_pipeline', 'validation', "
        "'backfill', 'publication_retry', 'legacy_unclassified')",
    )

    # These checks constrain legacy columns whose historical values were never
    # database-validated. NOT VALID preserves every existing row while still
    # rejecting invalid inserts and updates after this migration.
    op.create_check_constraint(
        op.f("ck_ingestion_runs_status"),
        "ingestion_runs",
        "status IN ('running', 'success', 'partial_success', 'failed')",
        postgresql_not_valid=True,
    )
    for counter_name in (
        "records_collected",
        "records_inserted",
        "records_updated",
        "records_failed",
    ):
        op.create_check_constraint(
            op.f(f"ck_ingestion_runs_{counter_name}_nonnegative"),
            "ingestion_runs",
            f"{counter_name} >= 0",
            postgresql_not_valid=True,
        )
    op.create_check_constraint(
        op.f("ck_ingestion_runs_status_finished_at"),
        "ingestion_runs",
        "((status = 'running' AND finished_at IS NULL) OR "
        "(status IN ('success', 'partial_success', 'failed') AND finished_at IS NOT NULL))",
        postgresql_not_valid=True,
    )
    op.create_check_constraint(
        op.f("ck_ingestion_runs_finished_at_order"),
        "ingestion_runs",
        "finished_at IS NULL OR finished_at >= started_at",
        postgresql_not_valid=True,
    )


def _upgrade_raw_payloads() -> None:
    op.add_column(
        "raw_payloads",
        sa.Column("entity_body", sa.LargeBinary(), nullable=True),
    )
    op.add_column(
        "raw_payloads",
        sa.Column("entity_body_sha256", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "raw_payloads",
        sa.Column("entity_body_length", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "raw_payloads",
        sa.Column(
            "content_evidence_kind",
            sa.String(length=40),
            server_default=sa.text("'legacy_body_missing'"),
            nullable=False,
        ),
    )
    op.add_column(
        "raw_payloads",
        sa.Column("entity_hash_algorithm", sa.String(length=50), nullable=True),
    )
    op.add_column(
        "raw_payloads",
        sa.Column(
            "storage_status",
            sa.String(length=20),
            server_default=sa.text("'stored'"),
            nullable=False,
        ),
    )
    op.add_column(
        "raw_payloads",
        sa.Column(
            "legacy_hash_algorithm",
            sa.String(length=80),
            server_default=sa.text("'unknown_legacy'"),
            nullable=True,
        ),
    )

    op.execute(
        sa.text(
            "UPDATE raw_payloads "
            "SET content_evidence_kind = CASE "
            "WHEN payload_text IS NOT NULL THEN 'legacy_decoded_text' "
            "WHEN payload IS NOT NULL THEN 'legacy_jsonb_only' "
            "ELSE 'legacy_body_missing' END, "
            "storage_status = 'stored', "
            "legacy_hash_algorithm = 'unknown_legacy'"
        )
    )

    op.create_unique_constraint(
        op.f("uq_raw_payloads_id_source_id"),
        "raw_payloads",
        ["id", "source_id"],
    )
    op.create_check_constraint(
        op.f("ck_raw_payloads_content_evidence_kind"),
        "raw_payloads",
        "content_evidence_kind IN ('exact_entity_bytes', 'legacy_decoded_text', "
        "'legacy_jsonb_only', 'legacy_body_missing')",
    )
    op.create_check_constraint(
        op.f("ck_raw_payloads_storage_status"),
        "raw_payloads",
        "storage_status = 'stored'",
    )
    op.create_check_constraint(
        op.f("ck_raw_payloads_entity_body_length_nonnegative"),
        "raw_payloads",
        "entity_body_length IS NULL OR entity_body_length >= 0",
    )
    op.create_check_constraint(
        op.f("ck_raw_payloads_exact_fields_present"),
        "raw_payloads",
        "content_evidence_kind <> 'exact_entity_bytes' OR "
        "(entity_body IS NOT NULL AND entity_body_sha256 IS NOT NULL "
        "AND entity_body_length IS NOT NULL AND entity_hash_algorithm IS NOT NULL)",
    )
    op.create_check_constraint(
        op.f("ck_raw_payloads_exact_hash_format"),
        "raw_payloads",
        "content_evidence_kind <> 'exact_entity_bytes' OR "
        "entity_body_sha256 ~ '^[0-9a-f]{64}$'",
    )
    op.create_check_constraint(
        op.f("ck_raw_payloads_exact_hash_algorithm"),
        "raw_payloads",
        "content_evidence_kind <> 'exact_entity_bytes' OR "
        "entity_hash_algorithm = 'sha256_entity_body_v1'",
    )
    op.create_check_constraint(
        op.f("ck_raw_payloads_exact_body_length"),
        "raw_payloads",
        "content_evidence_kind <> 'exact_entity_bytes' OR "
        "octet_length(entity_body) = entity_body_length",
    )
    op.create_check_constraint(
        op.f("ck_raw_payloads_nonexact_entity_algorithm"),
        "raw_payloads",
        "content_evidence_kind = 'exact_entity_bytes' OR "
        "entity_hash_algorithm IS NULL OR "
        "entity_hash_algorithm <> 'sha256_entity_body_v1'",
    )
    op.create_index(
        "uq_raw_payloads_source_entity_sha256",
        "raw_payloads",
        ["source_id", "entity_body_sha256"],
        unique=True,
        postgresql_where=sa.text("content_evidence_kind = 'exact_entity_bytes'"),
    )


def _create_collection_groups() -> None:
    op.create_table(
        "collection_groups",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "group_sequence",
            sa.BigInteger(),
            sa.Identity(always=True),
            nullable=False,
        ),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("exchange_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ingestion_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dataset_code", sa.String(length=80), nullable=False),
        sa.Column("collection_mode", sa.String(length=30), nullable=False),
        sa.Column("group_purpose", sa.String(length=30), nullable=False),
        sa.Column("external_group_key", sa.String(length=160), nullable=True),
        sa.Column("page_limit", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("collection_completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("collection_status", sa.String(length=30), nullable=False),
        sa.Column("pagination_complete", sa.Boolean(), nullable=True),
        sa.Column("completion_evidence_kind", sa.String(length=50), nullable=False),
        sa.Column("expected_pages", sa.Integer(), nullable=True),
        sa.Column("selected_data_pages", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "terminal_page_present",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("coverage_status", sa.String(length=20), nullable=False),
        sa.Column("expected_instrument_count", sa.Integer(), nullable=True),
        sa.Column("observed_instrument_count", sa.Integer(), nullable=True),
        sa.Column("collection_stop_reason", sa.String(length=80), nullable=True),
        sa.Column(
            "safe_diagnostic_codes",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "collection_mode IN ('live_json', 'live_html', 'manual_fixture', 'replay', 'backfill')",
            name=op.f("ck_collection_groups_collection_mode"),
        ),
        sa.CheckConstraint(
            "group_purpose IN ('production', 'validation', 'backfill')",
            name=op.f("ck_collection_groups_group_purpose"),
        ),
        sa.CheckConstraint(
            "collection_status IN ('running', 'success', 'partial_success', 'failed')",
            name=op.f("ck_collection_groups_collection_status"),
        ),
        sa.CheckConstraint(
            "coverage_status IN ('unknown', 'proven', 'failed')",
            name=op.f("ck_collection_groups_coverage_status"),
        ),
        sa.CheckConstraint(
            "completion_evidence_kind IN ('authoritative_total', 'short_page', "
            "'terminal_sentinel', 'max_pages_exact_authoritative_total', "
            "'declared_fixture_scope', 'none', 'unknown_legacy')",
            name=op.f("ck_collection_groups_completion_evidence_kind"),
        ),
        sa.CheckConstraint(
            "page_limit > 0",
            name=op.f("ck_collection_groups_page_limit_positive"),
        ),
        sa.CheckConstraint(
            "expected_pages IS NULL OR expected_pages >= 0",
            name=op.f("ck_collection_groups_expected_pages_nonnegative"),
        ),
        sa.CheckConstraint(
            "selected_data_pages >= 0",
            name=op.f("ck_collection_groups_selected_data_pages_nonnegative"),
        ),
        sa.CheckConstraint(
            "expected_instrument_count IS NULL OR expected_instrument_count >= 0",
            name=op.f("ck_collection_groups_expected_instrument_count_nonnegative"),
        ),
        sa.CheckConstraint(
            "observed_instrument_count IS NULL OR observed_instrument_count >= 0",
            name=op.f("ck_collection_groups_observed_instrument_count_nonnegative"),
        ),
        sa.CheckConstraint(
            "collection_completed_at IS NULL OR collection_completed_at >= started_at",
            name=op.f("ck_collection_groups_collection_completed_at_order"),
        ),
        sa.CheckConstraint(
            "finalized_at IS NULL OR finalized_at >= started_at",
            name=op.f("ck_collection_groups_finalized_at_order"),
        ),
        sa.CheckConstraint(
            "((collection_status = 'running' AND finalized_at IS NULL) OR "
            "(collection_status IN ('success', 'partial_success', 'failed') "
            "AND finalized_at IS NOT NULL))",
            name=op.f("ck_collection_groups_status_finalized_at"),
        ),
        sa.CheckConstraint(
            "collection_status <> 'success' OR pagination_complete IS TRUE",
            name=op.f("ck_collection_groups_success_pagination_complete"),
        ),
        sa.CheckConstraint(
            "NOT (group_purpose = 'production' AND collection_mode = 'manual_fixture')",
            name=op.f("ck_collection_groups_production_not_manual_fixture"),
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["data_sources.id"],
            name=op.f("fk_collection_groups_source_id_data_sources"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["exchange_id"],
            ["exchanges.id"],
            name=op.f("fk_collection_groups_exchange_id_exchanges"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["ingestion_run_id", "source_id"],
            ["ingestion_runs.id", "ingestion_runs.source_id"],
            name=op.f("fk_collection_groups_ingestion_run_source"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_collection_groups")),
        sa.UniqueConstraint(
            "group_sequence",
            name=op.f("uq_collection_groups_group_sequence"),
        ),
        sa.UniqueConstraint(
            "id",
            "source_id",
            name=op.f("uq_collection_groups_id_source_id"),
        ),
        sa.UniqueConstraint(
            "id",
            "source_id",
            "ingestion_run_id",
            name=op.f("uq_collection_groups_id_source_id_ingestion_run_id"),
        ),
        sa.UniqueConstraint(
            "id",
            "source_id",
            "ingestion_run_id",
            "page_limit",
            name=op.f("uq_collection_groups_id_source_run_page_limit"),
        ),
    )
    op.create_index(
        "ix_collection_groups_source_sequence",
        "collection_groups",
        ["source_id", "group_sequence"],
    )
    op.create_index(
        "ix_collection_groups_exchange_dataset_purpose_sequence",
        "collection_groups",
        ["exchange_id", "dataset_code", "group_purpose", "group_sequence"],
    )
    op.create_index(
        "ix_collection_groups_status_sequence",
        "collection_groups",
        ["collection_status", "group_sequence"],
    )
    op.create_index(
        "ix_collection_groups_pagination_sequence",
        "collection_groups",
        ["pagination_complete", "group_sequence"],
    )


def _create_collection_group_pages() -> None:
    op.create_table(
        "collection_group_pages",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("group_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ingestion_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("page_limit", sa.Integer(), nullable=False),
        sa.Column("logical_page_number", sa.Integer(), nullable=False),
        sa.Column("page_offset", sa.Integer(), nullable=False),
        sa.Column("page_role", sa.String(length=30), nullable=False),
        sa.Column("collection_page_outcome", sa.String(length=30), nullable=False),
        sa.Column("structural_reason_code", sa.String(length=80), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "page_role IN ('data', 'terminal_sentinel', 'unknown')",
            name=op.f("ck_collection_group_pages_page_role"),
        ),
        sa.CheckConstraint(
            "collection_page_outcome IN ('pending', 'success', 'failed')",
            name=op.f("ck_collection_group_pages_outcome"),
        ),
        sa.CheckConstraint(
            "logical_page_number > 0",
            name=op.f("ck_collection_group_pages_page_number_positive"),
        ),
        sa.CheckConstraint(
            "page_offset >= 0",
            name=op.f("ck_collection_group_pages_page_offset_nonnegative"),
        ),
        sa.CheckConstraint(
            "page_limit > 0",
            name=op.f("ck_collection_group_pages_page_limit_positive"),
        ),
        sa.CheckConstraint(
            "CAST(page_offset AS BIGINT) = "
            "(CAST(logical_page_number AS BIGINT) - 1) * CAST(page_limit AS BIGINT)",
            name=op.f("ck_collection_group_pages_offset_formula"),
        ),
        sa.CheckConstraint(
            "((collection_page_outcome = 'pending' AND finalized_at IS NULL) OR "
            "(collection_page_outcome IN ('success', 'failed') AND finalized_at IS NOT NULL))",
            name=op.f("ck_collection_group_pages_outcome_finalized_at"),
        ),
        sa.ForeignKeyConstraint(
            ["group_id", "source_id", "ingestion_run_id", "page_limit"],
            [
                "collection_groups.id",
                "collection_groups.source_id",
                "collection_groups.ingestion_run_id",
                "collection_groups.page_limit",
            ],
            name=op.f("fk_collection_group_pages_group_source_run_limit"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_collection_group_pages")),
        sa.UniqueConstraint(
            "group_id",
            "logical_page_number",
            name=op.f("uq_collection_group_pages_group_page_number"),
        ),
        sa.UniqueConstraint(
            "group_id",
            "page_offset",
            name=op.f("uq_collection_group_pages_group_page_offset"),
        ),
        sa.UniqueConstraint(
            "id",
            "source_id",
            "ingestion_run_id",
            name=op.f("uq_collection_group_pages_id_source_run"),
        ),
        sa.UniqueConstraint(
            "id",
            "source_id",
            "group_id",
            name=op.f("uq_collection_group_pages_id_source_group"),
        ),
    )
    op.create_index(
        "ix_collection_group_pages_outcome_page_number",
        "collection_group_pages",
        ["group_id", "collection_page_outcome", "logical_page_number"],
    )


def _create_collection_occurrences() -> None:
    op.create_table(
        "collection_occurrences",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "occurrence_sequence",
            sa.BigInteger(),
            sa.Identity(always=True),
            nullable=False,
        ),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ingestion_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("group_page_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("raw_payload_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("request_sequence", sa.Integer(), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("redirect_hop", sa.Integer(), nullable=False),
        sa.Column("logical_request_url", sa.Text(), nullable=False),
        sa.Column("requested_url", sa.Text(), nullable=True),
        sa.Column("response_url", sa.Text(), nullable=True),
        sa.Column("source_endpoint", sa.String(length=160), nullable=True),
        sa.Column("request_profile", sa.String(length=80), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("response_received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("http_status", sa.SmallInteger(), nullable=True),
        sa.Column("content_type", sa.String(length=120), nullable=True),
        sa.Column("body_length", sa.BigInteger(), nullable=True),
        sa.Column("outcome", sa.String(length=30), nullable=False),
        sa.Column("safe_error_code", sa.String(length=80), nullable=True),
        sa.Column("safe_error_message", sa.String(length=500), nullable=True),
        sa.Column(
            "safe_response_headers",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "dropped_response_header_name_count",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "response_headers_overflow",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("response_headers_policy_version", sa.String(length=50), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "outcome IN ('success_response', 'redirect_response', 'http_error_response', "
            "'transport_failure', 'fixture_loaded')",
            name=op.f("ck_collection_occurrences_outcome"),
        ),
        sa.CheckConstraint(
            "request_sequence > 0",
            name=op.f("ck_collection_occurrences_request_sequence_positive"),
        ),
        sa.CheckConstraint(
            "attempt_number > 0",
            name=op.f("ck_collection_occurrences_attempt_number_positive"),
        ),
        sa.CheckConstraint(
            "redirect_hop >= 0",
            name=op.f("ck_collection_occurrences_redirect_hop_nonnegative"),
        ),
        sa.CheckConstraint(
            "requested_at <= finished_at",
            name=op.f("ck_collection_occurrences_requested_finished_order"),
        ),
        sa.CheckConstraint(
            "response_received_at IS NULL OR "
            "(requested_at <= response_received_at AND response_received_at <= finished_at)",
            name=op.f("ck_collection_occurrences_response_time_order"),
        ),
        sa.CheckConstraint(
            "http_status IS NULL OR http_status BETWEEN 100 AND 599",
            name=op.f("ck_collection_occurrences_http_status_range"),
        ),
        sa.CheckConstraint(
            "body_length IS NULL OR body_length >= 0",
            name=op.f("ck_collection_occurrences_body_length_nonnegative"),
        ),
        sa.CheckConstraint(
            "dropped_response_header_name_count >= 0",
            name=op.f("ck_collection_occurrences_dropped_header_count_nonnegative"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(safe_response_headers) = 'object'",
            name=op.f("ck_collection_occurrences_safe_headers_object"),
        ),
        sa.CheckConstraint(
            "(outcome = 'success_response' "
            "AND raw_payload_id IS NOT NULL "
            "AND response_url IS NOT NULL "
            "AND response_received_at IS NOT NULL "
            "AND http_status IS NOT NULL AND http_status BETWEEN 200 AND 299) "
            "OR (outcome = 'redirect_response' "
            "AND raw_payload_id IS NOT NULL "
            "AND response_url IS NOT NULL "
            "AND response_received_at IS NOT NULL "
            "AND http_status IS NOT NULL AND http_status IN (301, 302, 303, 307, 308)) "
            "OR (outcome = 'http_error_response' "
            "AND raw_payload_id IS NOT NULL "
            "AND response_url IS NOT NULL "
            "AND response_received_at IS NOT NULL "
            "AND http_status IS NOT NULL AND http_status BETWEEN 100 AND 599 "
            "AND http_status NOT BETWEEN 200 AND 299 "
            "AND http_status NOT IN (301, 302, 303, 307, 308)) "
            "OR (outcome = 'transport_failure' "
            "AND raw_payload_id IS NULL "
            "AND response_url IS NULL "
            "AND response_received_at IS NULL "
            "AND http_status IS NULL "
            "AND safe_error_code IS NOT NULL) "
            "OR (outcome = 'fixture_loaded' "
            "AND raw_payload_id IS NOT NULL "
            "AND response_url IS NULL "
            "AND response_received_at IS NULL "
            "AND http_status IS NULL)",
            name=op.f("ck_collection_occurrences_outcome_evidence"),
        ),
        sa.ForeignKeyConstraint(
            ["ingestion_run_id", "source_id"],
            ["ingestion_runs.id", "ingestion_runs.source_id"],
            name=op.f("fk_collection_occurrences_ingestion_run_source"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["group_page_id", "source_id", "ingestion_run_id"],
            [
                "collection_group_pages.id",
                "collection_group_pages.source_id",
                "collection_group_pages.ingestion_run_id",
            ],
            name=op.f("fk_collection_occurrences_group_page_source_run"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["raw_payload_id", "source_id"],
            ["raw_payloads.id", "raw_payloads.source_id"],
            name=op.f("fk_collection_occurrences_raw_payload_source"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_collection_occurrences")),
        sa.UniqueConstraint(
            "occurrence_sequence",
            name=op.f("uq_collection_occurrences_occurrence_sequence"),
        ),
        sa.UniqueConstraint(
            "ingestion_run_id",
            "request_sequence",
            "attempt_number",
            "redirect_hop",
            name=op.f("uq_collection_occurrences_run_request_attempt_redirect"),
        ),
        sa.UniqueConstraint(
            "id",
            "group_page_id",
            name=op.f("uq_collection_occurrences_id_group_page"),
        ),
        sa.UniqueConstraint(
            "id",
            "source_id",
            "raw_payload_id",
            name=op.f("uq_collection_occurrences_id_source_raw"),
        ),
        sa.UniqueConstraint(
            "id",
            "source_id",
            "raw_payload_id",
            "group_page_id",
            name=op.f("uq_collection_occurrences_id_source_raw_group_page"),
        ),
    )
    op.create_index(
        "ix_collection_occurrences_source_sequence",
        "collection_occurrences",
        ["source_id", "occurrence_sequence"],
    )
    op.create_index(
        "ix_collection_occurrences_ingestion_run_sequence",
        "collection_occurrences",
        ["ingestion_run_id", "occurrence_sequence"],
    )
    op.create_index(
        "ix_collection_occurrences_group_page_sequence",
        "collection_occurrences",
        ["group_page_id", "occurrence_sequence"],
    )
    op.create_index(
        "ix_collection_occurrences_raw_payload_sequence",
        "collection_occurrences",
        ["raw_payload_id", "occurrence_sequence"],
        postgresql_where=sa.text("raw_payload_id IS NOT NULL"),
    )
    op.create_index(
        "ix_collection_occurrences_outcome_sequence",
        "collection_occurrences",
        ["outcome", "occurrence_sequence"],
    )


def downgrade() -> None:
    # This downgrade is safe only while the new audit tables/columns contain no
    # irreplaceable collection evidence. Export and explicit approval are
    # required once runtime dual-write begins.
    op.drop_index(
        "ix_collection_occurrences_outcome_sequence",
        table_name="collection_occurrences",
    )
    op.drop_index(
        "ix_collection_occurrences_raw_payload_sequence",
        table_name="collection_occurrences",
    )
    op.drop_index(
        "ix_collection_occurrences_group_page_sequence",
        table_name="collection_occurrences",
    )
    op.drop_index(
        "ix_collection_occurrences_ingestion_run_sequence",
        table_name="collection_occurrences",
    )
    op.drop_index(
        "ix_collection_occurrences_source_sequence",
        table_name="collection_occurrences",
    )
    op.drop_table("collection_occurrences")

    op.drop_index(
        "ix_collection_group_pages_outcome_page_number",
        table_name="collection_group_pages",
    )
    op.drop_table("collection_group_pages")

    op.drop_index(
        "ix_collection_groups_pagination_sequence",
        table_name="collection_groups",
    )
    op.drop_index(
        "ix_collection_groups_status_sequence",
        table_name="collection_groups",
    )
    op.drop_index(
        "ix_collection_groups_exchange_dataset_purpose_sequence",
        table_name="collection_groups",
    )
    op.drop_index(
        "ix_collection_groups_source_sequence",
        table_name="collection_groups",
    )
    op.drop_table("collection_groups")

    op.drop_index(
        "uq_raw_payloads_source_entity_sha256",
        table_name="raw_payloads",
    )
    for constraint_name in (
        "ck_raw_payloads_nonexact_entity_algorithm",
        "ck_raw_payloads_exact_body_length",
        "ck_raw_payloads_exact_hash_algorithm",
        "ck_raw_payloads_exact_hash_format",
        "ck_raw_payloads_exact_fields_present",
        "ck_raw_payloads_entity_body_length_nonnegative",
        "ck_raw_payloads_storage_status",
        "ck_raw_payloads_content_evidence_kind",
        "uq_raw_payloads_id_source_id",
    ):
        op.drop_constraint(constraint_name, "raw_payloads")
    for column_name in (
        "legacy_hash_algorithm",
        "storage_status",
        "entity_hash_algorithm",
        "content_evidence_kind",
        "entity_body_length",
        "entity_body_sha256",
        "entity_body",
    ):
        op.drop_column("raw_payloads", column_name)

    for constraint_name in (
        "ck_ingestion_runs_finished_at_order",
        "ck_ingestion_runs_status_finished_at",
        "ck_ingestion_runs_records_failed_nonnegative",
        "ck_ingestion_runs_records_updated_nonnegative",
        "ck_ingestion_runs_records_inserted_nonnegative",
        "ck_ingestion_runs_records_collected_nonnegative",
        "ck_ingestion_runs_status",
        "ck_ingestion_runs_run_role",
        "uq_ingestion_runs_id_source_id",
        "fk_ingestion_runs_parent_run_id_ingestion_runs",
    ):
        op.drop_constraint(constraint_name, "ingestion_runs")
    op.drop_column("ingestion_runs", "safe_error_code")
    op.drop_column("ingestion_runs", "parent_run_id")
    op.drop_column("ingestion_runs", "run_role")

    # Keep alembic_version.version_num at VARCHAR(64). Alembic still stores this
    # revision's 36-character ID until downgrade() returns, so narrowing inside
    # this revision would break its own version-row update. The wider type is
    # backward-compatible with the 0001 identifier and contains no domain data.
