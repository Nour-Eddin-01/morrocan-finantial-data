from datetime import UTC, datetime
import json
from contextlib import contextmanager
from pathlib import Path

from tradehub_data.collectors.bvc_prices.constants import BVC_PRICE_PAYLOAD_TYPE
from tradehub_data.models import DataSource, RawPayload
from tradehub_data.parsers.bvc_prices.diagnostics import diagnose_bvc_market_listing_html, diagnose_bvc_price_payload, diagnose_file, diagnose_raw_payload
from tradehub_data.repositories.raw_payloads import insert_raw_payload_if_new


def test_bvc_diagnostics_reports_sample_fixture_fields():
    result = diagnose_file(Path("fixtures/bvc_prices/sample_market_listing.html"))

    assert result.status == "success"
    assert result.tables_found == 1
    assert result.selected_table_index == 0
    assert result.rows_detected == 3
    assert result.parseable_rows_count == 3
    assert result.row_parse_errors_count == 0
    assert "last_price" in result.mapped_fields
    assert "volume" in result.mapped_fields
    assert result.missing_required_fields == []
    assert result.candidate_tables[0].candidate is True
    assert result.candidate_tables[0].selected is True


def test_bvc_json_diagnostics_reports_rows_and_mapped_fields():
    payload = json.dumps(
        {
            "data": {
                "data": [
                    {
                        "type": "market_watch",
                        "id": "ABC",
                        "attributes": {
                            "code": "ABC-token",
                            "lastTradedPrice": "123.4500000000",
                            "openingPrice": "120.0000000000",
                            "highPrice": "125.0000000000",
                            "lowPrice": "119.0000000000",
                            "staticReferencePrice": "121.0000000000",
                            "varVeille": "1.2300000000",
                            "cumulTitresEchanges": "1000.0000000000",
                            "cumulVolumeEchange": "123450.0000000000",
                            "capitalisation": "999999.0000000000",
                            "totalTrades": 7,
                            "transactTime": "2026-05-18T16:00:00+00:00",
                            "unhandledSourceField": "kept visible",
                        },
                    }
                ]
            }
        }
    )

    result = diagnose_bvc_price_payload(
        payload_text=payload,
        content_type="application/json",
        metadata={"page_number": 1, "page_offset": 0, "page_limit": 50},
    )

    assert result.payload_format == "json"
    assert result.status == "success"
    assert result.tables_found == 0
    assert result.rows_detected == 1
    assert result.parseable_rows_count == 1
    assert result.row_parse_errors_count == 0
    assert "last_price" in result.mapped_fields
    assert "volume" in result.mapped_fields
    assert "traded_value" in result.mapped_fields
    assert "unhandledSourceField" in result.unmapped_fields
    assert result.source_trading_date.isoformat() == "2026-05-18"
    assert result.source_timestamp.isoformat() == "2026-05-18T16:00:00+00:00"
    assert result.page_number == 1
    assert result.page_offset == 0
    assert result.page_limit == 50


def test_bvc_diagnostics_reports_real_fixture_mapping():
    result = diagnose_file(Path("fixtures/bvc_prices/real/bvc_market_listing_20260518_page_1.html"))

    assert result.status == "success"
    assert result.tables_found == 1
    assert result.selected_table_index == 0
    assert result.rows_detected == 50
    assert result.parseable_rows_count == 50
    assert result.row_parse_errors_count == 0
    assert result.source_trading_date.isoformat() == "2026-05-18"
    assert result.pagination_detected is True
    assert "possible_incomplete_listing" in result.pagination_warnings
    assert "volume" in result.mapped_fields
    assert "traded_value" in result.mapped_fields
    assert "high_price" in result.mapped_fields
    assert "low_price" in result.mapped_fields

    table = result.candidate_tables[0]
    assert table.candidate is True
    assert table.missing_required_fields == []
    assert "Quantité échangée" not in table.unmapped_headers
    assert "Volume" not in table.unmapped_headers
    assert "+ haut jour" not in table.unmapped_headers
    assert "+ bas jour" not in table.unmapped_headers
    assert "Statut" in table.unmapped_headers
    assert "Meilleur prix à l'achat" in table.unmapped_headers


