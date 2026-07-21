import asyncio
import hashlib
import json
from pathlib import Path

import httpx
import pytest

from tradehub_data.collectors.bvc_prices.client import BvcPriceClient
from tradehub_data.collectors.bvc_prices.collector import BvcPriceCollector
from tradehub_data.collectors.bvc_prices.config import BvcPriceCollectorConfig
from tradehub_data.collectors.bvc_prices.constants import (
    BVC_EQUITY_PRICE_DATASET_CODE,
    BVC_PRICE_COLLECTOR_NAME,
    BVC_PRICE_JSON_SOURCE_ENDPOINT,
    BVC_PRICE_SOURCE_CODE,
)
from tradehub_data.collectors.bvc_prices.fixtures import store_local_fixture
from tradehub_data.models import (
    CollectionGroup,
    CollectionGroupPage,
    CollectionOccurrence,
    CollectionPageSelection,
    DataSource,
    Exchange,
    IngestionRun,
    ProcessingAttempt,
    RawPayload,
)


def run_async(coroutine):
    return asyncio.run(coroutine)


def make_config(**overrides) -> BvcPriceCollectorConfig:
    values = {
        "enabled": True,
        "base_url": "https://www.casablanca-bourse.com",
        "source_paths": ["/prices"],
        "timeout_seconds": 5,
        "max_retries": 1,
        "retry_backoff_seconds": 0,
        "sleep_between_requests_ms": 0,
        "user_agent": "TradeHubDataBot/0.1",
        "verify_ssl": True,
    }
    values.update(overrides)
    return BvcPriceCollectorConfig(**values)


def make_json_payload(count: int, *, prefix: str = "SYM") -> str:
    rows = []
    for index in range(count):
        symbol = f"{prefix}{index:03d}"
        rows.append(
            {
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
                    "cumulVolumeEchange": "123450.0000000000",
                    "capitalisation": "999999.0000000000",
                    "totalTrades": 7,
                    "transactTime": "2026-05-18T16:00:00+00:00",
                },
            }
        )
    return json.dumps({"data": {"data": rows}}, separators=(",", ":"))


def _json_collector(db_session, handler, **config_overrides) -> BvcPriceCollector:
    config = make_config(**config_overrides)
    client = BvcPriceClient(config, transport=httpx.MockTransport(handler))
    return BvcPriceCollector(db=db_session, config=config, client=client)


def _all(db_session, model):
    db_session.expire_all()
    ordering = {
        CollectionGroup: CollectionGroup.group_sequence,
        CollectionGroupPage: CollectionGroupPage.logical_page_number,
        CollectionOccurrence: CollectionOccurrence.occurrence_sequence,
        IngestionRun: IngestionRun.started_at,
    }
    query = db_session.query(model)
    if model in ordering:
        query = query.order_by(ordering[model])
    return query.all()


def _assert_no_processing_attempts(db_session) -> None:
    assert db_session.query(ProcessingAttempt).count() == 0


def _assert_single_selected_data_page(db_session):
    groups = _all(db_session, CollectionGroup)
    pages = _all(db_session, CollectionGroupPage)
    occurrences = _all(db_session, CollectionOccurrence)
    selections = _all(db_session, CollectionPageSelection)

    assert len(groups) == len(pages) == len(occurrences) == len(selections) == 1
    group = groups[0]
    page = pages[0]
    occurrence = occurrences[0]
    selection = selections[0]
    assert page.group_id == group.id
    assert occurrence.group_page_id == page.id
    assert selection.group_page_id == page.id
    assert selection.occurrence_id == occurrence.id
    assert page.page_role == "data"
    assert page.collection_page_outcome == "success"
    assert occurrence.outcome == "success_response"
    assert selection.selection_reason == "first_qualifying_success"
    return group, page, occurrence, selection


def _raw_immutable_snapshot(raw_payload: RawPayload) -> tuple:
    return (
        raw_payload.id,
        raw_payload.ingestion_run_id,
        raw_payload.source_url,
        raw_payload.source_endpoint,
        raw_payload.payload_hash,
        bytes(raw_payload.entity_body or b""),
        raw_payload.entity_body_sha256,
        raw_payload.entity_body_length,
        raw_payload.content_evidence_kind,
        raw_payload.entity_hash_algorithm,
        raw_payload.storage_status,
        raw_payload.legacy_hash_algorithm,
        raw_payload.http_status,
        raw_payload.content_type,
        raw_payload.collected_at,
        raw_payload.source_published_at,
        raw_payload.status,
        raw_payload.error_message,
        raw_payload.metadata_,
        raw_payload.payload_text,
    )


