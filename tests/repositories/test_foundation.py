from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from tradehub_data.models import DataSource, Exchange, Instrument, LatestPrice, PriceBar, RawPayload, SyncState
from tradehub_data.repositories.raw_payloads import insert_raw_payload_if_new
from tradehub_data.repositories.sync import update_sync_state


def test_raw_payload_idempotency(db_session):
    source = DataSource(code="test_source", name="Test Source", source_type="manual", priority=100)
    db_session.add(source)
    db_session.commit()

    first, inserted = insert_raw_payload_if_new(
        db_session,
        source_id=source.id,
        payload_hash="a" * 64,
        payload_type="json",
        payload={"ok": True},
        collected_at=datetime.now(UTC),
    )
    second, duplicate_inserted = insert_raw_payload_if_new(
        db_session,
        source_id=source.id,
        payload_hash="a" * 64,
        payload_type="json",
        payload={"ok": True},
        collected_at=datetime.now(UTC),
    )

    assert inserted is True
    assert duplicate_inserted is False
    assert second.id == first.id
    assert db_session.query(RawPayload).count() == 1


def test_instrument_symbol_is_unique_per_exchange(db_session):
    exchange = Exchange(code="BVC", name="Casablanca Stock Exchange", country_code="MA", currency_code="MAD", timezone="Africa/Casablanca")
    db_session.add(exchange)
    db_session.commit()

    db_session.add_all(
        [
            Instrument(exchange_id=exchange.id, symbol="ATW", name="Attijariwafa Bank", instrument_type="equity", currency_code="MAD"),
            Instrument(exchange_id=exchange.id, symbol="ATW", name="Duplicate", instrument_type="equity", currency_code="MAD"),
        ]
    )

    with pytest.raises(IntegrityError):
        db_session.commit()


def test_latest_price_is_unique_per_instrument(db_session):
    exchange = Exchange(code="BVC", name="Casablanca Stock Exchange", country_code="MA", currency_code="MAD", timezone="Africa/Casablanca")
    instrument = Instrument(exchange=exchange, symbol="ATW", name="Attijariwafa Bank", instrument_type="equity", currency_code="MAD")
    db_session.add(instrument)
    db_session.commit()

    now = datetime.now(UTC)
    db_session.add_all(
        [
            LatestPrice(instrument_id=instrument.id, price=Decimal("100.00"), price_timestamp=now, trading_date=now.date(), data_quality_status="valid"),
            LatestPrice(instrument_id=instrument.id, price=Decimal("101.00"), price_timestamp=now, trading_date=now.date(), data_quality_status="valid"),
        ]
    )

    with pytest.raises(IntegrityError):
        db_session.commit()


def test_price_bar_uniqueness_allows_different_timeframes(db_session):
    exchange = Exchange(code="BVC", name="Casablanca Stock Exchange", country_code="MA", currency_code="MAD", timezone="Africa/Casablanca")
    instrument = Instrument(exchange=exchange, symbol="ATW", name="Attijariwafa Bank", instrument_type="equity", currency_code="MAD")
    db_session.add(instrument)
    db_session.commit()

    now = datetime.now(UTC)
    db_session.add_all(
        [
            PriceBar(instrument_id=instrument.id, timeframe="1d", bar_timestamp=now, trading_date=now.date(), close_price=Decimal("100.00"), data_quality_status="valid"),
            PriceBar(instrument_id=instrument.id, timeframe="1h", bar_timestamp=now, trading_date=now.date(), close_price=Decimal("101.00"), data_quality_status="valid"),
        ]
    )
    db_session.commit()

    assert db_session.query(PriceBar).count() == 2


def test_sync_state_updates_existing_component(db_session):
    first = update_sync_state(
        db_session,
        component_name="api",
        values={"component_type": "api", "status": "healthy", "message": "ok"},
    )
    second = update_sync_state(
        db_session,
        component_name="api",
        values={"component_type": "api", "status": "degraded", "message": "database slow"},
    )

    assert second.id == first.id
    assert db_session.query(SyncState).count() == 1
    assert second.status == "degraded"

