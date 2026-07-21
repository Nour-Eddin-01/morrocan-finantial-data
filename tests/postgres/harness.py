from __future__ import annotations

import hashlib
import os
import re
import uuid
import warnings
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import MetaData, Table, create_engine, inspect
from sqlalchemy.engine import Engine, URL, make_url
from sqlalchemy.exc import ArgumentError, SAWarning
from sqlalchemy.pool import NullPool


TEST_DATABASE_URL_ENV = "TRADEHUB_DATA_TEST_DATABASE_URL"
ALEMBIC_DATABASE_URL_ATTRIBUTE = "tradehub_data_database_url"
FOUNDATION_REVISION = "0001_initial_foundation"
COLLECTION_AUDIT_REVISION = "0002_add_collection_audit_foundation"
PROCESSING_SELECTION_REVISION = "0003_add_page_selection_and_processing_attempts"
CURRENT_HEAD_REVISION = PROCESSING_SELECTION_REVISION
FOUNDATION_APPLICATION_TABLES = frozenset(
    {
        "companies",
        "data_sources",
        "exchanges",
        "index_bars",
        "ingestion_runs",
        "instruments",
        "latest_index_values",
        "latest_prices",
        "market_indices",
        "normalization_errors",
        "price_bars",
        "raw_payloads",
        "sectors",
        "sync_states",
    }
)
COLLECTION_AUDIT_TABLES = frozenset(
    {
        "collection_groups",
        "collection_group_pages",
        "collection_occurrences",
    }
)
PROCESSING_SELECTION_TABLES = frozenset(
    {
        "processing_attempts",
        "collection_page_selections",
    }
)
COLLECTION_AUDIT_APPLICATION_TABLES = FOUNDATION_APPLICATION_TABLES | COLLECTION_AUDIT_TABLES
APPLICATION_TABLES = COLLECTION_AUDIT_APPLICATION_TABLES | PROCESSING_SELECTION_TABLES
FOUNDATION_ALL_MIGRATED_TABLES = FOUNDATION_APPLICATION_TABLES | {"alembic_version"}
COLLECTION_AUDIT_ALL_MIGRATED_TABLES = COLLECTION_AUDIT_APPLICATION_TABLES | {
    "alembic_version"
}
ALL_MIGRATED_TABLES = APPLICATION_TABLES | {"alembic_version"}

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DISPOSABLE_DATABASE_PREFIX = "tradehub_data_test_"
_DISPOSABLE_DATABASE_PATTERN = re.compile(rf"{_DISPOSABLE_DATABASE_PREFIX}[0-9a-f]{{32}}\Z")
_FORBIDDEN_DATABASE_NAMES = frozenset({"postgres", "template0", "template1", "tradehub_data"})


class MigrationDatabaseConfigurationError(ValueError):
    """Base class for credential-safe migration-harness configuration errors."""


class MissingMigrationDatabaseUrl(MigrationDatabaseConfigurationError):
    """Raised when the explicit test-only URL was not supplied."""


class UnsafeMigrationDatabaseUrl(MigrationDatabaseConfigurationError):
    """Raised when a URL is not clearly scoped to disposable PostgreSQL tests."""


class UnsafeDatabaseOperation(RuntimeError):
    """Raised before an operation could target a non-owned database."""


def load_test_database_url(environ: Mapping[str, str] | None = None) -> URL:
    source = os.environ if environ is None else environ
    raw_url = source.get(TEST_DATABASE_URL_ENV)
    if raw_url is None or not raw_url.strip():
        raise MissingMigrationDatabaseUrl(
            f"{TEST_DATABASE_URL_ENV} must be set explicitly for PostgreSQL migration tests"
        )
    return validate_test_database_url(raw_url)


def validate_test_database_url(raw_url: str) -> URL:
    try:
        url = make_url(raw_url)
    except (ArgumentError, TypeError, ValueError) as exc:
        raise UnsafeMigrationDatabaseUrl("the test database URL is malformed") from exc

    if url.get_backend_name() != "postgresql":
        raise UnsafeMigrationDatabaseUrl("the test database URL must use PostgreSQL")

    database_name = url.database
    if not database_name:
        raise UnsafeMigrationDatabaseUrl("the test database URL must name a database")

    normalized_name = database_name.casefold()
    if normalized_name in _FORBIDDEN_DATABASE_NAMES or "test" not in normalized_name:
        raise UnsafeMigrationDatabaseUrl(
            "the configured database name must contain 'test' and must not be a normal or system database"
        )

    return url


def new_disposable_database_name() -> str:
    return f"{_DISPOSABLE_DATABASE_PREFIX}{uuid.uuid4().hex}"


