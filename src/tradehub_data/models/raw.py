import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from tradehub_data.models.base import Base
from tradehub_data.models.enums import RAW_CONTENT_EVIDENCE_KINDS, RAW_STORAGE_STATUSES
from tradehub_data.models.mixins import CreatedAtMixin, UUIDPrimaryKeyMixin
from tradehub_data.models.types import JSONBType


class RawPayload(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "raw_payloads"
    __table_args__ = (
        UniqueConstraint("source_id", "payload_hash", name="uq_raw_payloads_source_payload_hash"),
        UniqueConstraint("id", "source_id", name="uq_raw_payloads_id_source_id"),
        CheckConstraint(
            f"content_evidence_kind IN {RAW_CONTENT_EVIDENCE_KINDS!r}",
            name="content_evidence_kind",
        ),
        CheckConstraint(
            f"storage_status = '{RAW_STORAGE_STATUSES[0]}'",
            name="storage_status",
        ),
        CheckConstraint(
            "entity_body_length IS NULL OR entity_body_length >= 0",
            name="entity_body_length_nonnegative",
        ),
        CheckConstraint(
            "content_evidence_kind <> 'exact_entity_bytes' OR "
            "(entity_body IS NOT NULL AND entity_body_sha256 IS NOT NULL "
            "AND entity_body_length IS NOT NULL AND entity_hash_algorithm IS NOT NULL)",
            name="exact_fields_present",
        ),
        CheckConstraint(
            "content_evidence_kind <> 'exact_entity_bytes' OR "
            "entity_hash_algorithm = 'sha256_entity_body_v1'",
            name="exact_hash_algorithm",
        ),
        CheckConstraint(
            "content_evidence_kind = 'exact_entity_bytes' OR "
            "entity_hash_algorithm IS NULL OR "
            "entity_hash_algorithm <> 'sha256_entity_body_v1'",
            name="nonexact_entity_algorithm",
        ),
        CheckConstraint(
            "content_evidence_kind <> 'exact_entity_bytes' OR "
            "entity_body_sha256 ~ '^[0-9a-f]{64}$'",
            name="exact_hash_format",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "content_evidence_kind <> 'exact_entity_bytes' OR "
            "octet_length(entity_body) = entity_body_length",
            name="exact_body_length",
        ).ddl_if(dialect="postgresql"),
        Index(
            "uq_raw_payloads_source_entity_sha256",
            "source_id",
            "entity_body_sha256",
            unique=True,
            postgresql_where=text("content_evidence_kind = 'exact_entity_bytes'"),
        ),
        Index("ix_raw_payloads_source_id", "source_id"),
        Index("ix_raw_payloads_ingestion_run_id", "ingestion_run_id"),
        Index("ix_raw_payloads_payload_hash", "payload_hash"),
        Index("ix_raw_payloads_collected_at", "collected_at"),
        Index("ix_raw_payloads_status", "status"),
    )

    source_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("data_sources.id"),
        nullable=False,
    )
    ingestion_run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("ingestion_runs.id"),
        nullable=True,
    )
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_endpoint: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload_type: Mapped[str] = mapped_column(String(50), nullable=False)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONBType, nullable=True)
    payload_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_body: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    entity_body_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    entity_body_length: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    content_evidence_kind: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        default=lambda context: _legacy_content_evidence_kind(context.get_current_parameters()),
        server_default="legacy_body_missing",
    )
    entity_hash_algorithm: Mapped[str | None] = mapped_column(String(50), nullable=True)
    storage_status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="stored",
        server_default="stored",
    )
    legacy_hash_algorithm: Mapped[str | None] = mapped_column(
        String(80),
        nullable=True,
        default="unknown_legacy",
        server_default="unknown_legacy",
    )
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    content_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONBType, nullable=True)

    source: Mapped["DataSource"] = relationship(back_populates="raw_payloads")
    ingestion_run: Mapped["IngestionRun | None"] = relationship(back_populates="raw_payloads")


def _legacy_content_evidence_kind(parameters: dict[str, Any]) -> str:
    if parameters.get("payload_text") is not None:
        return "legacy_decoded_text"
    # SQLAlchemy's JSON/JSONB type persists explicit Python None as the JSON
    # literal null by default, not as SQL NULL. That is still a stored decoded
    # JSONB representation and must match the migration's `payload IS NOT NULL`
    # classification rule.
    if "payload" in parameters:
        return "legacy_jsonb_only"
    return "legacy_body_missing"
