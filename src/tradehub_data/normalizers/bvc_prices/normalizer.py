import argparse
import json
import logging
from datetime import UTC, datetime
from typing import Iterable
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from tradehub_data.collectors.bvc_prices.constants import BVC_PRICE_JSON_SOURCE_ENDPOINT, BVC_PRICE_PAYLOAD_TYPE
from tradehub_data.core.config import get_settings
from tradehub_data.core.logging import configure_logging
from tradehub_data.db.session import SessionLocal
from tradehub_data.models import RawPayload
from tradehub_data.normalizers.bvc_prices.errors import BvcPriceNormalizationError
from tradehub_data.normalizers.bvc_prices.models import BvcPriceNormalizationResult, BvcPriceNormalizerSummary
from tradehub_data.normalizers.bvc_prices.validation import validate_row
from tradehub_data.parsers.bvc_prices.errors import BvcPriceParseError
from tradehub_data.parsers.bvc_prices.html_parser import parse_bvc_market_listing_html
from tradehub_data.parsers.bvc_prices.json_parser import parse_bvc_market_listing_json
from tradehub_data.parsers.bvc_prices.models import BvcParsedPriceRow, BvcPriceParseResult
from tradehub_data.repositories.exchanges import get_or_create_exchange
from tradehub_data.repositories.instruments import upsert_instrument
from tradehub_data.repositories.normalization_errors import create_normalization_error
from tradehub_data.repositories.prices import upsert_latest_price, upsert_price_bar
from tradehub_data.repositories.raw_payloads import get_raw_payload_by_id, list_eligible_raw_payloads, update_raw_payload_status

logger = logging.getLogger(__name__)
MARKET_TIMEZONE = ZoneInfo("Africa/Casablanca")


