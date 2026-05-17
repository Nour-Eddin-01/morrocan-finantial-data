from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from tradehub_data.models import LatestPrice, PriceBar


def get_latest_price_by_instrument(db: Session, *, instrument_id) -> LatestPrice | None:
    return db.scalar(select(LatestPrice).where(LatestPrice.instrument_id == instrument_id))


def upsert_latest_price(db: Session, values: dict) -> tuple[LatestPrice, bool, bool]:
    latest_price = get_latest_price_by_instrument(db, instrument_id=values["instrument_id"])
    if latest_price is None:
        latest_price = LatestPrice(**values)
        db.add(latest_price)
        db.flush()
        return latest_price, True, False

    if _as_utc_naive(latest_price.price_timestamp) > _as_utc_naive(values["price_timestamp"]):
        return latest_price, False, False

    changed = False
    for key, value in values.items():
        if key == "instrument_id":
            continue
        if getattr(latest_price, key) != value:
            setattr(latest_price, key, value)
            changed = True
    db.flush()
    return latest_price, False, changed


def get_price_bar(db: Session, *, instrument_id, timeframe: str, bar_timestamp) -> PriceBar | None:
    return db.scalar(
        select(PriceBar).where(
            PriceBar.instrument_id == instrument_id,
            PriceBar.timeframe == timeframe,
            PriceBar.bar_timestamp == bar_timestamp,
        )
    )


def upsert_price_bar(db: Session, values: dict) -> tuple[PriceBar, bool, bool]:
    price_bar = get_price_bar(
        db,
        instrument_id=values["instrument_id"],
        timeframe=values["timeframe"],
        bar_timestamp=values["bar_timestamp"],
    )
    if price_bar is None:
        price_bar = PriceBar(**values)
        db.add(price_bar)
        db.flush()
        return price_bar, True, False

    changed = False
    for key, value in values.items():
        if key in {"instrument_id", "timeframe", "bar_timestamp"}:
            continue
        if getattr(price_bar, key) != value:
            setattr(price_bar, key, value)
            changed = True
    db.flush()
    return price_bar, False, changed


def add_price_bar(db: Session, values: dict) -> PriceBar:
    price_bar, _, _ = upsert_price_bar(db, values)
    return price_bar


def _as_utc_naive(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)