def test_json_short_page_creates_exact_raw_group_page_occurrence_and_selection(
    db_session,
):
    body = make_json_payload(2).encode()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["accept"] == "application/vnd.api+json"
        assert request.headers["accept-language"] == "fr-FR,fr;q=0.9,en;q=0.8"
        return httpx.Response(
            200,
            content=body,
            headers={"content-type": "application/json"},
            request=request,
        )

    result = run_async(
        _json_collector(
            db_session,
            handler,
            json_page_limit=50,
            json_max_pages=5,
        ).run_json_pages()
    )

    assert result.status == "success"
    assert result.payloads_stored == 1
    assert result.payloads_skipped == 0
    assert result.errors_count == 0

    source = db_session.query(DataSource).filter_by(code=BVC_PRICE_SOURCE_CODE).one()
    run = db_session.query(IngestionRun).filter_by(
        collector_name=BVC_PRICE_COLLECTOR_NAME
    ).one()
    raw_payload = db_session.query(RawPayload).one()
    group, page, occurrence, _ = _assert_single_selected_data_page(db_session)

    assert run.run_role == "acquisition"
    assert run.status == "success"
    assert group.source_id == source.id
    assert group.ingestion_run_id == run.id
    assert group.dataset_code == BVC_EQUITY_PRICE_DATASET_CODE
    assert group.collection_mode == "live_json"
    assert group.group_purpose == "validation"
    assert group.collection_status == "success"
    assert group.pagination_complete is True
    assert group.completion_evidence_kind == "short_page"
    assert group.coverage_status == "unknown"
    assert group.selected_data_pages == 1
    assert group.terminal_page_present is False
    assert page.logical_page_number == 1
    assert page.page_offset == 0
    assert page.page_limit == 50

    assert occurrence.raw_payload_id == raw_payload.id
    assert occurrence.request_sequence == 1
    assert occurrence.attempt_number == 1
    assert occurrence.redirect_hop == 0
    assert occurrence.http_status == 200
    assert occurrence.body_length == len(body)
    assert occurrence.source_endpoint == BVC_PRICE_JSON_SOURCE_ENDPOINT
    assert raw_payload.source_id == source.id
    assert raw_payload.entity_body == body
    assert raw_payload.entity_body_sha256 == hashlib.sha256(body).hexdigest()
    assert raw_payload.entity_body_length == len(body)
    assert raw_payload.content_evidence_kind == "exact_entity_bytes"
    assert raw_payload.entity_hash_algorithm == "sha256_entity_body_v1"
    assert raw_payload.legacy_hash_algorithm == "target_exact_compat_filler_v1"
    assert raw_payload.payload_hash != raw_payload.entity_body_sha256
    assert raw_payload.payload is None
    assert raw_payload.payload_text == body.decode()
    assert raw_payload.metadata_ is None
    assert raw_payload.status == "collected"
    _assert_no_processing_attempts(db_session)


def test_same_exact_body_in_a_later_run_reuses_content_but_adds_a_complete_graph(
    db_session,
):
    body = make_json_payload(1).encode()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=body,
            headers={"content-type": "application/json"},
            request=request,
        )

    collector = _json_collector(
        db_session,
        handler,
        json_page_limit=10,
        json_max_pages=2,
    )
    first = run_async(collector.run_json_pages())
    raw_payload = db_session.query(RawPayload).one()
    original = _raw_immutable_snapshot(raw_payload)
    second = run_async(collector.run_json_pages())

    assert first.payloads_stored == 1
    assert second.status == "success"
    assert second.payloads_stored == 0
    assert second.payloads_skipped == 1
    assert db_session.query(RawPayload).count() == 1
    assert db_session.query(IngestionRun).count() == 2
    assert db_session.query(CollectionGroup).count() == 2
    assert db_session.query(CollectionGroupPage).count() == 2
    assert db_session.query(CollectionOccurrence).count() == 2
    assert db_session.query(CollectionPageSelection).count() == 2
    db_session.expire_all()
    assert _raw_immutable_snapshot(db_session.query(RawPayload).one()) == original
    assert {
        occurrence.ingestion_run_id for occurrence in _all(db_session, CollectionOccurrence)
    } == {run.id for run in _all(db_session, IngestionRun)}
    _assert_no_processing_attempts(db_session)


