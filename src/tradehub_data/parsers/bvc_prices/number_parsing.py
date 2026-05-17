from decimal import Decimal, InvalidOperation
import re
import unicodedata

from tradehub_data.parsers.bvc_prices.errors import BvcPriceParseError

EMPTY_VALUES = {"", "-", "--", "n/a", "na", "nd", "n.d.", "null"}


def normalize_label(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", ascii_value.strip().lower())


def clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = re.sub(r"\s+", " ", value.replace("\xa0", " ")).strip()
    return cleaned or None


def parse_decimal(value: str | None) -> Decimal | None:
    cleaned = clean_text(value)
    if cleaned is None or cleaned.lower() in EMPTY_VALUES:
        return None

    normalized = cleaned.lower()
    normalized = normalized.replace("%", "")
    normalized = normalized.replace("mad", "")
    normalized = normalized.replace("dh", "")
    normalized = normalized.replace("+", "")
    normalized = normalized.replace("\u202f", " ")
    normalized = normalized.strip()

    if normalized in EMPTY_VALUES:
        return None

    normalized = re.sub(r"\s+", "", normalized)
    if "," in normalized and "." in normalized:
        normalized = normalized.replace(".", "").replace(",", ".")
    elif "," in normalized:
        normalized = normalized.replace(",", ".")

    if not re.fullmatch(r"-?\d+(\.\d+)?", normalized):
        raise BvcPriceParseError(f"invalid decimal value: {value}")

    try:
        return Decimal(normalized)
    except InvalidOperation as exc:
        raise BvcPriceParseError(f"invalid decimal value: {value}") from exc


def parse_int(value: str | None) -> int | None:
    decimal_value = parse_decimal(value)
    if decimal_value is None:
        return None
    if decimal_value != decimal_value.to_integral_value():
        raise BvcPriceParseError(f"invalid integer value: {value}")
    return int(decimal_value)

