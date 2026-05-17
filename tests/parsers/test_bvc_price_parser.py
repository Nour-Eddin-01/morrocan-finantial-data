from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest

from tradehub_data.parsers.bvc_prices.errors import BvcPriceParseError
from tradehub_data.parsers.bvc_prices.html_parser import parse_bvc_market_listing_html
from tradehub_data.parsers.bvc_prices.number_parsing import parse_decimal, parse_int
from tradehub_data.parsers.bvc_prices.source_metadata import parse_french_source_datetime


def test_french_source_date_parser_handles_textual_date():
    parsed = parse_french_source_datetime("vendredi 15 mai 2026")

    assert parsed is not None
    assert parsed[0] == date(2026, 5, 15)
    assert parsed[1] is None


def test_french_source_date_parser_handles_textual_date_without_day_name():
    parsed = parse_french_source_datetime("15 mai 2026")

    assert parsed is not None
    assert parsed[0] == date(2026, 5, 15)
    assert parsed[1] is None


def test_french_source_date_parser_handles_numeric_datetime():
    parsed = parse_french_source_datetime("15/05/2026 12:34")

    assert parsed is not None
    assert parsed[0] == date(2026, 5, 15)
    assert parsed[1] is not None
    assert parsed[1].hour == 12
    assert parsed[1].minute == 34


def test_french_source_date_parser_handles_update_sentence():
    parsed = parse_french_source_datetime("Mise à jour le vendredi 15 mai 2026 à 12:34")

    assert parsed is not None
    assert parsed[0] == date(2026, 5, 15)
    assert parsed[1] is not None
    assert parsed[1].hour == 12
    assert parsed[1].minute == 34


def test_bvc_parser_parses_sample_fixture():
    fixture = Path("fixtures/bvc_prices/sample_market_listing.html")

    result = parse_bvc_market_listing_html(
        raw_payload_id=uuid4(),
        payload_text=fixture.read_text(encoding="utf-8"),
        collected_at=datetime(2026, 5, 15, 12, 0, tzinfo=UTC),
    )

    assert len(result.rows) == 3
    assert result.errors == []
    row = result.rows[0]
    assert row.source_name == "SAMPLE BANK"
    assert row.source_symbol == "SBK"
    assert row.isin == "MA0000000001"
    assert row.last_price == Decimal("123.45")
    assert row.open_price == Decimal("120.00")
    assert row.high_price == Decimal("125.00")
    assert row.low_price == Decimal("119.50")
    assert row.change_percent == Decimal("1.23")
    assert row.volume == 10000
    assert row.traded_value == Decimal("1234500.00")
    assert row.raw_values["last_price"] == "123,45"

    optional_row = result.rows[1]
    assert optional_row.source_symbol == "OFS"
    assert optional_row.isin is None
    assert optional_row.last_price == Decimal("45.10")
    assert optional_row.open_price is None
    assert optional_row.high_price is None
    assert optional_row.low_price is None
    assert optional_row.change_percent is None
    assert optional_row.volume == 2500
    assert optional_row.traded_value is None

    invalid_row = result.rows[2]
    assert invalid_row.source_symbol is None
    assert invalid_row.isin is None
    assert invalid_row.last_price == Decimal("11.00")


def test_bvc_parser_extracts_symbol_from_instrument_link():
    result = parse_bvc_market_listing_html(
        raw_payload_id=uuid4(),
        payload_text="""
        <table>
          <thead>
            <tr>
              <th>Instrument</th>
              <th>Cours de référence</th>
              <th>Ouverture</th>
              <th>Dernier cours</th>
              <th>Quantité échangée</th>
              <th>Volume</th>
              <th>Variation en %</th>
              <th>+ haut jour</th>
              <th>+ bas jour</th>
              <th>Capitalisation</th>
              <th>Nombre de transactions</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td><a href="/fr/live-market/instruments/ATW?csrt=token">ATTIJARIWAFA BANK</a></td>
              <td>686,00</td>
              <td>687,00</td>
              <td>685,00</td>
              <td>10 312</td>
              <td>7 063 382,80</td>
              <td>-0,15 %</td>
              <td>687,00</td>
              <td>685,00</td>
              <td>147 371 474 715,00</td>
              <td>153</td>
            </tr>
          </tbody>
        </table>
        """,
        collected_at=datetime(2026, 5, 15, 12, 0, tzinfo=UTC),
    )

    assert result.errors == []
    row = result.rows[0]
    assert row.source_name == "ATTIJARIWAFA BANK"
    assert row.source_symbol == "ATW"
    assert row.previous_close == Decimal("686.00")
    assert row.open_price == Decimal("687.00")
    assert row.last_price == Decimal("685.00")
    assert row.volume == 10312
    assert row.traded_value == Decimal("7063382.80")
    assert row.change_percent == Decimal("-0.15")
    assert row.high_price == Decimal("687.00")
    assert row.low_price == Decimal("685.00")
    assert row.market_cap == Decimal("147371474715.00")
    assert row.number_of_trades == 153


