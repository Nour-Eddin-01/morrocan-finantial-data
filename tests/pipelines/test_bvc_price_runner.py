from datetime import UTC, datetime
from pathlib import Path

from tradehub_data.collectors.bvc_prices.constants import BVC_PRICE_PAYLOAD_TYPE
from tradehub_data.models import DataSource, Instrument, LatestPrice, PriceBar, RawPayload
from tradehub_data.pipelines.bvc_prices.runner import BvcPipelineRunner
from tradehub_data.repositories.raw_payloads import insert_raw_payload_if_new


def test_bvc_pipeline_runs_from_raw_payload_id(db_session):
    source = DataSource(code="bvc_prices", name="BVC Prices", source_type="exchange", priority=100)
    db_session.add(source)
    db_session.flush()
    raw_payload, _ = insert_raw_payload_if_new(
        db_session,
        source_id=source.id,
        payload_hash="1" * 64,
        payload_type=BVC_PRICE_PAYLOAD_TYPE,
        payload_text=Path("fixtures/bvc_prices/sample_market_listing.html").read_text(encoding="utf-8"),
        collected_at=datetime(2026, 5, 15, 12, 0, tzinfo=UTC),
        status="collected",
    )
    db_session.commit()

    result = BvcPipelineRunner(db_session).run_raw_payload(raw_payload.id)

    assert result.mode == "raw_payload_id"
    assert result.raw_payload_id == raw_payload.id
    assert result.source_id == source.id
    assert result.diagnostics_status == "success"
    assert result.tables_found == 1
    assert result.rows_detected == 3
    assert result.parseable_rows_count == 3
    assert result.row_parse_errors_count == 0
    assert result.normalization_status == "partial_success"
    assert result.instruments_inserted == 2
    assert result.latest_prices_inserted == 2
    assert result.price_bars_inserted == 2
    assert result.errors_count == 1
    assert result.final_raw_payload_status == "normalized"


def test_bvc_pipeline_runs_from_fixture_path(db_session):
    result = BvcPipelineRunner(db_session).run_fixture(Path("fixtures/bvc_prices/sample_market_listing.html"))

    assert result.mode == "fixture_path"
    assert result.raw_payload_id is not None
    assert result.source_id is not None
    assert result.diagnostics_status == "success"
    assert result.normalization_status == "partial_success"
    assert result.rows_detected == 3
    assert result.parseable_rows_count == 3
    assert db_session.query(RawPayload).count() == 1
    assert db_session.query(Instrument).count() == 2
    assert db_session.query(LatestPrice).count() == 2
    assert db_session.query(PriceBar).count() == 2


def test_bvc_pipeline_exposes_timestamp_and_pagination_fields(db_session):
    result = BvcPipelineRunner(db_session).run_fixture(Path("fixtures/bvc_prices/paginated_market_listing.html"))

    assert result.mode == "fixture_path"
    assert result.diagnostics_status == "success"
    assert result.source_trading_date.isoformat() == "2026-05-15"
    assert result.source_timestamp is None
    assert result.source_timestamp_raw == "Séance du vendredi 15 mai 2026"
    assert result.source_timestamp_policy == "trading_date_only"
    assert result.pagination_detected is True
    assert "multiple_pages_detected" in result.pagination_warnings
    assert result.normalization_status == "success"
    assert result.status == "partial_success"
    assert db_session.query(Instrument).count() == 1
    assert db_session.query(LatestPrice).count() == 1
    assert db_session.query(PriceBar).count() == 1


def test_bvc_pipeline_diagnostics_failure_prevents_normalization(db_session):
    source = DataSource(code="bvc_prices", name="BVC Prices", source_type="exchange", priority=100)
    db_session.add(source)
    db_session.flush()
    raw_payload, _ = insert_raw_payload_if_new(
        db_session,
        source_id=source.id,
        payload_hash="2" * 64,
        payload_type=BVC_PRICE_PAYLOAD_TYPE,
        payload_text="<html><body><p>no market table</p></body></html>",
        collected_at=datetime(2026, 5, 15, 12, 0, tzinfo=UTC),
        status="collected",
    )
    db_session.commit()

    result = BvcPipelineRunner(db_session).run_raw_payload(raw_payload.id)

    assert result.status == "failed"
    assert result.diagnostics_status == "failed"
    assert result.normalization_status == "skipped"
    assert result.tables_found == 0
    assert result.rows_detected == 0
    assert db_session.query(Instrument).count() == 0
    assert db_session.query(LatestPrice).count() == 0
    assert db_session.query(PriceBar).count() == 0
    db_session.refresh(raw_payload)
    assert raw_payload.status == "collected"


def test_bvc_pipeline_second_fixture_run_is_idempotent(db_session):
    runner = BvcPipelineRunner(db_session)
    first = runner.run_fixture(Path("fixtures/bvc_prices/sample_market_listing.html"))
    second = runner.run_fixture(Path("fixtures/bvc_prices/sample_market_listing.html"))

    assert first.raw_payload_id == second.raw_payload_id
    assert first.instruments_inserted == 2
    assert first.latest_prices_inserted == 2
    assert first.price_bars_inserted == 2
    assert second.instruments_inserted == 0
    assert second.latest_prices_inserted == 0
    assert second.price_bars_inserted == 0
    assert db_session.query(RawPayload).count() == 1
    assert db_session.query(Instrument).count() == 2
    assert db_session.query(LatestPrice).count() == 2
    assert db_session.query(PriceBar).count() == 2
