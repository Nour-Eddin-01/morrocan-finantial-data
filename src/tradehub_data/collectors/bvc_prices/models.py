from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel


@dataclass(frozen=True, slots=True)
class BvcHttpResponseEvidence:
    """One fully received client-visible HTTP response, before text decoding."""

    requested_at: datetime
    response_received_at: datetime
    finished_at: datetime
    requested_url: str = field(repr=False)
    response_url: str = field(repr=False)
    status_code: int
    entity_body: bytes = field(repr=False)
    response_header_items: tuple[tuple[str, str], ...] = field(repr=False)
    content_type: str | None = field(repr=False)
    redirect_location: str | None = field(default=None, repr=False)
    source_published_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class BvcTransportFailureEvidence:
    """A bounded no-response transport outcome safe for audit persistence."""

    requested_at: datetime
    finished_at: datetime
    safe_error_code: str
    safe_error_message: str


BvcFetchAttemptResult = BvcHttpResponseEvidence | BvcTransportFailureEvidence


class BvcPriceCollectorResult(BaseModel):
    status: Literal["success", "partial_success", "failed", "skipped"]
    ingestion_run_id: UUID | None
    source_urls_count: int
    payloads_stored: int
    payloads_skipped: int
    errors_count: int
    message: str | None = None
