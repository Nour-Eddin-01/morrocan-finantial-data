"""Safe, deterministic HTTP metadata retained by collection auditing.

The helpers in this module are deliberately pure.  They never log rejected
metadata and never include a rejected header name, value, or URL component in
an exception message.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
import json
import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from tradehub_data.collectors.bvc_prices.constants import DEFAULT_ALLOWED_DOMAINS


BVC_SAFE_RESPONSE_HEADERS_POLICY_VERSION = "bvc-safe-response-headers-v1"
BVC_SAFE_RESPONSE_HEADER_NAMES = frozenset(
    {
        "content-type",
        "content-length",
        "content-encoding",
        "etag",
        "last-modified",
        "date",
        "cache-control",
    }
)
BVC_SINGLETON_RESPONSE_HEADER_NAMES = frozenset(
    {
        "content-type",
        "content-length",
        "content-encoding",
        "etag",
        "last-modified",
        "date",
    }
)
BVC_PUBLIC_PAGINATION_QUERY_KEYS = frozenset(
    {"page[offset]", "page[limit]", "offset", "limit"}
)

_MAX_HEADER_VALUE_UTF8_BYTES = 2_048
_MAX_CACHE_CONTROL_VALUES = 16
_MAX_CANONICAL_HEADERS_UTF8_BYTES = 8_192
_DENIED_HEADER_NAME_PARTS = (
    "authorization",
    "cookie",
    "credential",
    "csrf",
    "private",
    "secret",
    "security",
    "session",
    "token",
    "waf",
    "xsrf",
)
_PERCENT_ESCAPE = re.compile(r"%[0-9a-fA-F]{2}")
_LOWERCASE_PERCENT_ESCAPE = re.compile(r"%([0-9a-fA-F]{2})")
_ASCII_DIGITS = re.compile(r"^[0-9]+$")
_FIXTURE_LOGICAL_IDENTIFIER = "manual-fixture://bvc-equity-prices"


@dataclass(frozen=True, slots=True)
class SafeResponseHeaders:
    """The complete bounded header result suitable for occurrence storage."""

    safe_response_headers: dict[str, list[str]]
    dropped_response_header_name_count: int
    response_headers_overflow: bool
    policy_version: str = field(
        default=BVC_SAFE_RESPONSE_HEADERS_POLICY_VERSION,
        init=False,
    )


class UnsafeBvcUrlError(ValueError):
    """Raised without echoing any unsafe URL material."""


def filter_safe_response_headers(
    headers: Mapping[str, str] | Iterable[tuple[str, str]],
) -> SafeResponseHeaders:
    """Apply the closed BVC response-header retention policy.

    A duplicate-capable collection exposing ``multi_items()`` is consumed
    through that interface.  A plain mapping cannot represent repeated
    singleton fields and is therefore treated as one instance per name.
    """

    values_by_name: dict[str, list[str]] = defaultdict(list)
    dropped_names: set[str] = set()

    multi_items = getattr(headers, "multi_items", None)
    if callable(multi_items):
        items = multi_items()
    elif isinstance(headers, Mapping):
        items = headers.items()
    else:
        items = headers
    for raw_name, raw_value in items:
        normalized_name = raw_name.lower()

        # Deny checks intentionally precede the allowlist.  Rejected material
        # remains local to this function and only its distinct-name count is
        # returned.
        if _is_denied_header_name(normalized_name):
            dropped_names.add(normalized_name)
            continue
        if normalized_name not in BVC_SAFE_RESPONSE_HEADER_NAMES:
            dropped_names.add(normalized_name)
            continue

        values_by_name[normalized_name].append(raw_value)

    retained: dict[str, list[str]] = {}
    for name in sorted(values_by_name):
        values = values_by_name[name]
        if name in BVC_SINGLETON_RESPONSE_HEADER_NAMES and len(values) != 1:
            dropped_names.add(name)
            continue
        if name == "cache-control" and len(values) > _MAX_CACHE_CONTROL_VALUES:
            dropped_names.add(name)
            continue
        if any(not _is_safe_header_value(value) for value in values):
            dropped_names.add(name)
            continue
        retained[name] = list(values)

    canonical = json.dumps(
        retained,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    if len(canonical) > _MAX_CANONICAL_HEADERS_UTF8_BYTES:
        dropped_names.update(retained)
        return SafeResponseHeaders(
            safe_response_headers={},
            dropped_response_header_name_count=len(dropped_names),
            response_headers_overflow=True,
        )

    return SafeResponseHeaders(
        safe_response_headers=retained,
        dropped_response_header_name_count=len(dropped_names),
        response_headers_overflow=False,
    )


def sanitize_bvc_http_url(
    url: str,
    *,
    allowed_hosts: Iterable[str] = DEFAULT_ALLOWED_DOMAINS,
) -> str:
    """Return a safe canonical BVC HTTP URL for audit persistence.

    Host matching is exact after lowercase IDNA normalization.  User
    information and fragments are always removed.  Query persistence is
    restricted to nonnegative ASCII-decimal pagination values.
    """

    try:
        if not url or _contains_control_character(url) or "\\" in url:
            raise ValueError
        if _has_invalid_percent_escape(url):
            raise ValueError

        parsed = urlsplit(url)
        scheme = parsed.scheme.lower()
        if scheme not in {"http", "https"} or parsed.hostname is None:
            raise ValueError

        host = _normalize_host(parsed.hostname)
        normalized_allowed_hosts = {_normalize_host(value) for value in allowed_hosts}
        if host not in normalized_allowed_hosts:
            raise ValueError

        # Accessing .port performs urllib's numeric/range validation.
        port = parsed.port
        if ":" in host:
            netloc = f"[{host}]"
        else:
            netloc = host
        if port is not None and not (
            (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
        ):
            netloc = f"{netloc}:{port}"

        safe_query_pairs: list[tuple[str, str]] = []
        for name, value in parse_qsl(
            parsed.query,
            keep_blank_values=True,
            # Unknown valueless fields are private/irrelevant input and must
            # be discarded, not allowed to make an otherwise auditable BVC
            # response URL fail sanitization after its body was received.
            strict_parsing=False,
        ):
            if name not in BVC_PUBLIC_PAGINATION_QUERY_KEYS:
                continue
            if _ASCII_DIGITS.fullmatch(value) is None:
                continue
            canonical_value = value.lstrip("0") or "0"
            safe_query_pairs.append((name, canonical_value))

        safe_query_pairs.sort()
        safe_query = urlencode(safe_query_pairs)
        path = _normalize_percent_escapes(parsed.path or "/")
        return urlunsplit((scheme, netloc, path, safe_query, ""))
    except (TypeError, UnicodeError, ValueError):
        # The message is intentionally constant: it cannot disclose removed
        # userinfo, query values, fragments, or a disallowed host.
        raise UnsafeBvcUrlError("URL is not safe for BVC audit persistence") from None


def safe_bvc_fixture_logical_identifier() -> str:
    """Return a stable fixture identifier that cannot contain a local path."""

    return _FIXTURE_LOGICAL_IDENTIFIER


def _is_denied_header_name(name: str) -> bool:
    return any(part in name for part in _DENIED_HEADER_NAME_PARTS)


def _is_safe_header_value(value: str) -> bool:
    if _contains_control_character(value):
        return False
    return len(value.encode("utf-8")) <= _MAX_HEADER_VALUE_UTF8_BYTES


def _contains_control_character(value: str) -> bool:
    return any(unicodedata.category(character) == "Cc" for character in value)


def _has_invalid_percent_escape(value: str) -> bool:
    index = 0
    while True:
        index = value.find("%", index)
        if index < 0:
            return False
        if _PERCENT_ESCAPE.fullmatch(value[index : index + 3]) is None:
            return True
        index += 3


def _normalize_percent_escapes(value: str) -> str:
    return _LOWERCASE_PERCENT_ESCAPE.sub(lambda match: f"%{match.group(1).upper()}", value)


def _normalize_host(value: str) -> str:
    normalized = value.rstrip(".").encode("idna").decode("ascii").lower()
    if not normalized or _contains_control_character(normalized):
        raise ValueError
    return normalized
