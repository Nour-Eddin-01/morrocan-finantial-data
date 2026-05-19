import argparse
import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from bs4 import BeautifulSoup
from pydantic import BaseModel, Field

from tradehub_data.db.session import SessionLocal
from tradehub_data.parsers.bvc_prices.errors import BvcPriceParseError
from tradehub_data.parsers.bvc_prices.html_parser import MINIMUM_TABLE_FIELDS, _map_headers, _parse_row, _raw_values_from_row
from tradehub_data.parsers.bvc_prices.json_parser import (
    HANDLED_JSON_KEYS,
    JSON_FIELD_ALIASES,
    extract_json_pagination_metadata,
    json_payload_shape,
    parse_bvc_market_listing_json,
)
from tradehub_data.parsers.bvc_prices.number_parsing import clean_text, normalize_label
from tradehub_data.parsers.bvc_prices.source_metadata import detect_pagination, extract_source_date_info
from tradehub_data.repositories.raw_payloads import get_raw_payload_by_id

ROW_REQUIRED_FIELDS = {
    "last_price",
    "source_timestamp",
    "trading_date",
    "instrument_identifier",
    "instrument_name",
}


class BvcTableDiagnostic(BaseModel):
    table_index: int
    headers_detected: list[str] = Field(default_factory=list)
    normalized_headers: list[str] = Field(default_factory=list)
    rows_detected: int
    mapped_fields: list[str] = Field(default_factory=list)
    unmapped_headers: list[str] = Field(default_factory=list)
    missing_required_fields: list[str] = Field(default_factory=list)
    candidate: bool
    selected: bool = False
    selected_reason: str | None = None


class BvcParserDiagnosticResult(BaseModel):
    payload_format: str = "html"
    file_path: str | None = None
    raw_payload_id: UUID | None = None
    payload_hash: str | None = None
    tables_found: int
    candidate_tables: list[BvcTableDiagnostic] = Field(default_factory=list)
    headers_detected: list[str] = Field(default_factory=list)
    normalized_headers: list[str] = Field(default_factory=list)
    rows_detected: int = 0
    mapped_fields: list[str] = Field(default_factory=list)
    unmapped_headers: list[str] = Field(default_factory=list)
    unmapped_fields: list[str] = Field(default_factory=list)
    missing_required_fields: list[str] = Field(default_factory=list)
    parseable_rows_count: int = 0
    row_parse_errors_count: int = 0
    row_parse_errors_sample: list[dict[str, Any]] = Field(default_factory=list)
    selected_table_index: int | None = None
    selected_table_reason: str | None = None
    source_trading_date: date | None = None
    source_timestamp: datetime | None = None
    source_timestamp_raw: str | None = None
    source_timestamp_policy: str = "raw_payload_collected_at_no_source_date"
    raw_date_candidates: list[str] = Field(default_factory=list)
    pagination_detected: bool = False
    pagination_controls: dict[str, Any] = Field(default_factory=dict)
    pagination_warnings: list[str] = Field(default_factory=list)
    page_number: int | None = None
    page_offset: int | None = None
    page_limit: int | None = None
    status: str


