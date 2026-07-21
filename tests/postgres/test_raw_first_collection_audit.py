from __future__ import annotations

import hashlib
import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from tradehub_data.collectors.http_metadata import filter_safe_response_headers
from tradehub_data.core.hashing import sha256_source_payload
from tradehub_data.models import (
    CollectionGroup,
    CollectionGroupPage,
    CollectionOccurrence,
    CollectionPageSelection,
    DataSource,
    Exchange,
    RawPayload,
)
from tradehub_data.repositories.collection_audit import (
    CollectionAuditStateError,
    create_collection_group,
    create_collection_group_page,
    finalize_page_with_selection,
    record_response_occurrence,
)
from tradehub_data.repositories.raw_contents import (
    EXACT_COMPATIBILITY_HASH_ALGORITHM,
    ExactRawCompatibilityContext,
    exact_compatibility_payload_hash,
    exact_entity_body_sha256,
)
from tradehub_data.repositories.sources import create_ingestion_run
from tests.postgres.harness import (
    CURRENT_HEAD_REVISION,
    make_database_engine,
    upgrade_database,
)


pytestmark = pytest.mark.postgres


STARTED_AT = datetime(2026, 7, 21, 12, 0, tzinfo=UTC)
RESPONSE_AT = STARTED_AT + timedelta(seconds=1)
FINISHED_AT = RESPONSE_AT + timedelta(milliseconds=100)
SELECTION_AT = FINISHED_AT + timedelta(milliseconds=100)
PAGE_LIMIT = 50
SAFE_POLICY_VERSION = "bvc-safe-response-headers-v1"


@dataclass(frozen=True, slots=True)
class RawFirstDatabase:
    engine: Engine
    sessions: Any
    source_id: uuid.UUID
    exchange_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class AuditGraph:
    run_id: uuid.UUID
    group_id: uuid.UUID
    page_ids: tuple[uuid.UUID, ...]


@dataclass(frozen=True, slots=True)
class StoredOccurrence:
    occurrence_id: uuid.UUID
    raw_payload_id: uuid.UUID
    raw_content_inserted: bool


@pytest.fixture()
def raw_first_database(empty_postgres_database_url) -> RawFirstDatabase:
    upgrade_database(empty_postgres_database_url, CURRENT_HEAD_REVISION)
    engine = make_database_engine(empty_postgres_database_url)
    sessions = sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
    )
    source_id = uuid.uuid4()
    exchange_id = uuid.uuid4()
    try:
        with sessions.begin() as db:
            db.add(
                DataSource(
                    id=source_id,
                    code="bvc_raw_first_runtime_test",
                    name="Synthetic BVC raw-first source",
                    source_type="exchange",
                    base_url="https://www.casablanca-bourse.com",
                    country_code="MA",
                )
            )
            db.add(
                Exchange(
                    id=exchange_id,
                    code="BVC-RAW-FIRST-TEST",
                    name="Synthetic BVC raw-first exchange",
                    country_code="MA",
                    currency_code="MAD",
                    timezone="Africa/Casablanca",
                )
            )
        yield RawFirstDatabase(
            engine=engine,
            sessions=sessions,
            source_id=source_id,
            exchange_id=exchange_id,
        )
    finally:
        engine.dispose()


def _create_graph(
    database: RawFirstDatabase,
    *,
    label: str,
    page_count: int = 1,
) -> AuditGraph:
    with database.sessions.begin() as db:
        run = create_ingestion_run(
            db,
            source_id=database.source_id,
            collector_name=f"raw_first_test_{label}",
            run_type="manual",
            run_role="acquisition",
            started_at=STARTED_AT,
        )
        group = create_collection_group(
            db,
            source_id=database.source_id,
            exchange_id=database.exchange_id,
            ingestion_run_id=run.id,
            dataset_code="bvc_equity_prices",
            collection_mode="live_json",
            group_purpose="validation",
            page_limit=PAGE_LIMIT,
            started_at=STARTED_AT,
            external_group_key=f"synthetic-{label}",
        )
        pages = tuple(
            create_collection_group_page(
                db,
                group_id=group.id,
                logical_page_number=page_number,
            )
            for page_number in range(1, page_count + 1)
        )
        return AuditGraph(
            run_id=run.id,
            group_id=group.id,
            page_ids=tuple(page.id for page in pages),
        )


