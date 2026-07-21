import json
from datetime import UTC, datetime
from pathlib import Path

from tradehub_data.collectors.bvc_prices.constants import BVC_PRICE_JSON_SOURCE_ENDPOINT, BVC_PRICE_PAYLOAD_TYPE
from tradehub_data.collectors.bvc_prices.models import BvcPriceCollectorResult
from tradehub_data.models import (
    CollectionGroup,
    CollectionGroupPage,
    CollectionOccurrence,
    CollectionPageSelection,
    DataSource,
    Instrument,
    LatestPrice,
    PriceBar,
    RawPayload,
)
from tradehub_data.pipelines.bvc_prices import runner as runner_module
from tradehub_data.pipelines.bvc_prices.runner import BvcPipelineRunner
from tradehub_data.repositories.raw_payloads import insert_raw_payload_if_new
from tradehub_data.repositories.sources import create_ingestion_run


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


def test_bvc_pipeline_runs_multi_page_real_fixture_group(db_session):
    runner = BvcPipelineRunner(db_session)

    result = runner.run_fixture_group(
        [
            Path("fixtures/bvc_prices/real/bvc_market_listing_20260518_page_1.html"),
            Path("fixtures/bvc_prices/real/bvc_market_listing_20260518_page_2.html"),
        ]
    )

    assert result.mode == "fixture_group"
    assert result.status == "success"
    assert result.pages_found == 2
    assert result.pages_processed == 2
    assert result.expected_pages == 2
    assert result.missing_pages == []
    assert result.pagination_complete is True
    assert result.source_trading_date.isoformat() == "2026-05-18"
    assert result.source_timestamp is None
    assert result.total_rows_detected == 80
    assert result.total_rows_normalized == 80
    assert result.duplicate_symbols_count == 0
    assert result.duplicate_symbols == []
    assert [page.page_number for page in result.per_page_summaries] == [1, 2]
    assert [page.rows_detected for page in result.per_page_summaries] == [50, 30]

    raw_payloads = db_session.query(RawPayload).all()
    assert len(raw_payloads) == 2
    assert {payload.content_evidence_kind for payload in raw_payloads} == {
        "exact_entity_bytes"
    }
    assert all(payload.metadata_ is None for payload in raw_payloads)
    assert all(payload.status == "collected" for payload in raw_payloads)

    # Each fixture load owns immutable acquisition evidence.  The runner's
    # legacy multi-page aggregation remains a computed compatibility result;
    # it no longer mutates raw-content rows with page/group processing state.
    groups = db_session.query(CollectionGroup).all()
    pages = db_session.query(CollectionGroupPage).all()
    occurrences = db_session.query(CollectionOccurrence).all()
    selections = db_session.query(CollectionPageSelection).all()
    assert len(groups) == len(pages) == len(occurrences) == len(selections) == 2
    assert {group.collection_mode for group in groups} == {"manual_fixture"}
    assert {group.collection_status for group in groups} == {"success"}
    assert {page.page_role for page in pages} == {"data"}
    assert {occurrence.outcome for occurrence in occurrences} == {"fixture_loaded"}


def test_bvc_pipeline_multi_page_missing_page_is_partial_success(db_session):
    result = BvcPipelineRunner(db_session).run_fixture_group(
        [Path("fixtures/bvc_prices/real/bvc_market_listing_20260518_page_1.html")]
    )

    assert result.status == "partial_success"
    assert result.pages_found == 1
    assert result.pages_processed == 1
    assert result.expected_pages == 2
    assert result.missing_pages == [2]
    assert result.pagination_complete is False
    assert result.total_rows_detected == 50
    assert result.total_rows_normalized == 50


def test_bvc_pipeline_multi_page_detects_duplicate_symbols(db_session, tmp_path):
    page_1 = tmp_path / "bvc_market_listing_20260518_page_1.html"
    page_2 = tmp_path / "bvc_market_listing_20260518_page_2.html"
    html = """
    <html><body>
      <p>Séance du lundi 18 mai 2026</p>
      <table>
        <thead><tr><th>Instrument</th><th>Symbole</th><th>Dernier cours</th><th>Volume</th></tr></thead>
        <tbody><tr><td>DUPLICATE SA</td><td>DUP</td><td>12,34</td><td>100</td></tr></tbody>
      </table>
      <nav><button>1</button><button>2</button></nav>
    </body></html>
    """
    page_1.write_text(html, encoding="utf-8")
    page_2.write_text(html.replace("12,34", "13,34"), encoding="utf-8")

    result = BvcPipelineRunner(db_session).run_fixture_group([page_1, page_2])

    assert result.status == "partial_success"
    assert result.duplicate_symbols_count == 1
    assert result.duplicate_symbols == ["DUP"]
    assert result.pagination_complete is True
    assert db_session.query(Instrument).count() == 1
    assert db_session.query(LatestPrice).count() == 1
    assert db_session.query(PriceBar).count() == 1


def test_bvc_pipeline_multi_page_second_run_is_idempotent(db_session):
    runner = BvcPipelineRunner(db_session)
    paths = [
        Path("fixtures/bvc_prices/real/bvc_market_listing_20260518_page_1.html"),
        Path("fixtures/bvc_prices/real/bvc_market_listing_20260518_page_2.html"),
    ]

    first = runner.run_fixture_group(paths)
    second = runner.run_fixture_group(paths)

    assert first.status == "success"
    assert second.status == "success"
    assert first.total_rows_normalized == 80
    assert second.total_rows_normalized == 80
    assert db_session.query(Instrument).count() == 80
    assert db_session.query(LatestPrice).count() == 80
    assert db_session.query(PriceBar).count() == 80
    assert sum(page.price_bars_inserted for page in second.per_page_summaries) == 0
    assert sum(page.latest_prices_inserted for page in second.per_page_summaries) == 0
    assert sum(page.instruments_inserted for page in second.per_page_summaries) == 0


