from __future__ import annotations

import argparse
import json
import mimetypes
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from tradehub_data.collectors.bvc_prices.constants import (
    BVC_EQUITY_PRICE_DATASET_CODE,
    BVC_FIXTURE_REQUEST_PROFILE,
    BVC_PRICE_PAYLOAD_TYPE,
    BVC_PRICE_SOURCE_CODE,
    BVC_PRICE_SOURCE_NAME,
    DEFAULT_BVC_BASE_URL,
    DEFAULT_ALLOWED_DOMAINS,
)
from tradehub_data.collectors.http_metadata import (
    BVC_SAFE_RESPONSE_HEADERS_POLICY_VERSION,
    safe_bvc_fixture_logical_identifier,
    sanitize_bvc_http_url,
)
from tradehub_data.core.config import get_settings
from tradehub_data.core.logging import configure_logging
from tradehub_data.db.session import SessionLocal
from tradehub_data.parsers.bvc_prices.diagnostics import diagnose_bvc_price_payload
from tradehub_data.repositories.collection_audit import (
    create_collection_group,
    create_collection_group_page,
    finalize_collection_group_and_run,
    finalize_page_failure,
    finalize_page_with_selection,
    record_fixture_occurrence,
)
from tradehub_data.repositories.exchanges import get_or_create_exchange
from tradehub_data.repositories.raw_contents import (
    ExactRawCompatibilityContext,
    fill_exact_raw_content_text_cache,
)
from tradehub_data.repositories.sources import create_ingestion_run, get_or_create_data_source

BVC_PRICE_FIXTURE_LOADER_NAME = "bvc_price_fixture_loader"


class FixtureLoadError(RuntimeError):
    """A path-free fixture acquisition error safe to expose to callers."""