def test_malformed_json_is_audited_before_page_failure(db_session):
    body = b'{"data": [malformed]'

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=body,
            headers={"content-type": "application/json"},
            request=request,
        )

    result = run_async(_json_collector(db_session, handler).run_json_pages())

    assert result.status == "failed"
    assert db_session.query(RawPayload).one().entity_body == body
    occurrence = db_session.query(CollectionOccurrence).one()
    page = db_session.query(CollectionGroupPage).one()
    group = db_session.query(CollectionGroup).one()
    assert occurrence.outcome == "success_response"
    assert page.page_role == "unknown"
    assert page.collection_page_outcome == "failed"
    assert group.collection_status == "failed"
    assert group.pagination_complete is False
    assert db_session.query(CollectionPageSelection).count() == 0
    _assert_no_processing_attempts(db_session)


def test_unexpected_json_shape_is_preserved_before_structural_failure(db_session):
    body = b'{"data":{"unexpected":[]}}'

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=body,
            headers={"content-type": "application/json"},
            request=request,
        )

    result = run_async(_json_collector(db_session, handler).run_json_pages())

    assert result.status == "failed"
    assert db_session.query(RawPayload).one().entity_body == body
    assert db_session.query(CollectionOccurrence).count() == 1
    assert db_session.query(CollectionGroupPage).one().collection_page_outcome == "failed"
    assert db_session.query(CollectionPageSelection).count() == 0


def test_zero_byte_http_body_is_exact_content_and_not_a_terminal_sentinel(db_session):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"",
            headers={"content-type": "application/json"},
            request=request,
        )

    result = run_async(_json_collector(db_session, handler).run_json_pages())

    raw_payload = db_session.query(RawPayload).one()
    occurrence = db_session.query(CollectionOccurrence).one()
    page = db_session.query(CollectionGroupPage).one()
    assert result.status == "failed"
    assert raw_payload.entity_body == b""
    assert raw_payload.entity_body_length == 0
    assert raw_payload.entity_body_sha256 == hashlib.sha256(b"").hexdigest()
    assert occurrence.raw_payload_id == raw_payload.id
    assert occurrence.body_length == 0
    assert page.page_role == "unknown"
    assert page.collection_page_outcome == "failed"
    assert db_session.query(CollectionPageSelection).count() == 0


def test_structurally_valid_zero_row_first_page_fails_safely(db_session):
    body = make_json_payload(0).encode()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=body,
            headers={"content-type": "application/json"},
            request=request,
        )

    result = run_async(_json_collector(db_session, handler).run_json_pages())

    group = db_session.query(CollectionGroup).one()
    page = db_session.query(CollectionGroupPage).one()
    assert result.status == "failed"
    assert db_session.query(RawPayload).one().entity_body == body
    assert db_session.query(CollectionOccurrence).count() == 1
    assert page.page_role == "unknown"
    assert page.collection_page_outcome == "failed"
    assert group.pagination_complete is False
    assert db_session.query(CollectionPageSelection).count() == 0


def test_later_zero_row_page_is_preserved_and_selected_as_terminal_sentinel(
    db_session,
):
    requested_offsets = []
    bodies = {"0": make_json_payload(2).encode(), "2": make_json_payload(0).encode()}

    def handler(request: httpx.Request) -> httpx.Response:
        offset = request.url.params["page[offset]"]
        requested_offsets.append(offset)
        return httpx.Response(
            200,
            content=bodies[offset],
            headers={"content-type": "application/json"},
            request=request,
        )

    result = run_async(
        _json_collector(
            db_session,
            handler,
            json_page_limit=2,
            json_max_pages=5,
        ).run_json_pages()
    )

    group = db_session.query(CollectionGroup).one()
    pages = _all(db_session, CollectionGroupPage)
    occurrences = _all(db_session, CollectionOccurrence)
    selections = _all(db_session, CollectionPageSelection)
    assert result.status == "success"
    assert requested_offsets == ["0", "2"]
    assert db_session.query(RawPayload).count() == 2
    assert len(pages) == len(occurrences) == len(selections) == 2
    assert [page.page_role for page in pages] == ["data", "terminal_sentinel"]
    assert all(page.collection_page_outcome == "success" for page in pages)
    assert group.collection_status == "success"
    assert group.pagination_complete is True
    assert group.completion_evidence_kind == "terminal_sentinel"
    assert group.selected_data_pages == 1
    assert group.terminal_page_present is True


