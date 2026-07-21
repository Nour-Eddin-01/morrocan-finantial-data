# PostgreSQL Migration Testing

This harness tests Alembic against an isolated PostgreSQL 16 service. It does not use the normal `postgres` Compose service, the `tradehub_data` database, or the persistent `postgres_data` volume.

## Start the isolated service

From the repository root:

```bash
docker compose \
  -p tradehub-data-migration-test \
  -f docker-compose.test.yml \
  up -d --wait --wait-timeout 60 postgres-test
```

The standalone service:

- uses `postgres:16-alpine`;
- binds only to `127.0.0.1:55432` by default;
- stores its PostgreSQL data directory in container tmpfs, not a named volume;
- uses deterministic synthetic credentials and the administrative database `tradehub_data_test`; and
- has a bounded health check used by Compose `--wait`.

If port 55432 is occupied, set `POSTGRES_TEST_PORT` when starting the service and use the same port in the URL below.

## Run PostgreSQL migration tests

The fully isolated Compose runner supplies `TRADEHUB_DATA_TEST_DATABASE_URL` explicitly on the private test network:

```bash
docker compose \
  -p tradehub-data-migration-test \
  -f docker-compose.test.yml \
  run --rm --build migration-tests
```

This is the preferred repeatable command and needs no host Python environment. The
explicit `--build` prevents an older local image from running stale test sources.

To run the same tests from an existing local virtual environment instead, install
the existing development dependencies and explicitly provide the localhost test
URL:

```bash
python3 -m pip install -e '.[dev]'
export TRADEHUB_DATA_TEST_DATABASE_URL='postgresql+psycopg://tradehub_data_test:tradehub_data_test@127.0.0.1:55432/tradehub_data_test'
python3 -m pytest -m postgres
```

The migration tests never fall back to `TRADEHUB_DATA_DATABASE_URL`. An absent test URL skips only PostgreSQL integration tests. A supplied URL fails safely unless it uses PostgreSQL and names a database containing `test`; the normal `tradehub_data` database and PostgreSQL system databases are explicitly rejected.

The configured `tradehub_data_test` database is not migrated, reset, or dropped. Each test creates a UUID-named child database, verifies that it is initially empty, and drops only that recorded child database during teardown. A process crash can leave a child temporarily, but removing the disposable tmpfs-backed service removes all of its state.

## Run SQLite and full suites

The existing SQLite suite does not need PostgreSQL:

```bash
python3 -m pytest -m 'not postgres'
```

The existing Docker command also remains valid. Because the API service does not receive `TRADEHUB_DATA_TEST_DATABASE_URL`, PostgreSQL integration tests skip while the normal suite runs:

```bash
docker compose run --rm api sh -c "pip install -e '.[dev]' && pytest"
```

## Cleanup

Use the same standalone file and project name:

```bash
unset TRADEHUB_DATA_TEST_DATABASE_URL
docker compose \
  -p tradehub-data-migration-test \
  -f docker-compose.test.yml \
  down --volumes --remove-orphans
```

Do not combine this cleanup command with `docker-compose.yml`. The distinct project name and standalone file ensure cleanup cannot stop the normal application services or remove `postgres_data`.
