from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from tradehub_data.collectors.bvc_prices.fixtures import store_local_fixture
from tradehub_data.collectors.bvc_prices.constants import BVC_PRICE_PAYLOAD_TYPE
from tradehub_data.models import DataSource, Instrument, LatestPrice, NormalizationError, PriceBar, RawPayload
from tradehub_data.normalizers.bvc_prices.normalizer import BvcPriceNormalizer
from tradehub_data.repositories.raw_payloads import insert_raw_payload_if_new


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
    assert raw_payload.status == "normalized"
    assert raw_payload.metadata_["normalization_rows_normalized"] == 2
    assert raw_payload.metadata_["normalization_errors_count"] == 1

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
    assert raw_payload.metadata_["source_trading_date"] == "2026-05-15"
    assert raw_payload.metadata_["source_timestamp_policy"] == "trading_date_only"


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
