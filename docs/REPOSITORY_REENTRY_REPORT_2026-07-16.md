# `tradehub-data` Repository Re-entry Report

Audit date: 2026-07-16  
Repository: `/home/hax01/Projects/tradehub-data`  
Branch audited: `main`  
Audited commit: `6b39381` (`fixed the ssl CA issue`)

This report is a read-only reconstruction of the repository state, except for the creation of this report file at the user's request. No runtime code, existing documentation, migrations, Git history, or live BVC state was changed. No live BVC collection was run.

Evidence labels used below:

- **Verified**: confirmed directly from source, tests, Git, Docker configuration, or local runtime behavior.
- **Historical evidence**: recorded in repository documentation or supplied project history, but not reproduced during this audit.
- **Unverified**: could not be confirmed from the available local database or by a safe non-live check.

## 1. Executive Summary

`tradehub-data` is a standalone financial-data infrastructure project intended to collect, preserve, normalize, validate, store, and serve Moroccan financial-market data. Its core rule is correctly expressed in [`AGENTS.md`](../AGENTS.md): collect raw source data first, then parse and normalize it into canonical tables. TradeHub application integration is not part of the current implementation or recommended next milestone.

The repository is no longer just a skeleton or raw HTML scraper. It contains a substantial end-to-end vertical slice for Casablanca Stock Exchange (BVC) listed-equity prices:

```text
BVC HTML/JSON source or fixture
    -> collector and ingestion-run tracking
    -> raw payload storage
    -> parser diagnostics
    -> HTML/JSON parser
    -> validation and normalization
    -> instruments, latest prices, and price bars
    -> read-only FastAPI endpoints
```

The implementation includes multi-page collection, source timestamps, raw-payload grouping, idempotent canonical upserts, normalization-error recording, group-aware diagnostics, TLS verification with optional custom CA-bundle support, and safe public API serialization/redaction. The complete automated suite still passes: **92 passed, 0 failed, 0 skipped**.

Current maturity is best described as an **advanced, tested BVC price-pipeline prototype**, not production-ready infrastructure. The principal reason is no longer the historically reported TLS/HTTP timeout or missing-price issue. The larger risks now concern canonical data semantics, incomplete raw/audit preservation in edge cases, group completion/status rules, PostgreSQL/migration coverage, bootstrap readiness, and operational observability. Scheduler/worker implementation would amplify those uncertainties and is therefore premature.

The safest next milestone is **better data-quality rules, contract first**: explicitly decide instrument merge behavior, daily-bar identity, stale-data behavior, group completion/status semantics, raw failure preservation, header retention, duplicate occurrence auditing, freshness meaning, and exact Decimal parsing before changing runtime behavior.

## 2. Current Architecture

### Implemented runtime shape

The real runtime is a synchronous FastAPI/SQLAlchemy modular monolith backed by PostgreSQL in Docker Compose:

