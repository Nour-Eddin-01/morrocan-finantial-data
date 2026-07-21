import pytest

from tests.postgres.harness import (
    MissingMigrationDatabaseUrl,
    UnsafeDatabaseOperation,
    UnsafeMigrationDatabaseUrl,
    load_test_database_url,
    new_disposable_database_name,
    require_disposable_database_name,
    validate_test_database_url,
)


pytestmark = pytest.mark.postgres


def test_postgres_test_url_is_required_and_never_falls_back_to_application_url():
    environment = {
        "TRADEHUB_DATA_DATABASE_URL": (
            "postgresql+psycopg://application@127.0.0.1:5432/tradehub_data"
        )
    }

    with pytest.raises(MissingMigrationDatabaseUrl):
        load_test_database_url(environment)


@pytest.mark.parametrize(
    "database_url",
    [
        "postgresql+psycopg://user:synthetic-secret@127.0.0.1:5432/tradehub_data",
        "postgresql+psycopg://user:synthetic-secret@127.0.0.1:5432/postgres",
        "postgresql+psycopg://user:synthetic-secret@127.0.0.1:5432/template0",
        "postgresql+psycopg://user:synthetic-secret@127.0.0.1:5432/market_data",
        "sqlite+pysqlite:///tradehub_data_test.db",
        "not a database URL",
    ],
)
def test_unsafe_or_non_test_database_urls_are_rejected_without_credentials(database_url):
    with pytest.raises(UnsafeMigrationDatabaseUrl) as error:
        validate_test_database_url(database_url)

    assert "synthetic-secret" not in str(error.value)


def test_clearly_named_postgresql_test_database_url_is_accepted():
    url = validate_test_database_url(
        "postgresql+psycopg://tradehub_data_test:synthetic-secret@127.0.0.1:55432/"
        "tradehub_data_test"
    )

    assert url.get_backend_name() == "postgresql"
    assert url.database == "tradehub_data_test"


def test_only_generated_child_database_names_are_disposable():
    generated_name = new_disposable_database_name()
    require_disposable_database_name(generated_name)

    for unsafe_name in (
        "tradehub_data",
        "tradehub_data_test",
        "postgres",
        "tradehub_data_test_not-a-uuid",
        'tradehub_data_test_x"; DROP DATABASE tradehub_data; --',
    ):
        with pytest.raises(UnsafeDatabaseOperation):
            require_disposable_database_name(unsafe_name)
