from datetime import date
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy.orm import Session

from tradehub_data.db.session import get_db
from tradehub_data.models import Instrument, LatestPrice, PriceBar
from tradehub_data.repositories import bvc_market
from tradehub_data.schemas.bvc_market import (
    BvcDiagnosticsSummaryResponse,
    BvcFreshness,
    BvcInstrumentDetailResponse,
    BvcInstrumentItem,
    BvcInstrumentListResponse,
    BvcLatestPriceItem,
    BvcLatestPriceListResponse,
    BvcPriceBarItem,
    BvcPriceBarListResponse,
)

router = APIRouter(prefix="/api/v1/markets/bvc", tags=["bvc-market"])

LimitParam = Annotated[int, Query(ge=1, le=500)]
OffsetParam = Annotated[int, Query(ge=0)]
SymbolQuery = Annotated[str | None, Query(min_length=1, max_length=30)]
SymbolPath = Annotated[str, Path(min_length=1, max_length=30)]
TimeframeParam = Annotated[Literal["1d"], Query()]


def _decimal_to_string(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None


def _normalized_symbol_or_422(symbol: str) -> str:
    normalized = bvc_market.normalize_symbol(symbol)
    if normalized is None:
        raise HTTPException(status_code=422, detail="symbol must be non-empty")
    return normalized


def _optional_symbol_or_422(symbol: str | None) -> str | None:
    if symbol is None:
        return None
    return _normalized_symbol_or_422(symbol)


def _safe_price_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    if not metadata:
        return {}
    safe_keys = {
        "timestamp_policy",
        "source_timestamp_policy",
        "source_trading_date",
        "source_timestamp",
        "source_timestamp_raw",
        "pagination_group_id",
        "page_number",
        "page_offset",
        "page_limit",
        "collection_mode",
    }
    return {key: metadata[key] for key in safe_keys if key in metadata}


def _instrument_item(instrument: Instrument) -> BvcInstrumentItem:
    return BvcInstrumentItem(
        id=instrument.id,
        symbol=instrument.symbol,
        isin=instrument.isin,
        name=instrument.name,
        instrument_type=instrument.instrument_type,
        currency_code=instrument.currency_code,
        market_segment=instrument.market_segment,
        is_active=instrument.is_active,
    )


def _latest_price_item(latest_price: LatestPrice, instrument: Instrument) -> BvcLatestPriceItem:
    return BvcLatestPriceItem(
        instrument_id=instrument.id,
        symbol=instrument.symbol,
        name=instrument.name,
        price=_decimal_to_string(latest_price.price) or "0",
        open_price=_decimal_to_string(latest_price.open_price),
        high_price=_decimal_to_string(latest_price.high_price),
        low_price=_decimal_to_string(latest_price.low_price),
        previous_close=_decimal_to_string(latest_price.previous_close),
        change_value=_decimal_to_string(latest_price.change_value),
        change_percent=_decimal_to_string(latest_price.change_percent),
        volume=latest_price.volume,
        traded_value=_decimal_to_string(latest_price.traded_value),
        market_cap=_decimal_to_string(latest_price.market_cap),
        price_timestamp=latest_price.price_timestamp,
        trading_date=latest_price.trading_date,
        data_quality_status=latest_price.data_quality_status,
        metadata=_safe_price_metadata(latest_price.metadata_),
    )


def _price_bar_item(price_bar: PriceBar) -> BvcPriceBarItem:
    return BvcPriceBarItem(
        id=price_bar.id,
        timeframe=price_bar.timeframe,
        bar_timestamp=price_bar.bar_timestamp,
        trading_date=price_bar.trading_date,
        open_price=_decimal_to_string(price_bar.open_price),
        high_price=_decimal_to_string(price_bar.high_price),
        low_price=_decimal_to_string(price_bar.low_price),
        close_price=_decimal_to_string(price_bar.close_price) or "0",
        volume=price_bar.volume,
        traded_value=_decimal_to_string(price_bar.traded_value),
        number_of_trades=price_bar.number_of_trades,
        is_adjusted=price_bar.is_adjusted,
        data_quality_status=price_bar.data_quality_status,
        metadata=_safe_price_metadata(price_bar.metadata_),
    )


@router.get("/instruments", response_model=BvcInstrumentListResponse)
def list_instruments(
    symbol: SymbolQuery = None,
    limit: LimitParam = 100,
    offset: OffsetParam = 0,
    db: Session = Depends(get_db),
) -> BvcInstrumentListResponse:
    normalized_symbol = _optional_symbol_or_422(symbol)
    instruments = bvc_market.list_bvc_instruments(db, symbol=normalized_symbol, limit=limit, offset=offset)
    return BvcInstrumentListResponse(
        count=len(instruments),
        limit=limit,
        offset=offset,
        items=[_instrument_item(instrument) for instrument in instruments],
    )


@router.get("/latest-prices", response_model=BvcLatestPriceListResponse)
def list_latest_prices(
    symbol: SymbolQuery = None,
    trading_date: date | None = None,
    limit: LimitParam = 100,
    offset: OffsetParam = 0,
    db: Session = Depends(get_db),
) -> BvcLatestPriceListResponse:
    normalized_symbol = _optional_symbol_or_422(symbol)
    rows = bvc_market.list_bvc_latest_prices(
        db,
        symbol=normalized_symbol,
        trading_date=trading_date,
        limit=limit,
        offset=offset,
    )
    freshness = bvc_market.bvc_data_freshness(db, trading_date=trading_date)
    return BvcLatestPriceListResponse(
        count=len(rows),
        limit=limit,
        offset=offset,
        freshness=BvcFreshness(**freshness),
        items=[_latest_price_item(latest_price, instrument) for latest_price, instrument in rows],
    )


@router.get("/instruments/{symbol}/price-bars", response_model=BvcPriceBarListResponse)
def list_price_bars(
    symbol: SymbolPath,
    timeframe: TimeframeParam = "1d",
    trading_date: date | None = None,
    limit: LimitParam = 100,
    offset: OffsetParam = 0,
    db: Session = Depends(get_db),
) -> BvcPriceBarListResponse:
    normalized_symbol = _normalized_symbol_or_422(symbol)
    instrument = bvc_market.get_bvc_instrument_by_symbol(db, symbol=normalized_symbol)
    if instrument is None:
        raise HTTPException(status_code=404, detail="BVC instrument not found")

    bars = bvc_market.list_bvc_price_bars(
        db,
        symbol=normalized_symbol,
        timeframe=timeframe,
        trading_date=trading_date,
        limit=limit,
        offset=offset,
    )
    return BvcPriceBarListResponse(
        symbol=normalized_symbol,
        timeframe=timeframe,
        count=len(bars),
        limit=limit,
        offset=offset,
        items=[_price_bar_item(price_bar) for price_bar in bars],
    )


@router.get("/instruments/{symbol}", response_model=BvcInstrumentDetailResponse)
def get_instrument(
    symbol: SymbolPath,
    db: Session = Depends(get_db),
) -> BvcInstrumentDetailResponse:
    normalized_symbol = _normalized_symbol_or_422(symbol)
    instrument = bvc_market.get_bvc_instrument_by_symbol(db, symbol=normalized_symbol)
    if instrument is None:
        raise HTTPException(status_code=404, detail="BVC instrument not found")

    latest_price = bvc_market.get_latest_price_for_instrument(db, instrument_id=instrument.id)
    return BvcInstrumentDetailResponse(
        **_instrument_item(instrument).model_dump(),
        latest_price=_latest_price_item(latest_price, instrument) if latest_price else None,
    )


@router.get("/diagnostics/summary", response_model=BvcDiagnosticsSummaryResponse)
def diagnostics_summary(
    trading_date: date | None = None,
    db: Session = Depends(get_db),
) -> BvcDiagnosticsSummaryResponse:
    summary = bvc_market.bvc_diagnostics_summary(db, trading_date=trading_date)
    return BvcDiagnosticsSummaryResponse(
        **summary,
        scheduler_blocked=True,
        live_collection_status="blocked",
        blockers=["Live BVC HTTP collection from Docker/terminal still times out; scheduler remains blocked."],
    )
