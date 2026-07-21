from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from tradehub_data.models import RawPayload


def get_raw_payload_by_hash(db: Session, *, source_id, payload_hash: str) -> RawPayload | None:
    return db.scalar(
        select(RawPayload).where(
            RawPayload.source_id == source_id,
            RawPayload.payload_hash == payload_hash,
        )
    )


def get_raw_payload_by_id(db: Session, raw_payload_id) -> RawPayload | None:
    return db.get(RawPayload, raw_payload_id)


def list_eligible_raw_payloads(
    db: Session,
    *,
    payload_type: str,
    status: str = "collected",
    limit: int = 10,
) -> list[RawPayload]:
    return list(
        db.scalars(
            select(RawPayload)
            .where(
                RawPayload.payload_type == payload_type,
                RawPayload.status == status,
                RawPayload.payload_text.is_not(None),
            )
            .order_by(RawPayload.collected_at.asc(), RawPayload.created_at.asc())
            .limit(limit)
        )
    )


def update_raw_payload_status(
    db: Session,
    raw_payload: RawPayload,
    *,
    status: str,
    error_message: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> RawPayload:
    # Target exact-content rows are immutable content identities.  Processing
    # state belongs to processing attempts, so legacy status/metadata writes
    # deliberately become no-ops for those rows during dual-write.
    if raw_payload.content_evidence_kind == "exact_entity_bytes":
        return raw_payload
    raw_payload.status = status
    raw_payload.error_message = error_message
    if metadata:
        raw_payload.metadata_ = {**(raw_payload.metadata_ or {}), **metadata}
    db.flush()
    return raw_payload


def update_raw_payload_metadata(
    db: Session,
    raw_payload: RawPayload,
    metadata: dict[str, Any],
) -> RawPayload:
    if raw_payload.content_evidence_kind == "exact_entity_bytes":
        return raw_payload
    raw_payload.metadata_ = {**(raw_payload.metadata_ or {}), **metadata}
    db.flush()
    return raw_payload


def insert_raw_payload_if_new(
    db: Session,
    *,
    source_id,
    payload_hash: str,
    payload_type: str,
    collected_at: datetime,
    ingestion_run_id=None,
    source_url: str | None = None,
    source_endpoint: str | None = None,
    payload: dict[str, Any] | None = None,
    payload_text: str | None = None,
    http_status: int | None = None,
    content_type: str | None = None,
    source_published_at: datetime | None = None,
    status: str = "collected",
    error_message: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> tuple[RawPayload, bool]:
    existing = get_raw_payload_by_hash(db, source_id=source_id, payload_hash=payload_hash)
    if existing is not None:
        return existing, False

    raw_payload = RawPayload(
        source_id=source_id,
        ingestion_run_id=ingestion_run_id,
        source_url=source_url,
        source_endpoint=source_endpoint,
        payload_type=payload_type,
        payload=payload,
        payload_text=payload_text,
        payload_hash=payload_hash,
        http_status=http_status,
        content_type=content_type,
        collected_at=collected_at,
        source_published_at=source_published_at,
        status=status,
        error_message=error_message,
        metadata_=metadata,
    )
    db.add(raw_payload)
    db.flush()
    return raw_payload, True
