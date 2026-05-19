from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from tradehub_data.api.app import create_app
from tradehub_data.db.session import get_db
from tradehub_data.models import Base, DataSource, Exchange, Instrument, LatestPrice, NormalizationError, PriceBar, RawPayload


@pytest.fixture()
def api_db_session() -> Session:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine, autoflush=False, autocommit=False, expire_on_commit=False) as session:
        yield session


@pytest.fixture()
def api_client(api_db_session: Session) -> TestClient:
    app = create_app()

    def override_get_db():
        yield api_db_session

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app)


@pytest.fixture()
def bvc_seed(api_db_session: Session) -> dict[str, object]:
    source = DataSource(
        code="bvc_prices",
        name="Casablanca Stock Exchange prices",
        source_type="exchange",
        base_url="https://www.casablanca-bourse.com",
        country_code="MA",
        is_active=True,
        priority=100,
        metadata_={"public": True},
    )
    exchange = Exchange(
        code="BVC",
        name="Bourse de Casablanca",
        country_code="MA",
        currency_code="MAD",
        timezone="Africa/Casablanca",
        website_url="https://www.casablanca-bourse.com",
    )
    api_db_session.add_all([source, exchange])
    api_db_session.flush()

    collected_at = datetime(2026, 5, 18, 12, 0, tzinfo=UTC)
    pagination_group_id = "bvc_price_snapshot:2026-05-18:manual"
    raw_payload = RawPayload(
        source_id=source.id,
        payload_type="bvc_price_snapshot",
        payload={"raw": "json payload must not be exposed"},
        payload_text="<html>raw payload text must not be exposed</html>",
        payload_hash="a" * 64,
        http_status=200,
        content_type="text/html",
        collected_at=collected_at,
        status="normalized",
        metadata_={
            "normalized_at": "2026-05-18T12:01:00+00:00",
            "pagination_group_id": pagination_group_id,
            "pagination_total_pages": 2,
            "page_number": 1,
            "normalization_rows_found": 50,
            "collection_mode": "manual_fixture",
            "private_headers": {"cookie": "do not expose"},
        },
    )
    raw_payload_page_2 = RawPayload(
        source_id=source.id,
        payload_type="bvc_price_snapshot",
        payload={"raw": "page 2 json payload must not be exposed"},
        payload_text="<html>page 2 raw payload text must not be exposed</html>",
        payload_hash="b" * 64,
        http_status=200,
        content_type="text/html",
        collected_at=datetime(2026, 5, 18, 12, 5, tzinfo=UTC),
        status="normalized",
        metadata_={
            "normalized_at": "2026-05-18T12:06:00+00:00",
            "pagination_group_id": pagination_group_id,
            "pagination_total_pages": 2,
            "page_number": 2,
            "normalization_rows_found": 30,
            "collection_mode": "manual_fixture",
            "private_headers": {"cookie": "do not expose"},
        },
    )
    api_db_session.add_all([raw_payload, raw_payload_page_2])
    api_db_session.flush()

    atw = Instrument(
        exchange_id=exchange.id,
        symbol="ATW",
        isin="MA0000012445",
        name="ATTIJARIWAFA BANK",
        instrument_type="stock",
        currency_code="MAD",
        market_segment="main",
        is_active=True,
        source_id=source.id,
        raw_payload_id=raw_payload.id,
        metadata_={"source_symbol": "ATW"},
    )
    bcp = Instrument(
        exchange_id=exchange.id,
        symbol="BCP",
        isin="MA0000011884",
        name="BANQUE CENTRALE POPULAIRE",
        instrument_type="stock",
        currency_code="MAD",
        market_segment="main",
        is_active=True,
        source_id=source.id,
        raw_payload_id=raw_payload.id,
        metadata_={"source_symbol": "BCP"},
    )
    api_db_session.add_all([atw, bcp])
    api_db_session.flush()

    timestamp = datetime(2026, 5, 18, 12, 0, tzinfo=UTC)
    api_db_session.add_all(
        [
            LatestPrice(
                instrument_id=atw.id,
                price=Decimal("522.500000"),
                open_price=Decimal("520.000000"),
                high_price=Decimal("525.000000"),
                low_price=Decimal("518.000000"),
                previous_close=Decimal("519.000000"),
                change_value=Decimal("3.500000"),
                change_percent=Decimal("0.674000"),
                volume=1200,
                traded_value=Decimal("626400.000000"),
                market_cap=Decimal("1000000000.000000"),
                price_timestamp=timestamp,
                trading_date=date(2026, 5, 18),
                source_id=source.id,
                raw_payload_id=raw_payload.id,
                data_quality_status="valid",
                metadata_={
                    "timestamp_policy": "source_timestamp",
                    "source_trading_date": "2026-05-18",
                    "raw_values": {"payload_text": "do not expose"},
                },
            ),
            LatestPrice(
                instrument_id=bcp.id,
                price=Decimal("298.000000"),
                open_price=None,
                high_price=Decimal("300.000000"),
                low_price=Decimal("297.000000"),
                previous_close=Decimal("296.000000"),
                change_value=None,
                change_percent=Decimal("0.676000"),
                volume=900,
                traded_value=Decimal("268200.000000"),
                market_cap=None,
                price_timestamp=timestamp,
                trading_date=date(2026, 5, 18),
                source_id=source.id,
                raw_payload_id=raw_payload.id,
                data_quality_status="valid",
                metadata_={"timestamp_policy": "source_timestamp", "raw_values": {"secret": "do not expose"}},
            ),
        ]
    )
    api_db_session.add(
        PriceBar(
            instrument_id=atw.id,
            timeframe="1d",
            bar_timestamp=datetime(2026, 5, 18, 0, 0, tzinfo=UTC),
            trading_date=date(2026, 5, 18),
            open_price=Decimal("520.000000"),
            high_price=Decimal("525.000000"),
            low_price=Decimal("518.000000"),
            close_price=Decimal("522.500000"),
            volume=1200,
            traded_value=Decimal("626400.000000"),
            number_of_trades=42,
            source_id=source.id,
            raw_payload_id=raw_payload.id,
            is_adjusted=False,
            data_quality_status="valid",
            metadata_={"timestamp_policy": "trading_date_midnight", "raw_values": {"payload": "do not expose"}},
        )
    )
    api_db_session.add(
        NormalizationError(
            raw_payload_id=raw_payload.id,
            source_id=source.id,
            entity_type="price_row",
            error_type="missing_required_field",
            error_message="Invalid row",
            raw_fragment={"payload_text": "do not expose"},
            status="open",
        )
    )
    api_db_session.commit()

    return {
        "source": source,
        "exchange": exchange,
        "raw_payload": raw_payload,
        "raw_payload_page_2": raw_payload_page_2,
        "atw": atw,
        "bcp": bcp,
    }


