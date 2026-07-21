import asyncio

import httpx
import pytest

from tradehub_data.collectors.bvc_prices.client import BvcPriceClient
from tradehub_data.collectors.bvc_prices.config import BvcPriceCollectorConfig
from tradehub_data.collectors.bvc_prices.models import (
    BvcHttpResponseEvidence,
    BvcTransportFailureEvidence,
)


def _run(coroutine):
    return asyncio.run(coroutine)


def _config(**overrides) -> BvcPriceCollectorConfig:
    values = {
        "base_url": "https://www.casablanca-bourse.com",
        "source_paths": ["/prices"],
        "timeout_seconds": 5,
        "max_retries": 5,
        "retry_backoff_seconds": 0,
        "sleep_between_requests_ms": 0,
        "user_agent": "TradeHubDataClientTest/1.0",
        "verify_ssl": True,
    }
    values.update(overrides)
    return BvcPriceCollectorConfig(**values)


def test_fetch_attempt_returns_exact_2xx_bytes_and_response_metadata_without_decoding():
    entity_body = b"\xff\xfe\x00raw-not-utf8\r\n"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["user-agent"] == "TradeHubDataClientTest/1.0"
        assert request.headers["accept"] == "application/octet-stream"
        return httpx.Response(
            200,
            content=entity_body,
            headers={"content-type": "application/octet-stream; charset=utf-8"},
            request=request,
        )

    client = BvcPriceClient(_config(), transport=httpx.MockTransport(handler))
    result = _run(
        client.fetch_attempt(
            "https://www.casablanca-bourse.com/prices?page%5Boffset%5D=0",
            headers={"Accept": "application/octet-stream"},
        )
    )

    assert isinstance(result, BvcHttpResponseEvidence)
    assert result.entity_body == entity_body
    assert result.status_code == 200
    assert result.requested_url == (
        "https://www.casablanca-bourse.com/prices?page%5Boffset%5D=0"
    )
    assert result.response_url == result.requested_url
    assert result.content_type == "application/octet-stream; charset=utf-8"
    assert result.redirect_location is None
    assert result.requested_at.tzinfo is not None
    assert result.requested_at <= result.response_received_at <= result.finished_at


def test_fetch_attempt_returns_non_2xx_body_without_raise_or_retry():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            503,
            content=b'{"safe":"temporary failure evidence"}',
            headers={"content-type": "application/json"},
            request=request,
        )

    client = BvcPriceClient(_config(max_retries=5), transport=httpx.MockTransport(handler))
    result = _run(client.fetch_attempt("https://www.casablanca-bourse.com/prices"))

    assert isinstance(result, BvcHttpResponseEvidence)
    assert calls == 1
    assert result.status_code == 503
    assert result.entity_body == b'{"safe":"temporary failure evidence"}'


def test_fetch_attempt_returns_redirect_evidence_without_following_it():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(
            302,
            content=b"redirect response body",
            headers={
                "content-type": "text/plain",
                "location": "https://www.casablanca-bourse.com/next?token=ephemeral",
            },
            request=request,
        )

    client = BvcPriceClient(_config(), transport=httpx.MockTransport(handler))
    result = _run(client.fetch_attempt("https://www.casablanca-bourse.com/prices"))

    assert isinstance(result, BvcHttpResponseEvidence)
    assert calls == ["https://www.casablanca-bourse.com/prices"]
    assert result.status_code == 302
    assert result.entity_body == b"redirect response body"
    assert result.redirect_location == (
        "https://www.casablanca-bourse.com/next?token=ephemeral"
    )


def test_fetch_attempt_preserves_zero_byte_response_content():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"", request=request)

    client = BvcPriceClient(_config(), transport=httpx.MockTransport(handler))
    result = _run(client.fetch_attempt("https://www.casablanca-bourse.com/prices"))

    assert isinstance(result, BvcHttpResponseEvidence)
    assert result.status_code == 200
    assert result.entity_body == b""


def test_fetch_attempt_exposes_ordered_repeated_response_headers_for_later_filtering():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"body",
            headers=[
                ("Cache-Control", "no-cache"),
                ("Cache-Control", "max-age=0"),
                ("Content-Type", "text/plain"),
                ("Set-Cookie", "session=not-for-persistence"),
            ],
            request=request,
        )

    client = BvcPriceClient(_config(), transport=httpx.MockTransport(handler))
    result = _run(client.fetch_attempt("https://www.casablanca-bourse.com/prices"))

    assert isinstance(result, BvcHttpResponseEvidence)
    assert result.response_header_items == (
        ("cache-control", "no-cache"),
        ("cache-control", "max-age=0"),
        ("content-type", "text/plain"),
        ("set-cookie", "session=not-for-persistence"),
        ("content-length", "4"),
    )
    assert result.content_type == "text/plain"


@pytest.mark.parametrize(
    ("exception_factory", "expected_code", "expected_message"),
    (
        (
            lambda request, secret: httpx.ReadTimeout(
                f"timeout at {request.url}?private={secret}",
                request=request,
            ),
            "timeout",
            "request timed out",
        ),
        (
            lambda request, secret: httpx.ConnectError(
                f"connection failed for {request.url}?private={secret}",
                request=request,
            ),
            "connect_error",
            "connection failed",
        ),
        (
            lambda request, secret: httpx.ConnectError(
                f"SSL certificate failure for {request.url}?private={secret}",
                request=request,
            ),
            "tls_verification_error",
            "TLS certificate verification failed",
        ),
        (
            lambda request, secret: httpx.RemoteProtocolError(
                f"protocol data included {secret}",
                request=request,
            ),
            "protocol_error",
            "HTTP protocol failure",
        ),
        (
            lambda request, secret: httpx.ReadError(
                f"network data included {secret}",
                request=request,
            ),
            "network_error",
            "network request failed",
        ),
    ),
)
def test_fetch_attempt_classifies_transport_failures_without_private_data(
    exception_factory,
    expected_code: str,
    expected_message: str,
):
    secret = "never-persist-this-secret"

    def handler(request: httpx.Request) -> httpx.Response:
        raise exception_factory(request, secret)

    client = BvcPriceClient(_config(), transport=httpx.MockTransport(handler))
    result = _run(
        client.fetch_attempt(
            "https://www.casablanca-bourse.com/prices?token=private-query-value"
        )
    )

    assert isinstance(result, BvcTransportFailureEvidence)
    assert result.safe_error_code == expected_code
    assert result.safe_error_message == expected_message
    assert result.requested_at.tzinfo is not None
    assert result.requested_at <= result.finished_at

    persisted_shape = repr(result)
    assert secret not in persisted_shape
    assert "private-query-value" not in persisted_shape
    assert "token=" not in persisted_shape


def test_unreadable_ca_bundle_becomes_safe_pre_transport_failure(tmp_path):
    private_path = tmp_path / "private-missing-ca.pem"
    client = BvcPriceClient(_config(ca_bundle_path=str(private_path)))

    result = _run(
        client.fetch_attempt("https://www.casablanca-bourse.com/prices")
    )

    assert isinstance(result, BvcTransportFailureEvidence)
    assert result.safe_error_code == "tls_verification_error"
    assert result.safe_error_message == "TLS certificate verification failed"
    assert str(private_path) not in repr(result)
