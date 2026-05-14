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