- [`create_app()`](../src/tradehub_data/api/app.py#L11) creates the FastAPI application, configures logging, and registers the root router.
- [`api/routes.py`](../src/tradehub_data/api/routes.py#L10) registers `/health` and mounts the BVC API router.
- [`get_db()`](../src/tradehub_data/db/session.py#L16) yields and closes synchronous SQLAlchemy sessions. The engine is created at import time with `pool_pre_ping=True`.
- [`configure_logging()`](../src/tradehub_data/core/logging.py#L5) installs plain console logging. Structured fields passed through `extra` are not rendered by its formatter.
- There are no global application exception handlers or middleware. `/health` catches `SQLAlchemyError`; normalized endpoints otherwise use FastAPI's default 500 behavior for unexpected database failures.
- Redis, workers, scheduler, and monitoring runtime services do **not** exist. Their source directories are empty package placeholders. Compose currently defines only `api` and `postgres`.

Routes call repositories directly. The intended service-layer boundary in `AGENTS.md` and the architecture documents is not implemented for the normalized API.

### Main package responsibilities

| Package/directory | Current responsibility |
|---|---|
| `src/tradehub_data/api/` | FastAPI creation, health route, normalized BVC read-only routes, response mapping/redaction |
| `src/tradehub_data/core/` | Application settings, hashing, and logging configuration |
| `src/tradehub_data/db/` | Declarative base, engine, session factory, and dependency |
| `src/tradehub_data/models/` | SQLAlchemy persistence models for reference, source, raw, normalized market, quality, and sync state |
| `src/tradehub_data/repositories/` | Query, insert, deduplication, and upsert operations |
| `src/tradehub_data/collectors/bvc_prices/` | BVC HTTP client, HTML collection, paginated JSON collection, fixture loading, and raw storage orchestration |
| `src/tradehub_data/parsers/bvc_prices/` | HTML/JSON parsing, exact textual number parsing, date/timestamp extraction, pagination detection, and diagnostics |
| `src/tradehub_data/normalizers/bvc_prices/` | Parsed-row validation, canonical mapping, persistence, and normalization-error recording |
| `src/tradehub_data/pipelines/bvc_prices/` | Manual raw/fixture/live orchestration and multi-page result aggregation |
| `src/tradehub_data/schemas/` | Pydantic public response contracts |
| `migrations/` | Alembic environment and the single foundation migration |
| `tests/` | SQLite-based unit/integration-style tests, fixtures, mocked HTTP tests, and API tests |
| `scripts/` | Seed helper; currently inconsistent with the pipeline's BVC source code |
| `services/`, `workers/`, `scheduler/`, `monitoring/`, `utils/` | Empty placeholders; no operational implementation |

### Database architecture

The only Alembic revision is [`0001_initial_foundation`](../migrations/versions/0001_initial_foundation.py#L12). It creates the broad v0.1 foundation represented by the ORM models.

| Required entity | Model | Important relationships and constraints |
|---|---|---|
| Exchanges | [`Exchange`](../src/tradehub_data/models/reference.py#L13) | Unique `code`; relates to instruments and market indices |
| Instruments | [`Instrument`](../src/tradehub_data/models/instrument.py#L40) | Unique `(exchange_id, symbol)` and `(exchange_id, isin)`; optional company/source/raw trace; one latest price and many bars |
| Data sources | [`DataSource`](../src/tradehub_data/models/source.py#L14) | Unique `code`; relates to ingestion runs and raw payloads |
| Ingestion runs | [`IngestionRun`](../src/tradehub_data/models/source.py#L30) | Source, collector, run type/status, lifecycle timestamps, counters, and metadata |
| Raw payloads | [`RawPayload`](../src/tradehub_data/models/raw.py#L14) | Unique `(source_id, payload_hash)`; text/JSON body, HTTP and collection metadata, source/publication times, normalization status |
| Latest prices | [`LatestPrice`](../src/tradehub_data/models/price.py#L15) | Unique `instrument_id`; exact `Numeric` values and source/raw trace |
| Price bars | [`PriceBar`](../src/tradehub_data/models/price.py#L52) | Unique `(instrument_id, timeframe, bar_timestamp)` |
| Normalization errors | [`NormalizationError`](../src/tradehub_data/models/quality.py#L14) | Source/run/raw trace and indexed error/status fields; no database uniqueness constraint |

The foundation also defines sectors, companies, market indices, latest index values, index bars, and sync states. These are not used by the BVC price pipeline. Future v0.2 entities described in `docs/02_DATABASE_SCHEMA.md` are not implemented.

The model enums are stored as ordinary strings without ORM validation or database check constraints. Repository upserts are query-then-insert operations and therefore do not provide concurrency-safe conflict handling.

### BVC collection and normalization flow

#### Fixture and HTML collection

[`store_local_fixture()`](../src/tradehub_data/collectors/bvc_prices/fixtures.py#L26) reads a local payload, gets or creates source `bvc_prices`, creates a manual ingestion run, hashes the source URL and normalized body, inserts the raw payload if new, and completes the run.

[`BvcPriceCollector.run()`](../src/tradehub_data/collectors/bvc_prices/collector.py#L43) performs configured HTML requests, preserves successful bodies as raw payloads before parsing, records duplicate hashes, and derives ingestion-run status from per-request results. The collector does not write normalized market records.

#### Live JSON pagination

[`BvcPriceCollector.run_json_pages()`](../src/tradehub_data/collectors/bvc_prices/collector.py#L172) implements bounded live JSON pagination:

- A UUID identifies the pagination group.
- Offset is `page_index * page_limit`; defaults are 50 rows per page and at most 5 pages.
- Requests send configured `Accept`, `Referer`, and `Accept-Language` headers.
- Every valid non-empty page is stored as JSON and text, with page number, offset, limit, detected size, collection mode, and pagination-group metadata.
- Stop reasons are `empty_page`, `short_page`, `max_pages`, `malformed_json`, or `fetch_error`.
- Earlier valid pages followed by an error produce collector `partial_success`; no stored page plus an error produces `failed`.

There is one important raw-first violation: a successful empty JSON response and a malformed/wrong-shape successful response cause the loop to break before the body is inserted into raw storage ([`collector.py`](../src/tradehub_data/collectors/bvc_prices/collector.py#L238), [`collector.py`](../src/tradehub_data/collectors/bvc_prices/collector.py#L284)).

#### Diagnostics and parsing

[`BvcPipelineRunner._run_payload()`](../src/tradehub_data/pipelines/bvc_prices/runner.py#L187) runs parser diagnostics before normalization. [`diagnose_bvc_price_payload()`](../src/tradehub_data/parsers/bvc_prices/diagnostics.py#L219) selects HTML or JSON diagnostics from content type, endpoint, collection mode, or body shape.

- [`parse_bvc_market_listing_html()`](../src/tradehub_data/parsers/bvc_prices/html_parser.py#L31) detects the appropriate table, maps headers, extracts link-based symbols, parses French-formatted numeric fields without floats, and records row-level parse errors.
- [`parse_bvc_market_listing_json()`](../src/tradehub_data/parsers/bvc_prices/json_parser.py#L33) accepts nested `data.data` or direct `data` arrays, maps aliases, derives source timestamps/trading dates, and records numeric row errors.
- [`parse_decimal()`](../src/tradehub_data/parsers/bvc_prices/number_parsing.py#L23) converts normalized text directly to `Decimal`.
- [`extract_source_date_info()`](../src/tradehub_data/parsers/bvc_prices/source_metadata.py#L71) and [`detect_pagination()`](../src/tradehub_data/parsers/bvc_prices/source_metadata.py#L126) derive visible source dates/times and HTML page information.

Parsers do not access the database or external network.

#### Normalization and persistence

[`BvcPriceNormalizer`](../src/tradehub_data/normalizers/bvc_prices/normalizer.py#L33) reparses the raw payload, gets or creates the BVC exchange, persists parser errors, validates rows with [`validate_row()`](../src/tradehub_data/normalizers/bvc_prices/validation.py#L5), and upserts canonical records through repositories.

Behavior confirmed from code and tests:

- A valid row requires a price, identifier, name, and trading date.
- Negative financial quantities are rejected.
- Inconsistent OHLC values are marked `suspect` rather than silently accepted as normal.
- Instruments are matched by ISIN first and symbol second.
- A strictly older timestamp cannot overwrite `LatestPrice`.
- Price bars are upserted by instrument, timeframe, and exact bar timestamp.
- Parser/validation failures create `NormalizationError` rows.
- A payload with some valid rows can finish `partial_success`; zero valid rows marks the raw payload `failed`.
- Repeated identical normalization errors for the same raw payload are returned instead of reinserted during sequential reprocessing.

Timestamp policy is source-aware:

- HTML with a visible source datetime uses it for latest-price and bar timestamps.
- HTML with a date but no time uses raw collection time for latest price and Africa/Casablanca midnight for the `1d` bar.
- HTML with no visible source date falls back to collection time.
- JSON uses each row's source timestamp where present, otherwise source publication/collection time.

The JSON rule currently permits multiple nominal `1d` bars for one instrument and trading date because bar uniqueness uses the intraday source timestamp, not the trading date.

#### Group aggregation, partial success, and idempotency

[`BvcPipelineRunner`](../src/tradehub_data/pipelines/bvc_prices/runner.py#L92) supports existing raw payloads, local fixtures, fixture groups, and a controlled live-collector mode. For page groups it diagnoses every page, derives expected/missing pages, normalizes eligible pages, detects duplicate symbols, and returns page and aggregate results.

The tracked two-page HTML fixtures prove 50 + 30 = 80 detected/normalized rows with no cross-page duplicate symbol. Missing pages and duplicates produce group `partial_success` in tests.

Canonical-record idempotency is implemented through source/hash uniqueness for raw payloads and natural-key lookups/upserts for instruments, latest prices, and price bars. Reprocessing does not duplicate those canonical records in sequential tests. It is not strict whole-system idempotency: a new ingestion-run audit record can be created, processing timestamps change, and database concurrency is untested.

Group semantics still have inconsistencies:

- Parser diagnostic `partial_success` prevents normalization of the entire page.
- Normalizer `partial_success` is not counted as a failed page and can still yield group `success`.
- Collector stop reason is not part of the runner's pagination-completeness calculation, so `max_pages` or a later fetch error can be reported as complete.
- Duplicate live content returns the older raw row unchanged, so a new run can refer to old `collected_at`, ingestion-run, and pagination-group metadata.

### Read-only normalized BVC API

The implemented prefix is `/api/v1/markets/bvc`, defined in [`api/bvc_market.py`](../src/tradehub_data/api/bvc_market.py#L23).

| Route | Query parameters | Repository method | Response schema and behavior |
|---|---|---|---|
| `GET /instruments` | `symbol`; `limit` 1–500; `offset` >= 0 | [`list_bvc_instruments()`](../src/tradehub_data/repositories/bvc_market.py#L20) | `BvcInstrumentListResponse`; alphabetical; `count` is returned page length, not total matching rows; empty returns 200 |
| `GET /latest-prices` | `symbol`; `trading_date`; `limit`; `offset` | [`list_bvc_latest_prices()`](../src/tradehub_data/repositories/bvc_market.py#L52), [`bvc_data_freshness()`](../src/tradehub_data/repositories/bvc_market.py#L105) | `BvcLatestPriceListResponse`; decimals are strings; empty returns 200 |
| `GET /instruments/{symbol}` | path symbol, normalized to uppercase | [`get_bvc_instrument_by_symbol()`](../src/tradehub_data/repositories/bvc_market.py#L41), [`get_latest_price_for_instrument()`](../src/tradehub_data/repositories/bvc_market.py#L77) | `BvcInstrumentDetailResponse`; unknown symbol returns 404 |
| `GET /instruments/{symbol}/price-bars` | `timeframe` currently only `1d`; `trading_date`; `limit`; `offset` | [`list_bvc_price_bars()`](../src/tradehub_data/repositories/bvc_market.py#L81) | `BvcPriceBarListResponse`; descending timestamps; unknown instrument 404; invalid query/timeframe 422 |
| `GET /diagnostics/summary` | optional `trading_date` | [`bvc_diagnostics_summary()`](../src/tradehub_data/repositories/bvc_market.py#L241) | Aggregate counts/freshness/group metadata only; no raw body |

Public response contracts are in [`schemas/bvc_market.py`](../src/tradehub_data/schemas/bvc_market.py#L8). Decimal fields are serialized with `str(Decimal)` rather than conversion through float. [`_safe_price_metadata()`](../src/tradehub_data/api/bvc_market.py#L49) exposes a strict metadata allowlist. Raw payload bodies, raw fragments, raw IDs, cookies, response headers, and `etatCotVal` raw values are absent from public schemas.

API freshness is useful but not fully authoritative: `latest_collected_at` is based on the latest raw payload regardless of normalized status and is not scoped to the requested trading date. Content deduplication can also prevent collection freshness from advancing.

## 3. Implemented Capabilities

The following capabilities are verified by source and automated tests:

- FastAPI application factory and database-connectivity `/health` endpoint.
- PostgreSQL/SQLAlchemy model foundation and one Alembic migration.
- Source registry records, ingestion-run lifecycle tracking, deterministic raw hashing, and raw-payload deduplication.
- Local HTML fixture loading without external network access.
- BVC HTML collection with domain checks, redirects, timeout, bounded retries/backoff, request spacing, and raw-first storage for ordinary successful pages.
- Bounded BVC JSON pagination with page offset/limit/group metadata and explicit stop reasons.
- SSL verification enabled by default and optional operator-provided CA-bundle path.
- Configurable safe `Accept`, `Referer`, and `Accept-Language` request headers.
- HTML and JSON payload diagnostics before normalization.
- French date/timestamp extraction, pagination detection, and locale-aware exact text-to-`Decimal`/integer parsing.
- Null/blank-aware JSON alias selection, including `lastTradedPrice` fallback to `coursCourant`.
- Internal preservation of `etatCotVal` source status in raw-value metadata.
- BVC exchange/instrument creation, latest-price update, and `1d` price-bar upsert.
- Validation for missing price/identity/name/date, negative values, and OHLC inconsistency.
- Row-level normalization-error persistence with sequential duplicate suppression.
- Multi-page runner aggregation, missing-page and duplicate-symbol detection, and group-aware API totals.
- Canonical same-payload idempotency in sequential SQLite tests.
- Strictly older timestamp protection for `LatestPrice`.
- Read-only normalized BVC instruments, latest-price, detail, bar, and diagnostics endpoints.
- String serialization of API Decimal values and public raw-payload/redaction protection.

### Configuration and dependencies

[`pyproject.toml`](../pyproject.toml) requires Python `>=3.11`; [`Dockerfile`](../Dockerfile) uses `python:3.12-slim`. The audited host used Python 3.14.4 and the test container used Python 3.12.13.

Declared runtime dependencies are Alembic, BeautifulSoup, FastAPI, httpx, psycopg binary, pydantic-settings, SQLAlchemy, and Uvicorn. The only declared development dependency is pytest. There is no Redis dependency. Dependencies have lower bounds but no lockfile or upper bounds.

Versions resolved during the audit were FastAPI 0.139.1, SQLAlchemy 2.0.51, Alembic 1.18.5, Pydantic 2.13.4, pydantic-settings 2.14.2, httpx 0.28.1, BeautifulSoup 4.15.0, psycopg 3.3.4, Uvicorn 0.51.0, and pytest 9.1.1.

Core settings in [`core/config.py`](../src/tradehub_data/core/config.py#L7) use a `.env` file and the `TRADEHUB_DATA_` prefix:

| Setting | Default/behavior |
|---|---|
| `TRADEHUB_DATA_APP_NAME` | `tradehub-data` |
| `TRADEHUB_DATA_APP_ENV` | `development` |
| `TRADEHUB_DATA_LOG_LEVEL` | `INFO` |
| `TRADEHUB_DATA_DATABASE_URL` | Local PostgreSQL/psycopg URL |
| `TRADEHUB_DATA_API_HOST` | `0.0.0.0` |
| `TRADEHUB_DATA_API_PORT` | `8000` |

[`BvcPriceCollectorConfig`](../src/tradehub_data/collectors/bvc_prices/config.py#L36) implements these environment settings:

```text
BVC_BASE_URL
BVC_PRICE_COLLECTOR_ENABLED
BVC_PRICE_COLLECTOR_SOURCE_PATHS
BVC_PRICE_COLLECTOR_SOURCE_URLS
BVC_PRICE_COLLECTOR_TIMEOUT_SECONDS
BVC_PRICE_COLLECTOR_MAX_RETRIES
BVC_PRICE_COLLECTOR_RETRY_BACKOFF_SECONDS
BVC_PRICE_COLLECTOR_SLEEP_BETWEEN_REQUESTS_MS
BVC_PRICE_COLLECTOR_USER_AGENT
BVC_PRICE_COLLECTOR_ALLOWED_DOMAINS
BVC_PRICE_COLLECTOR_VERIFY_SSL
BVC_PRICE_COLLECTOR_CA_BUNDLE_PATH
BVC_PRICE_COLLECTOR_FAIL_ON_ERROR
BVC_PRICE_COLLECTOR_JSON_ENABLED
BVC_PRICE_COLLECTOR_JSON_PATH
BVC_PRICE_COLLECTOR_PAGE_LIMIT
BVC_PRICE_COLLECTOR_MAX_PAGES
BVC_PRICE_COLLECTOR_JSON_ACCEPT
BVC_PRICE_COLLECTOR_JSON_REFERER
BVC_PRICE_COLLECTOR_ACCEPT_LANGUAGE
```

Important defaults are a 20-second timeout, 3 retries after the initial attempt, exponential backoff starting at 2 seconds, 500 ms between requests/pages, SSL verification enabled, JSON collection enabled, page limit 50, maximum 5 pages, and `Accept-Language: fr-FR,fr;q=0.9,en;q=0.8`.

Configuration gaps:

- [`.env.example`](../.env.example) omits allowed domains and the newer JSON/pagination/Accept/Referer/Accept-Language variables.
- [`docker-compose.yml`](../docker-compose.yml) injects only `TRADEHUB_DATA_DATABASE_URL` into the API container. Other `.env` values are Compose interpolation inputs, not automatically container environment variables.
- The container command binds Uvicorn to `0.0.0.0:8000`; `TRADEHUB_DATA_API_PORT` changes only the published host port in Compose.
- Compose has no API health check and does not apply Alembic migrations automatically.
- No local `.env` existed during the audit, and no secret value was printed.

## 4. Current Runtime and Database State

No pre-existing project Compose service, database, or historical live-data volume was available when the audit began. Running the documented test command pulled/built the image and created a fresh `tradehub-data_postgres_data` volume. That is not the historical database in which the reported live 80-row group would have existed.

A read-only PostgreSQL metadata query returned:

```text
database|postgres_version|alembic_version_table|public_table_count
tradehub_data|16.14|absent|0
```

The following tables were absent:

```text
alembic_version
instruments
latest_prices
price_bars
raw_payloads
normalization_errors
ingestion_runs
```

Therefore these values are **unavailable, not zero**:

- migration revision;
- instrument, latest-price, price-bar, raw-payload, normalization-error, and ingestion-run counts;
- latest trading date;
- latest pagination group and collection mode;
- latest pages found and total rows detected;
- latest normalization status.

No migration was applied during the audit.

A safe local API smoke test was performed without collection or normalization:

| Request | Result | Meaning |
|---|---:|---|
| `GET /health` | 200 | PostgreSQL accepted `SELECT 1` |
| `GET /api/v1/markets/bvc/instruments?limit=3` | 500 | Fresh DB lacked `instruments` |
| `GET /api/v1/markets/bvc/latest-prices?limit=3` | 500 | Fresh DB lacked `latest_prices` |
| `GET /api/v1/markets/bvc/diagnostics/summary` | 500 | Required application tables were absent |

The health body was:

```json
{"status":"healthy","service":"tradehub-data","environment":"development","database":"connected"}
```

Logs confirmed PostgreSQL `UndefinedTable` errors. This demonstrates that `/health` is a connectivity check, not an application-schema readiness check, and that Compose does not bootstrap migrations.

The API and PostgreSQL containers were stopped afterward without removing Docker images, the stopped containers, network, or newly created empty volume. No live BVC request was issued.

The historically reported controlled live result of 2 pages, 80 detected rows, 80 normalized rows, zero missing-price errors, zero duplicate symbols, and successful status cannot be reproduced from the current local database because that database is absent. Code/tests confirm the fixes that make this result plausible; the result itself remains **historical evidence, not current runtime verification**.

## 5. Test and Build Status

### Commands and exact outcomes

```bash
python3 --version
# Python 3.14.4

python3 -m compileall -q src tests
# Exit 0; no output
```

```bash
docker compose version
# Docker Compose version v5.1.4

docker compose config
# Exit 0; api and postgres services rendered successfully

docker compose config --quiet
# Exit 0; no output
```

Raw rendered Compose output was not included in the audit record to avoid exposing interpolated local values.

The repository's documented test command was run exactly:

```bash
docker compose run --rm api sh -c "pip install -e '.[dev]' && pytest"
```

Result:

```text
platform linux -- Python 3.12.13, pytest-9.1.1, pluggy-1.6.0
collected 92 items
92 passed, 1 warning in 8.95s
```

- Passed: **92**
- Failed: **0**
- Skipped: **0**
- Warnings: **1**
- Historical “92 passed” claim: **matches exactly**

The warning was:

```text
StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.
```

The install phase also emitted the usual pip-as-root warning and had one transient `Network is unreachable` retry before succeeding. No slow or flaky test was observed, although per-test duration reporting was not enabled.

### Test suite map

| Area | Tests | What is covered |
|---|---:|---|
| BVC API | 13 | Lists, pagination, filters, validation, detail/404, bars, diagnostics totals, freshness, Decimal strings, and redaction |
| Health API | 1 | Successful dependency-injected health response |
| Collector/config | 23 | Hashing, mocked HTTP, retries, TLS classification/default/CA path, headers, URL precedence, raw storage, pagination, fixture idempotency, and failures |
| HTML parser | 17 | Dates, numbers, source links/symbols, real/sample fixtures, pagination, and row errors |
| JSON parser | 4 | Null/blank alias fallback, Decimal mapping, and invalid numeric rows |
| Diagnostics | 8 | HTML/JSON fields, raw diagnostics, dates, pagination, unknown headers, and read-only behavior |
| Normalizer | 10 | HTML/JSON normalization, errors, status fallback, timestamps, stale-price protection, and idempotency |
| Pipeline runner | 11 | Raw/fixture modes, diagnostic gate, multi-page groups, duplicates, partial groups, idempotency, and faked live mode |
| Repositories | 5 | Raw deduplication, selected uniqueness constraints, and sync-state update |
| **Total** | **92** | |

Coverage limitations are significant:

- Shared and API test databases use in-memory SQLite and `Base.metadata.create_all()`, not PostgreSQL or Alembic.
- Health uses a fake database object.
- HTTP tests use `httpx.MockTransport`.
- Tests named `collect_live` substitute a `FakeCollector`; they do not make live requests.
- PostgreSQL types/constraints, the migration upgrade path, concurrent upserts, real TLS handshakes, and real scheduler/worker behavior are untested.

## 6. Git and Worktree Status

At the start and end of the read-only audit, before this report was requested, the worktree was clean:

```text
git status --short       -> no output
git diff --stat          -> no output
git diff --cached --stat -> no output
```

Branch:

```text
main
```

Recent history contains three commits:

```text
6b39381 (HEAD -> main, origin/main, origin/HEAD) fixed  the ssl CA issue
10e26a7 the data collected - filtered - normalized - stored
3c7279f tradehub-data initialized
```

Audit-time status:

- Modified tracked files: none.
- Staged files: none.
- Untracked files: none.
- Cached diff: empty.
- Worktree conclusion: no unfinished Git-visible user work was present.

Current status after fulfilling the follow-up request to persist this report:

- New untracked file: `docs/REPOSITORY_REENTRY_REPORT_2026-07-16.md`.
- No tracked file was modified.
- No file is staged.
- No commit was created.

Ignored `__pycache__` files may have been created by `compileall`; Git cleanliness does not assert that ignored cache files are absent. Docker artifacts are external to Git and are described in section 4.

No `PROJECT_GUIDE.md` or nested `AGENTS.md` file exists.

## 7. Documentation Status Matrix

| Document | Purpose | Implementation status | Still accurate? | Required update |
|---|---|---|---|---|
| [`AGENTS.md`](../AGENTS.md) | Normative architecture and agent guardrails | Active; implementation is beyond its example roadmap | Yes for raw-first, boundaries, safety, testing, and responsibilities; no for expected numbered-doc tree and directional API examples | Preserve rules; label obsolete document sequence and distinguish example endpoints from real routes |
| [`README.md`](../README.md) | Setup and original collector overview | Outdated initial snapshot | Partly; setup/migration/health/raw-fixture commands remain useful, but it still describes a raw-only pipeline and recommends disabling SSL verification | Rewrite capabilities/commands; document JSON, runner, API, CA bundle, and Accept-Language; remove unsafe `VERIFY_SSL=false` guidance |
| [`docs/00_PROJECT_OVERVIEW.md`](00_PROJECT_OVERVIEW.md) | Vision, product boundary, stack, and milestones | Partially implemented; milestones 0–3 substantially exist | Mission and ownership boundaries remain accurate; tree, routes, document sequence, and milestone state are stale | Add current-state markers and actual route/tree/roadmap |
| [`docs/01_ARCHITECTURE.md`](01_ARCHITECTURE.md) | Target components, data flow, runtime topology, and integration architecture | Partially implemented modular monolith | Raw-first flow is accurate; Redis, worker, scheduler, monitoring, sample tables/routes, and some folders are aspirational | Add “implemented today” topology; label future components explicitly |
| [`docs/02_DATABASE_SCHEMA.md`](02_DATABASE_SCHEMA.md) | v0.1 schema and v0.2+ future entities | Part A/v0.1 implemented; Part B pending | Best early schema source; closely matches ORM/migration | Mark v0.1 complete/Part B pending; reconcile seed/source code `bvc_market_data` versus `bvc_prices` |
| [`docs/03_SOURCES_AND_COLLECTORS.md`](03_SOURCES_AND_COLLECTORS.md) | General source registry, collector contracts, compliance, and roadmap | Partially implemented for one BVC collector | General raw-first/compliance/error rules remain useful; generic registry, schema, roadmap, and endpoints are not current | Document actual BVC configuration; label generic framework and other sources pending |
| [`docs/04_BVC_PRICE_COLLECTOR.md`](04_BVC_PRICE_COLLECTOR.md) | Initial raw-only BVC HTML collector specification | Implemented, then extended/superseded | Collector boundaries and raw storage are accurate; HTML-first assumptions and “future parser/API” language are historical | Add JSON/live settings and Accept-Language; mark downstream pipeline implemented; update coverage and links |
| [`docs/05_BVC_PRICE_NORMALIZER.md`](05_BVC_PRICE_NORMALIZER.md) | HTML parsing, validation, normalization, idempotency, and CLI | Implemented and extended with JSON | Core rules remain accurate; HTML-only framing, duplicate-error limitation, and `--retry-failed` command are stale | Document JSON dispatch, error deduplication, alias/status handling, and actual CLI |
| [`docs/06_BVC_REAL_PAYLOAD_VALIDATION.md`](06_BVC_REAL_PAYLOAD_VALIDATION.md) | Safe captured-HTML validation phase | Implemented and superseded | Historical criteria remain useful; missing-production-coverage claim and May-15 fixture paths are stale | Mark phase complete/archive; replace deleted fixture references with current May-18 fixtures |
| [`docs/07_PIPELINE_RUNNER.md`](07_PIPELINE_RUNNER.md) | Manual raw/fixture/live pipeline orchestration | Implemented and extended for groups/live JSON | Core flow is accurate; 50-row state, output example, and deleted fixture commands are stale | Document current group output/behavior and update fixture paths |
| [`docs/08_BVC_PAGINATION_AND_TIMESTAMP.md`](08_BVC_PAGINATION_AND_TIMESTAMP.md) | Pagination detection and source timestamp policy | Implemented; operationally superseded by docs 09–10 | Timestamp/no-invented-time policy remains accurate; page-one blocker narrative and fixtures are historical | Mark complete, update fixture paths, link multi-page/live outcome |
| [`docs/09_BVC_MULTIPAGE_COLLECTION.md`](09_BVC_MULTIPAGE_COLLECTION.md) | Manual multi-page grouping, diagnostics, normalization, and idempotency | Implemented | Mostly accurate; May-15/74-row example differs from current tracked May-18 50+30=80 fixtures | Mark complete; update or explicitly label example counts/dates historical |
| [`docs/10_BVC_LIVE_MULTIPAGE_COLLECTOR.md`](10_BVC_LIVE_MULTIPAGE_COLLECTOR.md) | Offset-paginated live JSON collector | Implemented | Endpoint/pagination/raw/diagnostics/safety rules are mostly accurate; header list omits implemented Accept-Language | Mark implementation/live status and document `BVC_PRICE_COLLECTOR_ACCEPT_LANGUAGE` |
| [`docs/11_BVC_SSL_AND_LIVE_RUN_VALIDATION.md`](11_BVC_SSL_AND_LIVE_RUN_VALIDATION.md) | TLS/CA and controlled live-validation record | Partially historical and internally contradictory | CA/Accept-Language findings are useful; “73 tests,” unresolved TODO/timeout, and scheduler-block claims are stale | Recast as history; update to 92 tests; include safe header in examples; remove contradictory resolved blockers |
| [`docs/12_NORMALIZED_DATA_API.md`](12_NORMALIZED_DATA_API.md) | Read-only normalized BVC API contract | Implemented with contract/status drift | Routes, filters, Decimal strings, and raw redaction are broadly accurate; several response/diagnostics/404 examples and timeout status differ from code | Regenerate examples from Pydantic schemas/runtime and remove stale timeout blocker |
| [`docs/13_BVC_DIAGNOSTICS_GROUP_TOTALS_FIX.md`](13_BVC_DIAGNOSTICS_GROUP_TOTALS_FIX.md) | Correct totals from last page to pagination-group scope | Implemented/resolved | Expected behavior and tests remain accurate; issue wording is historical | Mark resolved and cite final source/test evidence |
| [`docs/14_BVC_LIVE_HTTP_TIMEOUT_INVESTIGATION.md`](14_BVC_LIVE_HTTP_TIMEOUT_INVESTIGATION.md) | Timeout investigation and safe-header discovery | Investigation resolved; superseded by implemented fix | Confirmed-cause section is useful; “blocked” and future-change sections are obsolete | Mark resolved; separate pre-fix evidence from final behavior; remove timeout block |
| [`docs/15_BVC_LIVE_MISSING_PRICE_REVIEW.md`](15_BVC_LIVE_MISSING_PRICE_REVIEW.md) | Analysis of first live run's nine missing-price rows | Investigation resolved; alias/status fix implemented | 71/80 first-run evidence is valid history; proposed code changes and missing-price blocker are obsolete | Mark fixed; cite null/blank fallback/status tests; record later historical 80/80 result and remaining unverified validation |

Additional documentation/configuration inconsistencies:

- [`.env.example`](../.env.example) omits current JSON, pagination, allowed-domain, Accept, Referer, and Accept-Language settings.
- [`scripts/seed.py`](../scripts/seed.py#L8) creates source code `bvc_market_data`, while collectors/normalizers use `bvc_prices`, risking two logical BVC source records.
- Several package `__init__.py` docstrings still describe modules as placeholders after implementation.
- The original expected numbered sequence in `AGENTS.md` and `docs/00_PROJECT_OVERVIEW.md` conflicts with the actual BVC-focused documents 05–15.
- TODOs and blocker text in docs 11, 14, and 15 describe work that is already present in code and tests.
- The normalized diagnostics API itself still returns stale operational status, so the inconsistency is not documentation-only.

## 8. Confirmed Resolved Issues

### Fourteen requested fixes

| # | Finding | Source evidence | Test evidence | Qualification |
|---:|---|---|---|---|
| 1 | Exact `Accept-Language` value exists | [`DEFAULT_BVC_ACCEPT_LANGUAGE`](../src/tradehub_data/collectors/bvc_prices/constants.py#L11); sent by [`run_json_pages()`](../src/tradehub_data/collectors/bvc_prices/collector.py#L230) | [`test_bvc_price_collector.py`](../tests/collectors/test_bvc_price_collector.py#L303) | Default is exactly `fr-FR,fr;q=0.9,en;q=0.8` |
| 2 | Environment override exists | [`BvcPriceCollectorConfig.from_env()`](../src/tradehub_data/collectors/bvc_prices/config.py#L58), env key at line 82 | [`test_config_accept_language_supports_env_override`](../tests/collectors/test_bvc_price_collector.py#L318) | `.env.example` does not document it |
| 3 | SSL verification remains enabled by default | [`config.py`](../src/tradehub_data/collectors/bvc_prices/config.py#L46); [`BvcPriceClient._verify_setting()`](../src/tradehub_data/collectors/bvc_prices/client.py#L84) | [`test_config_ssl_verification_defaults_to_enabled`](../tests/collectors/test_bvc_price_collector.py#L249) | Explicit false is still technically configurable; it was never used in this audit |
| 4 | CA-bundle path is supported | [`config.py`](../src/tradehub_data/collectors/bvc_prices/config.py#L47); passed to HTTPX by [`client.py`](../src/tradehub_data/collectors/bvc_prices/client.py#L26) | [`test_config_supports_explicit_ca_bundle_path`](../tests/collectors/test_bvc_price_collector.py#L259), SSL failure tests at lines 135 and 387 | Actual custom-bundle handshake was not rerun/unit-tested |
| 5 | JSON aliases skip `null` | [`_first_attribute()`](../src/tradehub_data/parsers/bvc_prices/json_parser.py#L187) | [`test_bvc_json_parser_falls_back_to_cours_courant_when_last_traded_price_is_null`](../tests/parsers/test_bvc_price_json_parser.py#L35) | Confirmed |
| 6 | JSON aliases skip blank strings | Same helper | [`test_bvc_json_parser_falls_back_to_cours_courant_when_last_traded_price_is_empty`](../tests/parsers/test_bvc_price_json_parser.py#L54) | Confirmed |
| 7 | `lastTradedPrice` falls back to `coursCourant` | Alias order in [`JSON_FIELD_ALIASES`](../src/tradehub_data/parsers/bvc_prices/json_parser.py#L11) and `_first_attribute()` | JSON parser tests above; normalizer scenarios in [`test_bvc_price_normalizer.py`](../tests/normalizers/test_bvc_price_normalizer.py#L171) | Confirmed for null and blank primary aliases |
| 8 | `etatCotVal` is preserved safely | Alias/extraction in [`json_parser.py`](../src/tradehub_data/parsers/bvc_prices/json_parser.py#L27); normalized internal metadata in [`normalizer.py`](../src/tradehub_data/normalizers/bvc_prices/normalizer.py#L244); public allowlist in [`api/bvc_market.py`](../src/tradehub_data/api/bvc_market.py#L49) | Parser and normalizer tests above; redaction in [`test_bvc_market.py`](../tests/api/test_bvc_market.py#L392) | Preserved internally, not yet a canonical status field/quality rule |
| 9 | Multi-page totals are group-aware | [`_pagination_group_summary()`](../src/tradehub_data/repositories/bvc_market.py#L178) | [`test_diagnostics_summary_group_total_does_not_use_latest_page_only`](../tests/api/test_bvc_market.py#L312); 50+30 runner test at [`test_bvc_price_runner.py`](../tests/pipelines/test_bvc_price_runner.py#L128) | Confirmed for ordinary groups; latest-group selection has a separate edge case |
| 10 | Decimal values avoid float for current source shape | [`parse_decimal()`](../src/tradehub_data/parsers/bvc_prices/number_parsing.py#L23); `Numeric` columns in [`price.py`](../src/tradehub_data/models/price.py#L30); [`_decimal_to_string()`](../src/tradehub_data/api/bvc_market.py#L32) | Parser tests at [`test_bvc_price_json_parser.py`](../tests/parsers/test_bvc_price_json_parser.py#L73) and API tests at [`test_bvc_market.py`](../tests/api/test_bvc_market.py#L243) | Not universal: `json.loads()` uses default float decoding for unquoted JSON numeric literals |
| 11 | Older timestamps protect existing latest prices | [`upsert_latest_price()`](../src/tradehub_data/repositories/prices.py#L13), guard at lines 21–22 | [`test_bvc_normalizer_does_not_overwrite_latest_price_with_older_timestamp`](../tests/normalizers/test_bvc_price_normalizer.py#L342) | Protects `LatestPrice` only, not instrument metadata or all bar cases |
| 12 | Duplicate normalization errors are prevented | [`create_normalization_error()`](../src/tradehub_data/repositories/normalization_errors.py#L8) | Two-pass assertion in [`test_bvc_price_normalizer.py`](../tests/normalizers/test_bvc_price_normalizer.py#L63) | Sequential/application-level only; no DB unique constraint or concurrency proof |
| 13 | Reprocessing the same payload is canonically idempotent | Raw unique key in [`RawPayload`](../src/tradehub_data/models/raw.py#L16); insertion in [`raw_payloads.py`](../src/tradehub_data/repositories/raw_payloads.py#L70); canonical upserts in [`instruments.py`](../src/tradehub_data/repositories/instruments.py#L25) and [`prices.py`](../src/tradehub_data/repositories/prices.py#L13) | [`test_foundation.py`](../tests/repositories/test_foundation.py#L12), normalizer tests at line 63, runner tests at [`test_bvc_price_runner.py`](../tests/pipelines/test_bvc_price_runner.py#L110) | Audit runs/timestamps can still change; no concurrent guarantee |
| 14 | Public API does not expose raw bodies | No raw route; strict mapper allowlist in [`api/bvc_market.py`](../src/tradehub_data/api/bvc_market.py#L49); schemas omit raw bodies/IDs | Redaction tests in [`test_bvc_market.py`](../tests/api/test_bvc_market.py#L312) | Confirmed |

### Resolution assessment for historically prominent issues

- **TLS/CA-bundle handling — implemented and tested at configuration/error-classification level.** Verification defaults to true, and a custom CA path can be passed safely to HTTPX. The previous actual certificate-chain resolution remains historical evidence because the live handshake was not repeated.
- **HTTP timeout — code-level cause/fix is resolved.** The safe `Accept-Language` header is now a default, configurable, tested request header. Documents and the diagnostics API still contain obsolete timeout-blocker wording.
- **Missing prices — parser/normalizer cause is resolved.** Null and blank primary aliases fall through to `coursCourant`, and not-traded/suspended source status is retained internally. Tests cover these cases. The later 80/80 live result is historical, not recoverable from the local database.
- **Multi-page group totals — resolved for tested normal groups.** Repository aggregation and tests use all pages rather than only the latest page.
- **Normalized API behavior — implementation/tests are resolved, deployment runtime is not.** Thirteen API tests validate routes, schemas, Decimal strings, errors, freshness shape, and redaction. The local normalized endpoints returned 500 solely because the fresh Compose database had no migration/schema. `/health` reporting 200 in that state is an operational readiness gap.

## 9. Remaining Blockers and Risks

### Confirmed blockers

1. **No bootstrapped local schema.** The available Docker database has no Alembic revision or application tables. Normalized API endpoints cannot run until the existing migration is deliberately applied.
2. **Canonical data rules are underspecified.** Live JSON with a missing ISIN/name can overwrite richer HTML-derived instrument fields because [`upsert_instrument()`](../src/tradehub_data/repositories/instruments.py#L25) blindly applies `None` and fallback values.
3. **Daily-bar identity is ambiguous.** JSON `1d` bars use intraday source timestamps, permitting more than one daily bar per trading date.
4. **Group status/completeness is unreliable in edge cases.** Normalizer partial success, diagnostic partial success, `max_pages`, and later fetch failure are not represented consistently.
5. **Raw-first preservation is incomplete for fetched empty/malformed JSON responses.** This conflicts directly with the project's core architecture rule.
6. **Final live idempotency cannot be verified from current state.** The historical database is unavailable; a future controlled live validation needs explicit authorization and must follow the corrected data contract.

These blockers make scheduler/worker implementation unsafe now. The old HTTP-timeout/missing-price wording is not the actual current scheduler blocker.

### Technical debt

- Older-timestamp protection applies only to `LatestPrice`; instrument provenance and same-key bars can regress.
- JSON numeric literals pass through Python float because `json.loads()` does not use a Decimal decoder; current fixtures use quoted numeric strings.
- Normalization-error deduplication and repository upserts are not concurrency-safe.
- String enums lack database check constraints.
- A malformed JSON row with no `attributes` object can abort the whole payload before row-level isolation.
- Diagnostics row totals can undercount parser failures because normalization metadata records only parsed rows.
- API mappers use `"0"` fallbacks for unexpectedly null required price/close values, potentially hiding corruption.
- Diagnostics aggregation loads all BVC raw ORM rows, including bodies, into Python and will not scale.
- Routes bypass the intended service layer.
- No PostgreSQL/Alembic integration tests exist.
- Index repository inserts are not idempotent, although the BVC price flow does not currently use them.
- `scripts/seed.py` uses a conflicting BVC source code.

### Documentation debt

- `README.md` describes a raw-only system and recommends an SSL bypass.
- Docs 11, 12, 14, and 15 contain stale timeout, missing-price, test-count, and scheduler-block claims.
- `.env.example` omits current JSON/header settings.
- API examples differ from actual routes/schemas/error payloads.
- Deleted May-15 fixtures and stale 73/74-row examples remain referenced.
- The numbered-document roadmap conflicts with the documents that actually exist.
- Runtime diagnostics hardcodes stale documentation conclusions.

### Operational risks

- All response headers are retained in raw metadata; this could persist `Set-Cookie`, WAF, or session values. Public API redaction does not reduce the storage risk.
- Content-level raw deduplication can attach a new collection run to an older raw occurrence, making provenance and freshness misleading.
- Latest diagnostics can select an older grouped payload while ignoring a newer ungrouped payload.
- `/health` reports healthy when PostgreSQL is reachable but the application schema is absent.
- Compose does not run migrations or expose API schema readiness.
- Logging does not render structured event fields, lacks duration metrics, and is sparse in the JSON collector/runner/normalizer.
- Unexpected collector failures can leave an ingestion run marked `running`.
- A new HTTPX client is created per page, losing connection/session reuse and adding TLS/WAF/latency variability.
- Dependency resolution is unpinned; a future install can select incompatible versions.

### Unverified assumptions

- The historical successful live run really produced 2 pages, 80 detected, 80 normalized, zero missing-price errors, and zero duplicates. This is plausible from the fixes but not present in the current database.
- The operator's custom CA bundle still works against the current BVC certificate chain.
- Current BVC production JSON still represents all financial decimals as quoted strings.
- Current endpoint pagination and source field shapes have not changed since the recorded fixtures/live investigation.
- Sequential SQLite idempotency will hold under concurrent PostgreSQL workers.
- Existing migrations upgrade cleanly on a persistent PostgreSQL database; only an empty, unmigrated database was observed.

## 10. Recommended Next Milestone

### Primary milestone: better data-quality rules, contract first

The best next milestone is to define and approve a **BVC canonical data-quality, idempotency, and run-state contract** before implementing further runtime behavior.

The contract should decide:

1. whether a weaker source row may overwrite a known instrument name, ISIN, source, or provenance;
2. whether a `1d` bar is unique by trading date or source timestamp;
3. how older payloads affect instruments, bars, latest prices, and provenance;
4. when a page/group is `success`, `partial_success`, `failed`, or incomplete;
5. how `max_pages`, empty pages, malformed pages, and later fetch failures affect completion;
6. whether every fetched response, including empty/malformed content, must have a raw occurrence record;
7. which response headers may be stored;
8. how content identity differs from collection occurrence/audit identity;
9. what API freshness fields actually mean;
10. how quoted and unquoted JSON financial numbers are converted without float;
11. how `etatCotVal` and HTML status should affect canonical quality/status;
12. what invariants must be enforced in PostgreSQL versus application code.

This milestone is safer than immediately choosing scheduler/worker design or implementation because automated execution would repeatedly amplify currently ambiguous merge, bar, raw-audit, and completion semantics. It is also safer than a final live idempotency run: that run would not have a stable definition of correctness until these rules are decided.

Documentation cleanup alone would remove contradictions but would not settle the underlying canonical behavior. Another BVC data type or source would broaden the system before its first vertical slice has production-grade invariants. Collector observability is important, but the system must first define which states and failures it needs to observe.

Once the contract is approved, implementation can proceed as a sequence of small, testable missions, followed by PostgreSQL verification and a separately authorized controlled live idempotency check. Scheduler design should follow evidence from those steps.

## 11. Proposed Small Missions

### Mission 1 — Write the BVC data-quality and idempotency contract

**Objective:** Create one specification that resolves the semantic questions listed in section 10 without changing runtime behavior.

**Files likely involved:**

- New `docs/16_BVC_DATA_QUALITY_AND_IDEMPOTENCY_CONTRACT.md`
- Read-only references to `docs/02_DATABASE_SCHEMA.md`, `docs/05_BVC_PRICE_NORMALIZER.md`, `docs/10_BVC_LIVE_MULTIPAGE_COLLECTOR.md`, and source/tests

**Acceptance criteria:**

- Every merge, timestamp, bar identity, status/completeness, raw occurrence, header allowlist, freshness, Decimal, and source-status rule has a deterministic decision.
- Each rule includes examples and testable acceptance criteria.
- Open questions are explicitly labeled rather than silently assumed.
- No runtime code, migration, configuration, or existing document changes.

**Out of scope:** implementation, live requests, scheduler/workers, other data sources, and TradeHub integration.

### Mission 2 — Reconcile operational documentation and configuration examples

**Objective:** Make operator-facing documentation match the implemented code and clearly mark historical findings.

**Files likely involved:**

- `README.md`
- `.env.example`
- `docs/00_PROJECT_OVERVIEW.md`
- `docs/11_BVC_SSL_AND_LIVE_RUN_VALIDATION.md`
- `docs/12_NORMALIZED_DATA_API.md`
- `docs/14_BVC_LIVE_HTTP_TIMEOUT_INVESTIGATION.md`
- `docs/15_BVC_LIVE_MISSING_PRICE_REVIEW.md`
- possibly `scripts/seed.py` only in a later code-specific mission, not as part of documentation cleanup

**Acceptance criteria:**

- No documentation recommends disabling TLS verification.
- Current environment variables, commands, routes, schemas, 92-test result, and resolved timeout/missing-price status are represented accurately.
- Historical live figures are labeled historical and not presented as current database state.
- Aspirational scheduler/Redis/TradeHub content is clearly separated from implemented runtime.

**Out of scope:** runtime logic, migrations, live collection, and scheduler implementation.

### Mission 3 — Harden normalized market-data semantics

**Objective:** Implement the approved rules for instrument merging, exact JSON Decimal parsing, daily-bar identity, stale-data protection, source status, and corruption-safe API mapping.

**Files likely involved:**

- `src/tradehub_data/parsers/bvc_prices/json_parser.py`
- `src/tradehub_data/normalizers/bvc_prices/normalizer.py`
- `src/tradehub_data/normalizers/bvc_prices/validation.py`
- `src/tradehub_data/repositories/instruments.py`
- `src/tradehub_data/repositories/prices.py`
- `src/tradehub_data/api/bvc_market.py`
- corresponding parser, normalizer, repository, and API tests
- migration only if the approved bar/constraint model requires one, as a separately reviewed change

**Acceptance criteria:**

- Weaker/older payloads cannot degrade canonical fields.
- One deterministic daily-bar identity is enforced.
- Quoted and unquoted financial values reach `Decimal` without float.
- Source trading status has an approved canonical/quality outcome.
- Corrupt required values are surfaced, not serialized as invented zero.
- New regression and idempotency tests pass.

**Out of scope:** scheduler/workers, new sources/data types, live collection, and TradeHub integration.

### Mission 4 — Harden raw occurrence, group-state, and observability semantics

**Objective:** Implement the approved raw-first and pipeline-state rules, including safe metadata retention and useful operational signals.

**Files likely involved:**

- `src/tradehub_data/collectors/bvc_prices/collector.py`
- `src/tradehub_data/repositories/raw_payloads.py`
- `src/tradehub_data/pipelines/bvc_prices/runner.py`
- `src/tradehub_data/repositories/bvc_market.py`
- `src/tradehub_data/core/logging.py`
- `src/tradehub_data/api/bvc_market.py`
- collector, runner, diagnostics, repository, and API tests
- migration only if content identity and occurrence identity are separated in the schema

**Acceptance criteria:**

- Every fetched response has a safe auditable occurrence without exposing sensitive headers.
- Duplicate content preserves both content idempotency and current run/freshness evidence.
- Stop reasons and row-level partial results yield deterministic group status/completeness.
- Diagnostics selects the correct latest occurrence/group and reports accurate row/error totals.
- Stale hardcoded timeout/scheduler status is removed or replaced with computed state.
- Start/end/duration/count/error events are testable and useful.

**Out of scope:** automatic scheduling, aggressive/live requests, other sources, and TradeHub integration.

### Mission 5 — Prove PostgreSQL bootstrap and controlled idempotency readiness

**Objective:** Validate the existing migration and normalized API on PostgreSQL, then—only with separate explicit authorization—perform the smallest safe two-run live idempotency check.

**Files likely involved:**

- `migrations/env.py`
- `migrations/versions/0001_initial_foundation.py`
- `docker-compose.yml`
- PostgreSQL-focused test configuration/files
- operational validation documentation
- no runtime changes unless a separately reported bootstrap defect is approved for fixing

**Acceptance criteria:**

- A fresh PostgreSQL database upgrades to Alembic head.
- Required tables/constraints exist and repository/API tests run against PostgreSQL.
- `/health` and schema-readiness behavior are explicitly understood and documented.
- A controlled live check, if separately authorized, uses TLS verification, the configured CA path/header, bounded two-page collection, no secret/header disclosure, and compares two runs for canonical and audit idempotency.
- Exact safe aggregates are recorded without raw bodies.

**Out of scope:** scheduler/worker implementation, continuous polling, new sources/data types, and TradeHub integration.

## 12. Suggested Next Codex Prompt

```text
Resume work on tradehub-data with a documentation-only mission.

Read AGENTS.md, this repository re-entry report, docs/02_DATABASE_SCHEMA.md,
docs/05_BVC_PRICE_NORMALIZER.md, docs/10_BVC_LIVE_MULTIPAGE_COLLECTOR.md,
and the relevant current source/tests.

Create only docs/16_BVC_DATA_QUALITY_AND_IDEMPOTENCY_CONTRACT.md.

The document must define deterministic, testable rules for:
- merging instrument name, ISIN, source, and provenance from weaker/older payloads;
- LatestPrice and PriceBar stale-data protection;
- the identity of a 1d bar (trading date versus source timestamp);
- page/group success, partial success, failure, and completeness;
- max_pages, empty, malformed, and later-page fetch-error behavior;
- preserving every fetch occurrence while deduplicating identical content;
- a safe response-header storage allowlist;
- API freshness semantics;
- exact Decimal handling for quoted and unquoted JSON numbers;
- etatCotVal/HTML status quality semantics;
- normalization-error uniqueness and concurrency expectations;
- PostgreSQL constraints versus application-level checks.

For every rule, include rationale, edge cases, and acceptance tests. Clearly mark
any decision that cannot be derived safely from current requirements as an open
question.

Do not modify runtime code, tests, migrations, configuration, or existing docs.
Do not run live collection. Do not add scheduler/workers, new sources, new data
types, or TradeHub integration. Do not commit.
```
