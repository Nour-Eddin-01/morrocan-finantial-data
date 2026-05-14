import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import BigInteger, Boolean, Date, DateTime, ForeignKey, Index, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from tradehub_data.models.base import Base
from tradehub_data.models.mixins import SourceTraceMixin, TimestampMixin, UUIDPrimaryKeyMixin
from tradehub_data.models.types import JSONBType


class Company(UUIDPrimaryKeyMixin, SourceTraceMixin, TimestampMixin, Base):
    __tablename__ = "companies"
    __table_args__ = (
        Index("ix_companies_sector_id", "sector_id"),
        Index("ix_companies_is_active", "is_active"),
        Index("ix_companies_name", "name"),
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    legal_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    slug: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    sector_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("sectors.id"), nullable=True)
    country_code: Mapped[str] = mapped_column(String(2), nullable=False)
    website_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    logo_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONBType, nullable=True)

    sector: Mapped["Sector | None"] = relationship(back_populates="companies")
    source: Mapped["DataSource | None"] = relationship(foreign_keys="Company.source_id")
    raw_payload: Mapped["RawPayload | None"] = relationship(foreign_keys="Company.raw_payload_id")
    instruments: Mapped[list["Instrument"]] = relationship(back_populates="company")


class Instrument(UUIDPrimaryKeyMixin, SourceTraceMixin, TimestampMixin, Base):
    __tablename__ = "instruments"
    __table_args__ = (
        UniqueConstraint("exchange_id", "symbol", name="uq_instruments_exchange_symbol"),
        UniqueConstraint("exchange_id", "isin", name="uq_instruments_exchange_isin"),
        Index("ix_instruments_company_id", "company_id"),
        Index("ix_instruments_exchange_id", "exchange_id"),
        Index("ix_instruments_symbol", "symbol"),
        Index("ix_instruments_isin", "isin"),
        Index("ix_instruments_is_active", "is_active"),
        Index("ix_instruments_exchange_symbol", "exchange_id", "symbol"),
        Index("ix_instruments_exchange_isin", "exchange_id", "isin"),
    )

    company_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("companies.id"), nullable=True)
    exchange_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("exchanges.id"), nullable=False)
    symbol: Mapped[str] = mapped_column(String(30), nullable=False)
    isin: Mapped[str | None] = mapped_column(String(20), nullable=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    instrument_type: Mapped[str] = mapped_column(String(50), nullable=False)
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)
    market_segment: Mapped[str | None] = mapped_column(String(80), nullable=True)
    listing_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    delisting_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    shares_outstanding: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    free_float_percent: Mapped[Decimal | None] = mapped_column(Numeric(10, 6), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONBType, nullable=True)

    company: Mapped[Company | None] = relationship(back_populates="instruments")
    exchange: Mapped["Exchange"] = relationship(back_populates="instruments")
    source: Mapped["DataSource | None"] = relationship(foreign_keys="Instrument.source_id")
    raw_payload: Mapped["RawPayload | None"] = relationship(foreign_keys="Instrument.raw_payload_id")
    latest_price: Mapped["LatestPrice | None"] = relationship(back_populates="instrument")
    price_bars: Mapped[list["PriceBar"]] = relationship(back_populates="instrument")

