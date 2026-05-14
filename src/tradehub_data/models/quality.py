import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from tradehub_data.models.base import Base
from tradehub_data.models.mixins import CreatedAtMixin, UUIDPrimaryKeyMixin
from tradehub_data.models.types import JSONBType


class NormalizationError(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "normalization_errors"
    __table_args__ = (
        Index("ix_normalization_errors_raw_payload_id", "raw_payload_id"),
        Index("ix_normalization_errors_ingestion_run_id", "ingestion_run_id"),
        Index("ix_normalization_errors_source_id", "source_id"),
        Index("ix_normalization_errors_error_type", "error_type"),
        Index("ix_normalization_errors_status", "status"),
        Index("ix_normalization_errors_created_at", "created_at"),
        Index("ix_normalization_errors_status_created_at", "status", "created_at"),
    )

    raw_payload_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("raw_payloads.id"), nullable=True)
    ingestion_run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("ingestion_runs.id"), nullable=True)
    source_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("data_sources.id"), nullable=True)
    entity_type: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_type: Mapped[str] = mapped_column(String(80), nullable=False)
    error_message: Mapped[str] = mapped_column(Text, nullable=False)
    raw_fragment: Mapped[dict[str, Any] | None] = mapped_column(JSONBType, nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    raw_payload: Mapped["RawPayload | None"] = relationship()
    ingestion_run: Mapped["IngestionRun | None"] = relationship()
    source: Mapped["DataSource | None"] = relationship()

