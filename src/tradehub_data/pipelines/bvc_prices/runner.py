import argparse
import asyncio
import json
from pathlib import Path
from typing import Any
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy.orm import Session

from tradehub_data.collectors.bvc_prices.collector import BvcPriceCollector
from tradehub_data.collectors.bvc_prices.config import BvcPriceCollectorConfig
from tradehub_data.collectors.bvc_prices.fixtures import store_local_fixture
from tradehub_data.core.config import get_settings
from tradehub_data.core.logging import configure_logging
from tradehub_data.db.session import SessionLocal
from tradehub_data.models import IngestionRun, RawPayload
from tradehub_data.normalizers.bvc_prices.normalizer import BvcPriceNormalizer
from tradehub_data.parsers.bvc_prices.diagnostics import diagnose_bvc_market_listing_html
from tradehub_data.repositories.raw_payloads import get_raw_payload_by_id


class BvcPipelineResult(BaseModel):
    status: str
    mode: str
    raw_payload_id: UUID | None = None
    source_id: UUID | None = None
    payload_hash: str | None = None
    diagnostics_status: str | None = None
    tables_found: int = 0
    rows_detected: int = 0
    parseable_rows_count: int = 0
    row_parse_errors_count: int = 0
    source_trading_date: Any = None
    source_timestamp: Any = None
    source_timestamp_raw: str | None = None
    source_timestamp_policy: str | None = None
    pagination_detected: bool = False
    pagination_warnings: list[str] = []
    normalization_status: str = "skipped"
    rows_normalized: int = 0
    rows_failed: int = 0
    instruments_inserted: int = 0
    instruments_updated: int = 0
    latest_prices_inserted: int = 0
    latest_prices_updated: int = 0
    price_bars_inserted: int = 0
    price_bars_updated: int = 0
    errors_count: int = 0
    final_raw_payload_status: str | None = None
    message: str | None = None


class BvcPipelineMultiResult(BaseModel):
    status: str
    mode: str
    payloads_found: int
    payloads_processed: int
    payloads_failed: int
    errors_count: int
    results: list[BvcPipelineResult]
    message: str | None = None


