import json
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from tradehub_data.parsers.bvc_prices.json_parser import parse_bvc_market_listing_json


def json_payload(rows):
    return json.dumps({"data": {"data": rows}})


def market_row(symbol: str = "ABC"):
    return {
        "type": "market_watch",
        "id": symbol,
        "attributes": {
            "code": f"{symbol}-token",
            "lastTradedPrice": "123.4500000000",
            "openingPrice": "120.0000000000",
            "highPrice": "125.0000000000",
            "lowPrice": "119.0000000000",
            "staticReferencePrice": "121.0000000000",
            "varVeille": "1.2300000000",
            "difference": "1.5000000000",
            "cumulTitresEchanges": "1000.0000000000",
            "cumulVolumeEchange": "123450.5000000000",
            "capitalisation": "999999.0000000000",
            "totalTrades": 7,
            "transactTime": "2026-05-18T16:00:00+00:00",
        },
    }


def test_bvc_json_parser_falls_back_to_cours_courant_when_last_traded_price_is_null():
    row = market_row("AFM")
    row["attributes"]["lastTradedPrice"] = None
    row["attributes"]["coursCourant"] = "1240.0000000000"
    row["attributes"]["etatCotVal"] = "N.T"

    result = parse_bvc_market_listing_json(
        raw_payload_id=uuid4(),
        payload_text=json_payload([row]),
        collected_at=datetime(2026, 5, 19, 12, 0, tzinfo=UTC),
    )

    assert result.errors == []
    parsed = result.rows[0]
    assert parsed.last_price == Decimal("1240.0000000000")
    assert isinstance(parsed.last_price, Decimal)
    assert parsed.raw_values["source_status"] == "N.T"


def test_bvc_json_parser_falls_back_to_cours_courant_when_last_traded_price_is_empty():
    row = market_row("SAM")
    row["attributes"]["lastTradedPrice"] = " "
    row["attributes"]["coursCourant"] = "127.8000000000"
    row["attributes"]["etatCotVal"] = "S"

    result = parse_bvc_market_listing_json(
        raw_payload_id=uuid4(),
        payload_text=json_payload([row]),
        collected_at=datetime(2026, 5, 19, 12, 0, tzinfo=UTC),
    )

    assert result.errors == []
    parsed = result.rows[0]
    assert parsed.last_price == Decimal("127.8000000000")
    assert isinstance(parsed.last_price, Decimal)
    assert parsed.raw_values["source_status"] == "S"


def test_bvc_json_parser_maps_market_watch_records_with_decimal_values():
    result = parse_bvc_market_listing_json(
        raw_payload_id=uuid4(),
        payload_text=json_payload([market_row("ABC")]),
        collected_at=datetime(2026, 5, 18, 12, 0, tzinfo=UTC),
    )

    assert result.source_trading_date.isoformat() == "2026-05-18"
    assert result.source_timestamp.isoformat() == "2026-05-18T16:00:00+00:00"
    assert result.source_timestamp_policy == "source_timestamp"
    assert len(result.rows) == 1
    assert result.errors == []

    row = result.rows[0]
    assert row.source_symbol == "ABC"
    assert row.source_name == "ABC"
    assert row.last_price == Decimal("123.4500000000")
    assert row.open_price == Decimal("120.0000000000")
    assert row.high_price == Decimal("125.0000000000")
    assert row.low_price == Decimal("119.0000000000")
    assert row.previous_close == Decimal("121.0000000000")
    assert row.change_value == Decimal("1.5000000000")
    assert row.change_percent == Decimal("1.2300000000")
    assert row.volume == 1000
    assert row.traded_value == Decimal("123450.5000000000")
    assert row.market_cap == Decimal("999999.0000000000")
    assert row.number_of_trades == 7


def test_bvc_json_parser_records_invalid_rows_without_silently_normalizing():
    bad_row = market_row("BAD")
    bad_row["attributes"]["lastTradedPrice"] = "not-a-number"

    result = parse_bvc_market_listing_json(
        raw_payload_id=uuid4(),
        payload_text=json_payload([market_row("OK"), bad_row]),
        collected_at=datetime(2026, 5, 18, 12, 0, tzinfo=UTC),
    )

    assert len(result.rows) == 1
    assert len(result.errors) == 1
    assert result.errors[0].row_index == 1
    assert "invalid decimal value" in result.errors[0].error_message
