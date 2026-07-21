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
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from tradehub_data.models.base import Base
from tradehub_data.models.enums import (
    COLLECTION_COMPLETION_EVIDENCE_KINDS,
    COLLECTION_COVERAGE_STATUSES,
    COLLECTION_GROUP_PURPOSES,
    COLLECTION_MODES,
    COLLECTION_OCCURRENCE_OUTCOMES,
    COLLECTION_PAGE_OUTCOMES,
    COLLECTION_PAGE_ROLES,
    COLLECTION_PAGE_SELECTION_REASONS,
    COLLECTION_STATUSES,
)
from tradehub_data.models.mixins import CreatedAtMixin, UUIDPrimaryKeyMixin
from tradehub_data.models.types import JSONBType


def _sql_string_values(values: tuple[str, ...]) -> str:
    return "(" + ", ".join(f"'{value}'" for value in values) + ")"


class CollectionGroup(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "collection_groups"
    __table_args__ = (
        ForeignKeyConstraint(
            ["source_id"],
            ["data_sources.id"],
            name="fk_collection_groups_source_id_data_sources",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["exchange_id"],
            ["exchanges.id"],
            name="fk_collection_groups_exchange_id_exchanges",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["ingestion_run_id", "source_id"],
            ["ingestion_runs.id", "ingestion_runs.source_id"],
            name="fk_collection_groups_ingestion_run_source",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("group_sequence", name="uq_collection_groups_group_sequence"),
        UniqueConstraint("id", "source_id", name="uq_collection_groups_id_source_id"),
        UniqueConstraint(
            "id",
            "source_id",
            "ingestion_run_id",
            name="uq_collection_groups_id_source_id_ingestion_run_id",
        ),
        UniqueConstraint(
            "id",
            "source_id",
            "ingestion_run_id",
            "page_limit",
            name="uq_collection_groups_id_source_run_page_limit",
        ),
        CheckConstraint(
            f"collection_mode IN {_sql_string_values(COLLECTION_MODES)}",
            name="collection_mode",
        ),
        CheckConstraint(
            f"group_purpose IN {_sql_string_values(COLLECTION_GROUP_PURPOSES)}",
            name="group_purpose",
        ),
        CheckConstraint(
            f"collection_status IN {_sql_string_values(COLLECTION_STATUSES)}",
            name="collection_status",
        ),
        CheckConstraint(
            f"coverage_status IN {_sql_string_values(COLLECTION_COVERAGE_STATUSES)}",
            name="coverage_status",
        ),
        CheckConstraint(
            "completion_evidence_kind IN "
            f"{_sql_string_values(COLLECTION_COMPLETION_EVIDENCE_KINDS)}",
            name="completion_evidence_kind",
        ),
        CheckConstraint("page_limit > 0", name="page_limit_positive"),
        CheckConstraint(
            "expected_pages IS NULL OR expected_pages >= 0",
            name="expected_pages_nonnegative",
        ),
        CheckConstraint(
            "selected_data_pages >= 0",
            name="selected_data_pages_nonnegative",
        ),
        CheckConstraint(
            "expected_instrument_count IS NULL OR expected_instrument_count >= 0",
            name="expected_instrument_count_nonnegative",
        ),
        CheckConstraint(
            "observed_instrument_count IS NULL OR observed_instrument_count >= 0",
            name="observed_instrument_count_nonnegative",
        ),
        CheckConstraint(
            "collection_completed_at IS NULL OR collection_completed_at >= started_at",
            name="collection_completed_at_order",
        ),
        CheckConstraint(
            "finalized_at IS NULL OR finalized_at >= started_at",
            name="finalized_at_order",
        ),
        CheckConstraint(
            "((collection_status = 'running' AND finalized_at IS NULL) OR "
            "(collection_status IN ('success', 'partial_success', 'failed') "
            "AND finalized_at IS NOT NULL))",
            name="status_finalized_at",
        ),
        CheckConstraint(
            "collection_status <> 'success' OR pagination_complete IS TRUE",
            name="success_pagination_complete",
        ),
        CheckConstraint(
            "NOT (group_purpose = 'production' AND collection_mode = 'manual_fixture')",
            name="production_not_manual_fixture",
        ),
        Index("ix_collection_groups_source_sequence", "source_id", "group_sequence"),
        Index(
            "ix_collection_groups_exchange_dataset_purpose_sequence",
            "exchange_id",
            "dataset_code",
            "group_purpose",
            "group_sequence",
        ),
        Index(
            "ix_collection_groups_status_sequence",
            "collection_status",
            "group_sequence",
        ),
        Index(
            "ix_collection_groups_pagination_sequence",
            "pagination_complete",
            "group_sequence",
        ),
    )

    group_sequence: Mapped[int] = mapped_column(
        BigInteger,
        Identity(always=True),
        nullable=False,
    )
    source_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    exchange_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    ingestion_run_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    dataset_code: Mapped[str] = mapped_column(String(80), nullable=False)
    collection_mode: Mapped[str] = mapped_column(String(30), nullable=False)
    group_purpose: Mapped[str] = mapped_column(String(30), nullable=False)
    external_group_key: Mapped[str | None] = mapped_column(String(160), nullable=True)
    page_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    collection_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    collection_status: Mapped[str] = mapped_column(String(30), nullable=False)
    pagination_complete: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    completion_evidence_kind: Mapped[str] = mapped_column(String(50), nullable=False)
    expected_pages: Mapped[int | None] = mapped_column(Integer, nullable=True)
    selected_data_pages: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    terminal_page_present: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )
    coverage_status: Mapped[str] = mapped_column(String(20), nullable=False)
    expected_instrument_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    observed_instrument_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    collection_stop_reason: Mapped[str | None] = mapped_column(String(80), nullable=True)
    safe_diagnostic_codes: Mapped[list[str]] = mapped_column(
        JSONBType,
        nullable=False,
        default=list,
        server_default=text("'[]'"),
    )
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CollectionGroupPage(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "collection_group_pages"
    __table_args__ = (
        ForeignKeyConstraint(
            ["group_id", "source_id", "ingestion_run_id", "page_limit"],
            [
                "collection_groups.id",
                "collection_groups.source_id",
                "collection_groups.ingestion_run_id",
                "collection_groups.page_limit",
            ],
            name="fk_collection_group_pages_group_source_run_limit",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "group_id",
            "logical_page_number",
            name="uq_collection_group_pages_group_page_number",
        ),
        UniqueConstraint(
            "group_id",
            "page_offset",
            name="uq_collection_group_pages_group_page_offset",
        ),
        UniqueConstraint(
            "id",
            "source_id",
            "ingestion_run_id",
            name="uq_collection_group_pages_id_source_run",
        ),
        UniqueConstraint(
            "id",
            "source_id",
            "group_id",
            name="uq_collection_group_pages_id_source_group",
        ),
        CheckConstraint(
            f"page_role IN {_sql_string_values(COLLECTION_PAGE_ROLES)}",
            name="page_role",
        ),
        CheckConstraint(
            f"collection_page_outcome IN {_sql_string_values(COLLECTION_PAGE_OUTCOMES)}",
            name="outcome",
        ),
        CheckConstraint("logical_page_number > 0", name="page_number_positive"),
        CheckConstraint("page_offset >= 0", name="page_offset_nonnegative"),
        CheckConstraint("page_limit > 0", name="page_limit_positive"),
        CheckConstraint(
            "CAST(page_offset AS BIGINT) = "
            "(CAST(logical_page_number AS BIGINT) - 1) * CAST(page_limit AS BIGINT)",
            name="offset_formula",
        ),
        CheckConstraint(
            "((collection_page_outcome = 'pending' AND finalized_at IS NULL) OR "
            "(collection_page_outcome IN ('success', 'failed') AND finalized_at IS NOT NULL))",
            name="outcome_finalized_at",
        ),
        Index(
            "ix_collection_group_pages_outcome_page_number",
            "group_id",
            "collection_page_outcome",
            "logical_page_number",
        ),
    )

    group_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    source_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    ingestion_run_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    page_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    logical_page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    page_offset: Mapped[int] = mapped_column(Integer, nullable=False)
    page_role: Mapped[str] = mapped_column(String(30), nullable=False)
    collection_page_outcome: Mapped[str] = mapped_column(String(30), nullable=False)
    structural_reason_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CollectionOccurrence(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "collection_occurrences"
    __table_args__ = (
        ForeignKeyConstraint(
            ["ingestion_run_id", "source_id"],
            ["ingestion_runs.id", "ingestion_runs.source_id"],
            name="fk_collection_occurrences_ingestion_run_source",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["group_page_id", "source_id", "ingestion_run_id"],
            [
                "collection_group_pages.id",
                "collection_group_pages.source_id",
                "collection_group_pages.ingestion_run_id",
            ],
            name="fk_collection_occurrences_group_page_source_run",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["raw_payload_id", "source_id"],
            ["raw_payloads.id", "raw_payloads.source_id"],
            name="fk_collection_occurrences_raw_payload_source",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "occurrence_sequence",
            name="uq_collection_occurrences_occurrence_sequence",
        ),
        UniqueConstraint(
            "ingestion_run_id",
            "request_sequence",
            "attempt_number",
            "redirect_hop",
            name="uq_collection_occurrences_run_request_attempt_redirect",
        ),
        UniqueConstraint(
            "id",
            "group_page_id",
            name="uq_collection_occurrences_id_group_page",
        ),
        UniqueConstraint(
            "id",
            "source_id",
            "raw_payload_id",
            name="uq_collection_occurrences_id_source_raw",
        ),
        UniqueConstraint(
            "id",
            "source_id",
            "raw_payload_id",
            "group_page_id",
            name="uq_collection_occurrences_id_source_raw_group_page",
        ),
        CheckConstraint(
            f"outcome IN {_sql_string_values(COLLECTION_OCCURRENCE_OUTCOMES)}",
            name="outcome",
        ),
        CheckConstraint("request_sequence > 0", name="request_sequence_positive"),
        CheckConstraint("attempt_number > 0", name="attempt_number_positive"),
        CheckConstraint("redirect_hop >= 0", name="redirect_hop_nonnegative"),
        CheckConstraint("requested_at <= finished_at", name="requested_finished_order"),
        CheckConstraint(
            "response_received_at IS NULL OR "
            "(requested_at <= response_received_at AND response_received_at <= finished_at)",
            name="response_time_order",
        ),
        CheckConstraint(
            "http_status IS NULL OR http_status BETWEEN 100 AND 599",
            name="http_status_range",
        ),
        CheckConstraint(
            "body_length IS NULL OR body_length >= 0",
            name="body_length_nonnegative",
        ),
        CheckConstraint(
            "dropped_response_header_name_count >= 0",
            name="dropped_header_count_nonnegative",
        ),
        CheckConstraint(
            "jsonb_typeof(safe_response_headers) = 'object'",
            name="safe_headers_object",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
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
            name="outcome_evidence",
        ),
        Index(
            "ix_collection_occurrences_source_sequence",
            "source_id",
            "occurrence_sequence",
        ),
        Index(
            "ix_collection_occurrences_ingestion_run_sequence",
            "ingestion_run_id",
            "occurrence_sequence",
        ),
        Index(
            "ix_collection_occurrences_group_page_sequence",
            "group_page_id",
            "occurrence_sequence",
        ),
        Index(
            "ix_collection_occurrences_raw_payload_sequence",
            "raw_payload_id",
            "occurrence_sequence",
            postgresql_where=text("raw_payload_id IS NOT NULL"),
        ),
        Index(
            "ix_collection_occurrences_outcome_sequence",
            "outcome",
            "occurrence_sequence",
        ),
    )

    occurrence_sequence: Mapped[int] = mapped_column(
        BigInteger,
        Identity(always=True),
        nullable=False,
    )
    source_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    ingestion_run_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    group_page_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    raw_payload_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    request_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    redirect_hop: Mapped[int] = mapped_column(Integer, nullable=False)
    logical_request_url: Mapped[str] = mapped_column(Text, nullable=False)
    requested_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    response_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_endpoint: Mapped[str | None] = mapped_column(String(160), nullable=True)
    request_profile: Mapped[str] = mapped_column(String(80), nullable=False)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    response_received_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    http_status: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    content_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    body_length: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    outcome: Mapped[str] = mapped_column(String(30), nullable=False)
    safe_error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    safe_error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    safe_response_headers: Mapped[dict[str, list[str]]] = mapped_column(
        JSONBType,
        nullable=False,
        default=dict,
        server_default=text("'{}'"),
    )
    dropped_response_header_name_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    response_headers_overflow: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )
    response_headers_policy_version: Mapped[str] = mapped_column(String(50), nullable=False)


class CollectionPageSelection(CreatedAtMixin, Base):
    __tablename__ = "collection_page_selections"
    __table_args__ = (
        ForeignKeyConstraint(
            ["group_page_id"],
            ["collection_group_pages.id"],
            name="fk_collection_page_selections_group_page",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["occurrence_id", "group_page_id"],
            ["collection_occurrences.id", "collection_occurrences.group_page_id"],
            name="fk_collection_page_selections_occurrence_page",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["selected_by_processing_attempt_id"],
            ["processing_attempts.id"],
            name="fk_collection_page_selections_selected_by_attempt",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "occurrence_id",
            name="uq_collection_page_selections_occurrence_id",
        ),
        CheckConstraint(
            "selection_reason IN "
            f"{_sql_string_values(COLLECTION_PAGE_SELECTION_REASONS)}",
            name="selection_reason",
        ),
    )

    group_page_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    occurrence_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    selected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    selection_reason: Mapped[str] = mapped_column(String(80), nullable=False)
    selected_by_processing_attempt_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        nullable=True,
    )
