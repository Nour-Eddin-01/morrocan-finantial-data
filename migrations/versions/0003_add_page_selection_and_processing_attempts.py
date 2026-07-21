"""add page selection and processing attempts

Revision ID: 0003_add_page_selection_and_processing_attempts
Revises: 0002_add_collection_audit_foundation
Create Date: 2026-07-21
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0003_add_page_selection_and_processing_attempts"
down_revision = "0002_add_collection_audit_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _create_processing_attempts()
    _create_collection_page_selections()


def _create_processing_attempts() -> None:
    op.create_table(
        "processing_attempts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "processing_attempt_sequence",
            sa.BigInteger(),
            sa.Identity(always=True),
            nullable=False,
        ),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ingestion_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("group_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("group_page_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "collection_occurrence_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("raw_payload_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("processing_stage", sa.String(length=40), nullable=False),
        sa.Column("component_version", sa.String(length=100), nullable=False),
        sa.Column("rule_version", sa.String(length=100), nullable=False),
        sa.Column(
            "input_fingerprint_algorithm",
            sa.String(length=80),
            nullable=False,
        ),
        sa.Column("input_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rows_found", sa.Integer(), server_default="0", nullable=False),
        sa.Column("rows_usable", sa.Integer(), server_default="0", nullable=False),
        sa.Column("rows_failed", sa.Integer(), server_default="0", nullable=False),
        sa.Column("errors_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("selected_pages_evaluated", sa.Integer(), nullable=True),
        sa.Column("duplicate_symbol_count", sa.Integer(), nullable=True),
        sa.Column("blocking_conflict_count", sa.Integer(), nullable=True),
        sa.Column("staged_revision_count", sa.Integer(), nullable=True),
        sa.Column("pagination_complete_evaluated", sa.Boolean(), nullable=True),
        sa.Column("coverage_status_evaluated", sa.String(length=20), nullable=True),
        sa.Column("acceptance_eligibility", sa.String(length=20), nullable=True),
        sa.Column(
            "eligibility_reason_codes",
            postgresql.ARRAY(sa.Text()),
            nullable=True,
        ),
        sa.Column("output_fingerprint_algorithm", sa.String(length=80), nullable=True),
        sa.Column("output_fingerprint", sa.String(length=64), nullable=True),
        sa.Column(
            "safe_diagnostic_codes",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "processing_stage IN ('diagnostics', 'parser', 'normalizer', "
            "'repository_validation', 'group_evaluation', 'publication_staging')",
            name=op.f("ck_processing_attempts_processing_stage"),
        ),
        sa.CheckConstraint(
            "status IN ('running', 'success', 'partial_success', 'failed', 'skipped')",
            name=op.f("ck_processing_attempts_status"),
        ),
        sa.CheckConstraint(
            "coverage_status_evaluated IS NULL OR "
            "coverage_status_evaluated IN ('unknown', 'proven', 'failed')",
            name=op.f("ck_processing_attempts_coverage_status_evaluated"),
        ),
        sa.CheckConstraint(
            "acceptance_eligibility IS NULL OR "
            "acceptance_eligibility IN ('not_evaluated', 'eligible', 'ineligible')",
            name=op.f("ck_processing_attempts_acceptance_eligibility"),
        ),
        sa.CheckConstraint(
            "processing_attempt_sequence > 0",
            name=op.f("ck_processing_attempts_sequence_positive"),
        ),
        sa.CheckConstraint(
            "rows_found >= 0",
            name=op.f("ck_processing_attempts_rows_found_nonnegative"),
        ),
        sa.CheckConstraint(
            "rows_usable >= 0",
            name=op.f("ck_processing_attempts_rows_usable_nonnegative"),
        ),
        sa.CheckConstraint(
            "rows_failed >= 0",
            name=op.f("ck_processing_attempts_rows_failed_nonnegative"),
        ),
        sa.CheckConstraint(
            "errors_count >= 0",
            name=op.f("ck_processing_attempts_errors_count_nonnegative"),
        ),
        sa.CheckConstraint(
            "selected_pages_evaluated IS NULL OR selected_pages_evaluated >= 0",
            name=op.f("ck_processing_attempts_selected_pages_nonnegative"),
        ),
        sa.CheckConstraint(
            "duplicate_symbol_count IS NULL OR duplicate_symbol_count >= 0",
            name=op.f("ck_processing_attempts_duplicate_symbols_nonnegative"),
        ),
        sa.CheckConstraint(
            "blocking_conflict_count IS NULL OR blocking_conflict_count >= 0",
            name=op.f("ck_processing_attempts_blocking_conflicts_nonnegative"),
        ),
        sa.CheckConstraint(
            "staged_revision_count IS NULL OR staged_revision_count >= 0",
            name=op.f("ck_processing_attempts_staged_revisions_nonnegative"),
        ),
        sa.CheckConstraint(
            "completed_at IS NULL OR completed_at >= started_at",
            name=op.f("ck_processing_attempts_completed_at_order"),
        ),
        sa.CheckConstraint(
            "((status = 'running' AND completed_at IS NULL) OR "
            "(status IN ('success', 'partial_success', 'failed', 'skipped') "
            "AND completed_at IS NOT NULL))",
            name=op.f("ck_processing_attempts_status_completed_at"),
        ),
        sa.CheckConstraint(
            "input_fingerprint ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_processing_attempts_input_fingerprint_format"),
        ),
        sa.CheckConstraint(
            "((output_fingerprint_algorithm IS NULL AND output_fingerprint IS NULL) OR "
            "(output_fingerprint_algorithm IS NOT NULL AND output_fingerprint IS NOT NULL))",
            name=op.f("ck_processing_attempts_output_fingerprint_pair"),
        ),
        sa.CheckConstraint(
            "output_fingerprint IS NULL OR output_fingerprint ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_processing_attempts_output_fingerprint_format"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(safe_diagnostic_codes) = 'array'",
            name=op.f("ck_processing_attempts_safe_diagnostic_codes_array"),
        ),
        sa.CheckConstraint(
            "eligibility_reason_codes IS NULL OR "
            "array_position(eligibility_reason_codes, NULL::text) IS NULL",
            name=op.f("ck_processing_attempts_eligibility_reason_codes_no_nulls"),
        ),
        sa.CheckConstraint(
            "(processing_stage IN ('diagnostics', 'parser', 'normalizer', "
            "'repository_validation') AND "
            "(((group_id IS NOT NULL AND group_page_id IS NOT NULL "
            "AND collection_occurrence_id IS NOT NULL AND raw_payload_id IS NOT NULL)) OR "
            "((group_id IS NULL AND group_page_id IS NULL "
            "AND collection_occurrence_id IS NULL AND raw_payload_id IS NOT NULL)))) OR "
            "(processing_stage IN ('group_evaluation', 'publication_staging') "
            "AND group_id IS NOT NULL AND group_page_id IS NULL "
            "AND collection_occurrence_id IS NULL AND raw_payload_id IS NULL)",
            name=op.f("ck_processing_attempts_context_shape"),
        ),
        sa.CheckConstraint(
            "processing_stage IN ('group_evaluation', 'publication_staging') OR "
            "(selected_pages_evaluated IS NULL AND duplicate_symbol_count IS NULL "
            "AND blocking_conflict_count IS NULL AND staged_revision_count IS NULL "
            "AND pagination_complete_evaluated IS NULL "
            "AND coverage_status_evaluated IS NULL "
            "AND acceptance_eligibility IS NULL "
            "AND eligibility_reason_codes IS NULL)",
            name=op.f("ck_processing_attempts_evaluation_fields_stage"),
        ),
        sa.ForeignKeyConstraint(
            ["ingestion_run_id", "source_id"],
            ["ingestion_runs.id", "ingestion_runs.source_id"],
            name=op.f("fk_processing_attempts_ingestion_run_source"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["group_id", "source_id"],
            ["collection_groups.id", "collection_groups.source_id"],
            name=op.f("fk_processing_attempts_group_source"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["group_page_id", "source_id", "group_id"],
            [
                "collection_group_pages.id",
                "collection_group_pages.source_id",
                "collection_group_pages.group_id",
            ],
            name=op.f("fk_processing_attempts_group_page_source_group"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["raw_payload_id", "source_id"],
            ["raw_payloads.id", "raw_payloads.source_id"],
            name=op.f("fk_processing_attempts_raw_payload_source"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "collection_occurrence_id",
                "source_id",
                "raw_payload_id",
                "group_page_id",
            ],
            [
                "collection_occurrences.id",
                "collection_occurrences.source_id",
                "collection_occurrences.raw_payload_id",
                "collection_occurrences.group_page_id",
            ],
            name=op.f("fk_processing_attempts_occurrence_source_raw_group_page"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_processing_attempts")),
        sa.UniqueConstraint(
            "processing_attempt_sequence",
            name=op.f("uq_processing_attempts_processing_attempt_sequence"),
        ),
        sa.UniqueConstraint(
            "id",
            "group_id",
            name=op.f("uq_processing_attempts_id_group"),
        ),
        sa.UniqueConstraint(
            "id",
            "collection_occurrence_id",
            "group_id",
            name=op.f("uq_processing_attempts_id_occurrence_group"),
        ),
        sa.UniqueConstraint(
            "id",
            "source_id",
            "raw_payload_id",
            name=op.f("uq_processing_attempts_id_source_raw"),
        ),
        sa.UniqueConstraint(
            "id",
            "source_id",
            "raw_payload_id",
            "collection_occurrence_id",
            name=op.f("uq_processing_attempts_id_source_raw_occurrence"),
        ),
    )
    op.create_index(
        "ix_processing_attempts_group_sequence",
        "processing_attempts",
        ["group_id", "processing_attempt_sequence"],
    )
    op.create_index(
        "ix_processing_attempts_group_page_sequence",
        "processing_attempts",
        ["group_page_id", "processing_attempt_sequence"],
    )
    op.create_index(
        "ix_processing_attempts_raw_rule_sequence",
        "processing_attempts",
        ["raw_payload_id", "rule_version", "processing_attempt_sequence"],
    )
    op.create_index(
        "ix_processing_attempts_status_stage_sequence",
        "processing_attempts",
        ["status", "processing_stage", "processing_attempt_sequence"],
    )
    op.create_index(
        "ix_processing_attempts_completed_sequence",
        "processing_attempts",
        ["completed_at", "processing_attempt_sequence"],
        postgresql_where=sa.text("completed_at IS NOT NULL"),
    )


def _create_collection_page_selections() -> None:
    op.create_table(
        "collection_page_selections",
        sa.Column("group_page_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("occurrence_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("selected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("selection_reason", sa.String(length=80), nullable=False),
        sa.Column(
            "selected_by_processing_attempt_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "selection_reason IN ('first_qualifying_success', 'fixture_selected', "
            "'legacy_validation_selection')",
            name=op.f("ck_collection_page_selections_selection_reason"),
        ),
        sa.ForeignKeyConstraint(
            ["group_page_id"],
            ["collection_group_pages.id"],
            name=op.f("fk_collection_page_selections_group_page"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["occurrence_id", "group_page_id"],
            ["collection_occurrences.id", "collection_occurrences.group_page_id"],
            name=op.f("fk_collection_page_selections_occurrence_page"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["selected_by_processing_attempt_id"],
            ["processing_attempts.id"],
            name=op.f("fk_collection_page_selections_selected_by_attempt"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "group_page_id",
            name=op.f("pk_collection_page_selections"),
        ),
        sa.UniqueConstraint(
            "occurrence_id",
            name=op.f("uq_collection_page_selections_occurrence_id"),
        ),
    )


def downgrade() -> None:
    # This downgrade is safe only before page selections and processing
    # attempts contain irreplaceable audit evidence. Export and explicit
    # approval are required after runtime persistence is activated.
    op.drop_table("collection_page_selections")

    op.drop_index(
        "ix_processing_attempts_completed_sequence",
        table_name="processing_attempts",
    )
    op.drop_index(
        "ix_processing_attempts_status_stage_sequence",
        table_name="processing_attempts",
    )
    op.drop_index(
        "ix_processing_attempts_raw_rule_sequence",
        table_name="processing_attempts",
    )
    op.drop_index(
        "ix_processing_attempts_group_page_sequence",
        table_name="processing_attempts",
    )
    op.drop_index(
        "ix_processing_attempts_group_sequence",
        table_name="processing_attempts",
    )
    op.drop_table("processing_attempts")