def require_disposable_database_name(database_name: str) -> None:
    if _DISPOSABLE_DATABASE_PATTERN.fullmatch(database_name) is None:
        raise UnsafeDatabaseOperation(
            "refusing an operation on a database not created by the migration-test harness"
        )


def render_database_url(url: URL) -> str:
    return url.render_as_string(hide_password=False)


def make_alembic_config(database_url: URL) -> Config:
    config = Config(str(_PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(_PROJECT_ROOT / "migrations"))
    config.attributes[ALEMBIC_DATABASE_URL_ATTRIBUTE] = render_database_url(database_url)
    return config


def upgrade_database(database_url: URL, revision: str = "head") -> None:
    command.upgrade(make_alembic_config(database_url), revision)


def downgrade_database(database_url: URL, revision: str) -> None:
    command.downgrade(make_alembic_config(database_url), revision)


def make_database_engine(database_url: URL) -> Engine:
    return create_engine(
        database_url,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 5},
    )


def public_table_names(database_url: URL) -> set[str]:
    engine = make_database_engine(database_url)
    try:
        return set(inspect(engine).get_table_names(schema="public"))
    finally:
        engine.dispose()


def database_revision(database_url: URL) -> str | None:
    engine = make_database_engine(database_url)
    try:
        with engine.connect() as connection:
            return MigrationContext.configure(connection).get_current_revision()
    finally:
        engine.dispose()


class DisposablePostgresDatabaseFactory:
    """Creates and drops only UUID-named child databases beneath a validated test database."""

    def __init__(self, admin_url: URL) -> None:
        validate_test_database_url(render_database_url(admin_url))
        self._admin_url = admin_url
        self._admin_engine = create_engine(
            admin_url,
            isolation_level="AUTOCOMMIT",
            poolclass=NullPool,
            connect_args={"connect_timeout": 5},
        )
        self._owned_database_names: set[str] = set()

    def assert_postgresql_16(self) -> None:
        with self._admin_engine.connect() as connection:
            server_version_num = int(connection.exec_driver_sql("SHOW server_version_num").scalar_one())
        if server_version_num // 10_000 != 16:
            raise UnsafeMigrationDatabaseUrl("the migration-test service must run PostgreSQL 16")

    @contextmanager
    def database(self) -> Iterator[URL]:
        database_name = new_disposable_database_name()
        self._create_database(database_name)
        try:
            yield self._admin_url.set(database=database_name)
        finally:
            self._drop_database(database_name)

    def database_exists(self, database_name: str) -> bool:
        require_disposable_database_name(database_name)
        with self._admin_engine.connect() as connection:
            return bool(
                connection.exec_driver_sql(
                    "SELECT EXISTS (SELECT 1 FROM pg_database WHERE datname = %s)",
                    (database_name,),
                ).scalar_one()
            )

    def close(self) -> None:
        try:
            for database_name in tuple(self._owned_database_names):
                self._drop_database(database_name)
        finally:
            self._admin_engine.dispose()

    def _create_database(self, database_name: str) -> None:
        require_disposable_database_name(database_name)
        if database_name == self._admin_url.database:
            raise UnsafeDatabaseOperation("the configured test database cannot be used as a child database")
        quoted_name = _quoted_disposable_database_name(database_name)
        with self._admin_engine.connect() as connection:
            connection.exec_driver_sql(f"CREATE DATABASE {quoted_name}")
        self._owned_database_names.add(database_name)

    def _drop_database(self, database_name: str) -> None:
        require_disposable_database_name(database_name)
        if database_name not in self._owned_database_names:
            raise UnsafeDatabaseOperation("refusing to drop a database not owned by this harness instance")
        if database_name == self._admin_url.database:
            raise UnsafeDatabaseOperation("refusing to drop the configured test database")
        quoted_name = _quoted_disposable_database_name(database_name)
        with self._admin_engine.connect() as connection:
            connection.exec_driver_sql(f"DROP DATABASE {quoted_name} WITH (FORCE)")
        self._owned_database_names.remove(database_name)


def _quoted_disposable_database_name(database_name: str) -> str:
    require_disposable_database_name(database_name)
    return f'"{database_name}"'


@dataclass(frozen=True)
class LegacyFoundationBaseline:
    database_url: URL = field(repr=False)
    ids: Mapping[str, uuid.UUID]


@dataclass(frozen=True)
class CollectionAuditBaseline:
    database_url: URL = field(repr=False)
    ids: Mapping[str, uuid.UUID]


