import json
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from tradehub_data.parsers.bvc_prices.errors import BvcPriceParseError
from tradehub_data.parsers.bvc_prices.models import BvcParsedPriceRow, BvcPriceParseResult, BvcRowParseError
from tradehub_data.parsers.bvc_prices.number_parsing import clean_text, parse_decimal, parse_int

JSON_FIELD_ALIASES = {
    "source_symbol": ("symbol", "ticker", "instrumentCode", "code"),
    "source_name": ("instrument", "instrumentName", "label", "libelle", "libelleFR", "name", "shortName"),
    "isin": ("isin", "isinCode"),
    "last_price": ("lastTradedPrice", "coursCourant", "closingPrice"),
    "open_price": ("openingPrice",),
    "high_price": ("highPrice",),
    "low_price": ("lowPrice",),
    "previous_close": ("staticReferencePrice", "previousClose"),
    "change_value": ("difference", "changeValue"),
    "change_percent": ("varVeille", "changePercent"),
    "volume": ("cumulTitresEchanges",),
    "traded_value": ("cumulVolumeEchange",),
    "market_cap": ("capitalisation",),
    "number_of_trades": ("totalTrades",),
    "source_timestamp": ("transactTime", "lastTradedTime"),
    "source_status": ("etatCotVal", "status", "state"),
}

HANDLED_JSON_KEYS = {key for aliases in JSON_FIELD_ALIASES.values() for key in aliases}


def parse_bvc_market_listing_json(
    *,
    raw_payload_id: UUID,
    payload_text: str,
    collected_at: datetime,
    source_published_at: datetime | None = None,
) -> BvcPriceParseResult:
    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError as exc:
        raise BvcPriceParseError(f"invalid JSON payload: {exc}") from exc

    records = extract_json_records(payload)
    if not records:
        raise BvcPriceParseError("BVC JSON payload contains no market data rows")

    result = BvcPriceParseResult(raw_payload_id=raw_payload_id)
    for row_index, record in enumerate(records):
        raw_values = _raw_values_from_record(record)
        try:
            row = _parse_json_row(
                raw_payload_id=raw_payload_id,
                row_index=row_index,
                raw_values=raw_values,
                collected_at=collected_at,
                source_published_at=source_published_at,
            )
            if result.source_timestamp is None and row.source_timestamp is not None:
                result.source_timestamp = row.source_timestamp
                result.source_timestamp_raw = raw_values.get("source_timestamp")
                result.source_timestamp_policy = "source_timestamp"
                result.source_trading_date = row.source_timestamp.date()
            result.rows.append(row)
        except BvcPriceParseError as exc:
            result.errors.append(
                BvcRowParseError(
                    row_index=row_index,
                    error_type="parse_error",
                    error_message=str(exc),
                    raw_fragment=raw_values,
                )
            )

    if result.source_timestamp is None:
        result.source_timestamp_policy = "raw_payload_collected_at_no_source_date"

    result.pagination_metadata = extract_json_pagination_metadata(payload, rows_detected=len(records))
    return result


def extract_json_records(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        raise BvcPriceParseError("BVC JSON payload must be an object")
    data = payload.get("data")
    if isinstance(data, dict) and isinstance(data.get("data"), list):
        return _record_dicts(data["data"])
    if isinstance(data, list):
        return _record_dicts(data)
    raise BvcPriceParseError("BVC JSON market data array not found")


def extract_json_pagination_metadata(payload: Any, *, rows_detected: int) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "pagination_detected": False,
        "pagination_controls": {},
        "pagination_warnings": [],
        "rows_detected": rows_detected,
    }
    if not isinstance(payload, dict):
        return metadata
    root_data = payload.get("data")
    if isinstance(root_data, dict):
        links = root_data.get("links")
        if isinstance(links, dict) and links:
            metadata["pagination_detected"] = True
            metadata["pagination_controls"]["links"] = sorted(links.keys())
        filters = root_data.get("filters")
        if isinstance(filters, dict):
            metadata["pagination_controls"]["filters"] = filters
    return metadata


