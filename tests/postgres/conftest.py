from collections.abc import Generator

import pytest
from sqlalchemy.engine import URL

from tests.postgres.harness import (
    COLLECTION_AUDIT_REVISION,
    FOUNDATION_REVISION,
    CollectionAuditBaseline,
    DisposablePostgresDatabaseFactory,
    LegacyFoundationBaseline,
    MigrationDatabaseConfigurationError,
    MissingMigrationDatabaseUrl,
    load_test_database_url,
    seed_collection_audit_baseline,
    seed_legacy_foundation_baseline,
    upgrade_database,
)


@pytest.fixture(scope="session")
def postgres_database_factory() -> Generator[DisposablePostgresDatabaseFactory, None, None]:
    try:
        admin_url = load_test_database_url()
    except MissingMigrationDatabaseUrl:
        pytest.skip(
            "TRADEHUB_DATA_TEST_DATABASE_URL is not set; isolated PostgreSQL migration tests skipped"
        )
    except MigrationDatabaseConfigurationError as exc:
        pytest.fail(f"unsafe PostgreSQL migration-test configuration: {exc}", pytrace=False)

    factory = DisposablePostgresDatabaseFactory(admin_url)
    try:
        try:
            factory.assert_postgresql_16()
        except Exception as exc:
            pytest.fail(
                f"isolated PostgreSQL 16 test service is unavailable ({type(exc).__name__})",
                pytrace=False,
            )
        yield factory
    finally:
        factory.close()


@pytest.fixture()
def empty_postgres_database_url(
    postgres_database_factory: DisposablePostgresDatabaseFactory,
) -> Generator[URL, None, None]:
    with postgres_database_factory.database() as database_url:
        yield database_url


@pytest.fixture()
def legacy_foundation_database(
    postgres_database_factory: DisposablePostgresDatabaseFactory,
) -> Generator[LegacyFoundationBaseline, None, None]:
    with postgres_database_factory.database() as database_url:
        upgrade_database(database_url, FOUNDATION_REVISION)
        yield seed_legacy_foundation_baseline(database_url)


@pytest.fixture()
def collection_audit_baseline_database(
    postgres_database_factory: DisposablePostgresDatabaseFactory,
) -> Generator[CollectionAuditBaseline, None, None]:
    with postgres_database_factory.database() as database_url:
        upgrade_database(database_url, COLLECTION_AUDIT_REVISION)
        yield seed_collection_audit_baseline(database_url)
