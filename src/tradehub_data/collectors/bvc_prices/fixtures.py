import argparse
import json
import mimetypes
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from tradehub_data.collectors.bvc_prices.constants import (
    BVC_PRICE_PAYLOAD_TYPE,
    BVC_PRICE_SOURCE_CODE,
    BVC_PRICE_SOURCE_NAME,
    DEFAULT_BVC_BASE_URL,
)
from tradehub_data.core.config import get_settings
from tradehub_data.core.hashing import sha256_source_payload
from tradehub_data.core.logging import configure_logging
from tradehub_data.db.session import SessionLocal
from tradehub_data.repositories.raw_payloads import insert_raw_payload_if_new
from tradehub_data.repositories.sources import create_ingestion_run, finish_ingestion_run, get_or_create_data_source

BVC_PRICE_FIXTURE_LOADER_NAME = "bvc_price_fixture_loader"


def store_local_fixture(
    db: Session,
    *,
    file_path: Path,
    source_url: str | None = None,
    payload_type: str = BVC_PRICE_PAYLOAD_TYPE,
) -> dict[str, Any]:
    resolved_file_path = file_path.expanduser().resolve()
    payload_text = resolved_file_path.read_text(encoding="utf-8")
    effective_source_url = source_url or f"manual-fixture://bvc_prices/{resolved_file_path.name}"
    content_type = mimetypes.guess_type(resolved_file_path.name)[0] or "text/plain"
    collected_at = datetime.now(UTC)

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
    run = create_ingestion_run(
        db,
        source_id=source.id,
        collector_name=BVC_PRICE_FIXTURE_LOADER_NAME,
        run_type="manual",
        started_at=collected_at,
        metadata={
            "fixture_path": str(resolved_file_path),
            "source_url": effective_source_url,
            "payload_type": payload_type,
        },
    )

    payload_hash = sha256_source_payload(source_url=effective_source_url, body_text=payload_text)
    raw_payload, inserted = insert_raw_payload_if_new(
        db,
        source_id=source.id,
        ingestion_run_id=run.id,
        source_url=effective_source_url,
        source_endpoint="manual_fixture",
        payload_type=payload_type,
        payload_text=payload_text,
        payload_hash=payload_hash,
        http_status=None,
        content_type=content_type,
        collected_at=collected_at,
        status="collected",
        metadata={
            "fixture_path": str(resolved_file_path),
            "hash_strategy": "sha256(source_url + normalized_body)",
            "loaded_by": BVC_PRICE_FIXTURE_LOADER_NAME,
        },
    )

    status = "success"
    finish_ingestion_run(
        db,
        run,
        status=status,
        finished_at=datetime.now(UTC),
        records_collected=1,
        records_inserted=1 if inserted else 0,
        records_updated=0,
        records_failed=0,
        metadata={
            "fixture_path": str(resolved_file_path),
            "source_url": effective_source_url,
            "raw_payload_id": str(raw_payload.id),
            "payload_hash": payload_hash,
            "payload_inserted": inserted,
        },
    )
    db.commit()

    return {
        "status": status,
        "ingestion_run_id": str(run.id),
        "raw_payload_id": str(raw_payload.id),
        "payload_hash": payload_hash,
        "payload_inserted": inserted,
        "source_url": effective_source_url,
        "payload_type": payload_type,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Store a local BVC price raw payload fixture.")
    parser.add_argument("file_path", help="Path to a local HTML, JSON, CSV, or text payload fixture.")
    parser.add_argument(
        "--source-url",
        help="Original source URL represented by this fixture. Defaults to manual-fixture://bvc_prices/<filename>.",
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
