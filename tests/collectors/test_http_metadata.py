import json

import httpx
import pytest

from tradehub_data.collectors.http_metadata import (
    BVC_SAFE_RESPONSE_HEADERS_POLICY_VERSION,
    UnsafeBvcUrlError,
    filter_safe_response_headers,
    safe_bvc_fixture_logical_identifier,
    sanitize_bvc_http_url,
)


def test_all_safe_headers_are_canonical_lowercase_arrays():
    result = filter_safe_response_headers(
        [
            ("Content-Type", "application/json"),
            ("CONTENT-LENGTH", "123"),
            ("Content-Encoding", "gzip"),
            ("ETag", '"abc"'),
            ("Last-Modified", "Mon, 20 Jul 2026 10:00:00 GMT"),
            ("Date", "Mon, 20 Jul 2026 10:01:00 GMT"),
            ("Cache-Control", "private"),
            ("cache-control", "max-age=0"),
        ]
    )

    assert result.safe_response_headers == {
        "cache-control": ["private", "max-age=0"],
        "content-encoding": ["gzip"],
        "content-length": ["123"],
        "content-type": ["application/json"],
        "date": ["Mon, 20 Jul 2026 10:01:00 GMT"],
        "etag": ['"abc"'],
        "last-modified": ["Mon, 20 Jul 2026 10:00:00 GMT"],
    }
    assert result.dropped_response_header_name_count == 0
    assert result.response_headers_overflow is False
    assert result.policy_version == BVC_SAFE_RESPONSE_HEADERS_POLICY_VERSION


@pytest.mark.parametrize(
    "denied_name",
    (
        "Set-Cookie",
        "Cookie",
        "Authorization",
        "Proxy-Authorization",
        "X-CSRF-Token",
        "X-WAF-Identifier",
        "X-Session-Id",
        "X-Private-Security-Token",
    ),
)
def test_denied_and_unknown_headers_are_counted_but_never_returned(denied_name):
    secret = "do-not-store-this-private-value"
    result = filter_safe_response_headers(
        [
            (denied_name, secret),
            (denied_name.lower(), secret),
            ("X-Unknown", secret),
            ("Content-Type", "application/json"),
        ]
    )

    serialized = json.dumps(result.safe_response_headers, sort_keys=True)
    assert result.safe_response_headers == {"content-type": ["application/json"]}
    assert result.dropped_response_header_name_count == 2
    assert secret not in serialized
    assert denied_name.lower() not in serialized.lower()
    assert "x-unknown" not in serialized.lower()


def test_denylist_precedes_allowlist(monkeypatch):
    # Simulate a future accidental allowlist overlap without weakening the
    # production constant.  Denied semantics must still win.
    import tradehub_data.collectors.http_metadata as metadata

    monkeypatch.setattr(
        metadata,
        "BVC_SAFE_RESPONSE_HEADER_NAMES",
        frozenset({"x-session-token"}),
    )
    result = metadata.filter_safe_response_headers(
        [("X-Session-Token", "private")]
    )

    assert result.safe_response_headers == {}
    assert result.dropped_response_header_name_count == 1


@pytest.mark.parametrize("name", ("content-type", "etag", "date"))
def test_repeated_singleton_drops_the_whole_name(name):
    result = filter_safe_response_headers([(name, "first"), (name.upper(), "second")])

    assert result.safe_response_headers == {}
    assert result.dropped_response_header_name_count == 1
    assert result.response_headers_overflow is False


def test_duplicate_capable_header_collection_is_not_silently_collapsed():
    headers = httpx.Headers([("ETag", "first"), ("ETag", "second")])

    result = filter_safe_response_headers(headers)

    assert result.safe_response_headers == {}
    assert result.dropped_response_header_name_count == 1


def test_cache_control_preserves_received_order_and_rejects_more_than_sixteen_values():
    ordered = [("cache-control", f"directive-{index}") for index in range(16)]
    accepted = filter_safe_response_headers(ordered)
    rejected = filter_safe_response_headers(
        [*ordered, ("cache-control", "directive-16")]
    )

    assert accepted.safe_response_headers["cache-control"] == [
        f"directive-{index}" for index in range(16)
    ]
    assert rejected.safe_response_headers == {}
    assert rejected.dropped_response_header_name_count == 1


@pytest.mark.parametrize(
    "unsafe_value",
    (
        "contains\nnewline",
        "contains\rcarriage-return",
        "contains\ttab",
        "contains\x00null",
        "x" * 2_049,
        "é" * 1_025,
    ),
)
def test_invalid_or_oversized_value_drops_the_whole_name(unsafe_value):
    result = filter_safe_response_headers([("etag", unsafe_value)])

    assert result.safe_response_headers == {}
    assert result.dropped_response_header_name_count == 1


