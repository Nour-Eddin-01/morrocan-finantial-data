from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class BvcFetchResult(BaseModel):
    source_url: str
    http_status: int
    content_type: str | None
    body_text: str
    fetched_at: datetime
    headers: dict[str, str] = Field(default_factory=dict)


class BvcPriceCollectorResult(BaseModel):
    status: Literal["success", "partial_success", "failed", "skipped"]
    ingestion_run_id: UUID | None
    source_urls_count: int
    payloads_stored: int
    payloads_skipped: int
    errors_count: int
    message: str | None = None

