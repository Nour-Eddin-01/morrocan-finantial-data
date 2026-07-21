from alembic.script import ScriptDirectory
import pytest
from sqlalchemy import inspect, text

from tests.postgres.harness import (
    ALL_MIGRATED_TABLES,
    APPLICATION_TABLES,
    COLLECTION_AUDIT_TABLES,
    CURRENT_HEAD_REVISION,
    FOUNDATION_ALL_MIGRATED_TABLES,
    FOUNDATION_APPLICATION_TABLES,
    FOUNDATION_REVISION,
    PROCESSING_SELECTION_TABLES,
    DisposablePostgresDatabaseFactory,
    LegacyFoundationBaseline,
    database_revision,
    make_alembic_config,
    make_database_engine,
    public_table_names,
    upgrade_database,
)


pytestmark = pytest.mark.postgres


EXPECTED_UNIQUE_CONSTRAINTS = {
    "exchanges": {"uq_exchanges_code": ("code",)},
    "data_sources": {"uq_data_sources_code": ("code",)},
    "instruments": {
        "uq_instruments_exchange_symbol": ("exchange_id", "symbol"),
        "uq_instruments_exchange_isin": ("exchange_id", "isin"),
    },
    "latest_prices": {"uq_latest_prices_instrument_id": ("instrument_id",)},
    "price_bars": {
        "uq_price_bars_instrument_timeframe_timestamp": (
            "instrument_id",
            "timeframe",
            "bar_timestamp",
        )
    },
    "raw_payloads": {
        "uq_raw_payloads_source_payload_hash": ("source_id", "payload_hash")
    },
    "sync_states": {"uq_sync_states_component_name": ("component_name",)},
}


def test_empty_database_upgrades_to_current_head_and_expected_catalog(
    empty_postgres_database_url,
    monkeypatch,
):
    monkeypatch.setenv(
        "TRADEHUB_DATA_DATABASE_URL",
        "postgresql+psycopg://unused@127.0.0.1:1/tradehub_data",
    )
    assert public_table_names(empty_postgres_database_url) == set()

    alembic_config = make_alembic_config(empty_postgres_database_url)
    script = ScriptDirectory.from_config(alembic_config)
    assert script.get_heads() == [CURRENT_HEAD_REVISION]
    assert script.get_current_head() == CURRENT_HEAD_REVISION

    upgrade_database(empty_postgres_database_url, "head")

    assert database_revision(empty_postgres_database_url) == CURRENT_HEAD_REVISION
    assert len(FOUNDATION_APPLICATION_TABLES) == 14
    assert len(COLLECTION_AUDIT_TABLES) == 3
    assert len(PROCESSING_SELECTION_TABLES) == 2
    assert len(APPLICATION_TABLES) == 19
    assert public_table_names(empty_postgres_database_url) == ALL_MIGRATED_TABLES

    engine = make_database_engine(empty_postgres_database_url)
    try:
        inspector = inspect(engine)
        for table_name, expected_constraints in EXPECTED_UNIQUE_CONSTRAINTS.items():
            actual_constraints = {
                constraint["name"]: tuple(constraint["column_names"])
                for constraint in inspector.get_unique_constraints(table_name, schema="public")
            }
            for constraint_name, expected_columns in expected_constraints.items():
                assert actual_constraints[constraint_name] == expected_columns
    finally:
        engine.dispose()


def test_legacy_foundation_fixture_can_be_read_safely(
    legacy_foundation_database: LegacyFoundationBaseline,
):
    assert database_revision(legacy_foundation_database.database_url) == FOUNDATION_REVISION

    engine = make_database_engine(legacy_foundation_database.database_url)
    try:
        with engine.connect() as connection:
            assert connection.execute(text("SELECT code FROM exchanges")).scalar_one() == "BVC"
            assert connection.execute(text("SELECT code FROM data_sources")).scalar_one() == "bvc_prices"
            assert connection.execute(text("SELECT status FROM ingestion_runs")).scalar_one() == "partial_success"
            assert connection.execute(text("SELECT symbol FROM instruments")).scalar_one() == "TST"
            assert str(connection.execute(text("SELECT price FROM latest_prices")).scalar_one()) == "100.000000"
            assert connection.execute(text("SELECT timeframe FROM price_bars")).scalar_one() == "1d"
            assert connection.execute(text("SELECT status FROM normalization_errors")).scalar_one() == "open"
            assert connection.execute(text("SELECT component_name FROM sync_states")).scalar_one() == (
                "bvc_prices_test_baseline"
            )
            assert connection.execute(text("SELECT count(*) FROM raw_payloads")).scalar_one() == 1
    finally:
        engine.dispose()


def test_factory_recreates_a_known_empty_database(
    postgres_database_factory: DisposablePostgresDatabaseFactory,
):
    with postgres_database_factory.database() as first_database_url:
        first_database_name = first_database_url.database
        assert first_database_name is not None
        assert public_table_names(first_database_url) == set()
        upgrade_database(first_database_url, FOUNDATION_REVISION)
        assert public_table_names(first_database_url) == FOUNDATION_ALL_MIGRATED_TABLES

    assert postgres_database_factory.database_exists(first_database_name) is False

    with postgres_database_factory.database() as second_database_url:
        assert second_database_url.database != first_database_name
        assert public_table_names(second_database_url) == set()