def test_temporary_http_error_body_is_preserved_before_successful_retry(db_session):
    attempts = 0
    error_body = b'{"error":"temporary"}'
    success_body = make_json_payload(1).encode()

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(
                503,
                content=error_body,
                headers={"content-type": "application/json"},
                request=request,
            )
        return httpx.Response(
            200,
            content=success_body,
            headers={"content-type": "application/json"},
            request=request,
        )

    result = run_async(
        _json_collector(db_session, handler, max_retries=1).run_json_pages()
    )

    occurrences = _all(db_session, CollectionOccurrence)
    selection = db_session.query(CollectionPageSelection).one()
    assert result.status == "success"
    assert attempts == 2
    assert db_session.query(RawPayload).count() == 2
    assert [occurrence.outcome for occurrence in occurrences] == [
        "http_error_response",
        "success_response",
    ]
    assert [occurrence.attempt_number for occurrence in occurrences] == [1, 2]
    assert {raw.entity_body for raw in _all(db_session, RawPayload)} == {
        error_body,
        success_body,
    }
    assert selection.occurrence_id == occurrences[1].id


def test_each_exhausted_temporary_http_retry_has_its_own_occurrence(db_session):
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(
            503,
            content=f"temporary-{attempts}".encode(),
            headers={"content-type": "text/plain"},
            request=request,
        )

    result = run_async(
        _json_collector(db_session, handler, max_retries=2).run_json_pages()
    )

    occurrences = _all(db_session, CollectionOccurrence)
    assert result.status == "failed"
    assert attempts == 3
    assert len(occurrences) == 3
    assert [occurrence.attempt_number for occurrence in occurrences] == [1, 2, 3]
    assert {occurrence.outcome for occurrence in occurrences} == {
        "http_error_response"
    }
    assert db_session.query(RawPayload).count() == 3
    assert db_session.query(CollectionPageSelection).count() == 0
    assert db_session.query(CollectionGroupPage).one().collection_page_outcome == "failed"


def test_transport_failure_creates_occurrence_without_raw_content(db_session):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("private transport details", request=request)

    result = run_async(
        _json_collector(db_session, handler, max_retries=0).run_json_pages()
    )

    occurrence = db_session.query(CollectionOccurrence).one()
    assert result.status == "failed"
    assert db_session.query(RawPayload).count() == 0
    assert occurrence.outcome == "transport_failure"
    assert occurrence.raw_payload_id is None
    assert occurrence.response_url is None
    assert occurrence.response_received_at is None
    assert occurrence.http_status is None
    assert occurrence.safe_error_code == "timeout"
    assert "private transport details" not in (occurrence.safe_error_message or "")
    assert db_session.query(CollectionPageSelection).count() == 0


def test_unexpected_client_boundary_failure_is_redacted_and_finalized(
    db_session,
    caplog,
):
    secret = "unexpected-client-private-value"

    class ExplodingClient:
        async def fetch_attempt(self, source_url, *, headers=None):
            raise RuntimeError(f"unexpected failure containing {secret}")

    config = make_config(max_retries=0)
    collector = BvcPriceCollector(
        db=db_session,
        config=config,
        client=ExplodingClient(),
    )
    result = run_async(collector.run_json_pages())

    occurrence = db_session.query(CollectionOccurrence).one()
    run = db_session.query(IngestionRun).one()
    group = db_session.query(CollectionGroup).one()
    page = db_session.query(CollectionGroupPage).one()
    assert result.status == "failed"
    assert occurrence.outcome == "transport_failure"
    assert occurrence.safe_error_code == "network_error"
    assert occurrence.safe_error_message == "network request failed"
    assert occurrence.raw_payload_id is None
    assert run.status == "failed"
    assert group.collection_status == "failed"
    assert page.collection_page_outcome == "failed"
    assert secret not in caplog.text