def store_local_fixture(
    db: Session,
    *,
    file_path: Path,
    source_url: str | None = None,
    payload_type: str = BVC_PRICE_PAYLOAD_TYPE,
) -> dict[str, Any]:
    """Store exact fixture bytes, audit their load, then qualify the page.

    The local path and any caller-provided private URL material are never part
    of stored collection evidence.  The exact content/occurrence transaction
    commits before decoding or diagnostics.
    """

    if payload_type != BVC_PRICE_PAYLOAD_TYPE:
        raise ValueError("the BVC fixture audit path supports bvc_price_snapshot only")

    resolved_file_path = file_path.expanduser().resolve()
    fixture_identifier = safe_bvc_fixture_logical_identifier()
    compatibility_source_url = _safe_fixture_source_url(source_url)
    content_type = mimetypes.guess_type(resolved_file_path.name)[0] or "text/plain"
    started_at = datetime.now(UTC)

    try:
        source = get_or_create_data_source(
            db,
            code=BVC_PRICE_SOURCE_CODE,
            name=BVC_PRICE_SOURCE_NAME,
            source_type="exchange",
            base_url=DEFAULT_BVC_BASE_URL,
            country_code="MA",
            priority=100,
            metadata={
                "official": True,
                "market": "Casablanca Stock Exchange",
                "fixture_loader": BVC_PRICE_FIXTURE_LOADER_NAME,
            },
        )
        exchange, _ = get_or_create_exchange(
            db,
            code="BVC",
            name="Bourse de Casablanca",
            country_code="MA",
            currency_code="MAD",
            timezone="Africa/Casablanca",
            website_url=DEFAULT_BVC_BASE_URL,
            metadata={"official": True},
        )
        run = create_ingestion_run(
            db,
            source_id=source.id,
            collector_name=BVC_PRICE_FIXTURE_LOADER_NAME,
            run_type="manual",
            run_role="validation",
            started_at=started_at,
            metadata={"collection_mode": "manual_fixture", "page_limit": 1},
        )
        group = create_collection_group(
            db,
            source_id=source.id,
            exchange_id=exchange.id,
            ingestion_run_id=run.id,
            dataset_code=BVC_EQUITY_PRICE_DATASET_CODE,
            collection_mode="manual_fixture",
            group_purpose="validation",
            page_limit=1,
            started_at=started_at,
        )
        page = create_collection_group_page(
            db,
            group_id=group.id,
            logical_page_number=1,
        )
        db.commit()
    except Exception:
        db.rollback()
        raise

    requested_at = datetime.now(UTC)
    try:
        entity_body = resolved_file_path.read_bytes()
    except OSError:
        failure_time = datetime.now(UTC)
        try:
            finalize_page_failure(
                db,
                group_id=group.id,
                group_page_id=page.id,
                finalized_at=failure_time,
                structural_reason_code="fixture_read_failed",
            )
            finalize_collection_group_and_run(
                db,
                group_id=group.id,
                collection_status="failed",
                pagination_complete=False,
                completion_evidence_kind="none",
                finalized_at=failure_time,
                collection_stop_reason="fixture_read_failed",
                observed_instrument_count=0,
                records_collected=0,
                records_inserted=0,
                records_updated=0,
                records_failed=1,
                safe_error_code="fixture_read_failed",
                error_message="fixture could not be read",
                run_metadata={
                    "collection_mode": "manual_fixture",
                    "collection_group_id": str(group.id),
                    "pagination_complete": False,
                    "collection_stop_reason": "fixture_read_failed",
                    "raw_payload_ids": [],
                    "payloads_skipped": 0,
                },
                safe_diagnostic_codes=["fixture_read_failed"],
            )
            db.commit()
        except Exception:
            db.rollback()
            raise
        raise FixtureLoadError("fixture could not be read") from None
    finished_at = datetime.now(UTC)
    try:
        write = record_fixture_occurrence(
            db,
            group_id=group.id,
            group_page_id=page.id,
            source_id=source.id,
            ingestion_run_id=run.id,
            entity_body=entity_body,
            compatibility_context=ExactRawCompatibilityContext(
                ingestion_run_id=run.id,
                collected_at=finished_at,
                source_url=compatibility_source_url,
                source_endpoint="manual_fixture",
                http_status=None,
                content_type=content_type,
                source_published_at=None,
            ),
            request_sequence=1,
            logical_request_url=fixture_identifier,
            source_endpoint="manual_fixture",
            request_profile=BVC_FIXTURE_REQUEST_PROFILE,
            requested_at=requested_at,
            finished_at=finished_at,
            response_headers_policy_version=BVC_SAFE_RESPONSE_HEADERS_POLICY_VERSION,
        )
        db.commit()
    except Exception:
        db.rollback()
        raise

    raw_payload = write.raw_payload
    if raw_payload is None:  # fixture schema requires exact content
        raise RuntimeError("fixture audit did not retain exact content")

    diagnostic_code: str | None = None
    try:
        payload_text = entity_body.decode("utf-8", errors="strict")
        fill_exact_raw_content_text_cache(
            db,
            raw_payload_id=raw_payload.id,
            source_id=source.id,
            first_ingestion_run_id=run.id,
            payload_text=payload_text,
        )
        db.commit()
    except UnicodeDecodeError:
        db.rollback()
        payload_text = ""
        diagnostic_code = "entity_body_decode_failed"
    except Exception:
        db.rollback()
        payload_text = ""
        diagnostic_code = "compatibility_text_cache_failed"

    diagnostic = None
    if diagnostic_code is None:
        try:
            diagnostic = diagnose_bvc_price_payload(
                raw_payload_id=raw_payload.id,
                payload_text=payload_text,
                content_type=content_type,
                source_endpoint="manual_fixture",
            )
        except Exception:
            diagnostic_code = "structural_inspection_error"

    qualified = (
        diagnostic_code is None
        and diagnostic is not None
        and diagnostic.status == "success"
        and diagnostic.rows_detected > 0
    )
    finalized_at = datetime.now(UTC)
    try:
        if qualified:
            finalize_page_with_selection(
                db,
                group_id=group.id,
                group_page_id=page.id,
                occurrence_id=write.occurrence.id,
                page_role="data",
                selected_at=max(finalized_at, finished_at),
                selection_reason="fixture_selected",
                structural_reason_code="fixture_diagnostics_qualified",
            )
            status = "success"
            pagination_complete = True
            completion_evidence_kind = "declared_fixture_scope"
            stop_reason = "declared_fixture_scope"
            error_message = None
            records_failed = 0
            observed_rows = diagnostic.rows_detected
        else:
            diagnostic_code = diagnostic_code or "fixture_structure_not_qualified"
            finalize_page_failure(
                db,
                group_id=group.id,
                group_page_id=page.id,
                finalized_at=finalized_at,
                structural_reason_code=diagnostic_code,
            )
            status = "failed"
            pagination_complete = False
            completion_evidence_kind = "none"
            stop_reason = diagnostic_code
            error_message = "fixture did not pass structural qualification"
            records_failed = 1
            observed_rows = 0

        finalize_collection_group_and_run(
            db,
            group_id=group.id,
            collection_status=status,
            pagination_complete=pagination_complete,
            completion_evidence_kind=completion_evidence_kind,
            finalized_at=max(datetime.now(UTC), finalized_at),
            collection_stop_reason=stop_reason,
            observed_instrument_count=observed_rows,
            records_collected=1,
            records_inserted=1 if write.raw_content_inserted else 0,
            records_updated=0,
            records_failed=records_failed,
            safe_error_code=diagnostic_code,
            error_message=error_message,
            run_metadata={
                "collection_mode": "manual_fixture",
                "collection_group_id": str(group.id),
                "pagination_complete": pagination_complete,
                "collection_stop_reason": stop_reason,
                "raw_payload_ids": [str(raw_payload.id)] if qualified else [],
                "payloads_skipped": 0 if write.raw_content_inserted else 1,
            },
            safe_diagnostic_codes=[diagnostic_code] if diagnostic_code else [],
        )
        db.commit()
    except Exception:
        db.rollback()
        raise

    return {
        "status": status,
        "ingestion_run_id": str(run.id),
        "collection_group_id": str(group.id),
        "raw_payload_id": str(raw_payload.id),
        "payload_hash": raw_payload.payload_hash,
        "entity_body_sha256": raw_payload.entity_body_sha256,
        "payload_inserted": write.raw_content_inserted,
        "source_url": compatibility_source_url,
        "payload_type": payload_type,
    }


def _safe_fixture_source_url(source_url: str | None) -> str:
    if source_url and source_url.startswith(("http://", "https://")):
        return sanitize_bvc_http_url(source_url, allowed_hosts=DEFAULT_ALLOWED_DOMAINS)
    return safe_bvc_fixture_logical_identifier()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Store a local BVC price raw payload fixture.")
    parser.add_argument("file_path", help="Path to a local HTML, JSON, CSV, or text payload fixture.")
    parser.add_argument(
        "--source-url",
        help="Optional public BVC source URL represented by the fixture; private query fields are removed.",
    )
    parser.add_argument("--payload-type", default=BVC_PRICE_PAYLOAD_TYPE)
    return parser


def main() -> None:
    configure_logging(get_settings().log_level)
    args = build_parser().parse_args()
    with SessionLocal() as db:
        result = store_local_fixture(
            db,
            file_path=Path(args.file_path),
            source_url=args.source_url,
            payload_type=args.payload_type,
        )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
