import uuid
from typing import Any

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from tradehub_data.models.base import Base
from tradehub_data.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin
from tradehub_data.models.types import JSONBType


class Exchange(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "exchanges"

    code: Mapped[str] = mapped_column(String(30), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    country_code: Mapped[str] = mapped_column(String(2), nullable=False)
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)
    timezone: Mapped[str] = mapped_column(String(80), nullable=False)
    website_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONBType, nullable=True)

    instruments: Mapped[list["Instrument"]] = relationship(back_populates="exchange")
    market_indices: Mapped[list["MarketIndex"]] = relationship(back_populates="exchange")


class Sector(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "sectors"
    __table_args__ = (Index("ix_sectors_source_id", "source_id"),)

    code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("data_sources.id"),
        nullable=True,
    )
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONBType, nullable=True)

    source: Mapped["DataSource | None"] = relationship()
    companies: Mapped[list["Company"]] = relationship(back_populates="sector")