def json_payload_shape(payload_text: str) -> tuple[list[dict[str, Any]], list[str]]:
    payload = json.loads(payload_text)
    records = extract_json_records(payload)
    keys: list[str] = []
    for record in records:
        attributes = record.get("attributes")
        if isinstance(attributes, dict):
            keys.extend(str(key) for key in attributes.keys())
    return records, sorted(set(keys))


def _record_dicts(value: list[Any]) -> list[dict[str, Any]]:
    records = [record for record in value if isinstance(record, dict)]
    if len(records) != len(value):
        raise BvcPriceParseError("BVC JSON market data array contains non-object rows")
    return records


def _raw_values_from_record(record: dict[str, Any]) -> dict[str, str | None]:
    attributes = record.get("attributes")
    if not isinstance(attributes, dict):
        raise BvcPriceParseError("BVC JSON row is missing attributes")

    raw_values: dict[str, str | None] = {}
    for field_name, aliases in JSON_FIELD_ALIASES.items():
        value = _first_attribute(attributes, aliases)
        raw_values[field_name] = _string_value(value)

    symbol = _source_symbol(raw_values.get("source_symbol"))
    if symbol:
        raw_values["source_symbol"] = symbol
    if not raw_values.get("source_name"):
        raw_values["source_name"] = raw_values.get("source_symbol")
    return raw_values


def _parse_json_row(
    *,
    raw_payload_id: UUID,
    row_index: int,
    raw_values: dict[str, str | None],
    collected_at: datetime,
    source_published_at: datetime | None,
) -> BvcParsedPriceRow:
    source_timestamp = _parse_timestamp(raw_values.get("source_timestamp"))
    fallback_timestamp = source_published_at or collected_at
    trading_date = source_timestamp.date() if source_timestamp is not None else fallback_timestamp.date()
    symbol = clean_text(raw_values.get("source_symbol"))
    isin = clean_text(raw_values.get("isin"))
    return BvcParsedPriceRow(
        raw_payload_id=raw_payload_id,
        row_index=row_index,
        source_symbol=symbol.upper() if symbol else None,
        source_name=clean_text(raw_values.get("source_name")),
        isin=isin.upper() if isin else None,
        last_price=parse_decimal(raw_values.get("last_price")),
        open_price=parse_decimal(raw_values.get("open_price")),
        high_price=parse_decimal(raw_values.get("high_price")),
        low_price=parse_decimal(raw_values.get("low_price")),
        previous_close=parse_decimal(raw_values.get("previous_close")),
        change_value=parse_decimal(raw_values.get("change_value")),
        change_percent=parse_decimal(raw_values.get("change_percent")),
        volume=parse_int(raw_values.get("volume")),
        traded_value=parse_decimal(raw_values.get("traded_value")),
        market_cap=parse_decimal(raw_values.get("market_cap")),
        number_of_trades=parse_int(raw_values.get("number_of_trades")),
        source_timestamp=source_timestamp,
        trading_date=trading_date,
        raw_values=raw_values,
    )


def _first_attribute(attributes: dict[str, Any], aliases: tuple[str, ...]) -> Any:
    for alias in aliases:
        if alias in attributes:
            value = attributes[alias]
            if value is None:
                continue
            if isinstance(value, str) and not value.strip():
                continue
            return value
    return None


def _source_symbol(value: str | None) -> str | None:
    cleaned = clean_text(value)
    if cleaned is None:
        return None
    return cleaned.split("-", 1)[0].upper()


def _string_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return str(value)
    return str(value)


def _parse_timestamp(value: str | None) -> datetime | None:
    cleaned = clean_text(value)
    if cleaned is None:
        return None
    try:
        return datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BvcPriceParseError(f"invalid timestamp value: {value}") from exc
