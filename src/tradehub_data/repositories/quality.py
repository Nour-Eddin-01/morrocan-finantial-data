from sqlalchemy.orm import Session

from tradehub_data.models import NormalizationError


def record_normalization_error(db: Session, values: dict) -> NormalizationError:
    error = NormalizationError(**values)
    db.add(error)
    db.flush()
    return error

