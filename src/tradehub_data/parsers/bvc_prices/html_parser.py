from datetime import date, datetime
import re
from uuid import UUID

from bs4 import BeautifulSoup

from tradehub_data.parsers.bvc_prices.errors import BvcPriceParseError
from tradehub_data.parsers.bvc_prices.models import BvcParsedPriceRow, BvcPriceParseResult, BvcRowParseError
from tradehub_data.parsers.bvc_prices.number_parsing import clean_text, normalize_label, parse_decimal, parse_int
from tradehub_data.parsers.bvc_prices.source_metadata import detect_pagination, extract_source_date_info

HEADER_ALIASES = {
    "source_name": {"instrument", "valeur", "nom", "libelle", "instrument name"},
    "source_symbol": {"symbole", "ticker", "code", "instrument code", "symbol"},
    "isin": {"isin", "code isin"},
    "last_price": {"dernier cours", "cours", "last price", "close", "dernier"},
    "open_price": {"ouverture", "open", "cours d ouverture"},
    "high_price": {"plus haut", "haut", "high", "+ haut jour"},
    "low_price": {"plus bas", "bas", "low", "+ bas jour"},
    "previous_close": {"cours de cloture veille", "reference", "cours de reference", "previous close"},
    "change_percent": {"variation en %", "variation %", "% variation", "variation"},
    "volume": {"quantite", "qte", "quantite echangee"},
    "traded_value": {"capitaux", "valeur echangee", "traded value", "turnover"},
    "market_cap": {"capitalisation", "market cap", "capitalisation boursiere"},
    "number_of_trades": {"nombre de transactions", "transactions", "trades"},
}

MINIMUM_TABLE_FIELDS = {"last_price", "volume"}


def parse_bvc_market_listing_html(
    *,
    raw_payload_id: UUID,
    payload_text: str,
    collected_at: datetime,
    source_published_at: datetime | None = None,
) -> BvcPriceParseResult:
    soup = BeautifulSoup(payload_text, "html.parser")
    table_info = _find_market_table(soup)
    if table_info is None:
        raise BvcPriceParseError("BVC market listing table not found")

    table, header_map = table_info
    source_date_info = extract_source_date_info(payload_text)
    fallback_timestamp = source_published_at or collected_at
    effective_trading_date = source_date_info.source_trading_date or fallback_timestamp.date()
    result = BvcPriceParseResult(
        raw_payload_id=raw_payload_id,
        source_timestamp=source_date_info.source_timestamp,
        source_trading_date=source_date_info.source_trading_date,
        source_timestamp_raw=source_date_info.source_timestamp_raw,
        source_timestamp_policy=source_date_info.source_timestamp_policy,
        raw_date_candidates=source_date_info.raw_date_candidates,
    )

    tbody = table.find("tbody")
    rows = tbody.find_all("tr") if tbody else table.find_all("tr")[1:]
    rows_detected = 0
    for row_index, row in enumerate(rows):
        cells = [clean_text(cell.get_text(" ", strip=True)) for cell in row.find_all(["td", "th"])]
        if not any(cells):
            continue
        rows_detected += 1
        raw_values = _raw_values_from_row(row, header_map)
        try:
            parsed_row = _parse_row(
                raw_payload_id=raw_payload_id,
                row_index=row_index,
                raw_values=raw_values,
                source_timestamp=source_date_info.source_timestamp,
                trading_date=effective_trading_date,
            )
            result.rows.append(parsed_row)
        except BvcPriceParseError as exc:
            result.errors.append(
                BvcRowParseError(
                    row_index=row_index,
                    error_type="parse_error",
                    error_message=str(exc),
                    raw_fragment=raw_values,
                )
            )

    if not result.rows and not result.errors:
        raise BvcPriceParseError("BVC market listing table contains no data rows")
    pagination = detect_pagination(payload_text, rows_detected=rows_detected)
    result.pagination_metadata = {
        "pagination_detected": pagination.pagination_detected,
        "pagination_controls": pagination.pagination_controls,
        "pagination_warnings": pagination.pagination_warnings,
    }
    return result


def _find_market_table(soup: BeautifulSoup):
    for table in soup.find_all("table"):
        header_cells = table.find_all("th")
        if not header_cells:
            first_row = table.find("tr")
            header_cells = first_row.find_all(["td", "th"]) if first_row else []
        labels = [normalize_label(cell.get_text(" ", strip=True)) for cell in header_cells]
        header_map = _map_headers(labels)
        if MINIMUM_TABLE_FIELDS.issubset(set(header_map.values())):
            return table, header_map
    return None


def _map_headers(labels: list[str]) -> dict[int, str]:
    header_map: dict[int, str] = {}
    real_bvc_volume_shape = "quantite echangee" in labels
    for index, label in enumerate(labels):
        if label == "volume":
            header_map[index] = "traded_value" if real_bvc_volume_shape else "volume"
            continue
        for field_name, aliases in HEADER_ALIASES.items():
            if label in aliases:
                header_map[index] = field_name
                break
    return header_map


def _raw_values_from_row(row, header_map: dict[int, str]) -> dict[str, str | None]:
    cells = [clean_text(cell.get_text(" ", strip=True)) for cell in row.find_all(["td", "th"])]
    values = _raw_values(cells, header_map)
    if not values.get("source_symbol"):
        symbol = _extract_symbol_from_instrument_link(row, header_map)
        if symbol:
            values["source_symbol"] = symbol
    return values


def _raw_values(cells: list[str | None], header_map: dict[int, str]) -> dict[str, str | None]:
    values: dict[str, str | None] = {}
    for index, field_name in header_map.items():
        values[field_name] = cells[index] if index < len(cells) else None
    return values


def _extract_symbol_from_instrument_link(row, header_map: dict[int, str]) -> str | None:
    source_name_indexes = [index for index, field_name in header_map.items() if field_name == "source_name"]
    if not source_name_indexes:
        return None
    cells = row.find_all(["td", "th"])
    source_name_index = source_name_indexes[0]
    if source_name_index >= len(cells):
        return None
    link = cells[source_name_index].find("a", href=True)
    if link is None:
        return None
    match = re.search(r"/instruments/([^/?#]+)", link["href"])
    if match is None:
        return None
    return clean_text(match.group(1))


def _parse_row(
    *,
    raw_payload_id: UUID,
    row_index: int,
    raw_values: dict[str, str | None],
    source_timestamp: datetime | None,
    trading_date: date,
) -> BvcParsedPriceRow:
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
        change_percent=parse_decimal(raw_values.get("change_percent")),
        volume=parse_int(raw_values.get("volume")),
        traded_value=parse_decimal(raw_values.get("traded_value")),
        market_cap=parse_decimal(raw_values.get("market_cap")),
        number_of_trades=parse_int(raw_values.get("number_of_trades")),
        source_timestamp=source_timestamp,
        trading_date=trading_date,
        raw_values=raw_values,
    )
