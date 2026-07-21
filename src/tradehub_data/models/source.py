import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from tradehub_data.models.base import Base
from tradehub_data.models.enums import INGESTION_RUN_ROLES, INGESTION_RUN_STATUSES
from tradehub_data.models.mixins import CreatedAtMixin, TimestampMixin, UUIDPrimaryKeyMixin
from tradehub_data.models.types import JSONBType


class DataSource(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "data_sources"

    code: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    source_type: Mapped[str] = mapped_column(String(50), nullable=False)
    base_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    country_code: Mapped[str | None] = mapped_column(String(2), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100, server_default="100")
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONBType, nullable=True)

    ingestion_runs: Mapped[list["IngestionRun"]] = relationship(back_populates="source")
    raw_payloads: Mapped[list["RawPayload"]] = relationship(back_populates="source")


class IngestionRun(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "ingestion_runs"
    __table_args__ = (
        UniqueConstraint("id", "source_id", name="uq_ingestion_runs_id_source_id"),
        ForeignKeyConstraint(
            ["parent_run_id"],
            ["ingestion_runs.id"],
            name="fk_ingestion_runs_parent_run_id_ingestion_runs",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            f"run_role IN {INGESTION_RUN_ROLES!r}",
            name="run_role",
        ),
        CheckConstraint(
            f"status IN {INGESTION_RUN_STATUSES!r}",
            name="status",
            postgresql_not_valid=True,
        ),
        CheckConstraint(
            "records_collected >= 0",
            name="records_collected_nonnegative",
            postgresql_not_valid=True,
        ),
        CheckConstraint(
            "records_inserted >= 0",
            name="records_inserted_nonnegative",
            postgresql_not_valid=True,
        ),
        CheckConstraint(
            "records_updated >= 0",
            name="records_updated_nonnegative",
            postgresql_not_valid=True,
        ),
        CheckConstraint(
            "records_failed >= 0",
            name="records_failed_nonnegative",
            postgresql_not_valid=True,
        ),
        CheckConstraint(
            "((status = 'running' AND finished_at IS NULL) OR "
            "(status IN ('success', 'partial_success', 'failed') AND finished_at IS NOT NULL))",
            name="status_finished_at",
            postgresql_not_valid=True,
        ),
        CheckConstraint(
            "finished_at IS NULL OR finished_at >= started_at",
            name="finished_at_order",
            postgresql_not_valid=True,
        ),
        Index("ix_ingestion_runs_source_id", "source_id"),
        Index("ix_ingestion_runs_collector_name", "collector_name"),
        Index("ix_ingestion_runs_status", "status"),
        Index("ix_ingestion_runs_started_at", "started_at"),
        Index("ix_ingestion_runs_source_started_at", "source_id", "started_at"),
    )

    source_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("data_sources.id"),
        nullable=False,
    )
    collector_name: Mapped[str] = mapped_column(String(120), nullable=False)
    run_type: Mapped[str] = mapped_column(String(50), nullable=False)
    run_role: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        default="legacy_unclassified",
        server_default="legacy_unclassified",
    )
    parent_run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    records_collected: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    records_inserted: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    records_updated: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    records_failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    safe_error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONBType, nullable=True)

    source: Mapped[DataSource] = relationship(back_populates="ingestion_runs")
    raw_payloads: Mapped[list["RawPayload"]] = relationship(back_populates="ingestion_run")
