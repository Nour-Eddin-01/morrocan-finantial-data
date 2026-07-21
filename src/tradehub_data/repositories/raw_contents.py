"""Repository helpers for immutable exact response content.

The legacy raw-payload repository intentionally remains available while new
collection code writes exact entity bytes.  Functions in this module never
commit; the caller owns the transaction that also records the collection
occurrence.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Select, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from tradehub_data.models import CollectionOccurrence, IngestionRun, RawPayload


EXACT_CONTENT_EVIDENCE_KIND = "exact_entity_bytes"
EXACT_ENTITY_HASH_ALGORITHM = "sha256_entity_body_v1"
EXACT_COMPATIBILITY_HASH_ALGORITHM = "target_exact_compat_filler_v1"
_EXACT_COMPATIBILITY_HASH_NAMESPACE = b"target-exact-compat-v1"
_BVC_PRICE_PAYLOAD_TYPE = "bvc_price_snapshot"


class ExactRawContentError(RuntimeError):
    """Base class for safe exact-content persistence failures."""


class ExactRawContentIntegrityError(ExactRawContentError):
    """Raised when a stored exact-content row contradicts its identity."""


@dataclass(frozen=True, slots=True)
class ExactRawCompatibilityContext:
    """First-occurrence values required by still-non-null legacy columns.

    These values are a compatibility snapshot only.  They are frozen when the
    content row is first inserted and must not be used for target freshness,
    request-attempt identity, or page ownership.
    """

    ingestion_run_id: uuid.UUID
    collected_at: datetime
    source_url: str | None = None
    source_endpoint: str | None = None
    http_status: int | None = None
    content_type: str | None = None
    source_published_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ExactRawContentResult:
    raw_payload: RawPayload
    inserted: bool


def exact_entity_body_sha256(entity_body: bytes) -> str:
    """Return lowercase SHA-256 over the exact client-visible entity bytes."""

    return hashlib.sha256(entity_body).hexdigest()


def exact_compatibility_payload_hash(*, source_id: uuid.UUID, entity_body_sha256: str) -> str:
    """Return the namespaced filler for the legacy ``payload_hash`` column.

    The canonical source UUID and exact entity digest are separated with NUL
    bytes so this value cannot claim the legacy URL/normalized-text algorithm.
    """

    canonical_source_id = str(uuid.UUID(str(source_id))).encode("ascii")
    canonical_entity_digest = entity_body_sha256.encode("ascii")
    material = b"\0".join(
        (
            _EXACT_COMPATIBILITY_HASH_NAMESPACE,
            canonical_source_id,
            canonical_entity_digest,
        )
    )
    return hashlib.sha256(material).hexdigest()


def insert_or_get_exact_raw_content(
    db: Session,
    *,
    source_id: uuid.UUID,
    entity_body: bytes,
    compatibility_context: ExactRawCompatibilityContext,
) -> RawPayload:
    """Atomically insert or reuse immutable exact content for one source.

    PostgreSQL conflict inference explicitly targets the partial exact-content
    index.  An unrelated collision on the legacy compatibility key therefore
    remains an error instead of being silently swallowed.
    """

    return insert_or_get_exact_raw_content_result(
        db,
        source_id=source_id,
        entity_body=entity_body,
        compatibility_context=compatibility_context,
    ).raw_payload


def insert_or_get_exact_raw_content_result(
    db: Session,
    *,
    source_id: uuid.UUID,
    entity_body: bytes,
    compatibility_context: ExactRawCompatibilityContext,
) -> ExactRawContentResult:
    """Insert/reuse exact content and also report whether this call inserted it."""

    run_source_id = db.scalar(
        select(IngestionRun.source_id).where(
            IngestionRun.id == compatibility_context.ingestion_run_id
        )
    )
    if run_source_id != source_id:
        raise ExactRawContentIntegrityError("raw compatibility run/source context is incoherent")

    body = bytes(entity_body)
    entity_digest = exact_entity_body_sha256(body)
    compatibility_digest = exact_compatibility_payload_hash(
        source_id=source_id,
        entity_body_sha256=entity_digest,
    )
    raw_payload_id = uuid.uuid4()
    values = {
        "id": raw_payload_id,
        "source_id": source_id,
        "ingestion_run_id": compatibility_context.ingestion_run_id,
        "source_url": compatibility_context.source_url,
        "source_endpoint": compatibility_context.source_endpoint,
        "payload_type": _BVC_PRICE_PAYLOAD_TYPE,
        # Deliberately omit `payload`: SQL NULL is required.  Passing Python
        # None through JSONB may persist the JSON literal null instead.
        "payload_text": None,
        "payload_hash": compatibility_digest,
        "entity_body": body,
        "entity_body_sha256": entity_digest,
        "entity_body_length": len(body),
        "content_evidence_kind": EXACT_CONTENT_EVIDENCE_KIND,
        "entity_hash_algorithm": EXACT_ENTITY_HASH_ALGORITHM,
        "storage_status": "stored",
        "legacy_hash_algorithm": EXACT_COMPATIBILITY_HASH_ALGORITHM,
        "http_status": compatibility_context.http_status,
        "content_type": compatibility_context.content_type,
        "collected_at": compatibility_context.collected_at,
        "source_published_at": compatibility_context.source_published_at,
        "status": "collected",
        "error_message": None,
        # No page, run outcome, response headers, or processing state belongs
        # in legacy raw metadata for target exact rows.  Deliberately omit the
        # JSON column so PostgreSQL stores SQL NULL, not JSON literal null.
    }

    dialect_name = db.get_bind().dialect.name
    raw_payloads = RawPayload.__table__
    if dialect_name == "postgresql":
        statement = postgresql_insert(raw_payloads).values(**values)
        statement = statement.on_conflict_do_nothing(
            index_elements=[raw_payloads.c.source_id, raw_payloads.c.entity_body_sha256],
            index_where=raw_payloads.c.content_evidence_kind
            == EXACT_CONTENT_EVIDENCE_KIND,
        ).returning(raw_payloads.c.id)
    elif dialect_name == "sqlite":
        # SQLite is a fast, non-authoritative repository-test fallback.  The
        # production race guarantee is established only by PostgreSQL tests.
        statement = sqlite_insert(raw_payloads).values(**values)
        statement = statement.on_conflict_do_nothing(
            index_elements=[raw_payloads.c.source_id, raw_payloads.c.entity_body_sha256],
        ).returning(raw_payloads.c.id)
    else:  # pragma: no cover - the supported project databases are above
        raise ExactRawContentError(f"unsupported exact-content database dialect: {dialect_name}")

    inserted_id = db.execute(statement).scalar_one_or_none()
    inserted = inserted_id is not None
    lookup = _exact_content_lookup(source_id=source_id, entity_digest=entity_digest)
    raw_payload = db.scalar(lookup)
    if raw_payload is None:
        raise ExactRawContentIntegrityError("exact content conflict did not resolve to a stored row")

    _assert_exact_content_matches(
        raw_payload,
        source_id=source_id,
        entity_body=body,
        entity_digest=entity_digest,
    )
    if inserted and raw_payload.id != inserted_id:
        raise ExactRawContentIntegrityError("inserted exact content resolved to an unexpected row")
    return ExactRawContentResult(raw_payload=raw_payload, inserted=inserted)


def fill_exact_raw_content_text_cache(
    db: Session,
    *,
    raw_payload_id: uuid.UUID,
    source_id: uuid.UUID,
    first_ingestion_run_id: uuid.UUID,
    payload_text: str,
) -> bool:
    """Fill the derived text cache once, only for the first storing context.

    Call this only after exact content plus its occurrence have been durably
    stored and charset decoding has succeeded.  A later duplicate occurrence
    cannot rewrite or repair another run's first-context cache.
    """

    occurrence_exists = (
        select(CollectionOccurrence.id)
        .where(
            CollectionOccurrence.raw_payload_id == raw_payload_id,
            CollectionOccurrence.source_id == source_id,
            CollectionOccurrence.ingestion_run_id == first_ingestion_run_id,
        )
        .exists()
    )
    result = db.execute(
        update(RawPayload)
        .where(
            RawPayload.id == raw_payload_id,
            RawPayload.source_id == source_id,
            RawPayload.ingestion_run_id == first_ingestion_run_id,
            RawPayload.content_evidence_kind == EXACT_CONTENT_EVIDENCE_KIND,
            RawPayload.payload_text.is_(None),
            occurrence_exists,
        )
        .values(payload_text=payload_text)
    )
    return result.rowcount == 1


def _exact_content_lookup(*, source_id: uuid.UUID, entity_digest: str) -> Select[tuple[RawPayload]]:
    return select(RawPayload).where(
        RawPayload.source_id == source_id,
        RawPayload.entity_body_sha256 == entity_digest,
        RawPayload.content_evidence_kind == EXACT_CONTENT_EVIDENCE_KIND,
    )


def _assert_exact_content_matches(
    raw_payload: RawPayload,
    *,
    source_id: uuid.UUID,
    entity_body: bytes,
    entity_digest: str,
) -> None:
    stored_body = None if raw_payload.entity_body is None else bytes(raw_payload.entity_body)
    if (
        raw_payload.source_id != source_id
        or raw_payload.content_evidence_kind != EXACT_CONTENT_EVIDENCE_KIND
        or raw_payload.entity_hash_algorithm != EXACT_ENTITY_HASH_ALGORITHM
        or raw_payload.entity_body_sha256 != entity_digest
        or raw_payload.entity_body_length != len(entity_body)
        or stored_body != entity_body
    ):
        raise ExactRawContentIntegrityError("stored exact content contradicts its declared identity")
