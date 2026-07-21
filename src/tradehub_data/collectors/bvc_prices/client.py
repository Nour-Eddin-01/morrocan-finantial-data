from datetime import UTC, datetime
import ssl

import httpx

from tradehub_data.collectors.bvc_prices.config import BvcPriceCollectorConfig
from tradehub_data.collectors.bvc_prices.models import (
    BvcFetchAttemptResult,
    BvcHttpResponseEvidence,
    BvcTransportFailureEvidence,
)


class BvcPriceClient:
    def __init__(
        self,
        config: BvcPriceCollectorConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.config = config
        self.transport = transport

    async def fetch_attempt(
        self,
        source_url: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> BvcFetchAttemptResult:
        """Perform exactly one HTTP hop and expose evidence without interpretation.

        Retries and redirects deliberately live in the collector so every response
        or transport failure can be committed before another network operation.
        """

        request_headers = {"User-Agent": self.config.user_agent}
        if headers:
            request_headers.update(headers)
        requested_at = datetime.now(UTC)
        client: httpx.AsyncClient | None = None
        try:
            client = httpx.AsyncClient(
                timeout=self.config.timeout_seconds,
                headers=request_headers,
                follow_redirects=False,
                verify=self._verify_setting(),
                transport=self.transport,
            )
            response = await client.get(source_url)
            response_received_at = datetime.now(UTC)
            entity_body = response.content
            finished_at = datetime.now(UTC)
            redirect_locations = response.headers.get_list("location")
            return BvcHttpResponseEvidence(
                requested_at=requested_at,
                response_received_at=response_received_at,
                finished_at=finished_at,
                requested_url=str(response.request.url),
                response_url=str(response.url),
                status_code=response.status_code,
                entity_body=entity_body,
                response_header_items=tuple(response.headers.multi_items()),
                content_type=response.headers.get("content-type"),
                redirect_location=(
                    redirect_locations[0] if len(redirect_locations) == 1 else None
                ),
            )
        except httpx.TransportError as exc:
            finished_at = datetime.now(UTC)
            error_code = classify_transport_error(exc)
            return BvcTransportFailureEvidence(
                requested_at=requested_at,
                finished_at=finished_at,
                safe_error_code=error_code,
                safe_error_message=_SAFE_TRANSPORT_MESSAGES[error_code],
            )
        except (OSError, ValueError):
            # SSL-context construction (notably an unreadable custom CA path)
            # may fail before an httpx transport exists.  Return the same
            # bounded evidence shape without exposing path or exception text.
            finished_at = datetime.now(UTC)
            error_code = (
                "tls_verification_error"
                if self.config.ca_bundle_path
                else "network_error"
            )
            return BvcTransportFailureEvidence(
                requested_at=requested_at,
                finished_at=finished_at,
                safe_error_code=error_code,
                safe_error_message=_SAFE_TRANSPORT_MESSAGES[error_code],
            )
        finally:
            if client is not None:
                try:
                    await client.aclose()
                except Exception:
                    # A returned response is already fully read.  Cleanup
                    # failure must not replace or discard its evidence.
                    pass

    def _verify_setting(self) -> bool | ssl.SSLContext:
        if not self.config.verify_ssl:
            return False
        if self.config.ca_bundle_path:
            return ssl.create_default_context(cafile=self.config.ca_bundle_path)
        return True


_SAFE_TRANSPORT_MESSAGES = {
    "timeout": "request timed out",
    "connect_error": "connection failed",
    "tls_verification_error": "TLS certificate verification failed",
    "protocol_error": "HTTP protocol failure",
    "network_error": "network request failed",
}


def classify_transport_error(exc: httpx.TransportError) -> str:
    """Map transport exceptions to a stable code without returning exception text."""

    if isinstance(exc, httpx.TimeoutException):
        return "timeout"
    if isinstance(exc, httpx.ConnectError):
        message = str(exc).casefold()
        if "certificate" in message or "ssl" in message or "tls" in message:
            return "tls_verification_error"
        return "connect_error"
    if isinstance(exc, httpx.ProtocolError):
        return "protocol_error"
    return "network_error"
