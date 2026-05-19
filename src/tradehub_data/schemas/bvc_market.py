from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel


class BvcInstrumentItem(BaseModel):
    id: UUID
    symbol: str
    isin: str | None
    name: str
    instrument_type: str
    currency_code: str
    market_segment: str | None
    is_active: bool


class BvcInstrumentListResponse(BaseModel):
    count: int
    limit: int
    offset: int
    items: list[BvcInstrumentItem]


class BvcLatestPriceItem(BaseModel):
    instrument_id: UUID
    symbol: str
    name: str
    price: str
    open_price: str | None
    high_price: str | None
    low_price: str | None
    previous_close: str | None
    change_value: str | None
    change_percent: str | None
    volume: int | None
    traded_value: str | None
    market_cap: str | None
    price_timestamp: datetime
    trading_date: date
    data_quality_status: str
    metadata: dict[str, Any]


class BvcFreshness(BaseModel):
    latest_collected_at: datetime | None
    latest_price_timestamp: datetime | None
    latest_trading_date: date | None


class BvcLatestPriceListResponse(BaseModel):
    count: int
    limit: int
    offset: int
    freshness: BvcFreshness
    items: list[BvcLatestPriceItem]


class BvcInstrumentDetailResponse(BvcInstrumentItem):
    latest_price: BvcLatestPriceItem | None


class BvcPriceBarItem(BaseModel):
    id: UUID
    timeframe: str
    bar_timestamp: datetime
    trading_date: date
    open_price: str | None
    high_price: str | None
    low_price: str | None
    close_price: str
    volume: int | None
    traded_value: str | None
    number_of_trades: int | None
    is_adjusted: bool
    data_quality_status: str
    metadata: dict[str, Any]


class BvcPriceBarListResponse(BaseModel):
    symbol: str
    timeframe: str
    count: int
    limit: int
    offset: int
    items: list[BvcPriceBarItem]


class BvcRawPayloadDiagnostics(BaseModel):
    latest_collected_at: datetime | None
    latest_normalized_at: str | None
    latest_pagination_group_id: str | None
    latest_pages_found: int | None
    latest_total_rows_detected: int | None
    latest_collection_mode: str | None


class BvcDiagnosticsSummaryResponse(BaseModel):
    latest_trading_date: date | None
    instruments_count: int
    latest_prices_count: int
    price_bars_count: int
    open_normalization_errors_count: int
    raw_payloads: BvcRawPayloadDiagnostics
    scheduler_blocked: bool
    live_collection_status: str
    blockers: list[str]