def test_only_safe_headers_and_sanitized_urls_reach_persistence(db_session):
    secret = "never-persist-this-value"
    body = make_json_payload(1).encode()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=body,
            headers=[
                ("Content-Type", "application/json"),
                ("Cache-Control", "no-cache"),
                ("Cache-Control", "max-age=0"),
                ("ETag", '"public-etag"'),
                ("Set-Cookie", f"session={secret}"),
                ("X-WAF-Token", secret),
                ("X-Unknown", secret),
            ],
            request=request,
        )

    result = run_async(
        _json_collector(
            db_session,
            handler,
            json_path=(
                "https://www.casablanca-bourse.com/api/prices?"
                f"token={secret}&offset=000"
            ),
            json_page_limit=5,
        ).run_json_pages()
    )

    occurrence = db_session.query(CollectionOccurrence).one()
    raw_payload = db_session.query(RawPayload).one()
    run = db_session.query(IngestionRun).one()
    assert result.status == "success"
    assert occurrence.safe_response_headers["content-type"] == ["application/json"]
    assert occurrence.safe_response_headers["cache-control"] == [
        "no-cache",
        "max-age=0",
    ]
    assert occurrence.safe_response_headers["etag"] == ['"public-etag"']
    assert occurrence.dropped_response_header_name_count == 3
    persisted = json.dumps(
        {
            "headers": occurrence.safe_response_headers,
            "logical_request_url": occurrence.logical_request_url,
            "requested_url": occurrence.requested_url,
            "response_url": occurrence.response_url,
            "raw_source_url": raw_payload.source_url,
            "run_metadata": run.metadata_,
        },
        default=str,
        sort_keys=True,
    )
    assert secret not in persisted
    assert "set-cookie" not in persisted
    assert "x-waf-token" not in persisted
    assert "x-unknown" not in persisted
    assert "token=" not in persisted


def test_configured_base_url_private_components_never_enter_reference_tables(
    db_session,
):
    secret = "base-url-private-value"
    body = make_json_payload(1).encode()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=body,
            headers={"content-type": "application/json"},
            request=request,
        )

    result = run_async(
        _json_collector(
            db_session,
            handler,
            base_url=(
                f"https://user:{secret}@www.casablanca-bourse.com/root"
                f"?token={secret}#fragment-{secret}"
            ),
            json_page_limit=5,
        ).run_json_pages()
    )

    assert result.status == "success"
    source = db_session.query(DataSource).one()
    exchange = db_session.query(Exchange).one()
    persisted = json.dumps(
        {
            "source_base_url": source.base_url,
            "exchange_website_url": exchange.website_url,
            "occurrence_urls": [
                occurrence.logical_request_url
                for occurrence in db_session.query(CollectionOccurrence).all()
            ],
        }
    )
    assert secret not in persisted
    assert "user:" not in persisted
    assert "token=" not in persisted
    assert "fragment" not in persisted


def test_max_pages_without_positive_completion_evidence_is_partial_and_incomplete(
    db_session,
):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=make_json_payload(2, prefix=request.url.params["page[offset]"]).encode(),
            headers={"content-type": "application/json"},
            request=request,
        )

    result = run_async(
        _json_collector(
            db_session,
            handler,
            json_page_limit=2,
            json_max_pages=2,
        ).run_json_pages()
    )

    group = db_session.query(CollectionGroup).one()
    assert result.status == "partial_success"
    assert result.source_urls_count == 2
    assert group.collection_status == "partial_success"
    assert group.pagination_complete is False
    assert group.completion_evidence_kind == "none"
    assert group.collection_stop_reason == "max_pages"
    assert db_session.query(CollectionGroupPage).count() == 2
    assert db_session.query(CollectionPageSelection).count() == 2