def test_canonical_aggregate_overflow_is_all_or_nothing_and_counts_distinct_names():
    # Four values individually remain under 2,048 bytes, while their canonical
    # JSON aggregate exceeds 8,192 bytes.
    headers = [
        ("cache-control", "a" * 2_040),
        ("cache-control", "b" * 2_040),
        ("cache-control", "c" * 2_040),
        ("cache-control", "d" * 2_040),
        ("etag", "e" * 100),
        ("x-unknown", "not-retained"),
    ]
    forward = filter_safe_response_headers(headers)
    reverse = filter_safe_response_headers(reversed(headers))

    assert forward.safe_response_headers == {}
    assert forward.response_headers_overflow is True
    assert forward.dropped_response_header_name_count == 3
    assert reverse.safe_response_headers == {}
    assert reverse.response_headers_overflow is True
    assert reverse.dropped_response_header_name_count == 3


def test_dropped_count_is_distinct_normalized_names():
    result = filter_safe_response_headers(
        [
            ("X-Unknown", "one"),
            ("x-unknown", "two"),
            ("Set-Cookie", "one"),
            ("SET-COOKIE", "two"),
            ("Content-Type", "one"),
            ("content-type", "two"),
        ]
    )

    assert result.safe_response_headers == {}
    assert result.dropped_response_header_name_count == 3


def test_url_sanitizer_removes_userinfo_fragment_and_private_query_material():
    secret = "never-persist-this"
    sanitized = sanitize_bvc_http_url(
        "HTTPS://user:password@WWW.CASABLANCA-BOURSE.COM:443/prices"
        f"?page%5Boffset%5D=00050&token={secret}&page%5Blimit%5D=050"
        f"#fragment-{secret}"
    )

    assert sanitized == (
        "https://www.casablanca-bourse.com/prices?"
        "page%5Blimit%5D=50&page%5Boffset%5D=50"
    )
    assert "user" not in sanitized
    assert "password" not in sanitized
    assert secret not in sanitized
    assert "fragment" not in sanitized
    assert "token" not in sanitized


def test_url_sanitizer_retains_only_numeric_public_pagination_values():
    sanitized = sanitize_bvc_http_url(
        "https://casablanca-bourse.com/api?offset=10&limit=20&"
        "page%5Boffset%5D=-1&page%5Blimit%5D=secret&other=30"
    )

    assert sanitized == "https://casablanca-bourse.com/api?limit=20&offset=10"


def test_url_sanitizer_is_deterministic_for_query_order_default_port_and_percent_case():
    first = sanitize_bvc_http_url(
        "https://www.casablanca-bourse.com:443/a%2fb?offset=000&limit=050"
    )
    second = sanitize_bvc_http_url(
        "https://www.casablanca-bourse.com/a%2Fb?limit=50&offset=0"
    )

    assert first == second
    assert first == "https://www.casablanca-bourse.com/a%2Fb?limit=50&offset=0"


@pytest.mark.parametrize(
    "unsafe_url",
    (
        "ftp://www.casablanca-bourse.com/prices",
        "https://example.com/prices",
        "https://evil.casablanca-bourse.com/prices",
        "https://www.casablanca-bourse.com:invalid/prices",
        "https://www.casablanca-bourse.com/prices?offset=%ZZ",
        "https://www.casablanca-bourse.com\\@example.com/prices",
        "not-a-url",
    ),
)
def test_url_sanitizer_rejects_malformed_or_disallowed_urls_without_echo(unsafe_url):
    with pytest.raises(UnsafeBvcUrlError) as caught:
        sanitize_bvc_http_url(unsafe_url)

    assert str(caught.value) == "URL is not safe for BVC audit persistence"
    assert unsafe_url not in str(caught.value)


def test_url_sanitizer_uses_an_exact_configured_host_allowlist():
    assert sanitize_bvc_http_url(
        "https://prices.casablanca-bourse.com/page?limit=5",
        allowed_hosts=("prices.casablanca-bourse.com",),
    ) == "https://prices.casablanca-bourse.com/page?limit=5"

    with pytest.raises(UnsafeBvcUrlError):
        sanitize_bvc_http_url(
            "https://www.prices.casablanca-bourse.com/page?limit=5",
            allowed_hosts=("prices.casablanca-bourse.com",),
        )


def test_url_sanitizer_discards_unknown_or_valueless_query_fields():
    sanitized = sanitize_bvc_http_url(
        "https://www.casablanca-bourse.com/prices?opaque&offset&limit=20"
    )

    assert sanitized == "https://www.casablanca-bourse.com/prices?limit=20"


def test_fixture_logical_identifier_is_stable_and_contains_no_local_path():
    identifier = safe_bvc_fixture_logical_identifier()

    assert identifier == "manual-fixture://bvc-equity-prices"
    assert "/home/" not in identifier
    assert "\\" not in identifier
    assert identifier.count("/") == 2
