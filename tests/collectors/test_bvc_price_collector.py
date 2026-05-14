import asyncio
from datetime import UTC, datetime

import httpx

from tradehub_data.collectors.bvc_prices.client import BvcPriceClient
from tradehub_data.collectors.bvc_prices.collector import BvcPriceCollector
from tradehub_data.collectors.bvc_prices.config import BvcPriceCollectorConfig
from tradehub_data.collectors.bvc_prices.constants import BVC_PRICE_COLLECTOR_NAME, BVC_PRICE_SOURCE_CODE
from tradehub_data.collectors.bvc_prices.errors import BvcFetchError
from tradehub_data.collectors.bvc_prices.fixtures import store_local_fixture
from tradehub_data.core.hashing import sha256_source_payload, sha256_text
from tradehub_data.models import DataSource, IngestionRun, RawPayload


def run_async(coro):
    return asyncio.run(coro)


def make_config(**overrides) -> BvcPriceCollectorConfig:
    values = {
        "enabled": True,
        "base_url": "https://www.casablanca-bourse.com",
        "source_paths": ["/prices"],
        "timeout_seconds": 5,
        "max_retries": 1,
        "retry_backoff_seconds": 0,
        "sleep_between_requests_ms": 0,
        "user_agent": "TradeHubDataBot/0.1",
        "verify_ssl": True,
    }
    values.update(overrides)
    return BvcPriceCollectorConfig(**values)


def test_hashing_is_stable_and_normalizes_line_endings():
    first = sha256_source_payload(source_url="https://www.casablanca-bourse.com/prices", body_text="a\r\nb")
    second = sha256_source_payload(source_url="https://www.casablanca-bourse.com/prices", body_text="a\nb")

    assert first == second
    assert first != sha256_text("a\nb")


def test_client_fetch_success_captures_response_metadata():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["user-agent"] == "TradeHubDataBot/0.1"
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            text="<html>prix</html>",
            request=request,
        )

    client = BvcPriceClient(make_config(), transport=httpx.MockTransport(handler))

    result = run_async(client.fetch("https://www.casablanca-bourse.com/prices"))

    assert result.http_status == 200
    assert result.content_type == "text/html; charset=utf-8"
    assert result.body_text == "<html>prix</html>"
    assert result.source_url == "https://www.casablanca-bourse.com/prices"
    assert result.fetched_at.tzinfo is not None


def test_client_retries_temporary_http_status():
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(503, text="temporary", request=request)
        return httpx.Response(200, text="<html>ok</html>", request=request)

    client = BvcPriceClient(make_config(max_retries=2), transport=httpx.MockTransport(handler))

    result = run_async(client.fetch("https://www.casablanca-bourse.com/prices"))

    assert attempts == 2
    assert result.body_text == "<html>ok</html>"


def test_client_classifies_ssl_certificate_errors():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed", request=request)

    client = BvcPriceClient(make_config(max_retries=0), transport=httpx.MockTransport(handler))

    try:
        run_async(client.fetch("https://www.casablanca-bourse.com/prices"))
    except BvcFetchError as exc:
        assert exc.error_type == "ssl_certificate_error"
        assert "BVC_PRICE_COLLECTOR_CA_BUNDLE_PATH" in str(exc)
    else:
        raise AssertionError("expected SSL fetch error")


def test_collector_stores_raw_payload_and_ingestion_run(db_session):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text="<html><body><div class='price'>123,45 MAD</div></body></html>",
            request=request,
        )

    config = make_config()
    client = BvcPriceClient(config, transport=httpx.MockTransport(handler))
    collector = BvcPriceCollector(db=db_session, config=config, client=client)

    result = run_async(collector.run())

    assert result.status == "success"
    assert result.payloads_stored == 1
    assert result.payloads_skipped == 0
    assert result.errors_count == 0

    source = db_session.query(DataSource).filter_by(code=BVC_PRICE_SOURCE_CODE).one()
    run = db_session.query(IngestionRun).filter_by(collector_name=BVC_PRICE_COLLECTOR_NAME).one()
    raw_payload = db_session.query(RawPayload).one()

    assert source.source_type == "exchange"
    assert run.status == "success"
    assert run.records_inserted == 1
    assert raw_payload.source_id == source.id
    assert raw_payload.ingestion_run_id == run.id
    assert raw_payload.payload_type == "bvc_price_snapshot"
    assert raw_payload.payload_text.startswith("<html>")
    assert raw_payload.payload_hash


def test_collector_skips_duplicate_payload(db_session):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/html"}, text="<html>same</html>", request=request)

    config = make_config()
    client = BvcPriceClient(config, transport=httpx.MockTransport(handler))
    collector = BvcPriceCollector(db=db_session, config=config, client=client)

    first = run_async(collector.run())
    second = run_async(collector.run())

    assert first.payloads_stored == 1
    assert second.status == "success"
    assert second.payloads_stored == 0
    assert second.payloads_skipped == 1
    assert db_session.query(RawPayload).count() == 1
    assert db_session.query(IngestionRun).count() == 2