def test_later_http_failure_after_selected_page_is_partial_and_incomplete(db_session):
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(
                200,
                content=make_json_payload(2).encode(),
                headers={"content-type": "application/json"},
                request=request,
            )
        return httpx.Response(404, content=b"not found", request=request)

    result = run_async(
        _json_collector(
            db_session,
            handler,
            json_page_limit=2,
            json_max_pages=3,
            max_retries=0,
        ).run_json_pages()
    )

    group = db_session.query(CollectionGroup).one()
    pages = _all(db_session, CollectionGroupPage)
    occurrences = _all(db_session, CollectionOccurrence)
    assert result.status == "partial_success"
    assert group.collection_status == "partial_success"
    assert group.pagination_complete is False
    assert group.completion_evidence_kind == "none"
    assert [page.collection_page_outcome for page in pages] == ["success", "failed"]
    assert [occurrence.outcome for occurrence in occurrences] == [
        "success_response",
        "http_error_response",
    ]
    assert db_session.query(CollectionPageSelection).count() == 1
    assert db_session.query(RawPayload).count() == 2


def test_redirect_hop_body_is_audited_before_final_page_selection(db_session):
    secret = "redirect-private-value"
    final_body = make_json_payload(1).encode()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/redirect":
            return httpx.Response(
                302,
                content=b"redirect-hop-body",
                headers={
                    "Location": (
                        "https://www.casablanca-bourse.com/api/final?"
                        f"token={secret}&page%5Blimit%5D=5&page%5Boffset%5D=0"
                    )
                },
                request=request,
            )
        return httpx.Response(
            200,
            content=final_body,
            headers={"content-type": "application/json"},
            request=request,
        )

    result = run_async(
        _json_collector(
            db_session,
            handler,
            json_path=f"/redirect?token={secret}",
            json_page_limit=5,
            max_retries=0,
        ).run_json_pages()
    )

    occurrences = _all(db_session, CollectionOccurrence)
    selection = db_session.query(CollectionPageSelection).one()
    assert result.status == "success"
    assert [occurrence.outcome for occurrence in occurrences] == [
        "redirect_response",
        "success_response",
    ]
    assert [occurrence.redirect_hop for occurrence in occurrences] == [0, 1]
    assert selection.occurrence_id == occurrences[1].id
    assert db_session.query(RawPayload).count() == 2
    persisted = json.dumps(
        [
            {
                "logical": occurrence.logical_request_url,
                "requested": occurrence.requested_url,
                "response": occurrence.response_url,
                "headers": occurrence.safe_response_headers,
            }
            for occurrence in occurrences
        ],
        sort_keys=True,
    )
    assert secret not in persisted
    assert "location" not in persisted.lower()


def test_html_collection_audits_qualified_page_but_remains_incomplete(db_session):
    body = Path("fixtures/bvc_prices/dated_market_listing.html").read_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=body,
            headers={"content-type": "text/html; charset=utf-8"},
            request=request,
        )

    config = make_config(max_retries=0)
    collector = BvcPriceCollector(
        db=db_session,
        config=config,
        client=BvcPriceClient(config, transport=httpx.MockTransport(handler)),
    )
    result = run_async(collector.run())

    group, _, occurrence, _ = _assert_single_selected_data_page(db_session)
    assert result.status == "partial_success"
    assert result.errors_count == 0
    assert group.collection_mode == "live_html"
    assert group.group_purpose == "validation"
    assert group.collection_status == "partial_success"
    assert group.pagination_complete is False
    assert group.completion_evidence_kind == "none"
    assert group.collection_stop_reason == (
        "configured_html_scope_no_completion_evidence"
    )
    assert occurrence.request_profile == "bvc-html-safe-v1"
    assert db_session.query(RawPayload).one().entity_body == body


def _write_valid_json_fixture(path: Path, *, count: int = 1) -> bytes:
    body = make_json_payload(count).encode()
    path.write_bytes(body)
    return body