def _record_response(
    db: Session,
    database: RawFirstDatabase,
    graph: AuditGraph,
    *,
    page_index: int,
    entity_body: bytes,
    request_sequence: int,
    response_url: str,
    response_at: datetime = RESPONSE_AT,
    raw_headers: list[tuple[str, str]] | None = None,
) -> StoredOccurrence:
    headers = filter_safe_response_headers(
        raw_headers or [("Content-Type", "application/json")]
    )
    result = record_response_occurrence(
        db,
        group_id=graph.group_id,
        group_page_id=graph.page_ids[page_index],
        source_id=database.source_id,
        ingestion_run_id=graph.run_id,
        entity_body=entity_body,
        compatibility_context=ExactRawCompatibilityContext(
            ingestion_run_id=graph.run_id,
            collected_at=response_at,
            source_url=response_url,
            source_endpoint="bvc_price_snapshot_json_page",
            http_status=200,
            content_type="application/json",
        ),
        request_sequence=request_sequence,
        attempt_number=1,
        redirect_hop=0,
        logical_request_url=response_url,
        requested_url=response_url,
        response_url=response_url,
        source_endpoint="bvc_price_snapshot_json_page",
        request_profile="bvc-json-safe-v1",
        requested_at=response_at - timedelta(milliseconds=100),
        response_received_at=response_at,
        finished_at=response_at + timedelta(milliseconds=100),
        http_status=200,
        outcome="success_response",
        content_type="application/json",
        safe_response_headers=headers.safe_response_headers,
        dropped_response_header_name_count=(
            headers.dropped_response_header_name_count
        ),
        response_headers_overflow=headers.response_headers_overflow,
        response_headers_policy_version=headers.policy_version,
    )
    assert result.raw_payload is not None
    return StoredOccurrence(
        occurrence_id=result.occurrence.id,
        raw_payload_id=result.raw_payload.id,
        raw_content_inserted=result.raw_content_inserted,
    )


def _count(db: Session, model) -> int:
    return db.scalar(select(func.count()).select_from(model)) or 0


def test_concurrent_identical_exact_content_converges_without_losing_occurrences(
    raw_first_database: RawFirstDatabase,
):
    body = b'{"data":{"data":[{"id":"CONCURRENT"}]}}'
    graphs = (
        _create_graph(raw_first_database, label="concurrent-a"),
        _create_graph(raw_first_database, label="concurrent-b"),
    )
    barrier = threading.Barrier(2)

    def worker(index: int) -> StoredOccurrence:
        graph = graphs[index]
        with raw_first_database.sessions() as db:
            with db.begin():
                # Hold distinct group locks before releasing both transactions
                # toward the same partial raw-content unique index.
                db.execute(
                    select(CollectionGroup.id)
                    .where(CollectionGroup.id == graph.group_id)
                    .with_for_update()
                ).scalar_one()
                barrier.wait(timeout=10)
                return _record_response(
                    db,
                    raw_first_database,
                    graph,
                    page_index=0,
                    entity_body=body,
                    request_sequence=1,
                    response_url=(
                        "https://www.casablanca-bourse.com/prices"
                        f"?page%5Boffset%5D={index * PAGE_LIMIT}"
                        f"&page%5Blimit%5D={PAGE_LIMIT}"
                    ),
                    response_at=RESPONSE_AT + timedelta(seconds=index),
                )

    with ThreadPoolExecutor(max_workers=2) as executor:
        stored = tuple(executor.map(worker, range(2)))

    assert stored[0].raw_payload_id == stored[1].raw_payload_id
    assert sorted(item.raw_content_inserted for item in stored) == [False, True]
    assert stored[0].occurrence_id != stored[1].occurrence_id

    with raw_first_database.sessions() as db:
        assert _count(db, RawPayload) == 1
        assert _count(db, CollectionOccurrence) == 2
        occurrences = list(
            db.scalars(
                select(CollectionOccurrence).order_by(
                    CollectionOccurrence.occurrence_sequence
                )
            )
        )
        assert {row.ingestion_run_id for row in occurrences} == {
            graph.run_id for graph in graphs
        }
        assert {row.group_page_id for row in occurrences} == {
            graph.page_ids[0] for graph in graphs
        }


