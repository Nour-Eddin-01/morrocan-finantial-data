from typing import Literal
from uuid import UUID

from pydantic import BaseModel


class BvcPriceNormalizationResult(BaseModel):
    status: Literal["success", "partial_success", "failed", "skipped"] = "success"
    raw_payload_id: UUID | None = None
    rows_found: int = 0
    rows_normalized: int = 0
    rows_failed: int = 0
    instruments_inserted: int = 0
    instruments_updated: int = 0
    latest_prices_inserted: int = 0
    latest_prices_updated: int = 0
    price_bars_inserted: int = 0
    price_bars_updated: int = 0
    errors_count: int = 0
    message: str | None = None


class BvcPriceNormalizerSummary(BaseModel):
    status: Literal["success", "partial_success", "failed", "skipped"]
    payloads_found: int
    payloads_processed: int
    payloads_failed: int
    rows_normalized: int
    rows_failed: int
    instruments_inserted: int
    instruments_updated: int
    latest_prices_inserted: int
    latest_prices_updated: int
    price_bars_inserted: int
    price_bars_updated: int
    errors_count: int
    message: str | None = None