def test_valid_fixture_creates_exact_validation_graph_and_selection(db_session, tmp_path):
    fixture_path = tmp_path / "private-local-name.json"
    body = _write_valid_json_fixture(fixture_path)

    result = store_local_fixture(db_session, file_path=fixture_path)

    raw_payload = db_session.query(RawPayload).one()
    group = db_session.query(CollectionGroup).one()
    page = db_session.query(CollectionGroupPage).one()
    occurrence = db_session.query(CollectionOccurrence).one()
    selection = db_session.query(CollectionPageSelection).one()
    run = db_session.query(IngestionRun).one()
    assert result["status"] == "success"
    assert result["payload_inserted"] is True
    assert raw_payload.entity_body == body
    assert raw_payload.payload_text == body.decode()
    assert group.collection_mode == "manual_fixture"
    assert group.group_purpose == "validation"
    assert group.collection_status == "success"
    assert group.pagination_complete is True
    assert group.completion_evidence_kind == "declared_fixture_scope"
    assert page.page_role == "data"
    assert page.collection_page_outcome == "success"
    assert occurrence.outcome == "fixture_loaded"
    assert occurrence.response_url is None
    assert occurrence.response_received_at is None
    assert occurrence.http_status is None
    assert selection.occurrence_id == occurrence.id
    assert selection.selection_reason == "fixture_selected"
    persisted = json.dumps(
        {
            "raw_source_url": raw_payload.source_url,
            "raw_metadata": raw_payload.metadata_,
            "run_metadata": run.metadata_,
            "logical_request_url": occurrence.logical_request_url,
        },
        default=str,
        sort_keys=True,
    )
    assert str(tmp_path) not in persisted
    assert fixture_path.name not in persisted
    _assert_no_processing_attempts(db_session)


def test_duplicate_fixture_reuses_raw_content_without_mutating_first_context(
    db_session,
    tmp_path,
):
    fixture_path = tmp_path / "fixture.json"
    _write_valid_json_fixture(fixture_path)

    first = store_local_fixture(db_session, file_path=fixture_path)
    raw_payload = db_session.query(RawPayload).one()
    original = _raw_immutable_snapshot(raw_payload)
    second = store_local_fixture(db_session, file_path=fixture_path)

    assert first["payload_inserted"] is True
    assert second["payload_inserted"] is False
    assert first["raw_payload_id"] == second["raw_payload_id"]
    assert db_session.query(RawPayload).count() == 1
    assert db_session.query(IngestionRun).count() == 2
    assert db_session.query(CollectionGroup).count() == 2
    assert db_session.query(CollectionGroupPage).count() == 2
    assert db_session.query(CollectionOccurrence).count() == 2
    assert db_session.query(CollectionPageSelection).count() == 2
    db_session.expire_all()
    assert _raw_immutable_snapshot(db_session.query(RawPayload).one()) == original


def test_fixture_diagnostic_failure_does_not_rollback_audit_evidence(
    db_session,
    tmp_path,
):
    fixture_path = tmp_path / "malformed-market.json"
    body = b'{"data": [not-json]'
    fixture_path.write_bytes(body)

    result = store_local_fixture(db_session, file_path=fixture_path)

    assert result["status"] == "failed"
    assert db_session.query(RawPayload).one().entity_body == body
    assert db_session.query(CollectionOccurrence).one().outcome == "fixture_loaded"
    assert db_session.query(CollectionGroupPage).one().collection_page_outcome == "failed"
    assert db_session.query(CollectionGroup).one().collection_status == "failed"
    assert db_session.query(CollectionPageSelection).count() == 0
    _assert_no_processing_attempts(db_session)


def test_unreadable_fixture_finalizes_the_empty_audit_graph_safely(
    db_session,
    tmp_path,
):
    missing_path = tmp_path / "private-missing-fixture.json"

    with pytest.raises(RuntimeError) as caught:
        store_local_fixture(db_session, file_path=missing_path)

    run = db_session.query(IngestionRun).one()
    group = db_session.query(CollectionGroup).one()
    page = db_session.query(CollectionGroupPage).one()
    assert str(caught.value) == "fixture could not be read"
    assert str(missing_path) not in str(caught.value)
    assert run.status == "failed"
    assert run.safe_error_code == "fixture_read_failed"
    assert group.collection_status == "failed"
    assert group.pagination_complete is False
    assert page.collection_page_outcome == "failed"
    assert page.structural_reason_code == "fixture_read_failed"
    assert db_session.query(RawPayload).count() == 0
    assert db_session.query(CollectionOccurrence).count() == 0
    assert db_session.query(CollectionPageSelection).count() == 0


def test_disabled_collector_does_not_create_collection_evidence(db_session):
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("disabled collector should not fetch")

    config = make_config(enabled=False)
    client = BvcPriceClient(config, transport=httpx.MockTransport(handler))
    result = run_async(
        BvcPriceCollector(db=db_session, config=config, client=client).run_json_pages()
    )

    assert result.status == "skipped"
    assert result.ingestion_run_id is None
    assert db_session.query(IngestionRun).count() == 0
    assert db_session.query(RawPayload).count() == 0
    assert db_session.query(CollectionGroup).count() == 0
    assert db_session.query(CollectionOccurrence).count() == 0