def seed_legacy_foundation_baseline(database_url: URL) -> LegacyFoundationBaseline:
    ids = {
        entity: uuid.uuid5(uuid.NAMESPACE_URL, f"https://example.test/tradehub-data/{entity}")
        for entity in (
            "exchange",
            "source",
            "run",
            "raw_payload",
            "instrument",
            "latest_price",
            "price_bar",
            "normalization_error",
            "sync_state",
        )
    }
    observed_at = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
    trading_date = date(2026, 1, 15)
    payload_text = '{"rows":[],"synthetic":true}'

    engine = make_database_engine(database_url)
    try:
        metadata = MetaData()
        table_names = (
            "exchanges",
            "data_sources",
            "ingestion_runs",
            "raw_payloads",
            "instruments",
            "latest_prices",
            "price_bars",
            "normalization_errors",
            "sync_states",
        )
        tables = {
            table_name: Table(table_name, metadata, autoload_with=engine)
            for table_name in table_names
        }

        with engine.begin() as connection:
            connection.execute(
                tables["exchanges"].insert(),
                {
                    "id": ids["exchange"],
                    "code": "BVC",
                    "name": "Synthetic BVC",
                    "country_code": "MA",
                    "currency_code": "MAD",
                    "timezone": "Africa/Casablanca",
                },
            )
            connection.execute(
                tables["data_sources"].insert(),
                {
                    "id": ids["source"],
                    "code": "bvc_prices",
                    "name": "Synthetic BVC Prices",
                    "source_type": "exchange",
                    "base_url": "https://example.test",
                    "country_code": "MA",
                },
            )
            connection.execute(
                tables["ingestion_runs"].insert(),
                {
                    "id": ids["run"],
                    "source_id": ids["source"],
                    "collector_name": "synthetic_migration_baseline",
                    "run_type": "manual",
                    "status": "partial_success",
                    "started_at": observed_at,
                    "finished_at": observed_at,
                    "records_collected": 2,
                    "records_inserted": 1,
                    "records_updated": 0,
                    "records_failed": 1,
                },
            )
            connection.execute(
                tables["raw_payloads"].insert(),
                {
                    "id": ids["raw_payload"],
                    "source_id": ids["source"],
                    "ingestion_run_id": ids["run"],
                    "source_url": "https://example.test/bvc/prices",
                    "source_endpoint": "/bvc/prices",
                    "payload_type": "json",
                    "payload": {"rows": [], "synthetic": True},
                    "payload_text": payload_text,
                    "payload_hash": hashlib.sha256(payload_text.encode("utf-8")).hexdigest(),
                    "http_status": 200,
                    "content_type": "application/json",
                    "collected_at": observed_at,
                    "status": "normalized",
                    "metadata": {"fixture": "synthetic_migration_baseline"},
                },
            )
            connection.execute(
                tables["instruments"].insert(),
                {
                    "id": ids["instrument"],
                    "exchange_id": ids["exchange"],
                    "symbol": "TST",
                    "isin": "MA0000000000",
                    "name": "Synthetic Instrument",
                    "instrument_type": "equity",
                    "currency_code": "MAD",
                    "source_id": ids["source"],
                    "raw_payload_id": ids["raw_payload"],
                    "last_seen_at": observed_at,
                },
            )
            connection.execute(
                tables["latest_prices"].insert(),
                {
                    "id": ids["latest_price"],
                    "instrument_id": ids["instrument"],
                    "price": Decimal("100.000000"),
                    "price_timestamp": observed_at,
                    "trading_date": trading_date,
                    "source_id": ids["source"],
                    "raw_payload_id": ids["raw_payload"],
                    "data_quality_status": "valid",
                },
            )
            connection.execute(
                tables["price_bars"].insert(),
                {
                    "id": ids["price_bar"],
                    "instrument_id": ids["instrument"],
                    "timeframe": "1d",
                    "bar_timestamp": observed_at,
                    "trading_date": trading_date,
                    "close_price": Decimal("100.000000"),
                    "source_id": ids["source"],
                    "raw_payload_id": ids["raw_payload"],
                    "data_quality_status": "valid",
                },
            )
            connection.execute(
                tables["normalization_errors"].insert(),
                {
                    "id": ids["normalization_error"],
                    "raw_payload_id": ids["raw_payload"],
                    "ingestion_run_id": ids["run"],
                    "source_id": ids["source"],
                    "entity_type": "instrument",
                    "error_type": "synthetic_validation",
                    "error_message": "Synthetic baseline error",
                    "status": "open",
                },
            )
            connection.execute(
                tables["sync_states"].insert(),
                {
                    "id": ids["sync_state"],
                    "component_name": "bvc_prices_test_baseline",
                    "component_type": "collector",
                    "status": "degraded",
                    "last_failure_at": observed_at,
                    "last_run_id": ids["run"],
                    "message": "Synthetic baseline state",
                },
            )
    finally:
        engine.dispose()

    return LegacyFoundationBaseline(database_url=database_url, ids=ids)


