import argparse
import asyncio
import json
import re
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

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
from tradehub_data.parsers.bvc_prices.html_parser import parse_bvc_market_listing_html
from tradehub_data.parsers.bvc_prices.json_parser import parse_bvc_market_listing_json
from tradehub_data.parsers.bvc_prices.diagnostics import diagnose_bvc_price_payload
from tradehub_data.repositories.raw_payloads import get_raw_payload_by_id, update_raw_payload_metadata


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


class BvcPipelinePageResult(BvcPipelineResult):
    page_number: int | None = None


class BvcPipelineMultiResult(BaseModel):
    status: str
    mode: str
    payloads_found: int
    payloads_processed: int
    payloads_failed: int
    errors_count: int
    results: list[BvcPipelineResult]
    message: str | None = None


class BvcPipelineGroupResult(BaseModel):
    status: str
    mode: str
    pagination_group_id: str
    pages_found: int
    pages_processed: int
    expected_pages: int | None = None
    missing_pages: list[int] = []
    pagination_complete: bool
    source_trading_date: Any = None
    source_timestamp: Any = None
    total_rows_detected: int = 0
    total_rows_normalized: int = 0
    duplicate_symbols_count: int = 0
    duplicate_symbols: list[str] = []
    errors_count: int = 0
    per_page_summaries: list[BvcPipelinePageResult] = []
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

    def run_fixture_group(self, fixture_paths: list[Path]) -> BvcPipelineGroupResult:
        stored_pages: list[tuple[int, RawPayload]] = []
        for index, fixture_path in enumerate(fixture_paths, start=1):
            page_number = _page_number_from_path(fixture_path) or index
            fixture_result = store_local_fixture(self.db, file_path=fixture_path)
            raw_payload = get_raw_payload_by_id(self.db, UUID(fixture_result["raw_payload_id"]))
            if raw_payload is None:
                raise RuntimeError(f"stored fixture raw payload not found: {fixture_result['raw_payload_id']}")
            stored_pages.append((page_number, raw_payload))

        return self._run_payload_group(stored_pages, mode="fixture_group")

    def run_fixture_dir(self, fixture_dir: Path) -> BvcPipelineGroupResult:
        fixture_paths = sorted(path for path in fixture_dir.iterdir() if path.is_file() and path.suffix.lower() in {".html", ".htm"})
        if not fixture_paths:
            return BvcPipelineGroupResult(
                status="failed",
                mode="fixture_group",
                pagination_group_id=str(uuid4()),
                pages_found=0,
                pages_processed=0,
                pagination_complete=False,
                message=f"no HTML fixtures found in directory: {fixture_dir}",
            )
        return self.run_fixture_group(fixture_paths)

    async def run_collect_live(self) -> BvcPipelineGroupResult:
        collector = BvcPriceCollector(db=self.db, config=BvcPriceCollectorConfig.from_env())
        collector_result = await collector.run_json_pages()
        if collector_result.ingestion_run_id is None:
            return BvcPipelineGroupResult(
                status=collector_result.status,
                mode="collect_live",
                pagination_group_id=str(uuid4()),
                pages_found=0,
                pages_processed=0,
                pagination_complete=False,
                errors_count=collector_result.errors_count,
                message=collector_result.message,
            )

        run = self.db.get(IngestionRun, collector_result.ingestion_run_id)
        raw_payload_ids = ((run.metadata_ or {}).get("raw_payload_ids") if run else None) or []
        pages: list[tuple[int, RawPayload]] = []
        for index, raw_payload_id in enumerate(raw_payload_ids, start=1):
            raw_payload = get_raw_payload_by_id(self.db, UUID(raw_payload_id))
            if raw_payload is not None:
                pages.append(((raw_payload.metadata_ or {}).get("page_number") or index, raw_payload))
        if not pages:
            return BvcPipelineGroupResult(
                status=collector_result.status,
                mode="collect_live",
                pagination_group_id=((run.metadata_ or {}).get("pagination_group_id") if run else None) or str(uuid4()),
                pages_found=0,
                pages_processed=0,
                pagination_complete=False,
                errors_count=collector_result.errors_count,
                message=collector_result.message,
            )

        result = self._run_payload_group(pages, mode="collect_live")
        result.errors_count += collector_result.errors_count
        if collector_result.status != "success" and result.status == "success":
            result.status = "partial_success"
            result.message = collector_result.message
        return result

    def _run_payload(self, raw_payload: RawPayload, *, mode: str) -> BvcPipelineResult:
        base = self._base_result(raw_payload, mode=mode)
        if not raw_payload.payload_text:
            base.status = "failed"
            base.diagnostics_status = "failed"
            base.message = f"raw payload has no payload_text: {raw_payload.id}"
            return base

        diagnostics = diagnose_bvc_price_payload(
            raw_payload_id=raw_payload.id,
            payload_text=raw_payload.payload_text,
            payload_hash=raw_payload.payload_hash,
            collected_at=raw_payload.collected_at,
            source_published_at=raw_payload.source_published_at,
            content_type=raw_payload.content_type,
            source_endpoint=raw_payload.source_endpoint,
            metadata=raw_payload.metadata_,
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

    def _run_payload_group(self, pages: list[tuple[int, RawPayload]], *, mode: str) -> BvcPipelineGroupResult:
        diagnostics_by_payload: dict[UUID, Any] = {}
        symbols_by_page: dict[int, list[str]] = {}
        per_page: list[BvcPipelinePageResult] = []

        for page_number, raw_payload in pages:
            diagnostics = self._diagnose_payload(raw_payload)
            diagnostics_by_payload[raw_payload.id] = diagnostics
            symbols_by_page[page_number] = self._symbols_for_payload(raw_payload) if diagnostics.status == "success" else []

        expected_pages = _expected_pages(pages, diagnostics_by_payload)
        existing_group_id = _common_value([(payload.metadata_ or {}).get("pagination_group_id") for _, payload in pages])
        pagination_group_id = existing_group_id or _pagination_group_id(diagnostics_by_payload)

        for page_number, raw_payload in pages:
            diagnostics = diagnostics_by_payload[raw_payload.id]
            page_metadata = {
                "page_number": page_number,
                "page_size": diagnostics.rows_detected,
                "pagination_group_id": pagination_group_id,
                "pagination_total_pages": expected_pages,
                "page_offset": (raw_payload.metadata_ or {}).get("page_offset"),
                "page_limit": (raw_payload.metadata_ or {}).get("page_limit"),
                "source_trading_date": diagnostics.source_trading_date.isoformat() if diagnostics.source_trading_date else None,
                "source_timestamp": diagnostics.source_timestamp.isoformat() if diagnostics.source_timestamp else None,
                "source_timestamp_policy": diagnostics.source_timestamp_policy,
            }
            update_raw_payload_metadata(self.db, raw_payload, page_metadata)
        self.db.commit()

        for page_number, raw_payload in pages:
            diagnostics = diagnostics_by_payload[raw_payload.id]
            if diagnostics.status == "success":
                page_result = self._run_payload(raw_payload, mode="fixture_group_page")
            else:
                page_result = self._base_result(raw_payload, mode="fixture_group_page")
                page_result.status = "failed"
                page_result.diagnostics_status = diagnostics.status
                page_result.rows_detected = diagnostics.rows_detected
                page_result.parseable_rows_count = diagnostics.parseable_rows_count
                page_result.errors_count = diagnostics.row_parse_errors_count
                page_result.message = f"diagnostics did not pass: {diagnostics.status}"
            per_page.append(BvcPipelinePageResult(**page_result.model_dump(), page_number=page_number))

        found_page_numbers = {page_number for page_number, _ in pages}
        missing_pages = [page for page in range(1, expected_pages + 1) if page not in found_page_numbers] if expected_pages else []
        duplicate_symbols = _duplicate_symbols(symbols_by_page)
        pages_failed = sum(1 for result in per_page if result.diagnostics_status != "success" or result.normalization_status == "failed")
        pagination_complete = not missing_pages
        errors_count = sum(result.errors_count for result in per_page)
        status = _group_status(
            pages_failed=pages_failed,
            missing_pages=missing_pages,
            duplicate_symbols=duplicate_symbols,
        )
        messages = []
        if missing_pages:
            messages.append(f"missing pages: {missing_pages}")
        if duplicate_symbols:
            messages.append(f"duplicate symbols: {duplicate_symbols}")

        return BvcPipelineGroupResult(
            status=status,
            mode=mode,
            pagination_group_id=pagination_group_id,
            pages_found=len(pages),
            pages_processed=len([result for result in per_page if result.diagnostics_status == "success"]),
            expected_pages=expected_pages,
            missing_pages=missing_pages,
            pagination_complete=pagination_complete,
            source_trading_date=_common_value([diagnostics.source_trading_date for diagnostics in diagnostics_by_payload.values()]),
            source_timestamp=_common_value([diagnostics.source_timestamp for diagnostics in diagnostics_by_payload.values()]),
            total_rows_detected=sum(result.rows_detected for result in per_page),
            total_rows_normalized=sum(result.rows_normalized for result in per_page),
            duplicate_symbols_count=len(duplicate_symbols),
            duplicate_symbols=duplicate_symbols,
            errors_count=errors_count,
            per_page_summaries=sorted(per_page, key=lambda result: result.page_number or 0),
            message="; ".join(messages) or None,
        )

    def _base_result(self, raw_payload: RawPayload, *, mode: str) -> BvcPipelineResult:
        return BvcPipelineResult(
            status="skipped",
            mode=mode,
            raw_payload_id=raw_payload.id,
            source_id=raw_payload.source_id,
            payload_hash=raw_payload.payload_hash,
            final_raw_payload_status=raw_payload.status,
        )

    def _diagnose_payload(self, raw_payload: RawPayload):
        return diagnose_bvc_price_payload(
            raw_payload_id=raw_payload.id,
            payload_text=raw_payload.payload_text or "",
            payload_hash=raw_payload.payload_hash,
            collected_at=raw_payload.collected_at,
            source_published_at=raw_payload.source_published_at,
            content_type=raw_payload.content_type,
            source_endpoint=raw_payload.source_endpoint,
            metadata=raw_payload.metadata_,
        )

    def _symbols_for_payload(self, raw_payload: RawPayload) -> list[str]:
        if not raw_payload.payload_text:
            return []
        if _is_json_raw_payload(raw_payload):
            parse_result = parse_bvc_market_listing_json(
                raw_payload_id=raw_payload.id,
                payload_text=raw_payload.payload_text,
                collected_at=raw_payload.collected_at,
                source_published_at=raw_payload.source_published_at,
            )
        else:
            parse_result = parse_bvc_market_listing_html(
                raw_payload_id=raw_payload.id,
                payload_text=raw_payload.payload_text,
                collected_at=raw_payload.collected_at,
                source_published_at=raw_payload.source_published_at,
            )
        return [symbol for row in parse_result.rows if (symbol := (row.source_symbol or row.isin))]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the manual BVC price pipeline.")
    parser.add_argument("--raw-payload-id")
    parser.add_argument("--fixture-path", action="append")
    parser.add_argument("--fixture-dir")
    parser.add_argument("--collect-live", action="store_true")
    return parser


def _selected_modes(args: argparse.Namespace) -> list[str]:
    return [
        mode
        for mode, selected in (
            ("raw_payload_id", bool(args.raw_payload_id)),
            ("fixture_path", bool(args.fixture_path)),
            ("fixture_dir", bool(args.fixture_dir)),
            ("collect_live", bool(args.collect_live)),
        )
        if selected
    ]


def _page_number_from_path(path: Path) -> int | None:
    match = re.search(r"(?:^|[_-])page[_-]?(\d+)(?:\D|$)", path.stem, re.IGNORECASE)
    return int(match.group(1)) if match else None


def _expected_pages(pages: list[tuple[int, RawPayload]], diagnostics_by_payload: dict[UUID, Any]) -> int:
    explicit_pages = [page_number for page_number, _ in pages]
    visible_pages: list[int] = []
    for diagnostics in diagnostics_by_payload.values():
        visible_pages.extend(diagnostics.pagination_controls.get("visible_page_numbers") or [])
    return max([*explicit_pages, *visible_pages], default=len(pages))


def _pagination_group_id(diagnostics_by_payload: dict[UUID, Any]) -> str:
    trading_date = _common_value([diagnostics.source_trading_date for diagnostics in diagnostics_by_payload.values()])
    if trading_date is not None:
        return f"bvc_price_snapshot:{trading_date.isoformat()}:manual"
    return f"bvc_price_snapshot:unknown:{uuid4()}"


def _duplicate_symbols(symbols_by_page: dict[int, list[str]]) -> list[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for page_number in sorted(symbols_by_page):
        for symbol in symbols_by_page[page_number]:
            if symbol in seen:
                duplicates.add(symbol)
            seen.add(symbol)
    return sorted(duplicates)


def _common_value(values: list[Any]) -> Any:
    present = [value for value in values if value is not None]
    if not present:
        return None
    first = present[0]
    return first if all(value == first for value in present) else None


def _group_status(*, pages_failed: int, missing_pages: list[int], duplicate_symbols: list[str]) -> str:
    if pages_failed:
        return "failed"
    if missing_pages or duplicate_symbols:
        return "partial_success"
    return "success"


def _is_json_raw_payload(raw_payload: RawPayload) -> bool:
    content_type = (raw_payload.content_type or "").lower()
    if "json" in content_type:
        return True
    if raw_payload.source_endpoint and "json" in raw_payload.source_endpoint:
        return True
    if (raw_payload.metadata_ or {}).get("collection_mode") == "live_json":
        return True
    return bool(raw_payload.payload_text and raw_payload.payload_text.lstrip().startswith("{"))


def main() -> None:
    configure_logging(get_settings().log_level)
    args = build_parser().parse_args()
    selected_modes = _selected_modes(args)
    if len(selected_modes) != 1:
        raise SystemExit("provide exactly one input mode: --raw-payload-id, --fixture-path, or --collect-live")

    with SessionLocal() as db:
        runner = BvcPipelineRunner(db)
        if args.raw_payload_id:
            result: BvcPipelineResult | BvcPipelineMultiResult | BvcPipelineGroupResult = runner.run_raw_payload(UUID(args.raw_payload_id))
        elif args.fixture_path:
            fixture_paths = [Path(path) for path in args.fixture_path]
            result = runner.run_fixture(fixture_paths[0]) if len(fixture_paths) == 1 else runner.run_fixture_group(fixture_paths)
        elif args.fixture_dir:
            result = runner.run_fixture_dir(Path(args.fixture_dir))
        else:
            result = asyncio.run(runner.run_collect_live())

    print(json.dumps(result.model_dump(mode="json"), sort_keys=True))
    if result.status == "failed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
