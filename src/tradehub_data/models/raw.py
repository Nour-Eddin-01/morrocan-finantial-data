import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from tradehub_data.models.base import Base
from tradehub_data.models.mixins import CreatedAtMixin, UUIDPrimaryKeyMixin
from tradehub_data.models.types import JSONBType


class RawPayload(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "raw_payloads"
    __table_args__ = (
        UniqueConstraint("source_id", "payload_hash", name="uq_raw_payloads_source_payload_hash"),
        Index("ix_raw_payloads_source_id", "source_id"),
        Index("ix_raw_payloads_ingestion_run_id", "ingestion_run_id"),
        Index("ix_raw_payloads_payload_hash", "payload_hash"),
        Index("ix_raw_payloads_collected_at", "collected_at"),
        Index("ix_raw_payloads_status", "status"),
    )

    source_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("data_sources.id"),
        nullable=False,
    )
    ingestion_run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("ingestion_runs.id"),
        nullable=True,
    )
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_endpoint: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload_type: Mapped[str] = mapped_column(String(50), nullable=False)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONBType, nullable=True)
    payload_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    content_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONBType, nullable=True)

    source: Mapped["DataSource"] = relationship(back_populates="raw_payloads")
    ingestion_run: Mapped["IngestionRun | None"] = relationship(back_populates="raw_payloads")