def test_list_bvc_instruments(api_client: TestClient, bvc_seed: dict[str, object]):
    response = api_client.get("/api/v1/markets/bvc/instruments")

    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 2
    assert [item["symbol"] for item in body["items"]] == ["ATW", "BCP"]


def test_list_bvc_instruments_pagination(api_client: TestClient, bvc_seed: dict[str, object]):
    response = api_client.get("/api/v1/markets/bvc/instruments", params={"limit": 1, "offset": 1})

    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    assert body["items"][0]["symbol"] == "BCP"


def test_list_latest_prices_supports_symbol_and_trading_date_filters(
    api_client: TestClient,
    bvc_seed: dict[str, object],
):
    response = api_client.get(
        "/api/v1/markets/bvc/latest-prices",
        params={"symbol": "atw", "trading_date": "2026-05-18"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    assert body["items"][0]["symbol"] == "ATW"
    assert body["items"][0]["price"] == "522.500000"
    assert isinstance(body["items"][0]["price"], str)
    assert body["freshness"]["latest_trading_date"] == "2026-05-18"


def test_list_latest_prices_empty_collection(api_client: TestClient, bvc_seed: dict[str, object]):
    response = api_client.get("/api/v1/markets/bvc/latest-prices", params={"trading_date": "2026-05-17"})

    assert response.status_code == 200
    assert response.json()["items"] == []


def test_symbol_query_rejects_blank_symbol(api_client: TestClient, bvc_seed: dict[str, object]):
    response = api_client.get("/api/v1/markets/bvc/latest-prices", params={"symbol": " "})

    assert response.status_code == 422


def test_get_bvc_instrument_detail(api_client: TestClient, bvc_seed: dict[str, object]):
    response = api_client.get("/api/v1/markets/bvc/instruments/atw")

    assert response.status_code == 200
    body = response.json()
    assert body["symbol"] == "ATW"
    assert body["latest_price"]["price"] == "522.500000"


def test_get_bvc_instrument_detail_returns_404_for_missing_symbol(
    api_client: TestClient,
    bvc_seed: dict[str, object],
):
    response = api_client.get("/api/v1/markets/bvc/instruments/NOPE")

    assert response.status_code == 404


def test_list_price_bars_filters_by_trading_date(api_client: TestClient, bvc_seed: dict[str, object]):
    response = api_client.get(
        "/api/v1/markets/bvc/instruments/ATW/price-bars",
        params={"trading_date": "2026-05-18"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["symbol"] == "ATW"
    assert body["count"] == 1
    assert body["items"][0]["close_price"] == "522.500000"
    assert isinstance(body["items"][0]["close_price"], str)


def test_price_bars_timeframe_validation(api_client: TestClient, bvc_seed: dict[str, object]):
    response = api_client.get("/api/v1/markets/bvc/instruments/ATW/price-bars", params={"timeframe": "1h"})

    assert response.status_code == 422


def test_diagnostics_summary_is_safe_and_redacted(api_client: TestClient, bvc_seed: dict[str, object]):
    response = api_client.get("/api/v1/markets/bvc/diagnostics/summary")

    assert response.status_code == 200
    body = response.json()
    assert body["instruments_count"] == 2
    assert body["latest_prices_count"] == 2
    assert body["price_bars_count"] == 1
    assert body["open_normalization_errors_count"] == 1
    assert body["raw_payloads"]["latest_pagination_group_id"] == "bvc_price_snapshot:2026-05-18:manual"
    assert body["raw_payloads"]["latest_pages_found"] == 2
    assert body["raw_payloads"]["latest_total_rows_detected"] == 80
    assert body["raw_payloads"]["latest_collected_at"] == "2026-05-18T12:05:00Z"
    assert body["raw_payloads"]["latest_normalized_at"] == "2026-05-18T12:06:00+00:00"
    assert body["raw_payloads"]["latest_collection_mode"] == "manual_fixture"
    assert body["scheduler_blocked"] is True
    response_text = response.text
    assert "raw payload text must not be exposed" not in response_text
    assert "page 2 raw payload text must not be exposed" not in response_text
    assert "json payload must not be exposed" not in response_text
    assert "page 2 json payload must not be exposed" not in response_text
    assert "payload_text" not in response_text
    assert "cookie" not in response_text


def test_diagnostics_summary_group_total_does_not_use_latest_page_only(
    api_client: TestClient,
    bvc_seed: dict[str, object],
):
    response = api_client.get("/api/v1/markets/bvc/diagnostics/summary")

    assert response.status_code == 200
    raw_payloads = response.json()["raw_payloads"]
    assert raw_payloads["latest_collected_at"] == "2026-05-18T12:05:00Z"
    assert raw_payloads["latest_total_rows_detected"] == 80
    assert raw_payloads["latest_total_rows_detected"] != 30


def test_diagnostics_summary_single_payload_fallback(
    api_client: TestClient,
    api_db_session: Session,
):
    source = DataSource(
        code="bvc_prices",
        name="Casablanca Stock Exchange prices",
        source_type="exchange",
        country_code="MA",
    )
    api_db_session.add(source)
    api_db_session.flush()
    api_db_session.add(
        RawPayload(
            source_id=source.id,
            payload_type="bvc_price_snapshot",
            payload={"raw": "single payload json must not be exposed"},
            payload_text="<html>single payload text must not be exposed</html>",
            payload_hash="c" * 64,
            collected_at=datetime(2026, 5, 18, 13, 0, tzinfo=UTC),
            status="normalized",
            metadata_={
                "normalized_at": "2026-05-18T13:01:00+00:00",
                "page_size": 12,
                "loaded_by": "single_fixture_loader",
                "private_headers": {"cookie": "do not expose"},
            },
        )
    )
    api_db_session.commit()

    response = api_client.get("/api/v1/markets/bvc/diagnostics/summary")

    assert response.status_code == 200
    raw_payloads = response.json()["raw_payloads"]
    assert raw_payloads["latest_pagination_group_id"] is None
    assert raw_payloads["latest_total_rows_detected"] == 12
    assert raw_payloads["latest_collection_mode"] == "single_fixture_loader"
    assert "single payload text must not be exposed" not in response.text
    assert "single payload json must not be exposed" not in response.text


def test_latest_price_metadata_is_safe_and_redacted(api_client: TestClient, bvc_seed: dict[str, object]):
    response = api_client.get("/api/v1/markets/bvc/latest-prices", params={"symbol": "ATW"})

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["metadata"] == {
        "timestamp_policy": "source_timestamp",
        "source_trading_date": "2026-05-18",
    }
    assert "raw_values" not in response.text
