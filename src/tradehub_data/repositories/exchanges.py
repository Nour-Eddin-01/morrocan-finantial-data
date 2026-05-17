from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from tradehub_data.models import Exchange


def get_exchange_by_code(db: Session, code: str) -> Exchange | None:
    return db.scalar(select(Exchange).where(Exchange.code == code))


def get_or_create_exchange(
    db: Session,
    *,
    code: str,
    name: str,
    country_code: str,
    currency_code: str,
    timezone: str,
    website_url: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> tuple[Exchange, bool]:
    exchange = get_exchange_by_code(db, code)
    if exchange is not None:
        return exchange, False

    exchange = Exchange(
        code=code,
        name=name,
        country_code=country_code,
        currency_code=currency_code,
        timezone=timezone,
        website_url=website_url,
        metadata_=metadata,
    )
    db.add(exchange)
    db.flush()
    return exchange, True