def seed_legacy_raw_evidence_variants(
    baseline: LegacyFoundationBaseline,
) -> Mapping[str, uuid.UUID]:
    variant_ids = {
        "jsonb_only": uuid.uuid5(
            uuid.NAMESPACE_URL,
            "https://example.test/tradehub-data/raw-payload-jsonb-only",
        ),
        "body_missing": uuid.uuid5(
            uuid.NAMESPACE_URL,
            "https://example.test/tradehub-data/raw-payload-body-missing",
        ),
    }
    observed_at = datetime(2026, 1, 15, 12, 5, tzinfo=UTC)
    engine = make_database_engine(baseline.database_url)
    try:
        metadata = MetaData()
        raw_payloads = Table("raw_payloads", metadata, autoload_with=engine)
        with engine.begin() as connection:
            connection.execute(
                raw_payloads.insert(),
                {
                    "id": variant_ids["jsonb_only"],
                    "source_id": baseline.ids["source"],
                    "ingestion_run_id": baseline.ids["run"],
                    "source_url": "https://example.test/bvc/jsonb-only",
                    "source_endpoint": "/bvc/jsonb-only",
                    "payload_type": "json",
                    "payload": {"synthetic": "jsonb-only"},
                    "payload_text": None,
                    "payload_hash": hashlib.sha256(b"synthetic-jsonb-only").hexdigest(),
                    "http_status": 200,
                    "content_type": "application/json",
                    "collected_at": observed_at,
                    "status": "collected",
                    "metadata": {"fixture": "synthetic-jsonb-only"},
                },
            )
            # Omit both body columns so PostgreSQL stores SQL NULL. Passing
            # Python None through JSONB would represent JSON null, which is
            # evidence that a decoded JSON value existed rather than evidence
            # that the legacy body is missing.
            connection.execute(
                raw_payloads.insert(),
                {
                    "id": variant_ids["body_missing"],
                    "source_id": baseline.ids["source"],
                    "ingestion_run_id": baseline.ids["run"],
                    "source_url": "https://example.test/bvc/body-missing",
                    "source_endpoint": "/bvc/body-missing",
                    "payload_type": "json",
                    "payload_hash": hashlib.sha256(b"synthetic-body-missing").hexdigest(),
                    "http_status": 200,
                    "content_type": "application/json",
                    "collected_at": observed_at,
                    "status": "failed",
                    "metadata": {"fixture": "synthetic-body-missing"},
                },
            )
    finally:
        engine.dispose()

    return variant_ids


