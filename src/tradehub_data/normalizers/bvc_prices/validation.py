from tradehub_data.normalizers.bvc_prices.errors import BvcPriceNormalizationError
from tradehub_data.parsers.bvc_prices.models import BvcParsedPriceRow


def validate_row(row: BvcParsedPriceRow) -> str:
    if row.last_price is None:
        raise BvcPriceNormalizationError("missing latest price")
    if row.source_symbol is None and row.isin is None:
        raise BvcPriceNormalizationError("missing instrument identifier")
    if row.source_name is None and row.source_symbol is None:
        raise BvcPriceNormalizationError("missing instrument name")
    if row.trading_date is None:
        raise BvcPriceNormalizationError("missing trading date")

    for field_name in (
        "last_price",
        "open_price",
        "high_price",
        "low_price",
        "previous_close",
        "volume",
        "traded_value",
        "market_cap",
        "number_of_trades",
    ):
        value = getattr(row, field_name)
        if value is not None and value < 0:
            raise BvcPriceNormalizationError(f"{field_name} cannot be negative")

    if row.high_price is not None and row.low_price is not None and row.high_price < row.low_price:
        return "suspect"
    if row.high_price is not None:
        if row.open_price is not None and row.high_price < row.open_price:
            return "suspect"
        if row.last_price is not None and row.high_price < row.last_price:
            return "suspect"
    if row.low_price is not None:
        if row.open_price is not None and row.low_price > row.open_price:
            return "suspect"
        if row.last_price is not None and row.low_price > row.last_price:
            return "suspect"

    return "valid"