class BvcPipelineRunner:
    def __init__(self, db: Session) -> None:
        self.db = db

    def run_raw_payload(self, raw_payload_id: UUID) -> BvcPipelineResult:
        raw_payload = get_raw_payload_by_id(self.db, raw_payload_id)
        if raw_payload is None:
            return BvcPipelineResult(
                status="failed",
                mode="raw_payload_id",
                raw_payload_id=raw_payload_id,
                message=f"raw payload not found: {raw_payload_id}",
            )
        return self._run_payload(raw_payload, mode="raw_payload_id")

    def run_fixture(self, fixture_path: Path) -> BvcPipelineResult:
        fixture_result = store_local_fixture(self.db, file_path=fixture_path)
        raw_payload_id = UUID(fixture_result["raw_payload_id"])
        raw_payload = get_raw_payload_by_id(self.db, raw_payload_id)
        if raw_payload is None:
            return BvcPipelineResult(
                status="failed",
                mode="fixture_path",
                raw_payload_id=raw_payload_id,
                message=f"stored fixture raw payload not found: {raw_payload_id}",
            )
        return self._run_payload(raw_payload, mode="fixture_path")

    async def run_collect_live(self) -> BvcPipelineMultiResult:
        collector = BvcPriceCollector(db=self.db, config=BvcPriceCollectorConfig.from_env())
        collector_result = await collector.run()
        if collector_result.ingestion_run_id is None:
            return BvcPipelineMultiResult(
                status=collector_result.status,
                mode="collect_live",
                payloads_found=0,
                payloads_processed=0,
                payloads_failed=collector_result.errors_count,
                errors_count=collector_result.errors_count,
                results=[],
                message=collector_result.message,
            )

        run = self.db.get(IngestionRun, collector_result.ingestion_run_id)
        raw_payload_ids = ((run.metadata_ or {}).get("raw_payload_ids") if run else None) or []
        results = [self.run_raw_payload(UUID(raw_payload_id)) for raw_payload_id in raw_payload_ids]
        failed = sum(1 for result in results if result.status == "failed")
        status = "success" if failed == 0 and collector_result.status == "success" else "partial_success" if results else collector_result.status
        return BvcPipelineMultiResult(
            status=status,
            mode="collect_live",
            payloads_found=len(results),
            payloads_processed=len(results) - failed,
            payloads_failed=failed,
            errors_count=collector_result.errors_count + sum(result.errors_count for result in results),
            results=results,
            message=collector_result.message,
        )

    def _run_payload(self, raw_payload: RawPayload, *, mode: str) -> BvcPipelineResult:
        base = self._base_result(raw_payload, mode=mode)
        if not raw_payload.payload_text:
            base.status = "failed"
            base.diagnostics_status = "failed"
            base.message = f"raw payload has no payload_text: {raw_payload.id}"
            return base

        diagnostics = diagnose_bvc_market_listing_html(
            raw_payload_id=raw_payload.id,
            payload_text=raw_payload.payload_text,
            payload_hash=raw_payload.payload_hash,
            collected_at=raw_payload.collected_at,
            source_published_at=raw_payload.source_published_at,
        )
        base.diagnostics_status = diagnostics.status
        base.tables_found = diagnostics.tables_found
        base.rows_detected = diagnostics.rows_detected
        base.parseable_rows_count = diagnostics.parseable_rows_count
        base.row_parse_errors_count = diagnostics.row_parse_errors_count
        base.source_trading_date = diagnostics.source_trading_date
        base.source_timestamp = diagnostics.source_timestamp
        base.source_timestamp_raw = diagnostics.source_timestamp_raw
        base.source_timestamp_policy = diagnostics.source_timestamp_policy
        base.pagination_detected = diagnostics.pagination_detected
        base.pagination_warnings = diagnostics.pagination_warnings

        if diagnostics.status != "success":
            base.status = "failed"
            base.normalization_status = "skipped"
            base.errors_count = diagnostics.row_parse_errors_count
            base.message = f"diagnostics did not pass: {diagnostics.status}"
            return base

        normalization = BvcPriceNormalizer(self.db).normalize_by_id(raw_payload.id)
        self.db.expire(raw_payload)
        refreshed_payload = get_raw_payload_by_id(self.db, raw_payload.id)
        base.normalization_status = normalization.status
        base.rows_normalized = normalization.rows_normalized
        base.rows_failed = normalization.rows_failed
        base.instruments_inserted = normalization.instruments_inserted
        base.instruments_updated = normalization.instruments_updated
        base.latest_prices_inserted = normalization.latest_prices_inserted
        base.latest_prices_updated = normalization.latest_prices_updated
        base.price_bars_inserted = normalization.price_bars_inserted
        base.price_bars_updated = normalization.price_bars_updated
        base.errors_count = normalization.errors_count
        base.final_raw_payload_status = refreshed_payload.status if refreshed_payload else None
        if normalization.status == "success" and diagnostics.pagination_warnings:
            base.status = "partial_success"
            base.message = "; ".join(diagnostics.pagination_warnings)
        else:
            base.status = normalization.status
            base.message = normalization.message
        return base

    def _base_result(self, raw_payload: RawPayload, *, mode: str) -> BvcPipelineResult:
        return BvcPipelineResult(
            status="skipped",
            mode=mode,
            raw_payload_id=raw_payload.id,
            source_id=raw_payload.source_id,
            payload_hash=raw_payload.payload_hash,
            final_raw_payload_status=raw_payload.status,
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the manual BVC price pipeline.")
    parser.add_argument("--raw-payload-id")
    parser.add_argument("--fixture-path")
    parser.add_argument("--collect-live", action="store_true")
    return parser


def _selected_modes(args: argparse.Namespace) -> list[str]:
    return [
        mode
        for mode, selected in (
            ("raw_payload_id", bool(args.raw_payload_id)),
            ("fixture_path", bool(args.fixture_path)),
            ("collect_live", bool(args.collect_live)),
        )
        if selected
    ]


def main() -> None:
    configure_logging(get_settings().log_level)
    args = build_parser().parse_args()
    selected_modes = _selected_modes(args)
    if len(selected_modes) != 1:
        raise SystemExit("provide exactly one input mode: --raw-payload-id, --fixture-path, or --collect-live")

    with SessionLocal() as db:
        runner = BvcPipelineRunner(db)
        if args.raw_payload_id:
            result: BvcPipelineResult | BvcPipelineMultiResult = runner.run_raw_payload(UUID(args.raw_payload_id))
        elif args.fixture_path:
            result = runner.run_fixture(Path(args.fixture_path))
        else:
            result = asyncio.run(runner.run_collect_live())

    print(json.dumps(result.model_dump(mode="json"), sort_keys=True))
    if result.status == "failed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
