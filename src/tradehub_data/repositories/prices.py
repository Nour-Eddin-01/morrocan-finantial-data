from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from tradehub_data.models import LatestPrice, PriceBar


def upsert_latest_price(db: Session, values: dict) -> LatestPrice:
    stmt = insert(LatestPrice).values(**values)
    update_values = {
        key: stmt.excluded[key]
        for key in values
        if key not in {"id", "instrument_id", "created_at"}
    }
    stmt = stmt.on_conflict_do_update(
        index_elements=[LatestPrice.instrument_id],
        set_=update_values,
    ).returning(LatestPrice)
    return db.scalar(stmt)


def add_price_bar(db: Session, values: dict) -> PriceBar:
    price_bar = PriceBar(**values)
    db.add(price_bar)
    db.flush()
    return price_bar

