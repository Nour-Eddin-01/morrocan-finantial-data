from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field


class BvcParsedPriceRow(BaseModel):
    raw_payload_id: UUID
    row_index: int
    source_symbol: str | None = None
    source_name: str | None = None
    isin: str | None = None
    last_price: Decimal | None = None
    open_price: Decimal | None = None
    high_price: Decimal | None = None
    low_price: Decimal | None = None
    previous_close: Decimal | None = None
    change_value: Decimal | None = None
    change_percent: Decimal | None = None
    volume: int | None = None
    traded_value: Decimal | None = None
    market_cap: Decimal | None = None
    number_of_trades: int | None = None
    source_timestamp: datetime | None = None
    trading_date: date | None = None
    raw_values: dict[str, str | None] = Field(default_factory=dict)


class BvcRowParseError(BaseModel):
    row_index: int | None = None
    error_type: str
    error_message: str
    raw_fragment: dict[str, str | None] | None = None


class BvcPriceParseResult(BaseModel):
    raw_payload_id: UUID
    rows: list[BvcParsedPriceRow] = Field(default_factory=list)
    errors: list[BvcRowParseError] = Field(default_factory=list)
    source_timestamp: datetime | None = None
    source_trading_date: date | None = None
    source_timestamp_raw: str | None = None
    source_timestamp_policy: str = "raw_payload_collected_at_no_source_date"
    raw_date_candidates: list[str] = Field(default_factory=list)
    pagination_metadata: dict = Field(default_factory=dict)

    @property
    def trading_date(self) -> date | None:
        return self.source_trading_date
