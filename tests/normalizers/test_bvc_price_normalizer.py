import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from tradehub_data.collectors.bvc_prices.fixtures import store_local_fixture
from tradehub_data.collectors.bvc_prices.constants import BVC_PRICE_JSON_SOURCE_ENDPOINT, BVC_PRICE_PAYLOAD_TYPE
from tradehub_data.models import DataSource, Instrument, LatestPrice, NormalizationError, PriceBar, RawPayload
from tradehub_data.normalizers.bvc_prices.normalizer import BvcPriceNormalizer
from tradehub_data.repositories.raw_payloads import insert_raw_payload_if_new


def json_market_payload(rows):
    return json.dumps({"data": {"data": rows}})


def json_market_row(
    symbol: str,
    *,
    last_traded_price="123.4500000000",
    cours_courant=None,
    status="N.T",
    transact_time="2026-05-19T10:00:00+00:00",
):
    attributes = {
        "code": f"{symbol}-token",
        "lastTradedPrice": last_traded_price,
        "coursCourant": cours_courant,
        "openingPrice": None,
        "highPrice": None,
        "lowPrice": None,
        "staticReferencePrice": cours_courant or "121.0000000000",
        "varVeille": "0.0000000000",
        "difference": "0.0000000000",
        "cumulTitresEchanges": None,
        "cumulVolumeEchange": None,
        "capitalisation": "999999.0000000000",
        "totalTrades": None,
        "transactTime": transact_time,
        "etatCotVal": status,
    }
    return {"type": "market_watch", "id": symbol, "attributes": attributes}


def insert_json_raw_payload(db_session, source: DataSource, payload_text: str, *, payload_hash: str):
    raw_payload, _ = insert_raw_payload_if_new(
        db_session,
        source_id=source.id,
        payload_hash=payload_hash,
        payload_type=BVC_PRICE_PAYLOAD_TYPE,
        source_endpoint=BVC_PRICE_JSON_SOURCE_ENDPOINT,
        content_type="application/json",
        payload_text=payload_text,
        collected_at=datetime(2026, 5, 19, 12, 0, tzinfo=UTC),
        status="collected",
        metadata={"collection_mode": "live_json", "page_number": 1},
    )
    db_session.commit()
    return raw_payload


def test_bvc_normalizer_writes_prices_records_partial_errors_and_is_idempotent(db_session):
    result = store_local_fixture(
        db_session,
        file_path=Path("fixtures/bvc_prices/sample_market_listing.html"),
        source_url="https://www.casablanca-bourse.com/fr/live-market/marche-actions-listing?amp=1",
    )
    raw_payload = db_session.get(RawPayload, UUID(result["raw_payload_id"]))

    normalizer = BvcPriceNormalizer(db_session)
    first = normalizer.normalize_by_id(raw_payload.id)
    second = normalizer.normalize_by_id(raw_payload.id)

    assert first.status == "partial_success"
    assert first.rows_normalized == 2
    assert first.rows_failed == 1
    assert first.instruments_inserted == 2
    assert first.latest_prices_inserted == 2
    assert first.price_bars_inserted == 2
    assert first.errors_count == 1

    assert second.status == "partial_success"
    assert second.rows_normalized == 2
    assert second.rows_failed == 1
    assert second.instruments_inserted == 0
    assert second.latest_prices_inserted == 0
    assert second.price_bars_inserted == 0
    assert db_session.query(Instrument).count() == 2
    assert db_session.query(LatestPrice).count() == 2
    assert db_session.query(PriceBar).count() == 2
    assert db_session.query(NormalizationError).count() == 1

    db_session.refresh(raw_payload)
    # Target exact-content rows keep collection identity immutable.  The
    # normalized tables/results above prove compatibility; processing state
    # will move to processing_attempts in a later mission.
    assert raw_payload.content_evidence_kind == "exact_entity_bytes"
    assert raw_payload.status == "collected"
    assert raw_payload.metadata_ is None

    instrument = db_session.query(Instrument).filter_by(symbol="SBK").one()
    optional_instrument = db_session.query(Instrument).filter_by(symbol="OFS").one()
    latest_price = db_session.query(LatestPrice).filter_by(instrument_id=instrument.id).one()
    price_bar = db_session.query(PriceBar).filter_by(instrument_id=instrument.id).one()

    assert instrument.symbol == "SBK"
    assert instrument.isin == "MA0000000001"
    assert instrument.source_id == raw_payload.source_id
    assert optional_instrument.isin is None
    assert latest_price.raw_payload_id == raw_payload.id
    assert price_bar.raw_payload_id == raw_payload.id
    assert price_bar.timeframe == "1d"

    error = db_session.query(NormalizationError).one()
    assert error.error_type == "missing_instrument_identifier"


