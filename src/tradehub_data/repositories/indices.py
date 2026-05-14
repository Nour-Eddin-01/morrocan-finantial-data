from sqlalchemy.orm import Session

from tradehub_data.models import IndexBar, LatestIndexValue


def add_latest_index_value(db: Session, values: dict) -> LatestIndexValue:
    latest = LatestIndexValue(**values)
    db.add(latest)
    db.flush()
    return latest


def add_index_bar(db: Session, values: dict) -> IndexBar:
    bar = IndexBar(**values)
    db.add(bar)
    db.flush()
    return bar