def seed_collection_audit_baseline(database_url: URL) -> CollectionAuditBaseline:
    """Seed synthetic 0002 acquisition evidence without inventing 0003 history."""

    ids = {
        entity: uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"https://example.test/tradehub-data/collection-audit/{entity}",
        )
        for entity in (
            "exchange",
            "source",
            "run",
            "raw_payload",
            "group",
            "page",
            "occurrence",
        )
    }
    started_at = datetime(2026, 7, 21, 10, 0, tzinfo=UTC)
    response_at = datetime(2026, 7, 21, 10, 0, 1, tzinfo=UTC)
    entity_body = b'{"rows":[{"symbol":"TST"}],"synthetic":true}'
    entity_digest = hashlib.sha256(entity_body).hexdigest()

    engine = make_database_engine(database_url)
    try:
        metadata = MetaData()
        table_names = (
            "exchanges",
            "data_sources",
            "ingestion_runs",
            "raw_payloads",
            "collection_groups",
            "collection_group_pages",
            "collection_occurrences",
        )
        # SQLAlchemy reflects PostgreSQL NOT VALID check options from 0002
        # under a generic dialect_options key that its own CheckConstraint
        # constructor warns about. Suppress only that known warning while
        # keeping this fixture pinned to the actual 0002 catalog.
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message="Can't validate argument 'dialect_options'.*",
                category=SAWarning,
            )
            tables = {
                table_name: Table(table_name, metadata, autoload_with=engine)
                for table_name in table_names
            }

        with engine.begin() as connection:
            connection.execute(
                tables["exchanges"].insert(),
                {
                    "id": ids["exchange"],
                    "code": "BVC-AUDIT-BASELINE",
                    "name": "Synthetic BVC collection-audit baseline",
                    "country_code": "MA",
                    "currency_code": "MAD",
                    "timezone": "Africa/Casablanca",
                },
            )
            connection.execute(
                tables["data_sources"].insert(),
                {
                    "id": ids["source"],
                    "code": "bvc_prices_audit_baseline",
                    "name": "Synthetic BVC prices audit baseline",
                    "source_type": "exchange",
                    "base_url": "https://example.test",
                    "country_code": "MA",
                },
            )
            connection.execute(
                tables["ingestion_runs"].insert(),
                {
                    "id": ids["run"],
                    "source_id": ids["source"],
                    "collector_name": "synthetic_collection_audit_baseline",
                    "run_type": "manual",
                    "run_role": "acquisition",
                    "status": "success",
                    "started_at": started_at,
                    "finished_at": response_at,
                    "records_collected": 1,
                    "records_inserted": 1,
                    "records_updated": 0,
                    "records_failed": 0,
                },
            )
            connection.execute(
                tables["raw_payloads"].insert(),
                {
                    "id": ids["raw_payload"],
                    "source_id": ids["source"],
                    "ingestion_run_id": ids["run"],
                    "source_url": "https://example.test/bvc/prices?offset=0&limit=80",
                    "source_endpoint": "/bvc/prices",
                    "payload_type": "json",
                    "payload": {"rows": [{"symbol": "TST"}], "synthetic": True},
                    "payload_text": entity_body.decode("utf-8"),
                    "payload_hash": hashlib.sha256(b"legacy-audit-baseline").hexdigest(),
                    "entity_body": entity_body,
                    "entity_body_sha256": entity_digest,
                    "entity_body_length": len(entity_body),
                    "content_evidence_kind": "exact_entity_bytes",
                    "entity_hash_algorithm": "sha256_entity_body_v1",
                    "storage_status": "stored",
                    "legacy_hash_algorithm": None,
                    "http_status": 200,
                    "content_type": "application/json",
                    "collected_at": response_at,
                    "status": "collected",
                },
            )
            connection.execute(
                tables["collection_groups"].insert(),
                {
                    "id": ids["group"],
                    "source_id": ids["source"],
                    "exchange_id": ids["exchange"],
                    "ingestion_run_id": ids["run"],
                    "dataset_code": "bvc_prices",
                    "collection_mode": "live_json",
                    "group_purpose": "validation",
                    "page_limit": 80,
                    "started_at": started_at,
                    "collection_completed_at": response_at,
                    "collection_status": "success",
                    "pagination_complete": True,
                    "completion_evidence_kind": "short_page",
                    "expected_pages": 1,
                    "selected_data_pages": 1,
                    "terminal_page_present": False,
                    "coverage_status": "proven",
                    "expected_instrument_count": 1,
                    "observed_instrument_count": 1,
                    "safe_diagnostic_codes": [],
                    "finalized_at": response_at,
                },
            )
            connection.execute(
                tables["collection_group_pages"].insert(),
                {
                    "id": ids["page"],
                    "group_id": ids["group"],
                    "source_id": ids["source"],
                    "ingestion_run_id": ids["run"],
                    "page_limit": 80,
                    "logical_page_number": 1,
                    "page_offset": 0,
                    "page_role": "data",
                    "collection_page_outcome": "success",
                    "finalized_at": response_at,
                },
            )
            connection.execute(
                tables["collection_occurrences"].insert(),
                {
                    "id": ids["occurrence"],
                    "source_id": ids["source"],
                    "ingestion_run_id": ids["run"],
                    "group_page_id": ids["page"],
                    "raw_payload_id": ids["raw_payload"],
                    "request_sequence": 1,
                    "attempt_number": 1,
                    "redirect_hop": 0,
                    "logical_request_url": (
                        "https://example.test/bvc/prices?offset=0&limit=80"
                    ),
                    "requested_url": (
                        "https://example.test/bvc/prices?offset=0&limit=80"
                    ),
                    "response_url": (
                        "https://example.test/bvc/prices?offset=0&limit=80"
                    ),
                    "source_endpoint": "/bvc/prices",
                    "request_profile": "synthetic_migration_v1",
                    "requested_at": started_at,
                    "response_received_at": response_at,
                    "finished_at": response_at,
                    "http_status": 200,
                    "content_type": "application/json",
                    "body_length": len(entity_body),
                    "outcome": "success_response",
                    "safe_response_headers": {
                        "content-type": ["application/json"]
                    },
                    "dropped_response_header_name_count": 0,
                    "response_headers_overflow": False,
                    "response_headers_policy_version": "synthetic_allowlist_v1",
                },
            )
    finally:
        engine.dispose()

    return CollectionAuditBaseline(database_url=database_url, ids=ids)