def test_bvc_normalizer_handles_json_raw_payload_idempotently(db_session):
    source = DataSource(code="bvc_prices", name="BVC Prices", source_type="exchange", priority=100)
    db_session.add(source)
    db_session.flush()
    payload_text = json_market_payload(
        [
            json_market_row(
                "JSN",
                last_traded_price="123.4500000000",
                status="OPEN",
                transact_time="2026-05-18T16:00:00+00:00",
            )
        ]
    )
    raw_payload, _ = insert_raw_payload_if_new(
        db_session,
        source_id=source.id,
        payload_hash="9" * 64,
        payload_type=BVC_PRICE_PAYLOAD_TYPE,
        source_endpoint=BVC_PRICE_JSON_SOURCE_ENDPOINT,
        content_type="application/json",
        payload_text=payload_text,
        collected_at=datetime(2026, 5, 18, 12, 0, tzinfo=UTC),
        status="collected",
        metadata={"collection_mode": "live_json", "page_number": 1},
    )
    db_session.commit()

    normalizer = BvcPriceNormalizer(db_session)
    first = normalizer.normalize_by_id(raw_payload.id)
    second = normalizer.normalize_by_id(raw_payload.id)

    assert first.status == "success"
    assert first.instruments_inserted == 1
    assert first.latest_prices_inserted == 1
    assert first.price_bars_inserted == 1
    assert second.status == "success"
    assert second.instruments_inserted == 0
    assert second.latest_prices_inserted == 0
    assert second.price_bars_inserted == 0

    instrument = db_session.query(Instrument).one()
    latest_price = db_session.query(LatestPrice).one()
    price_bar = db_session.query(PriceBar).one()

    assert instrument.symbol == "JSN"
    assert latest_price.price == Decimal("123.450000")
    assert latest_price.price_timestamp.replace(tzinfo=UTC) == datetime(2026, 5, 18, 16, 0, tzinfo=UTC)
    assert latest_price.metadata_["timestamp_policy"] == "source_timestamp"
    assert price_bar.bar_timestamp.replace(tzinfo=UTC) == datetime(2026, 5, 18, 16, 0, tzinfo=UTC)
    assert price_bar.metadata_["source_timestamp"] == "2026-05-18T16:00:00+00:00"
    db_session.refresh(raw_payload)
    assert raw_payload.status == "normalized"


def test_bvc_normalizer_accepts_not_traded_json_row_with_cours_courant(db_session):
    source = DataSource(code="bvc_prices", name="BVC Prices", source_type="exchange", priority=100)
    db_session.add(source)
    db_session.flush()
    raw_payload = insert_json_raw_payload(
        db_session,
        source,
        json_market_payload([json_market_row("AFM", last_traded_price=None, cours_courant="1240.0000000000", status="N.T")]),
        payload_hash="f" * 64,
    )

    normalizer = BvcPriceNormalizer(db_session)
    first = normalizer.normalize_by_id(raw_payload.id)
    second = normalizer.normalize_by_id(raw_payload.id)

    assert first.status == "success"
    assert first.rows_normalized == 1
    assert first.errors_count == 0
    assert first.latest_prices_inserted == 1
    assert first.price_bars_inserted == 1
    assert second.status == "success"
    assert second.latest_prices_inserted == 0
    assert second.price_bars_inserted == 0
    assert db_session.query(LatestPrice).count() == 1
    assert db_session.query(PriceBar).count() == 1
    assert db_session.query(NormalizationError).count() == 0

    latest_price = db_session.query(LatestPrice).one()
    price_bar = db_session.query(PriceBar).one()
    assert latest_price.price == Decimal("1240.000000")
    assert latest_price.metadata_["raw_values"]["source_status"] == "N.T"
    assert price_bar.close_price == Decimal("1240.000000")
    assert price_bar.metadata_["raw_values"]["source_status"] == "N.T"


def test_bvc_normalizer_accepts_suspended_json_row_with_cours_courant(db_session):
    source = DataSource(code="bvc_prices", name="BVC Prices", source_type="exchange", priority=100)
    db_session.add(source)
    db_session.flush()
    raw_payload = insert_json_raw_payload(
        db_session,
        source,
        json_market_payload([json_market_row("SAM", last_traded_price="", cours_courant="127.8000000000", status="S")]),
        payload_hash="1" * 64,
    )

    result = BvcPriceNormalizer(db_session).normalize_by_id(raw_payload.id)

    assert result.status == "success"
    assert result.rows_normalized == 1
    assert result.errors_count == 0
    latest_price = db_session.query(LatestPrice).one()
    price_bar = db_session.query(PriceBar).one()
    assert latest_price.price == Decimal("127.800000")
    assert latest_price.metadata_["raw_values"]["source_status"] == "S"
    assert price_bar.close_price == Decimal("127.800000")
    assert db_session.query(NormalizationError).count() == 0


