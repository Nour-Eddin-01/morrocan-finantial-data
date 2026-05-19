import asyncio
from datetime import UTC, datetime

import httpx

from tradehub_data.collectors.bvc_prices.config import BvcPriceCollectorConfig
from tradehub_data.collectors.bvc_prices.constants import TEMPORARY_STATUS_CODES
from tradehub_data.collectors.bvc_prices.errors import BvcFetchError
from tradehub_data.collectors.bvc_prices.models import BvcFetchResult


class BvcPriceClient:
    def __init__(
        self,
        config: BvcPriceCollectorConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.config = config
        self.transport = transport

    async def fetch(self, source_url: str, *, headers: dict[str, str] | None = None) -> BvcFetchResult:
        request_headers = {"User-Agent": self.config.user_agent}
        if headers:
            request_headers.update(headers)
        async with httpx.AsyncClient(
            timeout=self.config.timeout_seconds,
            headers=request_headers,
            follow_redirects=True,
            verify=self._verify_setting(),
            transport=self.transport,
        ) as client:
            last_error: Exception | None = None
            for attempt in range(self.config.max_retries + 1):
                try:
                    response = await client.get(source_url)
                except (httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout, httpx.NetworkError) as exc:
                    last_error = exc
                    if attempt >= self.config.max_retries:
                        message = str(exc) or exc.__class__.__name__
                        raise BvcFetchError(
                            self._format_network_error(exc, message),
                            source_url=source_url,
                            error_type=self._error_type(exc),
                        ) from exc
                    await self._sleep_before_retry(attempt)
                    continue

                if response.status_code in TEMPORARY_STATUS_CODES and attempt < self.config.max_retries:
                    await self._sleep_before_retry(attempt)
                    continue

                if response.status_code >= 400:
                    raise BvcFetchError(
                        f"HTTP {response.status_code}",
                        source_url=source_url,
                        error_type="http_error",
                    )

                body_text = response.text
                if not body_text:
                    raise BvcFetchError("empty response body", source_url=source_url, error_type="empty_response")

                return BvcFetchResult(
                    source_url=str(response.url),
                    http_status=response.status_code,
                    content_type=response.headers.get("content-type"),
                    body_text=body_text,
                    fetched_at=datetime.now(UTC),
                    headers=dict(response.headers),
                )

            raise BvcFetchError(
                str(last_error) or last_error.__class__.__name__ if last_error else "request failed",
                source_url=source_url,
                error_type="fetch_error",
            )

    async def _sleep_before_retry(self, attempt: int) -> None:
        if self.config.retry_backoff_seconds <= 0:
            return
        await asyncio.sleep(self.config.retry_backoff_seconds * (2**attempt))

    def _verify_setting(self) -> bool | str:
        if not self.config.verify_ssl:
            return False
        if self.config.ca_bundle_path:
            return self.config.ca_bundle_path
        return True

    def _error_type(self, exc: Exception) -> str:
        message = str(exc).lower()
        if "certificate" in message or "ssl" in message:
            return "ssl_certificate_error"
        return exc.__class__.__name__

    def _format_network_error(self, exc: Exception, message: str) -> str:
        if self._error_type(exc) != "ssl_certificate_error":
            return message
        ca_hint = ""
        if self.config.verify_ssl and not self.config.ca_bundle_path:
            ca_hint = " Set BVC_PRICE_COLLECTOR_CA_BUNDLE_PATH to a trusted CA bundle if the source requires an intermediate certificate."
        return f"{message}.{ca_hint}".strip()
