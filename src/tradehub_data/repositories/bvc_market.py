from datetime import date
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tradehub_data.models import DataSource, Exchange, Instrument, LatestPrice, NormalizationError, PriceBar, RawPayload

BVC_EXCHANGE_CODE = "BVC"
BVC_SOURCE_CODE = "bvc_prices"


def normalize_symbol(symbol: str | None) -> str | None:
    if symbol is None:
        return None
    normalized = symbol.strip().upper()
    return normalized or None


def list_bvc_instruments(
    db: Session,
    *,
    symbol: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[Instrument]:
    statement = (
        select(Instrument)
        .join(Exchange, Instrument.exchange_id == Exchange.id)
        .where(Exchange.code == BVC_EXCHANGE_CODE)
        .order_by(Instrument.symbol.asc())
        .limit(limit)
        .offset(offset)
    )
    normalized_symbol = normalize_symbol(symbol)
    if normalized_symbol:
        statement = statement.where(Instrument.symbol == normalized_symbol)
    return list(db.scalars(statement))


def get_bvc_instrument_by_symbol(db: Session, *, symbol: str) -> Instrument | None:
    normalized_symbol = normalize_symbol(symbol)
    if normalized_symbol is None:
        return None
    return db.scalar(
        select(Instrument)
        .join(Exchange, Instrument.exchange_id == Exchange.id)
        .where(Exchange.code == BVC_EXCHANGE_CODE, Instrument.symbol == normalized_symbol)
    )


def list_bvc_latest_prices(
    db: Session,
    *,
    symbol: str | None = None,
    trading_date: date | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[tuple[LatestPrice, Instrument]]:
    statement = (
        select(LatestPrice, Instrument)
        .join(Instrument, LatestPrice.instrument_id == Instrument.id)
        .join(Exchange, Instrument.exchange_id == Exchange.id)
        .where(Exchange.code == BVC_EXCHANGE_CODE)
        .order_by(Instrument.symbol.asc())
        .limit(limit)
        .offset(offset)
    )
    normalized_symbol = normalize_symbol(symbol)
    if normalized_symbol:
        statement = statement.where(Instrument.symbol == normalized_symbol)
    if trading_date is not None:
        statement = statement.where(LatestPrice.trading_date == trading_date)
    return list(db.execute(statement).all())


def get_latest_price_for_instrument(db: Session, *, instrument_id) -> LatestPrice | None:
    return db.scalar(select(LatestPrice).where(LatestPrice.instrument_id == instrument_id))


def list_bvc_price_bars(
    db: Session,
    *,
    symbol: str,
    timeframe: str = "1d",
    trading_date: date | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[PriceBar]:
    instrument = get_bvc_instrument_by_symbol(db, symbol=symbol)
    if instrument is None:
        return []
    statement = (
        select(PriceBar)
        .where(PriceBar.instrument_id == instrument.id, PriceBar.timeframe == timeframe)
        .order_by(PriceBar.bar_timestamp.desc())
        .limit(limit)
        .offset(offset)
    )
    if trading_date is not None:
        statement = statement.where(PriceBar.trading_date == trading_date)
    return list(db.scalars(statement))


def bvc_data_freshness(db: Session, *, trading_date: date | None = None) -> dict[str, Any]:
    latest_price_statement = (
        select(
            func.max(LatestPrice.price_timestamp),
            func.max(LatestPrice.trading_date),
        )
        .join(Instrument, LatestPrice.instrument_id == Instrument.id)
        .join(Exchange, Instrument.exchange_id == Exchange.id)
        .where(Exchange.code == BVC_EXCHANGE_CODE)
    )
    if trading_date is not None:
        latest_price_statement = latest_price_statement.where(LatestPrice.trading_date == trading_date)
    latest_price_timestamp, latest_trading_date = db.execute(latest_price_statement).one()

    latest_payload = db.scalar(
        select(RawPayload)
        .join(DataSource, RawPayload.source_id == DataSource.id)
        .where(DataSource.code == BVC_SOURCE_CODE)
        .order_by(RawPayload.collected_at.desc(), RawPayload.created_at.desc())
        .limit(1)
    )
    return {
        "latest_collected_at": latest_payload.collected_at if latest_payload else None,
        "latest_price_timestamp": latest_price_timestamp,
        "latest_trading_date": latest_trading_date,
    }


def _metadata_int(metadata: dict[str, Any], keys: tuple[str, ...]) -> int | None:
    for key in keys:
        value = metadata.get(key)
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _latest_bvc_raw_payload(db: Session) -> RawPayload | None:
    return db.scalar(
        select(RawPayload)
        .join(DataSource, RawPayload.source_id == DataSource.id)
        .where(DataSource.code == BVC_SOURCE_CODE)
        .order_by(RawPayload.collected_at.desc(), RawPayload.created_at.desc())
        .limit(1)
    )


def _bvc_raw_payloads(db: Session) -> list[RawPayload]:
    return list(
        db.scalars(
            select(RawPayload)
            .join(DataSource, RawPayload.source_id == DataSource.id)
            .where(DataSource.code == BVC_SOURCE_CODE)
            .order_by(RawPayload.collected_at.desc(), RawPayload.created_at.desc())
        )
    )


def _single_payload_summary(payload: RawPayload | None) -> dict[str, Any]:
    metadata = (payload.metadata_ or {}) if payload else {}
    return {
        "latest_collected_at": payload.collected_at if payload else None,
        "latest_normalized_at": metadata.get("normalized_at"),
        "latest_pagination_group_id": metadata.get("pagination_group_id"),
        "latest_pages_found": metadata.get("pagination_total_pages"),
        "latest_total_rows_detected": _metadata_int(metadata, ("normalization_rows_found", "rows_detected", "page_size")),
        "latest_collection_mode": metadata.get("collection_mode") or metadata.get("loaded_by"),
    }


def _pagination_group_summary(payloads: list[RawPayload], pagination_group_id: str) -> dict[str, Any]:
    group_payloads = [
        payload
        for payload in payloads
        if (payload.metadata_ or {}).get("pagination_group_id") == pagination_group_id
    ]
    if not group_payloads:
        return _single_payload_summary(payloads[0] if payloads else None)

    latest_payload = max(group_payloads, key=lambda payload: (payload.collected_at, payload.created_at))
    latest_metadata = latest_payload.metadata_ or {}
    total_rows_detected = 0
    page_numbers: set[int] = set()
    total_pages: int | None = None
    normalized_values: list[str] = []

    for payload in group_payloads:
        metadata = payload.metadata_ or {}
        total_rows_detected += _metadata_int(metadata, ("normalization_rows_found", "rows_detected", "page_size")) or 0

        page_number = _metadata_int(metadata, ("page_number",))
        if page_number is not None:
            page_numbers.add(page_number)

        metadata_total_pages = _metadata_int(metadata, ("pagination_total_pages",))
        if metadata_total_pages is not None:
            total_pages = max(total_pages or 0, metadata_total_pages)

        normalized_at = metadata.get("normalized_at")
        if normalized_at:
            normalized_values.append(str(normalized_at))

    if page_numbers:
        latest_pages_found = len(page_numbers)
    else:
        latest_pages_found = total_pages or len(group_payloads)

    return {
        "latest_collected_at": latest_payload.collected_at,
        "latest_normalized_at": max(normalized_values) if normalized_values else latest_metadata.get("normalized_at"),
        "latest_pagination_group_id": pagination_group_id,
        "latest_pages_found": latest_pages_found,
        "latest_total_rows_detected": total_rows_detected,
        "latest_collection_mode": latest_metadata.get("collection_mode") or latest_metadata.get("loaded_by"),
    }


def _raw_payloads_summary(db: Session) -> dict[str, Any]:
    payloads = _bvc_raw_payloads(db)
    if not payloads:
        return _single_payload_summary(None)

    latest_group_payload = next(
        (payload for payload in payloads if (payload.metadata_ or {}).get("pagination_group_id")),
        None,
    )
    if latest_group_payload is None:
        return _single_payload_summary(payloads[0])

    pagination_group_id = (latest_group_payload.metadata_ or {}).get("pagination_group_id")
    return _pagination_group_summary(payloads, pagination_group_id)


def bvc_diagnostics_summary(db: Session, *, trading_date: date | None = None) -> dict[str, Any]:
    instruments_count = db.scalar(
        select(func.count(Instrument.id))
        .join(Exchange, Instrument.exchange_id == Exchange.id)
        .where(Exchange.code == BVC_EXCHANGE_CODE)
    ) or 0

    latest_prices_statement = (
        select(func.count(LatestPrice.id))
        .join(Instrument, LatestPrice.instrument_id == Instrument.id)
        .join(Exchange, Instrument.exchange_id == Exchange.id)
        .where(Exchange.code == BVC_EXCHANGE_CODE)
    )
    price_bars_statement = (
        select(func.count(PriceBar.id))
        .join(Instrument, PriceBar.instrument_id == Instrument.id)
        .join(Exchange, Instrument.exchange_id == Exchange.id)
        .where(Exchange.code == BVC_EXCHANGE_CODE)
    )
    if trading_date is not None:
        latest_prices_statement = latest_prices_statement.where(LatestPrice.trading_date == trading_date)
        price_bars_statement = price_bars_statement.where(PriceBar.trading_date == trading_date)

    errors_count = db.scalar(
        select(func.count(NormalizationError.id))
        .join(DataSource, NormalizationError.source_id == DataSource.id)
        .where(DataSource.code == BVC_SOURCE_CODE, NormalizationError.status == "open")
    ) or 0

    latest_prices_count = db.scalar(latest_prices_statement) or 0
    price_bars_count = db.scalar(price_bars_statement) or 0
    freshness = bvc_data_freshness(db, trading_date=trading_date)
    raw_payloads_summary = _raw_payloads_summary(db)

    return {
        "latest_trading_date": freshness["latest_trading_date"],
        "instruments_count": instruments_count,
        "latest_prices_count": latest_prices_count,
        "price_bars_count": price_bars_count,
        "open_normalization_errors_count": errors_count,
        "raw_payloads": raw_payloads_summary,
    }