def test_bvc_normalizer_still_records_missing_price_when_json_price_aliases_are_missing(db_session):
    source = DataSource(code="bvc_prices", name="BVC Prices", source_type="exchange", priority=100)
    db_session.add(source)
    db_session.flush()
    raw_payload = insert_json_raw_payload(
        db_session,
        source,
        json_market_payload([json_market_row("MISS", last_traded_price=None, cours_courant=None, status="N.T")]),
        payload_hash="2" * 64,
    )

    result = BvcPriceNormalizer(db_session).normalize_by_id(raw_payload.id)

    assert result.status == "failed"
    assert result.rows_normalized == 0
    assert result.errors_count == 1
    assert db_session.query(LatestPrice).count() == 0
    assert db_session.query(PriceBar).count() == 0
    error = db_session.query(NormalizationError).one()
    assert error.error_type == "missing_price"


def test_bvc_normalizer_partial_success_for_mixed_payload(db_session):
    source = DataSource(code="bvc_prices", name="BVC Prices", source_type="exchange", priority=100)
    db_session.add(source)
    db_session.flush()
    raw_payload, _ = insert_raw_payload_if_new(
        db_session,
        source_id=source.id,
        payload_hash="c" * 64,
        payload_type=BVC_PRICE_PAYLOAD_TYPE,
        payload_text="""
        <table>
          <thead><tr><th>Instrument</th><th>Symbole</th><th>Dernier cours</th><th>Volume</th></tr></thead>
          <tbody>
            <tr><td>VALID SA</td><td>VAL</td><td>12,34</td><td>100</td></tr>
            <tr><td>NO IDENTIFIER SA</td><td></td><td>56,78</td><td>200</td></tr>
          </tbody>
        </table>
        """,
        collected_at=datetime(2026, 5, 15, 12, 0, tzinfo=UTC),
        status="collected",
    )
    db_session.commit()

    result = BvcPriceNormalizer(db_session).normalize_by_id(raw_payload.id)

    assert result.status == "partial_success"
    assert result.rows_normalized == 1
    assert result.rows_failed == 1
    assert result.errors_count == 1
    assert db_session.query(Instrument).count() == 1
    assert db_session.query(LatestPrice).count() == 1
    assert db_session.query(PriceBar).count() == 1
    assert db_session.query(NormalizationError).count() == 1
    db_session.refresh(raw_payload)
    assert raw_payload.status == "normalized"


def test_bvc_normalizer_uses_source_trading_date_timestamp_policy(db_session):
    result = store_local_fixture(
        db_session,
        file_path=Path("fixtures/bvc_prices/dated_market_listing.html"),
        source_url="https://www.casablanca-bourse.com/fr/live-market/marche-actions-listing?amp=1",
    )
    raw_payload = db_session.get(RawPayload, UUID(result["raw_payload_id"]))
    raw_payload.collected_at = datetime(2026, 5, 16, 12, 30, tzinfo=UTC)
    db_session.commit()

    summary = BvcPriceNormalizer(db_session).normalize_by_id(raw_payload.id)

    assert summary.status == "success"
    latest_price = db_session.query(LatestPrice).one()
    price_bar = db_session.query(PriceBar).one()
    db_session.refresh(raw_payload)

    assert latest_price.trading_date.isoformat() == "2026-05-15"
    assert latest_price.price_timestamp.replace(tzinfo=UTC) == datetime(2026, 5, 16, 12, 30, tzinfo=UTC)
    assert latest_price.metadata_["timestamp_policy"] == "raw_payload_collected_at_no_source_time"
    assert latest_price.metadata_["source_trading_date"] == "2026-05-15"
    assert latest_price.metadata_["source_timestamp"] is None

    assert price_bar.trading_date.isoformat() == "2026-05-15"
    assert price_bar.bar_timestamp.isoformat().startswith("2026-05-15T00:00:00")
    assert price_bar.metadata_["timestamp_policy"] == "trading_date_start_of_day"
    assert raw_payload.content_evidence_kind == "exact_entity_bytes"
    assert raw_payload.metadata_ is None