def test_bvc_parser_parses_real_market_listing_fixture():
    fixture = Path("fixtures/bvc_prices/real/bvc_market_listing_20260515_1200.html")

    result = parse_bvc_market_listing_html(
        raw_payload_id=uuid4(),
        payload_text=fixture.read_text(encoding="utf-8"),
        collected_at=datetime(2026, 5, 15, 12, 0, tzinfo=UTC),
    )

    assert len(result.rows) == 50
    assert result.errors == []
    atw = next(row for row in result.rows if row.source_symbol == "ATW")
    assert atw.source_name == "ATTIJARIWAFA BANK"
    assert atw.previous_close == Decimal("686.00")
    assert atw.open_price == Decimal("687.00")
    assert atw.last_price == Decimal("685.00")
    assert atw.traded_value == Decimal("7063382.80")
    assert atw.change_percent == Decimal("-0.15")
    assert atw.high_price == Decimal("694.00")
    assert atw.low_price == Decimal("685.00")
    assert atw.market_cap == Decimal("147371474715.00")
    assert atw.number_of_trades == 153
    assert isinstance(atw.volume, int)


def test_bvc_parser_extracts_visible_trading_date_without_inventing_time():
    fixture = Path("fixtures/bvc_prices/dated_market_listing.html")

    result = parse_bvc_market_listing_html(
        raw_payload_id=uuid4(),
        payload_text=fixture.read_text(encoding="utf-8"),
        collected_at=datetime(2026, 5, 16, 12, 0, tzinfo=UTC),
    )

    assert result.source_trading_date == date(2026, 5, 15)
    assert result.source_timestamp is None
    assert result.source_timestamp_raw == "Séance du vendredi 15 mai 2026"
    assert result.source_timestamp_policy == "trading_date_only"
    assert result.rows[0].trading_date == date(2026, 5, 15)
    assert result.rows[0].source_timestamp is None


def test_bvc_parser_handles_payload_without_visible_date_or_time():
    fixture = Path("fixtures/bvc_prices/no_timestamp_market_listing.html")

    result = parse_bvc_market_listing_html(
        raw_payload_id=uuid4(),
        payload_text=fixture.read_text(encoding="utf-8"),
        collected_at=datetime(2026, 5, 16, 12, 0, tzinfo=UTC),
    )

    assert result.source_trading_date is None
    assert result.source_timestamp is None
    assert result.source_timestamp_raw is None
    assert result.source_timestamp_policy == "raw_payload_collected_at_no_source_date"
    assert result.rows[0].trading_date == date(2026, 5, 16)
    assert result.rows[0].source_timestamp is None


def test_bvc_parser_detects_pagination_indicators():
    fixture = Path("fixtures/bvc_prices/paginated_market_listing.html")

    result = parse_bvc_market_listing_html(
        raw_payload_id=uuid4(),
        payload_text=fixture.read_text(encoding="utf-8"),
        collected_at=datetime(2026, 5, 15, 12, 0, tzinfo=UTC),
    )

    assert result.pagination_metadata["pagination_detected"] is True
    assert result.pagination_metadata["pagination_controls"]["visible_page_numbers"] == [1, 2]
    assert "multiple_pages_detected" in result.pagination_metadata["pagination_warnings"]


def test_decimal_parser_handles_comma_decimals():
    assert parse_decimal("123,45") == Decimal("123.45")


def test_decimal_parser_handles_space_thousand_separators():
    assert parse_decimal("1 234,56") == Decimal("1234.56")
    assert parse_int("10 000") == 10000


def test_decimal_parser_handles_percent_values():
    assert parse_decimal("-0,94 %") == Decimal("-0.94")


def test_decimal_parser_handles_dash_and_empty_values():
    assert parse_decimal("-") is None
    assert parse_decimal("") is None
    assert parse_decimal(" ") is None
    assert parse_int("--") is None


def test_decimal_parser_rejects_invalid_values():
    with pytest.raises(BvcPriceParseError):
        parse_decimal("not-a-number")


def test_bvc_parser_records_invalid_numeric_row_errors():
    result = parse_bvc_market_listing_html(
        raw_payload_id=uuid4(),
        payload_text="""
        <table>
          <thead><tr><th>Symbole</th><th>Dernier cours</th><th>Volume</th></tr></thead>
          <tbody><tr><td>BAD</td><td>not-a-number</td><td>100</td></tr></tbody>
        </table>
        """,
        collected_at=datetime(2026, 5, 15, 12, 0, tzinfo=UTC),
    )

    assert result.rows == []
    assert len(result.errors) == 1
    assert result.errors[0].error_type == "parse_error"
    assert "invalid decimal value" in result.errors[0].error_message


def test_bvc_parser_raises_when_market_table_missing():
    with pytest.raises(BvcPriceParseError):
        parse_bvc_market_listing_html(
            raw_payload_id=uuid4(),
            payload_text="<html><body><p>no table</p></body></html>",
            collected_at=datetime(2026, 5, 15, 12, 0, tzinfo=UTC),
        )