class BvcPriceNormalizer:
    def __init__(self, db: Session) -> None:
        self.db = db

    def normalize_payload(self, raw_payload: RawPayload) -> BvcPriceNormalizationResult:
        if not raw_payload.payload_text:
            return self._fail_payload(raw_payload, "missing payload_text")

        try:
            parse_result = self._parse_payload(raw_payload)
        except BvcPriceParseError as exc:
            return self._fail_payload(raw_payload, str(exc), error_type="unexpected_table_shape")

        result = BvcPriceNormalizationResult(raw_payload_id=raw_payload.id, rows_found=len(parse_result.rows))
        exchange, _ = get_or_create_exchange(
            self.db,
            code="BVC",
            name="Bourse de Casablanca",
            country_code="MA",
            currency_code="MAD",
            timezone="Africa/Casablanca",
            website_url="https://www.casablanca-bourse.com",
        )

        for parse_error in parse_result.errors:
            self._record_error(
                raw_payload,
                error_type=parse_error.error_type,
                error_message=parse_error.error_message,
                raw_fragment=parse_error.raw_fragment,
            )
            result.rows_failed += 1
            result.errors_count += 1

        for row in parse_result.rows:
            try:
                quality_status = validate_row(row)
                row_stats = self._normalize_row(
                    raw_payload,
                    row,
                    exchange.id,
                    quality_status,
                    parse_result=parse_result,
                )
                result.rows_normalized += 1
                result.instruments_inserted += row_stats["instrument_inserted"]
                result.instruments_updated += row_stats["instrument_updated"]
                result.latest_prices_inserted += row_stats["latest_price_inserted"]
                result.latest_prices_updated += row_stats["latest_price_updated"]
                result.price_bars_inserted += row_stats["price_bar_inserted"]
                result.price_bars_updated += row_stats["price_bar_updated"]
            except BvcPriceNormalizationError as exc:
                self._record_error(
                    raw_payload,
                    error_type=self._error_type(str(exc)),
                    error_message=str(exc),
                    raw_fragment=row.raw_values,
                )
                result.rows_failed += 1
                result.errors_count += 1

        if result.rows_normalized == 0:
            message = "no valid BVC price rows were normalized"
            update_raw_payload_status(
                self.db,
                raw_payload,
                status="failed",
                error_message=message,
                metadata=self._status_metadata(result, parse_result=parse_result),
            )
            result.status = "failed"
            result.message = message
        elif result.errors_count:
            update_raw_payload_status(
                self.db,
                raw_payload,
                status="normalized",
                metadata=self._status_metadata(result, parse_result=parse_result),
            )
            result.status = "partial_success"
            result.message = "some rows failed validation"
        else:
            update_raw_payload_status(
                self.db,
                raw_payload,
                status="normalized",
                metadata=self._status_metadata(result, parse_result=parse_result),
            )
            result.status = "success"

        self.db.commit()
        return result

    def normalize_eligible(self, *, limit: int = 10) -> BvcPriceNormalizerSummary:
        payloads = list_eligible_raw_payloads(self.db, payload_type=BVC_PRICE_PAYLOAD_TYPE, limit=limit)
        return self._normalize_many(payloads)

    def normalize_by_id(self, raw_payload_id: UUID) -> BvcPriceNormalizerSummary:
        raw_payload = get_raw_payload_by_id(self.db, raw_payload_id)
        if raw_payload is None:
            return BvcPriceNormalizerSummary(
                status="failed",
                payloads_found=0,
                payloads_processed=0,
                payloads_failed=1,
                rows_normalized=0,
                rows_failed=0,
                instruments_inserted=0,
                instruments_updated=0,
                latest_prices_inserted=0,
                latest_prices_updated=0,
                price_bars_inserted=0,
                price_bars_updated=0,
                errors_count=1,
                message=f"raw payload not found: {raw_payload_id}",
            )
        return self._normalize_many([raw_payload])

    def _normalize_many(self, payloads: Iterable[RawPayload]) -> BvcPriceNormalizerSummary:
        results = [self.normalize_payload(payload) for payload in payloads]
        if not results:
            return BvcPriceNormalizerSummary(
                status="skipped",
                payloads_found=0,
                payloads_processed=0,
                payloads_failed=0,
                rows_normalized=0,
                rows_failed=0,
                instruments_inserted=0,
                instruments_updated=0,
                latest_prices_inserted=0,
                latest_prices_updated=0,
                price_bars_inserted=0,
                price_bars_updated=0,
                errors_count=0,
                message="no eligible raw payloads found",
            )

        failed = sum(1 for result in results if result.status == "failed")
        errors = sum(result.errors_count for result in results)
        status = "success" if failed == 0 and errors == 0 else "partial_success" if failed < len(results) else "failed"
        return BvcPriceNormalizerSummary(
            status=status,
            payloads_found=len(results),
            payloads_processed=len(results) - failed,
            payloads_failed=failed,
            rows_normalized=sum(result.rows_normalized for result in results),
            rows_failed=sum(result.rows_failed for result in results),
            instruments_inserted=sum(result.instruments_inserted for result in results),
            instruments_updated=sum(result.instruments_updated for result in results),
            latest_prices_inserted=sum(result.latest_prices_inserted for result in results),
            latest_prices_updated=sum(result.latest_prices_updated for result in results),
            price_bars_inserted=sum(result.price_bars_inserted for result in results),
            price_bars_updated=sum(result.price_bars_updated for result in results),
            errors_count=errors,
        )

    def _normalize_row(
        self,
        raw_payload: RawPayload,
        row: BvcParsedPriceRow,
        exchange_id,
        quality_status: str,
        *,
        parse_result,
    ) -> dict[str, int]:
        symbol = row.source_symbol or row.isin
        if symbol is None:
            raise BvcPriceNormalizationError("missing instrument identifier")
        latest_timestamp, latest_policy = self._latest_price_timestamp(raw_payload, row)
        bar_timestamp, bar_policy = self._price_bar_timestamp(raw_payload, row, parse_result=parse_result)
        source_metadata = self._source_metadata(parse_result)
        instrument, instrument_inserted, instrument_updated = upsert_instrument(
            self.db,
            {
                "exchange_id": exchange_id,
                "symbol": symbol,
                "isin": row.isin,
                "name": row.source_name or symbol,
                "instrument_type": "equity",
                "currency_code": "MAD",
                "source_id": raw_payload.source_id,
                "raw_payload_id": raw_payload.id,
                "is_active": True,
                "last_seen_at": raw_payload.collected_at,
                "metadata_": {
                    "source": "bvc_price_normalizer",
                    "row_index": row.row_index,
                    "raw_values": row.raw_values,
                },
            },
        )
        latest_price, latest_inserted, latest_updated = upsert_latest_price(
            self.db,
            {
                "instrument_id": instrument.id,
                "price": row.last_price,
                "open_price": row.open_price,
                "high_price": row.high_price,
                "low_price": row.low_price,
                "previous_close": row.previous_close,
                "change_value": row.change_value,
                "change_percent": row.change_percent,
                "volume": row.volume,
                "traded_value": row.traded_value,
                "market_cap": row.market_cap,
                "price_timestamp": latest_timestamp,
                "trading_date": row.trading_date,
                "source_id": raw_payload.source_id,
                "raw_payload_id": raw_payload.id,
                "data_quality_status": quality_status,
                "metadata_": {
                    "source": "bvc_price_normalizer",
                    "timestamp_policy": latest_policy,
                    "row_index": row.row_index,
                    "raw_values": row.raw_values,
                    **source_metadata,
                },
            },
        )
        price_bar, bar_inserted, bar_updated = upsert_price_bar(
            self.db,
            {
                "instrument_id": instrument.id,
                "timeframe": "1d",
                "bar_timestamp": bar_timestamp,
                "trading_date": row.trading_date,
                "open_price": row.open_price,
                "high_price": row.high_price,
                "low_price": row.low_price,
                "close_price": row.last_price,
                "volume": row.volume,
                "traded_value": row.traded_value,
                "number_of_trades": row.number_of_trades,
                "source_id": raw_payload.source_id,
                "raw_payload_id": raw_payload.id,
                "is_adjusted": False,
                "data_quality_status": quality_status,
                "metadata_": {
                    "source": "bvc_price_normalizer",
                    "timestamp_policy": bar_policy,
                    "row_index": row.row_index,
                    "raw_values": row.raw_values,
                    **source_metadata,
                },
            },
        )
        return {
            "instrument_inserted": int(instrument_inserted),
            "instrument_updated": int(instrument_updated),
            "latest_price_inserted": int(latest_inserted),
            "latest_price_updated": int(latest_updated),
            "price_bar_inserted": int(bar_inserted),
            "price_bar_updated": int(bar_updated),
        }

    def _fail_payload(
        self,
        raw_payload: RawPayload,
        message: str,
        *,
        error_type: str = "parse_error",
    ) -> BvcPriceNormalizationResult:
        self._record_error(raw_payload, error_type=error_type, error_message=message, raw_fragment=None)
        result = BvcPriceNormalizationResult(
            status="failed",
            raw_payload_id=raw_payload.id,
            rows_found=0,
            rows_normalized=0,
            rows_failed=1,
            errors_count=1,
            message=message,
        )
        update_raw_payload_status(
            self.db,
            raw_payload,
            status="failed",
            error_message=message,
            metadata=self._status_metadata(result),
        )
        self.db.commit()
        return result

    def _parse_payload(self, raw_payload: RawPayload) -> BvcPriceParseResult:
        if _is_json_raw_payload(raw_payload):
            return parse_bvc_market_listing_json(
                raw_payload_id=raw_payload.id,
                payload_text=raw_payload.payload_text or "",
                collected_at=raw_payload.collected_at,
                source_published_at=raw_payload.source_published_at,
            )
        return parse_bvc_market_listing_html(
            raw_payload_id=raw_payload.id,
            payload_text=raw_payload.payload_text or "",
            collected_at=raw_payload.collected_at,
            source_published_at=raw_payload.source_published_at,
        )

    def _record_error(
        self,
        raw_payload: RawPayload,
        *,
        error_type: str,
        error_message: str,
        raw_fragment: dict | None,
    ) -> None:
        create_normalization_error(
            self.db,
            {
                "raw_payload_id": raw_payload.id,
                "ingestion_run_id": raw_payload.ingestion_run_id,
                "source_id": raw_payload.source_id,
                "entity_type": "bvc_price_row",
                "error_type": error_type,
                "error_message": error_message,
                "raw_fragment": raw_fragment,
                "status": "open",
            },
        )

    def _status_metadata(self, result: BvcPriceNormalizationResult, *, parse_result=None) -> dict:
        now = datetime.now(UTC).isoformat()
        metadata = {
            "processed_at": now,
            "normalized_at": now if result.rows_normalized else None,
            "normalization_rows_found": result.rows_found,
            "normalization_rows_normalized": result.rows_normalized,
            "normalization_errors_count": result.errors_count,
        }
        if parse_result is not None:
            metadata.update(self._source_metadata(parse_result))
        return metadata

    def _latest_price_timestamp(self, raw_payload: RawPayload, row: BvcParsedPriceRow) -> tuple[datetime, str]:
        if row.source_timestamp is not None:
            return row.source_timestamp, "source_timestamp"
        return raw_payload.collected_at, "raw_payload_collected_at_no_source_time"

    def _price_bar_timestamp(self, raw_payload: RawPayload, row: BvcParsedPriceRow, *, parse_result) -> tuple[datetime, str]:
        if row.source_timestamp is not None:
            return row.source_timestamp, "source_timestamp"
        if parse_result.source_trading_date is not None and row.trading_date is not None:
            return datetime.combine(row.trading_date, datetime.min.time(), tzinfo=MARKET_TIMEZONE), "trading_date_start_of_day"
        return raw_payload.collected_at, "raw_payload_collected_at_no_source_date"

    def _source_metadata(self, parse_result) -> dict:
        pagination = parse_result.pagination_metadata or {}
        return {
            "source_trading_date": parse_result.source_trading_date.isoformat() if parse_result.source_trading_date else None,
            "source_timestamp": parse_result.source_timestamp.isoformat() if parse_result.source_timestamp else None,
            "source_timestamp_raw": parse_result.source_timestamp_raw,
            "source_timestamp_policy": parse_result.source_timestamp_policy,
            "pagination_detected": pagination.get("pagination_detected", False),
            "pagination_warning": pagination.get("pagination_warnings", []),
            "pagination_controls": pagination.get("pagination_controls", {}),
        }

    def _error_type(self, message: str) -> str:
        if "identifier" in message:
            return "missing_instrument_identifier"
        if "price" in message:
            return "missing_price"
        if "negative" in message:
            return "invalid_number"
        return "validation_error"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Normalize stored BVC price raw payloads.")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--raw-payload-id")
    return parser


def _is_json_raw_payload(raw_payload: RawPayload) -> bool:
    content_type = (raw_payload.content_type or "").lower()
    if "json" in content_type:
        return True
    if raw_payload.source_endpoint == BVC_PRICE_JSON_SOURCE_ENDPOINT:
        return True
    if (raw_payload.metadata_ or {}).get("collection_mode") == "live_json":
        return True
    return bool(raw_payload.payload_text and raw_payload.payload_text.lstrip().startswith("{"))


def main() -> None:
    configure_logging(get_settings().log_level)
    args = build_parser().parse_args()
    with SessionLocal() as db:
        normalizer = BvcPriceNormalizer(db)
        if args.raw_payload_id:
            summary = normalizer.normalize_by_id(UUID(args.raw_payload_id))
        else:
            summary = normalizer.normalize_eligible(limit=args.limit)
    print(json.dumps(summary.model_dump(mode="json"), sort_keys=True))
    if summary.status == "failed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