def test_bvc_normalizer_uses_collected_at_when_source_date_is_missing(db_session):
    result = store_local_fixture(
        db_session,
        file_path=Path("fixtures/bvc_prices/no_timestamp_market_listing.html"),
        source_url="https://www.casablanca-bourse.com/fr/live-market/marche-actions-listing?amp=1",
    )
    raw_payload = db_session.get(RawPayload, UUID(result["raw_payload_id"]))
    raw_payload.collected_at = datetime(2026, 5, 16, 12, 30, tzinfo=UTC)
    db_session.commit()

    summary = BvcPriceNormalizer(db_session).normalize_by_id(raw_payload.id)

    assert summary.status == "success"
    latest_price = db_session.query(LatestPrice).one()
    price_bar = db_session.query(PriceBar).one()

    assert latest_price.price_timestamp.replace(tzinfo=UTC) == datetime(2026, 5, 16, 12, 30, tzinfo=UTC)
    assert latest_price.metadata_["timestamp_policy"] == "raw_payload_collected_at_no_source_time"
    assert latest_price.metadata_["source_trading_date"] is None
    assert price_bar.bar_timestamp.replace(tzinfo=UTC) == datetime(2026, 5, 16, 12, 30, tzinfo=UTC)
    assert price_bar.metadata_["timestamp_policy"] == "raw_payload_collected_at_no_source_date"


def test_bvc_normalizer_does_not_overwrite_latest_price_with_older_timestamp(db_session):
    source = DataSource(code="bvc_prices", name="BVC Prices", source_type="exchange", priority=100)
    db_session.add(source)
    db_session.flush()
    newer_payload, _ = insert_raw_payload_if_new(
        db_session,
        source_id=source.id,
        payload_hash="d" * 64,
        payload_type=BVC_PRICE_PAYLOAD_TYPE,
        payload_text="""
        <table>
          <thead><tr><th>Instrument</th><th>Symbole</th><th>Dernier cours</th><th>Volume</th></tr></thead>
          <tbody><tr><td>TIME SAFE SA</td><td>TSS</td><td>20,00</td><td>100</td></tr></tbody>
        </table>
        """,
        collected_at=datetime(2026, 5, 15, 12, 0, tzinfo=UTC),
        status="collected",
    )
    older_payload, _ = insert_raw_payload_if_new(
        db_session,
        source_id=source.id,
        payload_hash="e" * 64,
        payload_type=BVC_PRICE_PAYLOAD_TYPE,
        payload_text="""
        <table>
          <thead><tr><th>Instrument</th><th>Symbole</th><th>Dernier cours</th><th>Volume</th></tr></thead>
          <tbody><tr><td>TIME SAFE SA</td><td>TSS</td><td>10,00</td><td>100</td></tr></tbody>
        </table>
        """,
        collected_at=datetime(2026, 5, 14, 12, 0, tzinfo=UTC),
        status="collected",
    )
    db_session.commit()

    normalizer = BvcPriceNormalizer(db_session)
    newer = normalizer.normalize_by_id(newer_payload.id)
    older = normalizer.normalize_by_id(older_payload.id)

    assert newer.status == "success"
    assert older.status == "success"
    assert older.latest_prices_inserted == 0
    assert older.latest_prices_updated == 0

    latest_price = db_session.query(LatestPrice).one()
    assert latest_price.price == Decimal("20.000000")
    assert latest_price.price_timestamp.replace(tzinfo=UTC) == datetime(2026, 5, 15, 12, 0, tzinfo=UTC)


def test_bvc_normalizer_records_errors_for_invalid_rows(db_session):
    source = DataSource(code="bvc_prices", name="BVC Prices", source_type="exchange", priority=100)
    db_session.add(source)
    db_session.flush()
    raw_payload, _ = insert_raw_payload_if_new(
        db_session,
        source_id=source.id,
        payload_hash="b" * 64,
        payload_type=BVC_PRICE_PAYLOAD_TYPE,
        payload_text="""
        <table>
          <thead><tr><th>Instrument</th><th>Dernier cours</th><th>Volume</th></tr></thead>
          <tbody><tr><td>NO SYMBOL</td><td>12,34</td><td>100</td></tr></tbody>
        </table>
        """,
        collected_at=datetime(2026, 5, 15, 12, 0, tzinfo=UTC),
        status="collected",
    )
    db_session.commit()

    normalizer = BvcPriceNormalizer(db_session)
    result = normalizer.normalize_by_id(raw_payload.id)

    assert result.status == "failed"
    assert result.rows_normalized == 0
    assert result.errors_count == 1
    assert db_session.query(Instrument).count() == 0
    assert db_session.query(LatestPrice).count() == 0
    assert db_session.query(PriceBar).count() == 0

    error = db_session.query(NormalizationError).one()
    assert error.error_type == "missing_instrument_identifier"
    db_session.refresh(raw_payload)
    assert raw_payload.status == "failed"