def test_bvc_pipeline_collect_live_runs_json_group(monkeypatch, db_session):
    monkeypatch.setattr(runner_module.BvcPriceCollectorConfig, "from_env", classmethod(lambda cls: object()))

    class FakeCollector:
        def __init__(self, db, config):
            self.db = db

        async def run_json_pages(self):
            source = DataSource(code="bvc_prices", name="BVC Prices", source_type="exchange", priority=100)
            self.db.add(source)
            self.db.flush()
            run = create_ingestion_run(
                self.db,
                source_id=source.id,
                collector_name="bvc_price_collector",
                run_type="manual",
                started_at=datetime(2026, 5, 18, 12, 0, tzinfo=UTC),
                metadata={"collection_mode": "live_json"},
            )
            page_ids = []
            for page_number, symbols in [(1, ["LIV1", "LIV2"]), (2, ["LIV3"])]:
                raw_payload, _ = insert_raw_payload_if_new(
                    self.db,
                    source_id=source.id,
                    ingestion_run_id=run.id,
                    payload_hash=f"{page_number}" * 64,
                    payload_type=BVC_PRICE_PAYLOAD_TYPE,
                    source_endpoint=BVC_PRICE_JSON_SOURCE_ENDPOINT,
                    content_type="application/json",
                    payload_text=_json_payload(symbols),
                    collected_at=datetime(2026, 5, 18, 12, 0, tzinfo=UTC),
                    status="collected",
                    metadata={
                        "collection_mode": "live_json",
                        "page_number": page_number,
                        "page_offset": (page_number - 1) * 50,
                        "page_limit": 50,
                        "page_size": len(symbols),
                        "pagination_group_id": "bvc_price_snapshot:live_json:test",
                    },
                )
                page_ids.append(str(raw_payload.id))
            run.metadata_ = {**(run.metadata_ or {}), "raw_payload_ids": page_ids, "pagination_group_id": "bvc_price_snapshot:live_json:test"}
            self.db.commit()
            return BvcPriceCollectorResult(
                status="success",
                ingestion_run_id=run.id,
                source_urls_count=2,
                payloads_stored=2,
                payloads_skipped=0,
                errors_count=0,
            )

    monkeypatch.setattr(runner_module, "BvcPriceCollector", FakeCollector)

    result = runner_module.asyncio.run(BvcPipelineRunner(db_session).run_collect_live())

    assert result.mode == "collect_live"
    assert result.status == "success"
    assert result.pagination_group_id == "bvc_price_snapshot:live_json:test"
    assert result.pages_found == 2
    assert result.pages_processed == 2
    assert result.total_rows_detected == 3
    assert result.total_rows_normalized == 3
    assert result.duplicate_symbols_count == 0
    assert db_session.query(Instrument).count() == 3
    assert db_session.query(LatestPrice).count() == 3
    assert db_session.query(PriceBar).count() == 3


def test_bvc_pipeline_collect_live_detects_duplicate_json_symbols(monkeypatch, db_session):
    monkeypatch.setattr(runner_module.BvcPriceCollectorConfig, "from_env", classmethod(lambda cls: object()))

    class FakeCollector:
        def __init__(self, db, config):
            self.db = db

        async def run_json_pages(self):
            source = DataSource(code="bvc_prices", name="BVC Prices", source_type="exchange", priority=100)
            self.db.add(source)
            self.db.flush()
            run = create_ingestion_run(
                self.db,
                source_id=source.id,
                collector_name="bvc_price_collector",
                run_type="manual",
                started_at=datetime(2026, 5, 18, 12, 0, tzinfo=UTC),
            )
            page_ids = []
            for page_number in [1, 2]:
                raw_payload, _ = insert_raw_payload_if_new(
                    self.db,
                    source_id=source.id,
                    ingestion_run_id=run.id,
                    payload_hash=f"dup{page_number}".ljust(64, "0"),
                    payload_type=BVC_PRICE_PAYLOAD_TYPE,
                    source_endpoint=BVC_PRICE_JSON_SOURCE_ENDPOINT,
                    content_type="application/json",
                    payload_text=_json_payload(["DUP"]),
                    collected_at=datetime(2026, 5, 18, 12, 0, tzinfo=UTC),
                    status="collected",
                    metadata={
                        "collection_mode": "live_json",
                        "page_number": page_number,
                        "page_offset": (page_number - 1) * 50,
                        "page_limit": 50,
                        "page_size": 1,
                        "pagination_group_id": "bvc_price_snapshot:live_json:dup",
                    },
                )
                page_ids.append(str(raw_payload.id))
            run.metadata_ = {"raw_payload_ids": page_ids, "pagination_group_id": "bvc_price_snapshot:live_json:dup"}
            self.db.commit()
            return BvcPriceCollectorResult(
                status="success",
                ingestion_run_id=run.id,
                source_urls_count=2,
                payloads_stored=2,
                payloads_skipped=0,
                errors_count=0,
            )

    monkeypatch.setattr(runner_module, "BvcPriceCollector", FakeCollector)

    result = runner_module.asyncio.run(BvcPipelineRunner(db_session).run_collect_live())

    assert result.status == "partial_success"
    assert result.duplicate_symbols_count == 1
    assert result.duplicate_symbols == ["DUP"]


def _json_payload(symbols: list[str]) -> str:
    return json.dumps(
        {
            "data": {
                "data": [
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
                    for symbol in symbols
                ]
            }
        }
    )
