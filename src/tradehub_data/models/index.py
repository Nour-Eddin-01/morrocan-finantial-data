import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from tradehub_data.models.base import Base
from tradehub_data.models.mixins import SourceTraceMixin, TimestampMixin, UUIDPrimaryKeyMixin
from tradehub_data.models.types import JSONBType


class MarketIndex(UUIDPrimaryKeyMixin, SourceTraceMixin, TimestampMixin, Base):
    __tablename__ = "market_indices"
    __table_args__ = (
        UniqueConstraint("exchange_id", "symbol", name="uq_market_indices_exchange_symbol"),
        Index("ix_market_indices_exchange_symbol", "exchange_id", "symbol"),
    )

    exchange_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("exchanges.id"), nullable=False)
    symbol: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONBType, nullable=True)

    exchange: Mapped["Exchange"] = relationship(back_populates="market_indices")
    source: Mapped["DataSource | None"] = relationship(foreign_keys="MarketIndex.source_id")
    raw_payload: Mapped["RawPayload | None"] = relationship(foreign_keys="MarketIndex.raw_payload_id")
    latest_value: Mapped["LatestIndexValue | None"] = relationship(back_populates="index")
    index_bars: Mapped[list["IndexBar"]] = relationship(back_populates="index")


class LatestIndexValue(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "latest_index_values"

    index_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("market_indices.id"),
        nullable=False,
        unique=True,
    )
    value: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    open_value: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    high_value: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    low_value: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    previous_close: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    change_value: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    change_percent: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), nullable=True)
    value_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    trading_date: Mapped[date] = mapped_column(Date, nullable=False)
    source_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("data_sources.id"), nullable=True)
    raw_payload_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("raw_payloads.id"), nullable=True)
    data_quality_status: Mapped[str] = mapped_column(String(30), nullable=False)
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONBType, nullable=True)

    index: Mapped[MarketIndex] = relationship(back_populates="latest_value")
    source: Mapped["DataSource | None"] = relationship()
    raw_payload: Mapped["RawPayload | None"] = relationship()


class IndexBar(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "index_bars"
    __table_args__ = (
        UniqueConstraint("index_id", "timeframe", "bar_timestamp", name="uq_index_bars_index_timeframe_timestamp"),
        Index("ix_index_bars_index_timeframe_timestamp", "index_id", "timeframe", "bar_timestamp"),
        Index("ix_index_bars_trading_date", "trading_date"),
    )

    index_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("market_indices.id"), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(20), nullable=False)
    bar_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    trading_date: Mapped[date] = mapped_column(Date, nullable=False)
    open_value: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    high_value: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    low_value: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    close_value: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    source_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("data_sources.id"), nullable=True)
    raw_payload_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("raw_payloads.id"), nullable=True)
    data_quality_status: Mapped[str] = mapped_column(String(30), nullable=False)
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONBType, nullable=True)

    index: Mapped[MarketIndex] = relationship(back_populates="index_bars")
    source: Mapped["DataSource | None"] = relationship()
    raw_payload: Mapped["RawPayload | None"] = relationship()

