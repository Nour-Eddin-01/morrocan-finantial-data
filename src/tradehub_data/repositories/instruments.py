from sqlalchemy import select
from sqlalchemy.orm import Session

from tradehub_data.models import Instrument


def get_instrument_by_symbol(db: Session, *, exchange_id, symbol: str) -> Instrument | None:
    return db.scalar(
        select(Instrument).where(
            Instrument.exchange_id == exchange_id,
            Instrument.symbol == symbol,
        )
    )


def get_instrument_by_isin(db: Session, *, exchange_id, isin: str) -> Instrument | None:
    return db.scalar(
        select(Instrument).where(
            Instrument.exchange_id == exchange_id,
            Instrument.isin == isin,
        )
    )


def upsert_instrument(db: Session, values: dict) -> tuple[Instrument, bool, bool]:
    isin = values.get("isin")
    symbol = values["symbol"]
    exchange_id = values["exchange_id"]

    instrument = get_instrument_by_isin(db, exchange_id=exchange_id, isin=isin) if isin else None
    if instrument is None:
        instrument = get_instrument_by_symbol(db, exchange_id=exchange_id, symbol=symbol)

    if instrument is None:
        instrument = Instrument(**values)
        db.add(instrument)
        db.flush()
        return instrument, True, False

    changed = False
    for key, value in values.items():
        if getattr(instrument, key) != value:
            setattr(instrument, key, value)
            changed = True
    db.flush()
    return instrument, False, changed