def test_collector_records_full_failure(db_session):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="source unavailable", request=request)

    config = make_config(max_retries=0)
    client = BvcPriceClient(config, transport=httpx.MockTransport(handler))
    collector = BvcPriceCollector(db=db_session, config=config, client=client)

    result = run_async(collector.run())

    assert result.status == "failed"
    assert result.payloads_stored == 0
    assert result.errors_count == 1
    assert db_session.query(RawPayload).count() == 0

    run = db_session.query(IngestionRun).one()
    assert run.status == "failed"
    assert run.records_failed == 1
    assert run.metadata_["failed_urls"][0]["error_type"] == "http_error"


def test_disabled_collector_does_not_create_run_or_fetch(db_session):
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("disabled collector should not fetch")

    config = make_config(enabled=False)
    client = BvcPriceClient(config, transport=httpx.MockTransport(handler))
    collector = BvcPriceCollector(db=db_session, config=config, client=client)

    result = run_async(collector.run())

    assert result.status == "skipped"
    assert result.ingestion_run_id is None
    assert db_session.query(IngestionRun).count() == 0
    assert db_session.query(RawPayload).count() == 0


def test_config_rejects_unapproved_source_url():
    try:
        make_config(source_paths=["https://example.com/prices"])
    except Exception as exc:
        assert "not allowed" in str(exc)
    else:
        raise AssertionError("expected unapproved source URL to be rejected")


def test_config_ssl_verification_defaults_to_enabled(monkeypatch):
    monkeypatch.delenv("BVC_PRICE_COLLECTOR_VERIFY_SSL", raising=False)
    monkeypatch.delenv("BVC_PRICE_COLLECTOR_CA_BUNDLE_PATH", raising=False)

    config = BvcPriceCollectorConfig.from_env()

    assert config.verify_ssl is True
    assert config.ca_bundle_path is None


def test_config_supports_explicit_ca_bundle_path(monkeypatch):
    monkeypatch.setenv("BVC_PRICE_COLLECTOR_CA_BUNDLE_PATH", "/app/certs/bvc.pem")

    config = BvcPriceCollectorConfig.from_env()

    assert config.verify_ssl is True
    assert config.ca_bundle_path == "/app/certs/bvc.pem"


def test_config_rejects_invalid_ssl_verification_value(monkeypatch):
    monkeypatch.setenv("BVC_PRICE_COLLECTOR_VERIFY_SSL", "treu")

    try:
        BvcPriceCollectorConfig.from_env()
    except Exception as exc:
        assert "BVC_PRICE_COLLECTOR_VERIFY_SSL" in str(exc)
    else:
        raise AssertionError("expected invalid SSL verification value to be rejected")


def test_config_defaults_to_market_actions_listing(monkeypatch):
    monkeypatch.delenv("BVC_PRICE_COLLECTOR_SOURCE_URLS", raising=False)
    monkeypatch.delenv("BVC_PRICE_COLLECTOR_SOURCE_PATHS", raising=False)

    config = BvcPriceCollectorConfig.from_env()

    assert config.source_urls == ["https://www.casablanca-bourse.com/fr/live-market/marche-actions-listing?amp=1"]


def test_config_source_urls_override_source_paths(monkeypatch):
    monkeypatch.setenv("BVC_PRICE_COLLECTOR_SOURCE_PATHS", "/")
    monkeypatch.setenv(
        "BVC_PRICE_COLLECTOR_SOURCE_URLS",
        "https://www.casablanca-bourse.com/fr/live-market/marche-actions-listing?amp=1,https://www.casablanca-bourse.com/fr/live-market/instruments/BCP",
    )

    config = BvcPriceCollectorConfig.from_env()

    assert config.source_urls == [
        "https://www.casablanca-bourse.com/fr/live-market/marche-actions-listing?amp=1",
        "https://www.casablanca-bourse.com/fr/live-market/instruments/BCP",
    ]


def test_store_local_fixture_creates_raw_payload(db_session, tmp_path):
    fixture_path = tmp_path / "bvc-market.html"
    fixture_path.write_text("<html><body>fixture only</body></html>", encoding="utf-8")

    result = store_local_fixture(db_session, file_path=fixture_path)

    assert result["status"] == "success"
    assert result["payload_inserted"] is True
    assert result["source_url"] == "manual-fixture://bvc_prices/bvc-market.html"
    assert db_session.query(RawPayload).count() == 1

    raw_payload = db_session.query(RawPayload).one()
    run = db_session.query(IngestionRun).one()

    assert run.collector_name == "bvc_price_fixture_loader"
    assert raw_payload.payload_type == "bvc_price_snapshot"
    assert raw_payload.source_endpoint == "manual_fixture"
    assert raw_payload.payload_text == "<html><body>fixture only</body></html>"
    assert raw_payload.metadata_["loaded_by"] == "bvc_price_fixture_loader"


def test_store_local_fixture_is_idempotent(db_session, tmp_path):
    fixture_path = tmp_path / "bvc-market.html"
    fixture_path.write_text("<html><body>same</body></html>", encoding="utf-8")

    first = store_local_fixture(db_session, file_path=fixture_path)
    second = store_local_fixture(db_session, file_path=fixture_path)

    assert first["payload_inserted"] is True
    assert second["payload_inserted"] is False
    assert first["raw_payload_id"] == second["raw_payload_id"]
    assert db_session.query(RawPayload).count() == 1
    assert db_session.query(IngestionRun).count() == 2
