import asyncio
import json
from datetime import UTC, datetime

import httpx

from tradehub_data.collectors.bvc_prices.client import BvcPriceClient
from tradehub_data.collectors.bvc_prices.collector import BvcPriceCollector
from tradehub_data.collectors.bvc_prices.config import BvcPriceCollectorConfig
from tradehub_data.collectors.bvc_prices.constants import BVC_PRICE_COLLECTOR_NAME, BVC_PRICE_JSON_SOURCE_ENDPOINT, BVC_PRICE_SOURCE_CODE
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


def make_json_payload(count: int, *, prefix: str = "SYM") -> str:
    rows = []
    for index in range(count):
        symbol = f"{prefix}{index:03d}"
        rows.append(
            {
                "type": "market_watch",
                "id": symbol,
                "attributes": {
                    "code": f"{symbol}-token",
                    "lastTradedPrice": "123.4500000000",
                    "openingPrice": "120.0000000000",
                    "highPrice": "125.0000000000",
                    "lowPrice": "119.0000000000",
                    "staticReferencePrice": "121.0000000000",
                    "varVeille": "1.2300000000",
                    "difference": "1.5000000000",
                    "cumulTitresEchanges": "1000.0000000000",
                    "cumulVolumeEchange": "123450.0000000000",
                    "capitalisation": "999999.0000000000",
                    "totalTrades": 7,
                    "transactTime": "2026-05-18T16:00:00+00:00",
                },
            }
        )
    return json.dumps({"data": {"data": rows}})


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


def test_client_fetch_supports_json_endpoint_headers():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["accept"] == "application/vnd.api+json"
        assert request.headers["referer"] == "https://www.casablanca-bourse.com/fr/live-market/marche-actions-listing"
        assert request.headers["accept-language"] == "fr-FR,fr;q=0.9,en;q=0.8"
        return httpx.Response(200, headers={"content-type": "application/json"}, text=make_json_payload(1), request=request)

    client = BvcPriceClient(make_config(), transport=httpx.MockTransport(handler))

    result = run_async(
        client.fetch(
            "https://www.casablanca-bourse.com/api/proxy/fr/api/bourse_data/last_market_watches/action?page%5Blimit%5D=50&page%5Boffset%5D=0",
            headers={
                "Accept": "application/vnd.api+json",
                "Referer": "https://www.casablanca-bourse.com/fr/live-market/marche-actions-listing",
                "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
            },
        )
    )

    assert result.content_type == "application/json"


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


def test_config_defaults_to_json_endpoint(monkeypatch):
    monkeypatch.delenv("BVC_PRICE_COLLECTOR_JSON_PATH", raising=False)
    monkeypatch.delenv("BVC_PRICE_COLLECTOR_PAGE_LIMIT", raising=False)
    monkeypatch.delenv("BVC_PRICE_COLLECTOR_MAX_PAGES", raising=False)
    monkeypatch.delenv("BVC_PRICE_COLLECTOR_ACCEPT_LANGUAGE", raising=False)

    config = BvcPriceCollectorConfig.from_env()

    assert config.json_enabled is True
    assert config.json_endpoint_base_url == "https://www.casablanca-bourse.com/api/proxy/fr/api/bourse_data/last_market_watches/action"
    assert config.json_page_limit == 50
    assert config.json_max_pages == 5
    assert config.accept_language == "fr-FR,fr;q=0.9,en;q=0.8"


def test_config_accept_language_supports_env_override(monkeypatch):
    monkeypatch.setenv("BVC_PRICE_COLLECTOR_ACCEPT_LANGUAGE", "fr-MA,fr;q=0.8,en;q=0.5")

    config = BvcPriceCollectorConfig.from_env()

    assert config.accept_language == "fr-MA,fr;q=0.8,en;q=0.5"


def test_json_collector_stores_paginated_raw_payloads_and_stops_on_short_page(db_session):
    requested_offsets = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_offsets.append(request.url.params["page[offset]"])
        assert request.headers["accept"] == "application/vnd.api+json"
        assert request.headers["referer"] == "https://www.casablanca-bourse.com/fr/live-market/marche-actions-listing"
        assert request.headers["accept-language"] == "fr-FR,fr;q=0.9,en;q=0.8"
        count = 50 if request.url.params["page[offset]"] == "0" else 30
        return httpx.Response(200, headers={"content-type": "application/json"}, text=make_json_payload(count), request=request)

    config = make_config(json_page_limit=50, json_max_pages=5)
    collector = BvcPriceCollector(db=db_session, config=config, client=BvcPriceClient(config, transport=httpx.MockTransport(handler)))

    result = run_async(collector.run_json_pages())

    assert result.status == "success"
    assert result.payloads_stored == 2
    assert requested_offsets == ["0", "50"]

    raw_payloads = db_session.query(RawPayload).order_by(RawPayload.source_url).all()
    assert {payload.source_endpoint for payload in raw_payloads} == {BVC_PRICE_JSON_SOURCE_ENDPOINT}
    assert {payload.content_type for payload in raw_payloads} == {"application/json"}
    assert {payload.metadata_["collection_mode"] for payload in raw_payloads} == {"live_json"}
    assert {payload.metadata_["page_limit"] for payload in raw_payloads} == {50}
    assert {payload.metadata_["page_offset"] for payload in raw_payloads} == {0, 50}
    assert {payload.metadata_["page_number"] for payload in raw_payloads} == {1, 2}
    assert {payload.metadata_["pagination_group_id"] for payload in raw_payloads}
    assert db_session.query(IngestionRun).one().metadata_["pagination_stop_reason"] == "short_page"


def test_json_collector_stops_on_empty_page(db_session):
    def handler(request: httpx.Request) -> httpx.Response:
        count = 50 if request.url.params["page[offset]"] == "0" else 0
        return httpx.Response(200, headers={"content-type": "application/json"}, text=make_json_payload(count), request=request)

    config = make_config(json_page_limit=50, json_max_pages=5)
    collector = BvcPriceCollector(db=db_session, config=config, client=BvcPriceClient(config, transport=httpx.MockTransport(handler)))

    result = run_async(collector.run_json_pages())

    assert result.status == "success"
    assert result.payloads_stored == 1
    assert db_session.query(IngestionRun).one().metadata_["pagination_stop_reason"] == "empty_page"


def test_json_collector_respects_max_pages(db_session):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "application/json"}, text=make_json_payload(50), request=request)

    config = make_config(json_page_limit=50, json_max_pages=2)
    collector = BvcPriceCollector(db=db_session, config=config, client=BvcPriceClient(config, transport=httpx.MockTransport(handler)))

    result = run_async(collector.run_json_pages())

    assert result.status == "success"
    assert result.source_urls_count == 2
    assert db_session.query(RawPayload).count() == 2
    assert db_session.query(IngestionRun).one().metadata_["pagination_stop_reason"] == "max_pages"


def test_json_collector_records_ssl_failure_clearly(db_session):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed", request=request)

    config = make_config(max_retries=0)
    collector = BvcPriceCollector(db=db_session, config=config, client=BvcPriceClient(config, transport=httpx.MockTransport(handler)))

    result = run_async(collector.run_json_pages())

    assert result.status == "failed"
    assert result.errors_count == 1
    run = db_session.query(IngestionRun).one()
    assert run.metadata_["failed_urls"][0]["error_type"] == "ssl_certificate_error"
    assert "BVC_PRICE_COLLECTOR_CA_BUNDLE_PATH" in run.metadata_["failed_urls"][0]["error"]


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
