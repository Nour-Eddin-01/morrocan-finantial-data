from sqlalchemy import select
from sqlalchemy.orm import Session

from tradehub_data.models import NormalizationError
from tradehub_data.repositories.quality import record_normalization_error


def create_normalization_error(db: Session, values: dict) -> NormalizationError:
    existing = list(
        db.scalars(
            select(NormalizationError).where(
                NormalizationError.raw_payload_id == values.get("raw_payload_id"),
                NormalizationError.entity_type == values.get("entity_type"),
                NormalizationError.error_type == values["error_type"],
                NormalizationError.error_message == values["error_message"],
                NormalizationError.status == values.get("status", "open"),
            )
        )
    )
    for error in existing:
        if error.raw_fragment == values.get("raw_fragment"):
            return error
    return record_normalization_error(db, values)