def diagnose_bvc_market_listing_html(
    *,
    payload_text: str,
    raw_payload_id: UUID | None = None,
    file_path: str | None = None,
    payload_hash: str | None = None,
    collected_at: datetime | None = None,
    source_published_at: datetime | None = None,
) -> BvcParserDiagnosticResult:
    diagnostic_payload_id = raw_payload_id or uuid4()
    fallback_timestamp = source_published_at or collected_at or datetime.now(UTC)
    source_date_info = extract_source_date_info(payload_text)
    effective_trading_date = source_date_info.source_trading_date or fallback_timestamp.date()
    soup = BeautifulSoup(payload_text, "html.parser")
    tables = soup.find_all("table")
    table_diagnostics: list[BvcTableDiagnostic] = []
    selected_table = None
    selected_header_map: dict[int, str] = {}
    selected_index: int | None = None

    for table_index, table in enumerate(tables):
        headers = _extract_headers(table)
        normalized_headers = [normalize_label(header) for header in headers]
        header_map = _map_headers(normalized_headers)
        mapped_fields = _mapped_fields(header_map)
        unmapped_headers = [
            header
            for index, header in enumerate(headers)
            if header and index not in header_map
        ]
        missing_required_fields = sorted(MINIMUM_TABLE_FIELDS - set(header_map.values()))
        candidate = not missing_required_fields
        rows_detected = _count_data_rows(table)
        table_diagnostic = BvcTableDiagnostic(
            table_index=table_index,
            headers_detected=headers,
            normalized_headers=normalized_headers,
            rows_detected=rows_detected,
            mapped_fields=mapped_fields,
            unmapped_headers=unmapped_headers,
            missing_required_fields=missing_required_fields,
            candidate=candidate,
            selected_reason=_candidate_reason(candidate, missing_required_fields),
        )

        if candidate and selected_table is None:
            selected_table = table
            selected_header_map = header_map
            selected_index = table_index
            table_diagnostic.selected = True
            table_diagnostic.selected_reason = "selected first table containing required market fields"

        table_diagnostics.append(table_diagnostic)

    selected_diagnostic = table_diagnostics[selected_index] if selected_index is not None else None
    parseable_rows_count = 0
    row_parse_errors: list[dict[str, Any]] = []
    if selected_table is not None:
        for row_index, row in enumerate(_data_rows(selected_table)):
            if not any(_row_cells(row)):
                continue
            raw_values = _raw_values_from_row(row, selected_header_map)
            try:
                _parse_row(
                    raw_payload_id=diagnostic_payload_id,
                    row_index=row_index,
                    raw_values=raw_values,
                    source_timestamp=source_date_info.source_timestamp,
                    trading_date=effective_trading_date,
                )
                parseable_rows_count += 1
            except BvcPriceParseError as exc:
                row_parse_errors.append(
                    {
                        "row_index": row_index,
                        "error_type": "parse_error",
                        "error_message": str(exc),
                        "raw_fragment": raw_values,
                    }
                )

    rows_detected = selected_diagnostic.rows_detected if selected_diagnostic else 0
    pagination = detect_pagination(payload_text, rows_detected=rows_detected)

    return BvcParserDiagnosticResult(
        file_path=file_path,
        raw_payload_id=raw_payload_id,
        payload_hash=payload_hash,
        tables_found=len(tables),
        candidate_tables=table_diagnostics,
        headers_detected=selected_diagnostic.headers_detected if selected_diagnostic else [],
        normalized_headers=selected_diagnostic.normalized_headers if selected_diagnostic else [],
        rows_detected=rows_detected,
        mapped_fields=selected_diagnostic.mapped_fields if selected_diagnostic else [],
        unmapped_headers=selected_diagnostic.unmapped_headers if selected_diagnostic else [],
        missing_required_fields=selected_diagnostic.missing_required_fields if selected_diagnostic else sorted(MINIMUM_TABLE_FIELDS),
        parseable_rows_count=parseable_rows_count,
        row_parse_errors_count=len(row_parse_errors),
        row_parse_errors_sample=row_parse_errors[:5],
        selected_table_index=selected_index,
        selected_table_reason=selected_diagnostic.selected_reason if selected_diagnostic else None,
        source_trading_date=source_date_info.source_trading_date,
        source_timestamp=source_date_info.source_timestamp,
        source_timestamp_raw=source_date_info.source_timestamp_raw,
        source_timestamp_policy=source_date_info.source_timestamp_policy,
        raw_date_candidates=source_date_info.raw_date_candidates,
        pagination_detected=pagination.pagination_detected,
        pagination_controls=pagination.pagination_controls,
        pagination_warnings=pagination.pagination_warnings,
        status=_status(selected_index=selected_index, parseable_rows_count=parseable_rows_count, row_parse_errors_count=len(row_parse_errors)),
    )


def diagnose_file(file_path: Path) -> BvcParserDiagnosticResult:
    return diagnose_bvc_price_payload(
        payload_text=file_path.read_text(encoding="utf-8"),
        file_path=str(file_path),
    )