@pytest.mark.parametrize(
    "body",
    (
        pytest.param(b"", id="zero-byte-content"),
        pytest.param(b'{"same":"body-across-pages"}', id="nonempty-content"),
    ),
)
def test_duplicate_content_across_pages_reuses_raw_and_keeps_occurrences(
    raw_first_database: RawFirstDatabase,
    body: bytes,
):
    graph = _create_graph(raw_first_database, label="duplicate-pages", page_count=2)
    stored: list[StoredOccurrence] = []
    for page_index in range(2):
        with raw_first_database.sessions.begin() as db:
            stored.append(
                _record_response(
                    db,
                    raw_first_database,
                    graph,
                    page_index=page_index,
                    entity_body=body,
                    request_sequence=page_index + 1,
                    response_url=(
                        "https://www.casablanca-bourse.com/prices"
                        f"?page%5Boffset%5D={page_index * PAGE_LIMIT}"
                        f"&page%5Blimit%5D={PAGE_LIMIT}"
                    ),
                    response_at=RESPONSE_AT + timedelta(seconds=page_index),
                )
            )

    assert stored[0].raw_payload_id == stored[1].raw_payload_id
    assert [item.raw_content_inserted for item in stored] == [True, False]
    with raw_first_database.sessions() as db:
        raw = db.get(RawPayload, stored[0].raw_payload_id)
        assert raw is not None
        assert bytes(raw.entity_body or b"") == body
        assert raw.entity_body_length == len(body)
        assert raw.entity_body_sha256 == hashlib.sha256(body).hexdigest()
        assert _count(db, RawPayload) == 1
        assert _count(db, CollectionOccurrence) == 2
        assert set(
            db.scalars(select(CollectionOccurrence.group_page_id))
        ) == set(graph.page_ids)


def test_occurrence_ownership_mismatch_is_rejected_without_orphan_raw_content(
    raw_first_database: RawFirstDatabase,
):
    first = _create_graph(raw_first_database, label="ownership-first")
    second = _create_graph(raw_first_database, label="ownership-second")

    with raw_first_database.sessions() as db:
        with pytest.raises(
            CollectionAuditStateError,
            match="logical page does not belong",
        ):
            _record_response(
                db,
                raw_first_database,
                AuditGraph(
                    run_id=first.run_id,
                    group_id=first.group_id,
                    page_ids=second.page_ids,
                ),
                page_index=0,
                entity_body=b"must roll back before raw storage",
                request_sequence=1,
                response_url="https://www.casablanca-bourse.com/prices",
            )
        db.rollback()

    with raw_first_database.sessions() as db:
        assert _count(db, RawPayload) == 0
        assert _count(db, CollectionOccurrence) == 0


