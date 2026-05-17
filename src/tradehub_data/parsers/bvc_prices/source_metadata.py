from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
import re
import unicodedata
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from tradehub_data.parsers.bvc_prices.number_parsing import clean_text, normalize_label

MARKET_TIMEZONE = ZoneInfo("Africa/Casablanca")

FRENCH_MONTHS = {
    "janvier": 1,
    "fevrier": 2,
    "mars": 3,
    "avril": 4,
    "mai": 5,
    "juin": 6,
    "juillet": 7,
    "aout": 8,
    "septembre": 9,
    "octobre": 10,
    "novembre": 11,
    "decembre": 12,
}

FRENCH_DAY_NAMES = (
    "lundi",
    "mardi",
    "mercredi",
    "jeudi",
    "vendredi",
    "samedi",
    "dimanche",
)

_MONTH_PATTERN = "|".join(FRENCH_MONTHS)
_DAY_PATTERN = "|".join(FRENCH_DAY_NAMES)
_TEXTUAL_DATE_RE = re.compile(
    rf"(?:(?:seance|séance|mise a jour|mise à jour|derniere mise a jour|dernière mise à jour)"
    rf"(?:\s+le|\s+du)?\s*)?(?:(?:{_DAY_PATTERN})\s+)?"
    rf"(?P<day>\d{{1,2}})\s+(?P<month>{_MONTH_PATTERN})\s+(?P<year>\d{{4}})"
    rf"(?:\s*(?:a|à)?\s*(?P<hour>\d{{1,2}}):(?P<minute>\d{{2}}))?",
    re.IGNORECASE,
)
_NUMERIC_DATETIME_RE = re.compile(
    r"(?P<day>\d{1,2})/(?P<month>\d{1,2})/(?P<year>\d{4})"
    r"(?:\s+(?P<hour>\d{1,2}):(?P<minute>\d{2}))?"
)


@dataclass(frozen=True)
class BvcSourceDateInfo:
    source_trading_date: date | None = None
    source_timestamp: datetime | None = None
    source_timestamp_raw: str | None = None
    source_timestamp_policy: str = "raw_payload_collected_at_no_source_date"
    raw_date_candidates: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class BvcPaginationInfo:
    pagination_detected: bool = False
    pagination_controls: dict = field(default_factory=dict)
    pagination_warnings: list[str] = field(default_factory=list)


def extract_source_date_info(payload_text: str) -> BvcSourceDateInfo:
    text = _visible_text(payload_text)
    candidates = _date_candidates(text)
    for candidate in candidates:
        parsed = parse_french_source_datetime(candidate)
        if parsed is None:
            continue
        trading_date, timestamp = parsed
        if timestamp is not None:
            return BvcSourceDateInfo(
                source_trading_date=trading_date,
                source_timestamp=timestamp,
                source_timestamp_raw=candidate,
                source_timestamp_policy="source_timestamp",
                raw_date_candidates=candidates,
            )
        return BvcSourceDateInfo(
            source_trading_date=trading_date,
            source_timestamp=None,
            source_timestamp_raw=candidate,
            source_timestamp_policy="trading_date_only",
            raw_date_candidates=candidates,
        )

    return BvcSourceDateInfo(raw_date_candidates=candidates)


def parse_french_source_datetime(value: str) -> tuple[date, datetime | None] | None:
    normalized = _strip_accents(clean_text(value) or "").lower()
    if not normalized:
        return None

    textual = _TEXTUAL_DATE_RE.search(normalized)
    if textual:
        day = int(textual.group("day"))
        month = FRENCH_MONTHS[textual.group("month")]
        year = int(textual.group("year"))
        parsed_date = date(year, month, day)
        if textual.group("hour") and textual.group("minute"):
            return parsed_date, datetime(year, month, day, int(textual.group("hour")), int(textual.group("minute")), tzinfo=MARKET_TIMEZONE)
        return parsed_date, None

    numeric = _NUMERIC_DATETIME_RE.search(normalized)
    if numeric:
        day = int(numeric.group("day"))
        month = int(numeric.group("month"))
        year = int(numeric.group("year"))
        parsed_date = date(year, month, day)
        if numeric.group("hour") and numeric.group("minute"):
            return parsed_date, datetime(year, month, day, int(numeric.group("hour")), int(numeric.group("minute")), tzinfo=MARKET_TIMEZONE)
        return parsed_date, None

    return None


def detect_pagination(payload_text: str, *, rows_detected: int = 0) -> BvcPaginationInfo:
    soup = BeautifulSoup(payload_text, "html.parser")
    controls: dict[str, object] = {
        "current_page": None,
        "visible_page_numbers": [],
        "next_page_hint": None,
        "page_size_hint": None,
        "control_texts": [],
    }

    page_numbers: list[int] = []
    control_texts: list[str] = []
    next_hint: str | None = None

    for element in soup.find_all(["a", "button"]):
        text = clean_text(element.get_text(" ", strip=True)) or ""
        if not text:
            continue
        normalized = normalize_label(text)
        if text.isdigit():
            page_numbers.append(int(text))
            control_texts.append(text)
        elif normalized in {"suivant", "next", ">", "»"} or "suivant" in normalized:
            next_hint = text
            control_texts.append(text)
        elif normalized in {"precedent", "previous", "<", "«"} or "precedent" in normalized:
            control_texts.append(text)

        href = element.get("href")
        if href and re.search(r"(?:page|p)=\d+", href):
            control_texts.append(href)

    for select in soup.find_all("select"):
        text = clean_text(select.get_text(" ", strip=True)) or ""
        if text and re.search(r"\b(25|50|100)\b", text):
            controls["page_size_hint"] = text
            control_texts.append(text)

    page_numbers = sorted(set(page_numbers))
    controls["visible_page_numbers"] = page_numbers
    controls["next_page_hint"] = next_hint
    controls["control_texts"] = list(dict.fromkeys(control_texts))[:20]
    if page_numbers:
        controls["current_page"] = page_numbers[0]

    detected = len(page_numbers) > 1 or next_hint is not None or controls["page_size_hint"] is not None
    warnings: list[str] = []
    if rows_detected == 50 and (detected or len(page_numbers) > 1):
        warnings.append("possible_incomplete_listing")
    if len(page_numbers) > 1:
        warnings.append("multiple_pages_detected")

    return BvcPaginationInfo(
        pagination_detected=detected,
        pagination_controls=controls,
        pagination_warnings=warnings,
    )


def _visible_text(payload_text: str) -> str:
    soup = BeautifulSoup(payload_text, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return clean_text(soup.get_text(" ", strip=True)) or ""


def _date_candidates(text: str) -> list[str]:
    normalized = _strip_accents(text)
    candidates: list[str] = []
    for match in _TEXTUAL_DATE_RE.finditer(normalized):
        candidates.append(text[match.start() : match.end()])
    for match in _NUMERIC_DATETIME_RE.finditer(text):
        candidates.append(match.group(0))
    return list(dict.fromkeys(clean_text(candidate) or "" for candidate in candidates if clean_text(candidate)))


def _strip_accents(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    return "".join(char for char in normalized if not unicodedata.combining(char))
