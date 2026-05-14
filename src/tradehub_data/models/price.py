import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import BigInteger, Boolean, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from tradehub_data.models.base import Base
from tradehub_data.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin
from tradehub_data.models.types import JSONBType


class LatestPrice(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "latest_prices"
    __table_args__ = (
        Index("ix_latest_prices_instrument_id", "instrument_id"),
        Index("ix_latest_prices_price_timestamp", "price_timestamp"),
        Index("ix_latest_prices_trading_date", "trading_date"),
        Index("ix_latest_prices_data_quality_status", "data_quality_status"),
    )

    instrument_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("instruments.id"),
        nullable=False,
        unique=True,
    )
    price: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    open_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    high_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    low_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    previous_close: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    change_value: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    change_percent: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), nullable=True)
    volume: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    traded_value: Mapped[Decimal | None] = mapped_column(Numeric(24, 6), nullable=True)
    market_cap: Mapped[Decimal | None] = mapped_column(Numeric(24, 6), nullable=True)
    price_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    trading_date: Mapped[date] = mapped_column(Date, nullable=False)
    source_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("data_sources.id"), nullable=True)
    raw_payload_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("raw_payloads.id"), nullable=True)
    data_quality_status: Mapped[str] = mapped_column(String(30), nullable=False)
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONBType, nullable=True)

    instrument: Mapped["Instrument"] = relationship(back_populates="latest_price")
    source: Mapped["DataSource | None"] = relationship()
    raw_payload: Mapped["RawPayload | None"] = relationship()


class PriceBar(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "price_bars"
    __table_args__ = (
        UniqueConstraint("instrument_id", "timeframe", "bar_timestamp", name="uq_price_bars_instrument_timeframe_timestamp"),
        Index("ix_price_bars_instrument_timeframe_timestamp", "instrument_id", "timeframe", "bar_timestamp"),
        Index("ix_price_bars_trading_date", "trading_date"),
        Index("ix_price_bars_data_quality_status", "data_quality_status"),
    )

    instrument_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("instruments.id"), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(20), nullable=False)
    bar_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    trading_date: Mapped[date] = mapped_column(Date, nullable=False)
    open_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    high_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    low_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    close_price: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    volume: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    traded_value: Mapped[Decimal | None] = mapped_column(Numeric(24, 6), nullable=True)
    number_of_trades: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("data_sources.id"), nullable=True)
    raw_payload_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("raw_payloads.id"), nullable=True)
    is_adjusted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    data_quality_status: Mapped[str] = mapped_column(String(30), nullable=False)
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONBType, nullable=True)

    instrument: Mapped["Instrument"] = relationship(back_populates="price_bars")
    source: Mapped["DataSource | None"] = relationship()
    raw_payload: Mapped["RawPayload | None"] = relationship()