def test_safe_header_helper_keeps_denied_headers_out_of_occurrence_jsonb(
    raw_first_database: RawFirstDatabase,
):
    graph = _create_graph(raw_first_database, label="safe-headers")
    secret_cookie = "session=never-store-this-cookie"
    secret_waf = "never-store-this-waf-token"
    with raw_first_database.sessions.begin() as db:
        stored = _record_response(
            db,
            raw_first_database,
            graph,
            page_index=0,
            entity_body=b'{"data":{"data":[{"id":"SAFE"}]}}',
            request_sequence=1,
            response_url="https://www.casablanca-bourse.com/prices",
            raw_headers=[
                ("Content-Type", "application/json"),
                ("Cache-Control", "no-cache"),
                ("Set-Cookie", secret_cookie),
                ("X-WAF-Token", secret_waf),
            ],
        )

    with raw_first_database.sessions() as db:
        occurrence = db.get(CollectionOccurrence, stored.occurrence_id)
        raw = db.get(RawPayload, stored.raw_payload_id)
        assert occurrence is not None
        assert occurrence.safe_response_headers == {
            "cache-control": ["no-cache"],
            "content-type": ["application/json"],
        }
        assert occurrence.dropped_response_header_name_count == 2
        assert occurrence.response_headers_policy_version == SAFE_POLICY_VERSION
        persisted_headers = json.dumps(occurrence.safe_response_headers)
        assert "cookie" not in persisted_headers.casefold()
        assert "waf" not in persisted_headers.casefold()
        assert secret_cookie not in persisted_headers
        assert secret_waf not in persisted_headers
        assert raw is not None
        assert raw.metadata_ is None


def test_page_selection_accepts_only_its_owning_occurrence(
    raw_first_database: RawFirstDatabase,
):
    graph = _create_graph(raw_first_database, label="selection", page_count=2)
    with raw_first_database.sessions.begin() as db:
        stored = _record_response(
            db,
            raw_first_database,
            graph,
            page_index=0,
            entity_body=b'{"data":{"data":[{"id":"SELECT"}]}}',
            request_sequence=1,
            response_url="https://www.casablanca-bourse.com/prices",
        )

    with raw_first_database.sessions() as db:
        with pytest.raises(
            CollectionAuditStateError,
            match="selected occurrence does not own",
        ):
            finalize_page_with_selection(
                db,
                group_id=graph.group_id,
                group_page_id=graph.page_ids[1],
                occurrence_id=stored.occurrence_id,
                page_role="data",
                selected_at=SELECTION_AT,
                selection_reason="first_qualifying_success",
            )
        db.rollback()

    with raw_first_database.sessions.begin() as db:
        selection = finalize_page_with_selection(
            db,
            group_id=graph.group_id,
            group_page_id=graph.page_ids[0],
            occurrence_id=stored.occurrence_id,
            page_role="data",
            selected_at=SELECTION_AT,
            selection_reason="first_qualifying_success",
        )
        assert selection.group_page_id == graph.page_ids[0]
        assert selection.occurrence_id == stored.occurrence_id

    with raw_first_database.sessions() as db:
        page = db.get(CollectionGroupPage, graph.page_ids[0])
        selection = db.get(CollectionPageSelection, graph.page_ids[0])
        assert page is not None
        assert page.page_role == "data"
        assert page.collection_page_outcome == "success"
        assert selection is not None
        assert selection.occurrence_id == stored.occurrence_id


def test_parser_failure_after_audit_commit_leaves_raw_and_occurrence_evidence(
    raw_first_database: RawFirstDatabase,
):
    graph = _create_graph(raw_first_database, label="parser-failure")
    malformed_body = b'{"data":'
    with raw_first_database.sessions.begin() as db:
        stored = _record_response(
            db,
            raw_first_database,
            graph,
            page_index=0,
            entity_body=malformed_body,
            request_sequence=1,
            response_url="https://www.casablanca-bourse.com/prices",
        )

    with pytest.raises(json.JSONDecodeError):
        json.loads(malformed_body)

    with raw_first_database.sessions() as db:
        raw = db.get(RawPayload, stored.raw_payload_id)
        occurrence = db.get(CollectionOccurrence, stored.occurrence_id)
        page = db.get(CollectionGroupPage, graph.page_ids[0])
        assert raw is not None
        assert bytes(raw.entity_body or b"") == malformed_body
        assert occurrence is not None
        assert occurrence.raw_payload_id == raw.id
        assert page is not None
        assert page.collection_page_outcome == "pending"
        assert db.get(CollectionPageSelection, page.id) is None