def test_bvc_diagnostics_reports_source_date_without_time():
    result = diagnose_file(Path("fixtures/bvc_prices/dated_market_listing.html"))

    assert result.status == "success"
    assert result.source_trading_date.isoformat() == "2026-05-15"
    assert result.source_timestamp is None
    assert result.source_timestamp_raw == "Séance du vendredi 15 mai 2026"
    assert result.source_timestamp_policy == "trading_date_only"
    assert result.raw_date_candidates == ["Séance du vendredi 15 mai 2026"]


def test_bvc_diagnostics_reports_missing_source_date_safely():
    result = diagnose_file(Path("fixtures/bvc_prices/no_timestamp_market_listing.html"))

    assert result.status == "success"
    assert result.source_trading_date is None
    assert result.source_timestamp is None
    assert result.source_timestamp_raw is None
    assert result.source_timestamp_policy == "raw_payload_collected_at_no_source_date"


def test_bvc_diagnostics_reports_pagination_indicators():
    result = diagnose_file(Path("fixtures/bvc_prices/paginated_market_listing.html"))

    assert result.status == "success"
    assert result.pagination_detected is True
    assert result.pagination_controls["visible_page_numbers"] == [1, 2]
    assert result.pagination_controls["next_page_hint"] == "Suivant"
    assert "multiple_pages_detected" in result.pagination_warnings


def test_bvc_diagnostics_reports_tables_unknown_headers_and_parse_errors():
    result = diagnose_bvc_market_listing_html(
        payload_text="""
        <html>
          <body>
            <table>
              <thead><tr><th>Name</th><th>Other</th></tr></thead>
              <tbody><tr><td>not market data</td><td>x</td></tr></tbody>
            </table>
            <table>
              <thead>
                <tr>
                  <th>Instrument</th>
                  <th>Symbole</th>
                  <th>Dernier cours</th>
                  <th>Volume</th>
                  <th>Unknown Source Column</th>
                </tr>
              </thead>
              <tbody>
                <tr><td>VALID SA</td><td>VAL</td><td>12,34</td><td>1 000</td><td>x</td></tr>
                <tr><td>BAD SA</td><td>BAD</td><td>not-a-number</td><td>100</td><td>y</td></tr>
              </tbody>
            </table>
          </body>
        </html>
        """,
        collected_at=datetime(2026, 5, 15, 12, 0, tzinfo=UTC),
    )

    assert result.status == "partial_success"
    assert result.tables_found == 2
    assert result.selected_table_index == 1
    assert result.rows_detected == 2
    assert result.parseable_rows_count == 1
    assert result.row_parse_errors_count == 1
    assert result.row_parse_errors_sample[0]["row_index"] == 1
    assert "invalid decimal value" in result.row_parse_errors_sample[0]["error_message"]
    assert result.candidate_tables[0].candidate is False
    assert result.candidate_tables[0].missing_required_fields == ["last_price", "volume"]
    assert result.candidate_tables[1].unmapped_headers == ["Unknown Source Column"]


def test_bvc_diagnostics_can_read_raw_payload_without_mutating_it(db_session):
    source = DataSource(code="bvc_prices", name="BVC Prices", source_type="exchange", priority=100)
    db_session.add(source)
    db_session.flush()
    raw_payload, _ = insert_raw_payload_if_new(
        db_session,
        source_id=source.id,
        payload_hash="f" * 64,
        payload_type=BVC_PRICE_PAYLOAD_TYPE,
        payload_text=Path("fixtures/bvc_prices/sample_market_listing.html").read_text(encoding="utf-8"),
        collected_at=datetime(2026, 5, 15, 12, 0, tzinfo=UTC),
        status="collected",
    )
    db_session.commit()

    from tradehub_data.parsers.bvc_prices import diagnostics

    original_session_local = diagnostics.SessionLocal
    @contextmanager
    def session_local():
        yield db_session

    diagnostics.SessionLocal = session_local
    try:
        result = diagnose_raw_payload(raw_payload.id)
    finally:
        diagnostics.SessionLocal = original_session_local

    assert result.status == "success"
    assert result.raw_payload_id == raw_payload.id
    assert result.payload_hash == "f" * 64
    assert result.parseable_rows_count == 3

    db_session.refresh(raw_payload)
    assert raw_payload.status == "collected"
    assert db_session.query(RawPayload).count() == 1