def diagnose_raw_payload(raw_payload_id: UUID) -> BvcParserDiagnosticResult:
    with SessionLocal() as db:
        raw_payload = get_raw_payload_by_id(db, raw_payload_id)
        if raw_payload is None:
            raise SystemExit(f"raw payload not found: {raw_payload_id}")
        if not raw_payload.payload_text:
            raise SystemExit(f"raw payload has no payload_text: {raw_payload_id}")
        return diagnose_bvc_price_payload(
            raw_payload_id=raw_payload.id,
            payload_text=raw_payload.payload_text,
            payload_hash=raw_payload.payload_hash,
            collected_at=raw_payload.collected_at,
            source_published_at=raw_payload.source_published_at,
            content_type=raw_payload.content_type,
            source_endpoint=raw_payload.source_endpoint,
            metadata=raw_payload.metadata_,
        )


def diagnose_bvc_price_payload(
    *,
    payload_text: str,
    raw_payload_id: UUID | None = None,
    file_path: str | None = None,
    payload_hash: str | None = None,
    collected_at: datetime | None = None,
    source_published_at: datetime | None = None,
    content_type: str | None = None,
    source_endpoint: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> BvcParserDiagnosticResult:
    if _is_json_payload(payload_text, content_type=content_type, source_endpoint=source_endpoint, metadata=metadata):
        return diagnose_bvc_market_listing_json(
            payload_text=payload_text,
            raw_payload_id=raw_payload_id,
            file_path=file_path,
            payload_hash=payload_hash,
            collected_at=collected_at,
            source_published_at=source_published_at,
            metadata=metadata,
        )
    return diagnose_bvc_market_listing_html(
        payload_text=payload_text,
        raw_payload_id=raw_payload_id,
        file_path=file_path,
        payload_hash=payload_hash,
        collected_at=collected_at,
        source_published_at=source_published_at,
    )


def diagnose_bvc_market_listing_json(
    *,
    payload_text: str,
    raw_payload_id: UUID | None = None,
    file_path: str | None = None,
    payload_hash: str | None = None,
    collected_at: datetime | None = None,
    source_published_at: datetime | None = None,
    metadata: dict[str, Any] | None = None,
) -> BvcParserDiagnosticResult:
    diagnostic_payload_id = raw_payload_id or uuid4()
    fallback_timestamp = source_published_at or collected_at or datetime.now(UTC)
    try:
        records, attribute_keys = json_payload_shape(payload_text)
        parse_result = parse_bvc_market_listing_json(
            raw_payload_id=diagnostic_payload_id,
            payload_text=payload_text,
            collected_at=fallback_timestamp,
            source_published_at=source_published_at,
        )
        payload = json.loads(payload_text)
        pagination = extract_json_pagination_metadata(payload, rows_detected=len(records))
    except (json.JSONDecodeError, BvcPriceParseError) as exc:
        return BvcParserDiagnosticResult(
            payload_format="json",
            file_path=file_path,
            raw_payload_id=raw_payload_id,
            payload_hash=payload_hash,
            tables_found=0,
            rows_detected=0,
            mapped_fields=[],
            unmapped_fields=[],
            missing_required_fields=sorted(MINIMUM_TABLE_FIELDS),
            parseable_rows_count=0,
            row_parse_errors_count=1,
            row_parse_errors_sample=[{"row_index": None, "error_type": "parse_error", "error_message": str(exc)}],
            status="failed",
        )

    mapped_fields = _json_mapped_fields(attribute_keys)
    row_errors = [error.model_dump(mode="json") for error in parse_result.errors]
    page_number = (metadata or {}).get("page_number")
    page_offset = (metadata or {}).get("page_offset")
    page_limit = (metadata or {}).get("page_limit")
    return BvcParserDiagnosticResult(
        payload_format="json",
        file_path=file_path,
        raw_payload_id=raw_payload_id,
        payload_hash=payload_hash,
        tables_found=0,
        candidate_tables=[],
        headers_detected=attribute_keys,
        normalized_headers=attribute_keys,
        rows_detected=len(records),
        mapped_fields=mapped_fields,
        unmapped_headers=[],
        unmapped_fields=[key for key in attribute_keys if key not in HANDLED_JSON_KEYS],
        missing_required_fields=sorted(MINIMUM_TABLE_FIELDS - set(mapped_fields)),
        parseable_rows_count=len(parse_result.rows),
        row_parse_errors_count=len(parse_result.errors),
        row_parse_errors_sample=row_errors[:5],
        selected_table_index=None,
        selected_table_reason="selected JSON market data array",
        source_trading_date=parse_result.source_trading_date,
        source_timestamp=parse_result.source_timestamp,
        source_timestamp_raw=parse_result.source_timestamp_raw,
        source_timestamp_policy=parse_result.source_timestamp_policy,
        raw_date_candidates=parse_result.raw_date_candidates,
        pagination_detected=bool(pagination.get("pagination_detected")),
        pagination_controls=pagination.get("pagination_controls", {}),
        pagination_warnings=pagination.get("pagination_warnings", []),
        page_number=page_number,
        page_offset=page_offset,
        page_limit=page_limit,
        status=_status(
            selected_index=0,
            parseable_rows_count=len(parse_result.rows),
            row_parse_errors_count=len(parse_result.errors),
        ),
    )


def _extract_headers(table) -> list[str]:
    header_cells = table.find_all("th")
    if not header_cells:
        first_row = table.find("tr")
        header_cells = first_row.find_all(["td", "th"]) if first_row else []
    return [clean_text(cell.get_text(" ", strip=True)) or "" for cell in header_cells]


def _data_row_cells(table) -> list[list[str | None]]:
    return [_row_cells(row) for row in _data_rows(table)]


def _data_rows(table):
    tbody = table.find("tbody")
    return tbody.find_all("tr") if tbody else table.find_all("tr")[1:]


def _row_cells(row) -> list[str | None]:
    return [clean_text(cell.get_text(" ", strip=True)) for cell in row.find_all(["td", "th"])]


def _count_data_rows(table) -> int:
    return sum(1 for cells in _data_row_cells(table) if any(cells))


def _mapped_fields(header_map: dict[int, str]) -> list[str]:
    return list(dict.fromkeys(header_map.values()))


def _candidate_reason(candidate: bool, missing_required_fields: list[str]) -> str:
    if candidate:
        return "contains required market fields"
    return f"missing required fields: {', '.join(missing_required_fields)}"


def _status(*, selected_index: int | None, parseable_rows_count: int, row_parse_errors_count: int) -> str:
    if selected_index is None:
        return "failed"
    if parseable_rows_count == 0:
        return "failed"
    if row_parse_errors_count:
        return "partial_success"
    return "success"


def _is_json_payload(
    payload_text: str,
    *,
    content_type: str | None = None,
    source_endpoint: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> bool:
    if content_type and "json" in content_type.lower():
        return True
    if source_endpoint and "json" in source_endpoint:
        return True
    if (metadata or {}).get("collection_mode") == "live_json":
        return True
    return payload_text.lstrip().startswith("{")


def _json_mapped_fields(attribute_keys: list[str]) -> list[str]:
    fields: list[str] = []
    keys = set(attribute_keys)
    for field_name, aliases in JSON_FIELD_ALIASES.items():
        if any(alias in keys for alias in aliases):
            fields.append(field_name)
    return fields


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspect BVC market listing payloads without normalizing data.")
    parser.add_argument("file_path", nargs="?", help="Path to a local BVC HTML or JSON payload.")
    parser.add_argument("--raw-payload-id", help="Inspect an existing raw_payloads row by ID.")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if bool(args.file_path) == bool(args.raw_payload_id):
        raise SystemExit("provide exactly one input: file_path or --raw-payload-id")

    if args.raw_payload_id:
        result = diagnose_raw_payload(UUID(args.raw_payload_id))
    else:
        result = diagnose_file(Path(args.file_path))

    print(json.dumps(result.model_dump(mode="json"), sort_keys=True))
    if result.status == "failed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
