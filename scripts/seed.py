from sqlalchemy.orm import Session

from tradehub_data.db.session import SessionLocal
from tradehub_data.models import DataSource, Exchange


def seed(db: Session) -> None:
    if not db.query(DataSource).filter_by(code="bvc_market_data").one_or_none():
        db.add(
            DataSource(
                code="bvc_market_data",
                name="Bourse de Casablanca Market Data",
                source_type="exchange",
                base_url="https://www.casablanca-bourse.com",
                country_code="MA",
                priority=100,
                metadata_={"official": True},
            )
        )

    if not db.query(Exchange).filter_by(code="BVC").one_or_none():
        db.add(
            Exchange(
                code="BVC",
                name="Casablanca Stock Exchange",
                country_code="MA",
                currency_code="MAD",
                timezone="Africa/Casablanca",
                website_url="https://www.casablanca-bourse.com",
            )
        )


def main() -> None:
    with SessionLocal() as db:
        seed(db)
        db.commit()


if __name__ == "__main__":
    main()

