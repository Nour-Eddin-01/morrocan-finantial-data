import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Identity,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from tradehub_data.models.base import Base
from tradehub_data.models.enums import (
    PROCESSING_ACCEPTANCE_ELIGIBILITIES,
    PROCESSING_ATTEMPT_STATUSES,
    PROCESSING_COVERAGE_STATUSES,
    PROCESSING_GROUP_STAGES,
    PROCESSING_PAGE_STAGES,
    PROCESSING_STAGES,
)
from tradehub_data.models.mixins import CreatedAtMixin, UUIDPrimaryKeyMixin
from tradehub_data.models.types import JSONBType, TextArrayType


def _sql_string_values(values: tuple[str, ...]) -> str:
    return "(" + ", ".join(f"'{value}'" for value in values) + ")"


_PAGE_STAGES_SQL = _sql_string_values(PROCESSING_PAGE_STAGES)
_GROUP_STAGES_SQL = _sql_string_values(PROCESSING_GROUP_STAGES)


class ProcessingAttempt(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "processing_attempts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["ingestion_run_id", "source_id"],
            ["ingestion_runs.id", "ingestion_runs.source_id"],
            name="fk_processing_attempts_ingestion_run_source",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["group_id", "source_id"],
            ["collection_groups.id", "collection_groups.source_id"],
            name="fk_processing_attempts_group_source",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["group_page_id", "source_id", "group_id"],
            [
                "collection_group_pages.id",
                "collection_group_pages.source_id",
                "collection_group_pages.group_id",
            ],
            name="fk_processing_attempts_group_page_source_group",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["raw_payload_id", "source_id"],
            ["raw_payloads.id", "raw_payloads.source_id"],
            name="fk_processing_attempts_raw_payload_source",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
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
            name="fk_processing_attempts_occurrence_source_raw_group_page",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "processing_attempt_sequence",
            name="uq_processing_attempts_processing_attempt_sequence",
        ),
        UniqueConstraint(
            "id",
            "group_id",
            name="uq_processing_attempts_id_group",
        ),
        UniqueConstraint(
            "id",
            "collection_occurrence_id",
            "group_id",
            name="uq_processing_attempts_id_occurrence_group",
        ),
        UniqueConstraint(
            "id",
            "source_id",
            "raw_payload_id",
            name="uq_processing_attempts_id_source_raw",
        ),
        UniqueConstraint(
            "id",
            "source_id",
            "raw_payload_id",
            "collection_occurrence_id",
            name="uq_processing_attempts_id_source_raw_occurrence",
        ),
        CheckConstraint(
            f"processing_stage IN {_sql_string_values(PROCESSING_STAGES)}",
            name="processing_stage",
        ),
        CheckConstraint(
            f"status IN {_sql_string_values(PROCESSING_ATTEMPT_STATUSES)}",
            name="status",
        ),
        CheckConstraint(
            "coverage_status_evaluated IS NULL OR coverage_status_evaluated IN "
            f"{_sql_string_values(PROCESSING_COVERAGE_STATUSES)}",
            name="coverage_status_evaluated",
        ),
        CheckConstraint(
            "acceptance_eligibility IS NULL OR acceptance_eligibility IN "
            f"{_sql_string_values(PROCESSING_ACCEPTANCE_ELIGIBILITIES)}",
            name="acceptance_eligibility",
        ),
        CheckConstraint(
            "processing_attempt_sequence > 0",
            name="sequence_positive",
        ),
        CheckConstraint("rows_found >= 0", name="rows_found_nonnegative"),
        CheckConstraint("rows_usable >= 0", name="rows_usable_nonnegative"),
        CheckConstraint("rows_failed >= 0", name="rows_failed_nonnegative"),
        CheckConstraint("errors_count >= 0", name="errors_count_nonnegative"),
        CheckConstraint(
            "selected_pages_evaluated IS NULL OR selected_pages_evaluated >= 0",
            name="selected_pages_nonnegative",
        ),
        CheckConstraint(
            "duplicate_symbol_count IS NULL OR duplicate_symbol_count >= 0",
            name="duplicate_symbols_nonnegative",
        ),
        CheckConstraint(
            "blocking_conflict_count IS NULL OR blocking_conflict_count >= 0",
            name="blocking_conflicts_nonnegative",
        ),
        CheckConstraint(
            "staged_revision_count IS NULL OR staged_revision_count >= 0",
            name="staged_revisions_nonnegative",
        ),
        CheckConstraint(
            "completed_at IS NULL OR completed_at >= started_at",
            name="completed_at_order",
        ),
        CheckConstraint(
            "((status = 'running' AND completed_at IS NULL) OR "
            "(status IN ('success', 'partial_success', 'failed', 'skipped') "
            "AND completed_at IS NOT NULL))",
            name="status_completed_at",
        ),
        CheckConstraint(
            "input_fingerprint ~ '^[0-9a-f]{64}$'",
            name="input_fingerprint_format",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "((output_fingerprint_algorithm IS NULL AND output_fingerprint IS NULL) OR "
            "(output_fingerprint_algorithm IS NOT NULL AND output_fingerprint IS NOT NULL))",
            name="output_fingerprint_pair",
        ),
        CheckConstraint(
            "output_fingerprint IS NULL OR output_fingerprint ~ '^[0-9a-f]{64}$'",
            name="output_fingerprint_format",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "jsonb_typeof(safe_diagnostic_codes) = 'array'",
            name="safe_diagnostic_codes_array",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "eligibility_reason_codes IS NULL OR "
            "array_position(eligibility_reason_codes, NULL::text) IS NULL",
            name="eligibility_reason_codes_no_nulls",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            f"(processing_stage IN {_PAGE_STAGES_SQL} AND "
            "(((group_id IS NOT NULL AND group_page_id IS NOT NULL "
            "AND collection_occurrence_id IS NOT NULL AND raw_payload_id IS NOT NULL)) OR "
            "((group_id IS NULL AND group_page_id IS NULL "
            "AND collection_occurrence_id IS NULL AND raw_payload_id IS NOT NULL)))) OR "
            f"(processing_stage IN {_GROUP_STAGES_SQL} "
            "AND group_id IS NOT NULL AND group_page_id IS NULL "
            "AND collection_occurrence_id IS NULL AND raw_payload_id IS NULL)",
            name="context_shape",
        ),
        CheckConstraint(
            f"processing_stage IN {_GROUP_STAGES_SQL} OR "
            "(selected_pages_evaluated IS NULL AND duplicate_symbol_count IS NULL "
            "AND blocking_conflict_count IS NULL AND staged_revision_count IS NULL "
            "AND pagination_complete_evaluated IS NULL "
            "AND coverage_status_evaluated IS NULL "
            "AND acceptance_eligibility IS NULL "
            "AND eligibility_reason_codes IS NULL)",
            name="evaluation_fields_stage",
        ),
        Index(
            "ix_processing_attempts_group_sequence",
            "group_id",
            "processing_attempt_sequence",
        ),
        Index(
            "ix_processing_attempts_group_page_sequence",
            "group_page_id",
            "processing_attempt_sequence",
        ),
        Index(
            "ix_processing_attempts_raw_rule_sequence",
            "raw_payload_id",
            "rule_version",
            "processing_attempt_sequence",
        ),
        Index(
            "ix_processing_attempts_status_stage_sequence",
            "status",
            "processing_stage",
            "processing_attempt_sequence",
        ),
        Index(
            "ix_processing_attempts_completed_sequence",
            "completed_at",
            "processing_attempt_sequence",
            postgresql_where=text("completed_at IS NOT NULL"),
        ),
    )

    processing_attempt_sequence: Mapped[int] = mapped_column(
        BigInteger,
        Identity(always=True),
        nullable=False,
    )
    source_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    ingestion_run_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    group_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    group_page_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    collection_occurrence_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        nullable=True,
    )
    raw_payload_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    processing_stage: Mapped[str] = mapped_column(String(40), nullable=False)
    component_version: Mapped[str] = mapped_column(String(100), nullable=False)
    rule_version: Mapped[str] = mapped_column(String(100), nullable=False)
    input_fingerprint_algorithm: Mapped[str] = mapped_column(String(80), nullable=False)
    input_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    rows_found: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    rows_usable: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    rows_failed: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    errors_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    selected_pages_evaluated: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duplicate_symbol_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    blocking_conflict_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    staged_revision_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pagination_complete_evaluated: Mapped[bool | None] = mapped_column(
        Boolean,
        nullable=True,
    )
    coverage_status_evaluated: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
    )
    acceptance_eligibility: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
    )
    eligibility_reason_codes: Mapped[list[str] | None] = mapped_column(
        TextArrayType,
        nullable=True,
    )
    output_fingerprint_algorithm: Mapped[str | None] = mapped_column(
        String(80),
        nullable=True,
    )
    output_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    safe_diagnostic_codes: Mapped[list[str]] = mapped_column(
        JSONBType,
        nullable=False,
        default=list,
        server_default=text("'[]'"),
    )
