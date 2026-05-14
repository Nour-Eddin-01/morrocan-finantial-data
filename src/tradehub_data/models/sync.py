import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from tradehub_data.models.base import Base
from tradehub_data.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin
from tradehub_data.models.types import JSONBType


class SyncState(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "sync_states"
    __table_args__ = (Index("ix_sync_states_component_name", "component_name"),)

    component_name: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    component_type: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_failure_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("ingestion_runs.id"), nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONBType, nullable=True)

    last_run: Mapped["IngestionRun | None"] = relationship()

