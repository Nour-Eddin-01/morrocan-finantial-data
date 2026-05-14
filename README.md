# tradehub-data

Dedicated financial data infrastructure foundation for TradeHub.

This initial milestone includes the Python package, PostgreSQL/Alembic setup, SQLAlchemy v0.1 schema models, Docker Compose, basic configuration, logging, a health check API, and the raw-only BVC price collector foundation.

It intentionally does not include price parsing, normalization pipelines, schedulers, or TradeHub integration.

## Run Locally

```bash
cp .env.example .env
docker compose up -d postgres
python -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
alembic upgrade head
uvicorn tradehub_data.api.app:create_app --factory --host 0.0.0.0 --port 8000
```

Health check:

```bash
curl http://localhost:8000/health
```

## Docker

```bash
cp .env.example .env
docker compose up --build
```

The API listens on `http://localhost:8000` by default.

## Migrations

Apply migrations:

```bash
alembic upgrade head
```

Apply migrations through Docker Compose:

```bash
docker compose run --rm api alembic upgrade head
```

Create a new migration after model changes:

```bash
alembic revision --autogenerate -m "describe change"
```

## Tests

Run the current foundation tests through Docker Compose:

```bash
docker compose run --rm api sh -c "pip install -e '.[dev]' && pytest"
```

## BVC Price Collector

Run the BVC price collector once through Docker Compose:

```bash
docker compose run --rm api python -m tradehub_data.collectors.bvc_prices.collector
```

By default, the collector fetches the official BVC market actions listing:

```text
https://www.casablanca-bourse.com/fr/live-market/marche-actions-listing?amp=1
```

Configure source paths relative to `BVC_BASE_URL`:

```bash
BVC_PRICE_COLLECTOR_SOURCE_PATHS=/fr/live-market/marche-actions-listing?amp=1,/fr/live-market/instruments/BCP
```

Or provide full candidate URLs:

```bash
BVC_PRICE_COLLECTOR_SOURCE_URLS=https://www.casablanca-bourse.com/fr/live-market/marche-actions-listing?amp=1,https://www.casablanca-bourse.com/fr/live-market/instruments/BCP
```

`BVC_PRICE_COLLECTOR_SOURCE_URLS` takes precedence over `BVC_PRICE_COLLECTOR_SOURCE_PATHS`. Keep the list short during development; the collector is intentionally not a crawler.

The command prints a JSON result. By default, it exits successfully even if the live source is unreachable, while recording the failed run in `ingestion_runs`. For strict automation, use:

```bash
docker compose run --rm -e BVC_PRICE_COLLECTOR_FAIL_ON_ERROR=true api python -m tradehub_data.collectors.bvc_prices.collector
```

If the source website serves an incomplete certificate chain in your local Docker environment, the run may fail with `ssl_certificate_error`. Keep SSL verification enabled by default. If you have a trusted CA/intermediate bundle for the source, provide it explicitly:

```bash
docker compose run --rm -e BVC_PRICE_COLLECTOR_CA_BUNDLE_PATH=/path/in/container/ca-bundle.pem api python -m tradehub_data.collectors.bvc_prices.collector
```

For a local manual collection smoke test only, you can disable verification explicitly:

```bash
docker compose run --rm -e BVC_PRICE_COLLECTOR_VERIFY_SSL=false api python -m tradehub_data.collectors.bvc_prices.collector
```

The collector stores raw source responses in `raw_payloads` and records execution status in `ingestion_runs`. It does not parse, normalize, or update final price tables.

### Local Raw Payload Fixture

For parser development, store a manually saved source payload without calling the live BVC site:

```bash
docker compose run --rm api python -m tradehub_data.collectors.bvc_prices.fixtures /app/fixtures/bvc_prices/sample_market_listing.html
```

If the fixture represents a known source URL, pass it explicitly:

```bash
docker compose run --rm api python -m tradehub_data.collectors.bvc_prices.fixtures /app/fixtures/bvc_prices/sample_market_listing.html --source-url "https://www.casablanca-bourse.com/fr/live-market/marche-actions-listing?amp=1"
```

The fixture loader writes one `ingestion_runs` record and one idempotent `raw_payloads` record with `payload_type=bvc_price_snapshot`. It does not parse or normalize data.