def test_legacy_payload_hash_unique_constraint_remains_enforced(
    raw_first_database: RawFirstDatabase,
):
    graph = _create_graph(raw_first_database, label="legacy-unique")
    duplicate_hash = "a" * 64
    with raw_first_database.sessions.begin() as db:
        db.add(
            RawPayload(
                source_id=raw_first_database.source_id,
                ingestion_run_id=graph.run_id,
                source_url="https://www.casablanca-bourse.com/legacy",
                payload_type="bvc_price_snapshot",
                payload_text="legacy-one",
                payload_hash=duplicate_hash,
                collected_at=RESPONSE_AT,
                status="collected",
            )
        )

    with pytest.raises(IntegrityError) as caught:
        with raw_first_database.sessions.begin() as db:
            db.add(
                RawPayload(
                    source_id=raw_first_database.source_id,
                    ingestion_run_id=graph.run_id,
                    source_url="https://www.casablanca-bourse.com/legacy-two",
                    payload_type="bvc_price_snapshot",
                    payload_text="legacy-two",
                    payload_hash=duplicate_hash,
                    collected_at=RESPONSE_AT,
                    status="collected",
                )
            )

    diagnostic = getattr(caught.value.orig, "diag", None)
    assert diagnostic is not None
    assert diagnostic.constraint_name == "uq_raw_payloads_source_payload_hash"
    with raw_first_database.sessions() as db:
        assert _count(db, RawPayload) == 1


def test_exact_compatibility_filler_is_independent_of_legacy_url_text_identity(
    raw_first_database: RawFirstDatabase,
):
    graph = _create_graph(raw_first_database, label="compatibility", page_count=2)
    body_text = '{"data":{"data":[{"id":"COMPAT"}]}}'
    body = body_text.encode("utf-8")
    first_url = (
        "https://www.casablanca-bourse.com/prices"
        "?page%5Boffset%5D=0&page%5Blimit%5D=50"
    )
    second_url = (
        "https://www.casablanca-bourse.com/prices"
        "?page%5Boffset%5D=50&page%5Blimit%5D=50"
    )
    legacy_hash = sha256_source_payload(source_url=first_url, body_text=body_text)
    with raw_first_database.sessions.begin() as db:
        db.add(
            RawPayload(
                source_id=raw_first_database.source_id,
                ingestion_run_id=graph.run_id,
                source_url=first_url,
                payload_type="bvc_price_snapshot",
                payload_text=body_text,
                payload_hash=legacy_hash,
                collected_at=RESPONSE_AT,
                status="collected",
            )
        )

    stored: list[StoredOccurrence] = []
    for page_index, response_url in enumerate((first_url, second_url)):
        with raw_first_database.sessions.begin() as db:
            stored.append(
                _record_response(
                    db,
                    raw_first_database,
                    graph,
                    page_index=page_index,
                    entity_body=body,
                    request_sequence=page_index + 1,
                    response_url=response_url,
                    response_at=RESPONSE_AT + timedelta(seconds=page_index),
                )
            )

    assert stored[0].raw_payload_id == stored[1].raw_payload_id
    exact_digest = exact_entity_body_sha256(body)
    expected_filler = exact_compatibility_payload_hash(
        source_id=raw_first_database.source_id,
        entity_body_sha256=exact_digest,
    )
    with raw_first_database.sessions() as db:
        exact_raw = db.get(RawPayload, stored[0].raw_payload_id)
        assert exact_raw is not None
        assert _count(db, RawPayload) == 2
        assert _count(db, CollectionOccurrence) == 2
        assert exact_raw.payload_hash == expected_filler
        assert exact_raw.payload_hash != legacy_hash
        assert exact_raw.legacy_hash_algorithm == EXACT_COMPATIBILITY_HASH_ALGORITHM
        assert exact_raw.source_url == first_url
        assert exact_raw.payload_text is None
        payload_and_metadata_are_sql_null = db.execute(
            text(
                "SELECT payload IS NULL, metadata IS NULL "
                "FROM raw_payloads WHERE id = :raw_id"
            ),
            {"raw_id": exact_raw.id},
        ).one()
        assert payload_and_metadata_are_sql_null == (True, True)