def test_config_rejects_unapproved_source_url():
    try:
        make_config(source_paths=["https://example.com/prices"])
    except Exception as exc:
        assert "not allowed" in str(exc)
    else:
        raise AssertionError("expected unapproved source URL to be rejected")


def test_config_ssl_verification_defaults_to_enabled(monkeypatch):
    monkeypatch.delenv("BVC_PRICE_COLLECTOR_VERIFY_SSL", raising=False)
    monkeypatch.delenv("BVC_PRICE_COLLECTOR_CA_BUNDLE_PATH", raising=False)

    config = BvcPriceCollectorConfig.from_env()

    assert config.verify_ssl is True
    assert config.ca_bundle_path is None


def test_config_supports_explicit_ca_bundle_path(monkeypatch):
    monkeypatch.setenv("BVC_PRICE_COLLECTOR_CA_BUNDLE_PATH", "/app/certs/bvc.pem")

    config = BvcPriceCollectorConfig.from_env()

    assert config.verify_ssl is True
    assert config.ca_bundle_path == "/app/certs/bvc.pem"


def test_config_rejects_invalid_ssl_verification_value(monkeypatch):
    monkeypatch.setenv("BVC_PRICE_COLLECTOR_VERIFY_SSL", "treu")

    try:
        BvcPriceCollectorConfig.from_env()
    except Exception as exc:
        assert "BVC_PRICE_COLLECTOR_VERIFY_SSL" in str(exc)
    else:
        raise AssertionError("expected invalid SSL verification value to be rejected")


def test_config_defaults_to_market_actions_listing(monkeypatch):
    monkeypatch.delenv("BVC_PRICE_COLLECTOR_SOURCE_URLS", raising=False)
    monkeypatch.delenv("BVC_PRICE_COLLECTOR_SOURCE_PATHS", raising=False)

    config = BvcPriceCollectorConfig.from_env()

    assert config.source_urls == [
        "https://www.casablanca-bourse.com/fr/live-market/marche-actions-listing?amp=1"
    ]


def test_config_source_urls_override_source_paths(monkeypatch):
    monkeypatch.setenv("BVC_PRICE_COLLECTOR_SOURCE_PATHS", "/")
    monkeypatch.setenv(
        "BVC_PRICE_COLLECTOR_SOURCE_URLS",
        "https://www.casablanca-bourse.com/fr/live-market/marche-actions-listing?amp=1,"
        "https://www.casablanca-bourse.com/fr/live-market/instruments/BCP",
    )

    config = BvcPriceCollectorConfig.from_env()

    assert config.source_urls == [
        "https://www.casablanca-bourse.com/fr/live-market/marche-actions-listing?amp=1",
        "https://www.casablanca-bourse.com/fr/live-market/instruments/BCP",
    ]


def test_config_defaults_to_json_endpoint(monkeypatch):
    monkeypatch.delenv("BVC_PRICE_COLLECTOR_JSON_PATH", raising=False)
    monkeypatch.delenv("BVC_PRICE_COLLECTOR_PAGE_LIMIT", raising=False)
    monkeypatch.delenv("BVC_PRICE_COLLECTOR_MAX_PAGES", raising=False)
    monkeypatch.delenv("BVC_PRICE_COLLECTOR_ACCEPT_LANGUAGE", raising=False)

    config = BvcPriceCollectorConfig.from_env()

    assert config.json_enabled is True
    assert config.json_endpoint_base_url == (
        "https://www.casablanca-bourse.com/api/proxy/fr/api/"
        "bourse_data/last_market_watches/action"
    )
    assert config.json_page_limit == 50
    assert config.json_max_pages == 5
    assert config.accept_language == "fr-FR,fr;q=0.9,en;q=0.8"


def test_config_accept_language_supports_env_override(monkeypatch):
    monkeypatch.setenv(
        "BVC_PRICE_COLLECTOR_ACCEPT_LANGUAGE",
        "fr-MA,fr;q=0.8,en;q=0.5",
    )

    config = BvcPriceCollectorConfig.from_env()

    assert config.accept_language == "fr-MA,fr;q=0.8,en;q=0.5"
