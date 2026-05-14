# 04_BVC_PRICE_COLLECTOR.md

# TradeHub Data — BVC Price Collector Specification

## 1. Purpose

This document defines the implementation specification for the first market-price collector in `tradehub-data`.

The goal of this module is to collect latest market price snapshots from the Casablanca Stock Exchange / Bourse de Casablanca source layer and store them safely as raw payloads.

This file is written for Codex and future coding agents.

It must be treated as a strict implementation guide.

---

## 2. Scope

This collector is responsible for collecting raw price data for Moroccan listed instruments.

The first version should focus on:

- latest instrument prices
- daily variation
- open price
- high price
- low price
- previous close price
- volume if available
- traded value if available
- market capitalization if available
- source timestamp if available
- collection timestamp
- source URL
- raw HTML or raw JSON payload
- payload hash
- ingestion run metadata

The collector must not directly update final normalized tables such as:

- `instruments`
- `price_bars`
- `latest_prices`
- `indices`
- `companies`

The collector writes to raw storage only.

Normalization must be handled later by a parser/normalizer module.

---

## 3. Main Rule

The collector must follow this pipeline:

```txt
BVC source
    ↓
BVC price collector
    ↓
raw_payloads
    ↓
BVC price parser
    ↓
normalizer
    ↓
latest_prices / price_bars
    ↓
API
    ↓
TradeHub
```

Mandatory rule:

```txt
collector -> raw_payloads only
```

Do not bypass raw storage.

---

## 4. Why This Collector Exists

TradeHub currently needs market prices for:

- market page
- stock detail page
- trading simulator
- portfolio valuation
- watchlist prices
- charts
- future alerts
- future analytics

The existing TradeHub backend has a market worker, but the long-term architecture is to move market-data collection into `tradehub-data`.

This collector is the first step toward replacing TradeHub's internal market worker with a dedicated data infrastructure service.

---

## 5. Source Strategy

### 5.1 Preferred Source Priority

Use source priority in this order:

1. Official structured API if discovered and legally usable.
2. Official downloadable file if available.
3. Official HTML pages from the Casablanca Stock Exchange website.
4. Approved secondary public source only as fallback.

Initial implementation should assume official HTML collection unless a stable official structured endpoint is discovered during implementation.

### 5.2 Do Not Hardcode Fragile Details Too Deeply

Web pages may change.

The collector should isolate source-specific selectors and parsing assumptions in one place.

Do not scatter CSS selectors across the codebase.

Recommended structure:

```txt
src/tradehub_data/collectors/bvc_prices/
├── __init__.py
├── client.py
├── collector.py
├── config.py
├── constants.py
├── errors.py
├── models.py
└── selectors.py
```

### 5.3 Source URL Handling

The collector should support configurable source URLs through environment variables or settings.

Do not hardcode only one company page.

Suggested settings:

```env
BVC_BASE_URL=https://www.casablanca-bourse.com
BVC_PRICE_COLLECTOR_ENABLED=true
BVC_PRICE_COLLECTOR_SOURCE_PATHS=/fr/live-market/marche-actions-listing?amp=1
BVC_PRICE_COLLECTOR_SOURCE_URLS=
BVC_PRICE_COLLECTOR_TIMEOUT_SECONDS=20
BVC_PRICE_COLLECTOR_MAX_RETRIES=3
BVC_PRICE_COLLECTOR_RETRY_BACKOFF_SECONDS=2
BVC_PRICE_COLLECTOR_USER_AGENT=TradeHubDataBot/0.1
BVC_PRICE_COLLECTOR_SLEEP_BETWEEN_REQUESTS_MS=500
BVC_PRICE_COLLECTOR_VERIFY_SSL=true
BVC_PRICE_COLLECTOR_CA_BUNDLE_PATH=
```

If the source requires a different URL pattern, keep it in a single constants/config module.

`BVC_PRICE_COLLECTOR_SOURCE_PATHS` is a comma-separated list of paths relative to `BVC_BASE_URL`. `BVC_PRICE_COLLECTOR_SOURCE_URLS` is a comma-separated list of absolute URLs and takes precedence when set.

The default v0.1 candidate is the official market actions listing page:

```txt
https://www.casablanca-bourse.com/fr/live-market/marche-actions-listing?amp=1
```

This page is preferred over the BVC homepage because it is a market-data page and contains the live-market listing table shape needed for later parser work. Additional candidate URLs may be configured manually, but the list should stay small until source behavior and rate limits are understood.

SSL verification must remain enabled by default. If the BVC server serves an incomplete certificate chain in a local Docker environment, provide a trusted CA/intermediate bundle explicitly:

```env
BVC_PRICE_COLLECTOR_CA_BUNDLE_PATH=/path/in/container/bvc-ca-bundle.pem
```

Do not disable SSL verification in committed defaults.

---

## 6. Legal and Ethical Collection Rules

This project must collect public financial data responsibly.

The collector must:

- respect robots.txt and source terms where applicable
- avoid aggressive crawling
- use reasonable request timeouts
- use reasonable rate limiting
- identify itself with a clear user-agent if allowed
- not bypass authentication
- not bypass paywalls
- not scrape private data
- not break anti-bot mechanisms
- not overload the source website

The collector must be designed for low-frequency collection in the first version.

Even if the long-term goal is one-minute updates, version 0.1 should prioritize reliability and source safety.

---

## 7. Update Frequency

Long-term target:

```txt
collect latest market data every 1 minute during market hours
```

Version 0.1 target:

```txt
manual run + scheduled run every 5 minutes or more
```

Do not start with aggressive one-minute scraping until:

- collector stability is confirmed
- rate limits are understood
- source behavior is tested
- monitoring is available
- deduplication is working
- parser failures are visible

Recommended phased frequency:

| Phase | Frequency | Notes |
|---|---:|---|
| local prototype | manual | used during development |
| v0.1 dev | every 5-15 minutes | safe testing |
| v0.2 staging | every 1-5 minutes | only during market hours |
| production | every 1 minute | only after monitoring and source validation |

---

## 8. Market Hours Awareness

The collector should be able to run at any time, but it should label the collection context.

Do not assume every collection happens during an open market session.

The system should eventually detect:

- pre-open
- continuous trading
- close
- post-close
- weekend
- holiday
- source unavailable

For version 0.1, this can be simplified to:

```txt
market_session_status = unknown
```

or inferred from timestamp and configured schedule.

Do not block implementation waiting for a perfect market-calendar module.

---

## 9. Data Collected by This Module

The raw collector should attempt to collect source data containing fields like:

| Source Concept | Meaning | Raw Required? | Normalized Later? |
|---|---|---:|---:|
| symbol/ticker | short instrument code | yes if available | yes |
| ISIN | official instrument identifier | yes if available | yes |
| instrument name | displayed stock name | yes if available | yes |
| last price | latest displayed price | yes if available | yes |
| variation | price change or percent change | yes if available | yes |
| open | opening price | yes if available | yes |
| high | session high | yes if available | yes |
| low | session low | yes if available | yes |
| previous close | previous closing price | yes if available | yes |
| volume | traded quantity | yes if available | yes |
| traded value | turnover / transaction value | yes if available | yes |
| market cap | market capitalization | yes if available | yes |
| source timestamp | timestamp shown by source | yes if available | yes |
| collected_at | timestamp collected by our system | yes | yes |
| source_url | URL used for collection | yes | yes |
| payload_hash | hash of raw payload | yes | yes |

Important:

The collector should not enforce final financial interpretation.

Example:

If the source displays `Variation: -0,94 %`, the raw value can be preserved as text. The parser later converts it to Decimal.

---

## 10. Raw Payload Storage Contract

Every successful source fetch should create or reuse a `raw_payloads` record.

Suggested raw payload fields from `02_DATABASE_SCHEMA.md`:

```txt
id
source_id
ingestion_run_id
payload_type
source_url
http_status
content_type
raw_text
raw_json
raw_file_path
payload_hash
collected_at
created_at
```

For this collector:

```txt
payload_type = "bvc_price_snapshot"
```

or more specific:

```txt
payload_type = "bvc_instrument_price_page"
payload_type = "bvc_market_price_list"
```

depending on the source shape.

### 10.1 Deduplication Rule

The collector must compute a deterministic hash of the raw payload.

Recommended:

```txt
sha256(normalized_response_body)
```

If the same payload already exists for the same source and source URL, do not create duplicate raw records unless a new ingestion-run trace is required.

A safe version is:

```txt
unique(source_id, source_url, payload_hash)
```

### 10.2 Raw Text vs Raw JSON

If response is HTML:

```txt
raw_text = html
raw_json = null
content_type = "text/html"
```

If response is JSON:

```txt
raw_text = null or original JSON string
raw_json = parsed JSON
content_type = "application/json"
```

Do not throw away original source response.

---

## 11. Ingestion Run Contract

Every collector execution must create an `ingestion_runs` record.

Recommended fields:

```txt
id
source_id
collector_name
status
started_at
finished_at
records_found
records_stored
records_skipped
error_message
metadata
created_at
```

For this collector:

```txt
collector_name = "bvc_price_collector"
```

Possible statuses:

```txt
running
success
partial_success
failed
skipped
```

### 11.1 Status Rules

Use `success` when:

- source request completed
- payload was stored or identified as duplicate
- no critical failure occurred

Use `partial_success` when:

- some instruments were collected
- some failed
- source list was incomplete but useful data was stored

Use `failed` when:

- no useful payload was collected
- source is unreachable
- schema assumptions completely failed
- HTTP errors prevent data collection

Use `skipped` when:

- collector is disabled
- market is closed and configured to skip
- source is intentionally not queried

---

## 12. Collector Responsibilities

The BVC price collector is responsible for:

1. Loading collector configuration.
2. Creating an ingestion run.
3. Determining which source URL(s) to fetch.
4. Fetching source response safely.
5. Capturing HTTP metadata.
6. Computing payload hash.
7. Storing raw payload.
8. Updating ingestion run status.
9. Logging meaningful events.
10. Returning a clear execution result.

The collector is not responsible for:

- parsing financial values into Decimal
- matching instruments to internal IDs
- updating latest price tables
- creating OHLCV bars
- serving API endpoints
- making trading decisions
- deciding portfolio values

---

## 13. Parser Responsibilities Later

A future parser module should read `raw_payloads` with type:

```txt
bvc_price_snapshot
```

and produce parsed records such as:

```txt
BvcParsedPriceRecord
```

Example parsed model:

```python
class BvcParsedPriceRecord(BaseModel):
    source_symbol: str | None
    source_name: str | None
    isin: str | None
    last_price: Decimal | None
    open_price: Decimal | None
    high_price: Decimal | None
    low_price: Decimal | None
    previous_close: Decimal | None
    change_amount: Decimal | None
    change_percent: Decimal | None
    volume: Decimal | None
    traded_value: Decimal | None
    market_cap: Decimal | None
    source_timestamp: datetime | None
    raw_payload_id: UUID
```

This parser is not part of this file's implementation unless explicitly requested.

---

## 14. Normalizer Responsibilities Later

A future normalizer module should convert parsed price records into normalized tables:

- `latest_prices`
- `price_bars`
- maybe `instrument_aliases`
- maybe `data_quality_events`

The normalizer must:

- match source symbol/ISIN to internal instrument
- validate decimals
- validate timestamps
- avoid duplicates
- upsert latest price
- append or correct daily price bars carefully
- preserve source traceability through `raw_payload_id`

This normalizer is not part of this collector implementation.

---

## 15. Recommended Code Structure

Codex should implement this module inside the planned Python package.

Recommended structure:

```txt
src/tradehub_data/
├── collectors/
│   └── bvc_prices/
│       ├── __init__.py
│       ├── client.py
│       ├── collector.py
│       ├── config.py
│       ├── constants.py
│       ├── errors.py
│       ├── models.py
│       └── selectors.py
│
├── db/
│   ├── session.py
│   ├── models/
│   │   ├── data_source.py
│   │   ├── ingestion_run.py
│   │   └── raw_payload.py
│   └── repositories/
│       ├── ingestion_runs.py
│       └── raw_payloads.py
│
└── core/
    ├── config.py
    ├── logging.py
    └── hashing.py
```

If the repository does not exist yet, Codex should create only the minimum required files for this collector and its dependencies.

Do not create unrelated modules.

---

## 16. Suggested Class Design

### 16.1 Config Model

```python
class BvcPriceCollectorConfig(BaseModel):
    enabled: bool
    base_url: str
    timeout_seconds: int
    max_retries: int
    retry_backoff_seconds: float
    sleep_between_requests_ms: int
    user_agent: str
```

### 16.2 Source Response Model

```python
class BvcFetchResult(BaseModel):
    source_url: str
    http_status: int
    content_type: str | None
    body_text: str
    fetched_at: datetime
    headers: dict[str, str] = {}
```

### 16.3 Collector Result Model

```python
class BvcPriceCollectorResult(BaseModel):
    status: Literal["success", "partial_success", "failed", "skipped"]
    ingestion_run_id: UUID | None
    source_urls_count: int
    payloads_stored: int
    payloads_skipped: int
    errors_count: int
    message: str | None = None
```

### 16.4 Collector Class

```python
class BvcPriceCollector:
    def __init__(
        self,
        client: BvcPriceClient,
        raw_payload_repository: RawPayloadRepository,
        ingestion_run_repository: IngestionRunRepository,
        config: BvcPriceCollectorConfig,
        logger: Logger,
    ):
        ...

    async def run(self) -> BvcPriceCollectorResult:
        ...
```

---

## 17. HTTP Client Rules

Use `httpx.AsyncClient` for HTTP requests.

Client must support:

- timeout
- retries
- backoff
- user-agent
- graceful error reporting
- HTTP metadata capture

Do not use Playwright in version 0.1 unless the source cannot be read with normal HTTP.

If JavaScript rendering is required, create a separate future document before adding Playwright.

### 17.1 Retry Policy

Retry only for temporary failures:

- connection timeout
- read timeout
- 429 too many requests, if retry-after is reasonable
- 500
- 502
- 503
- 504

Do not retry aggressively.

Default:

```txt
max_retries = 3
backoff = 2 seconds
```

### 17.2 Error Handling

Do not crash the whole process on one failed URL if multiple URLs are being collected.

Store errors in ingestion run metadata.

Example metadata:

```json
{
  "failed_urls": [
    {
      "url": "https://example.com/...",
      "error": "ReadTimeout"
    }
  ]
}
```

---

## 18. Source URL Discovery Strategy

There are two possible strategies.

### 18.1 Strategy A — Market List Page

Fetch one page or endpoint that contains many instruments.

Advantages:

- fewer requests
- easier scheduling
- better source safety

Disadvantages:

- may require parsing a complex page
- may have dynamic JavaScript

### 18.2 Strategy B — Per-Instrument Pages

Fetch each instrument page individually.

Advantages:

- easier to parse one instrument at a time
- useful for detailed fields

Disadvantages:

- many requests
- requires rate limiting
- more fragile at scale

### 18.3 Recommended v0.1 Approach

Start with the least aggressive working option.

Preferred:

```txt
market list page or official structured endpoint if available
```

Fallback:

```txt
small allowlist of per-instrument pages for local prototype
```

Do not crawl all instruments blindly in v0.1.

Use a controlled list such as:

```txt
BVC_PRICE_COLLECTOR_SYMBOLS=BAL,IBC,...
```

or load from a local seed/config file.

### 18.4 Manual Raw Fixture Workflow

If live source fetching is blocked by a network or SSL issue, development can continue by manually saving a small source payload and storing it in `raw_payloads`.

Command:

```bash
python -m tradehub_data.collectors.bvc_prices.fixtures fixtures/bvc-market.html
```

Optional source URL:

```bash
python -m tradehub_data.collectors.bvc_prices.fixtures fixtures/bvc-market.html --source-url "https://www.casablanca-bourse.com/fr/live-market/marche-actions-listing?amp=1"
```

The fixture loader must:

- create an `ingestion_runs` record with `collector_name = "bvc_price_fixture_loader"`
- insert or reuse an idempotent `raw_payloads` record
- use `payload_type = "bvc_price_snapshot"` by default
- preserve the fixture text as raw payload content
- avoid parsing or normalizing the payload
- avoid writing to `latest_prices` or `price_bars`

---

## 19. Instrument Coverage in v0.1

The first implementation does not need to cover all listed companies.

Version 0.1 can support:

- 3 to 5 instruments for proof of concept
- manual list of symbols/source paths
- raw payload storage
- ingestion run status
- tests

Full market coverage comes later after:

- company/instrument collector exists
- instrument source mapping exists
- parser is stable
- scheduler is tested

---

## 20. Database Source Record

The collector must ensure a `data_sources` record exists.

Suggested record:

```txt
code: bvc_prices
name: Bourse de Casablanca Prices
source_type: market_data
base_url: https://www.casablanca-bourse.com
is_active: true
metadata: {
  "country": "MA",
  "market": "Casablanca Stock Exchange",
  "collector": "bvc_price_collector"
}
```

Do not create duplicate data source rows.

Use `code` as the natural unique key.

---

## 21. Hashing Rules

The collector must compute payload hashes consistently.

Recommended utility:

```python
def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
```

Before hashing:

- preserve meaningful source content
- avoid adding collection timestamps into the hashed body
- normalize line endings if needed

Do not hash parsed financial values only.

Hash the raw source payload.

---

## 22. Logging Requirements

Use structured logs where possible.

Log these events:

- collector started
- collector skipped because disabled
- ingestion run created
- source URL fetch started
- source URL fetch succeeded
- source URL fetch failed
- raw payload stored
- duplicate payload skipped
- collector finished
- collector failed

Example log fields:

```txt
collector=bvc_price_collector
ingestion_run_id=...
source_url=...
http_status=200
payload_hash=...
```

Do not log full raw HTML in normal logs.

---

## 23. Metrics Requirements

Version 0.1 can expose simple logs only.

Future metrics should include:

```txt
bvc_price_collector_runs_total
bvc_price_collector_success_total
bvc_price_collector_failed_total
bvc_price_collector_payloads_stored_total
bvc_price_collector_payloads_duplicate_total
bvc_price_collector_fetch_duration_seconds
bvc_price_collector_last_success_timestamp
```

Do not block v0.1 implementation on Prometheus metrics.

---

## 24. CLI Entry Point

The collector should be runnable manually from terminal.

Suggested command:

```bash
python -m tradehub_data.collectors.bvc_prices.collector
```

or, if a CLI module exists:

```bash
tradehub-data collect bvc-prices
```

For v0.1, a simple module command is enough.

The command should:

- load settings
- connect to database
- run collector
- print a short result
- exit non-zero only on full failure

---

## 25. Scheduler Integration

This document does not require a scheduler implementation.

But the collector must be designed so that a scheduler can call:

```python
await BvcPriceCollector(...).run()
```

A future scheduler document will define:

```txt
10_SCHEDULER_AND_WORKERS.md
```

Do not embed infinite loops inside the collector.

The collector should run once and exit.

Scheduler controls repetition.

---

## 26. API Integration

This collector must not create public API routes.

The API should later read from normalized tables, not from raw payloads.

Do not expose raw payloads to TradeHub as normal market data.

A future API document will define:

```txt
09_API_DESIGN.md
```

---

## 27. TradeHub Integration Boundary

This collector does not call TradeHub.

It does not update the TradeHub database.

It does not import TradeHub Prisma models.

It does not trigger TradeHub trading logic.

Integration comes later through:

```txt
TradeHub -> tradehub-data API
```

or internal service networking.

---

## 28. Testing Requirements

Codex must add tests for the collector module.

Minimum tests:

### 28.1 Hashing Test

Verify the same payload produces the same hash.

### 28.2 Client Success Test

Mock HTTP response and verify:

- status captured
- content type captured
- body captured
- source URL preserved

### 28.3 Client Retry Test

Mock temporary failure and verify retry behavior.

### 28.4 Collector Stores Raw Payload Test

Given a successful fake HTTP response:

- ingestion run is created
- raw payload is stored
- result is success

### 28.5 Duplicate Payload Test

Given the same payload hash:

- duplicate is skipped or reused
- no duplicate raw payload is created
- result remains success

### 28.6 Full Failure Test

Given all URLs fail:

- ingestion run status becomes failed
- error metadata is recorded
- result is failed

### 28.7 Disabled Collector Test

Given config enabled=false:

- no HTTP request is made
- result is skipped

---

## 29. Test Tools

Recommended testing stack:

- pytest
- pytest-asyncio
- respx or httpx MockTransport
- SQLAlchemy test session
- PostgreSQL test database if available
- SQLite only if repository code supports it safely

Do not write tests that depend on live BVC website availability.

External websites must not be called during unit tests.

---

## 30. Mock Payload Example

Use a small fake HTML payload in tests.

Example:

```html
<html>
  <body>
    <h1>Example Instrument</h1>
    <div class="price">123,45 MAD</div>
    <div class="variation">+1,23 %</div>
  </body>
</html>
```

This is only for raw storage tests.

Do not build real parsing logic from this fake HTML unless parser implementation is requested.

---

## 31. Data Quality Events

The collector can create basic quality events only if the database already supports them.

Examples:

- source unavailable
- empty payload
- unexpected content type
- repeated failure

If `data_quality_events` is not implemented yet, log the issue and store it in ingestion run metadata.

Do not create a new monitoring subsystem inside this task.

---

## 32. Failure Modes to Handle

The collector must handle:

- DNS failure
- connection timeout
- read timeout
- HTTP 403
- HTTP 404
- HTTP 429
- HTTP 500+
- empty response body
- unexpected content type
- database insert failure
- duplicate payload
- collector disabled
- partial URL failure

Do not hide failures.

Financial data pipelines must be explicit when data is stale or missing.

---

## 33. Staleness Awareness

The collector should not decide final price freshness.

But it should store enough metadata for later freshness checks:

- `collected_at`
- `source_url`
- `http_status`
- `payload_hash`
- source timestamp if visible in raw data
- ingestion run status

Later, API endpoints can report:

```txt
latest_price.last_seen_at
latest_price.source_timestamp
latest_price.is_stale
```

---

## 34. Decimal and Locale Notes

Moroccan/French financial pages may use:

```txt
123,45
1 234,56
1.234,56
+0,94 %
-0,94 %
MAD
DH
```

This collector does not need to parse these values yet.

But the parser later must handle locale-specific decimal formatting.

Do not convert financial values to float.

Use `Decimal` in parser/normalizer.

---

## 35. Character Encoding

The HTTP client should respect response encoding when provided.

If encoding is missing, default safely to UTF-8.

Raw text should preserve accents and French labels correctly.

Examples:

```txt
Ouverture
Plus haut
Plus bas
Cours de clôture veille
Capitalisation
```

---

## 36. Security Considerations

The collector must:

- not execute source HTML or JavaScript
- not store secrets in logs
- not accept arbitrary URLs from public users
- validate configured URLs
- restrict collection to approved source domains
- avoid server-side request forgery risks

For v0.1, approved domain should be configured but controlled internally.

Suggested allowed domains:

```txt
casablanca-bourse.com
www.casablanca-bourse.com
```

---

## 37. Configuration Validation

On startup, validate:

- base URL is present
- timeout is positive
- max retries is within safe limit
- sleep interval is non-negative
- user-agent is non-empty
- configured source URLs belong to allowed domains

Fail fast for bad local configuration.

---

## 38. Minimal Implementation Plan for Codex

When implementing this file, Codex should proceed in this order:

1. Read `AGENTS.md`.
2. Read `00_PROJECT_OVERVIEW.md`.
3. Read `01_ARCHITECTURE.md`.
4. Read `02_DATABASE_SCHEMA.md`.
5. Read `03_SOURCES_AND_COLLECTORS.md`.
6. Implement only this collector's required files.
7. Create or reuse database models for `data_sources`, `ingestion_runs`, and `raw_payloads` only if not already implemented.
8. Add hashing helper if missing.
9. Add HTTP client.
10. Add collector class.
11. Add CLI/manual runner.
12. Add unit tests.
13. Do not implement parser/normalizer/API unless explicitly requested.

---

## 39. Codex Prompt Template

Use this prompt when asking Codex to implement the module:

```txt
Read these files first:
- AGENTS.md
- docs/00_PROJECT_OVERVIEW.md
- docs/01_ARCHITECTURE.md
- docs/02_DATABASE_SCHEMA.md
- docs/03_SOURCES_AND_COLLECTORS.md
- docs/04_BVC_PRICE_COLLECTOR.md

Implement ONLY the BVC price collector foundation.

Requirements:
- collector writes raw payloads only
- no normalized price tables yet
- no parser yet
- no public API yet
- no TradeHub integration yet
- use httpx AsyncClient
- create ingestion_runs records
- create raw_payloads records
- compute sha256 payload hash
- support retries, timeouts, and safe rate limiting
- add tests with mocked HTTP responses
- do not call live external websites in tests
- keep changes small and architecture-compliant

Before coding, show the planned file changes.
After coding, show how to run tests and how to run the collector manually.
```

---

## 40. Acceptance Criteria

The task is complete when:

- BVC price collector can run once from terminal
- collector creates an ingestion run
- collector fetches configured source URL(s)
- collector stores raw payloads
- duplicate payloads are handled idempotently
- failures update ingestion run status
- HTTP client has timeout and retry behavior
- configuration is environment-driven
- tests pass without live external network calls
- no parser/normalizer/API is accidentally implemented
- no TradeHub project files are modified

---

## 41. Non-Goals

Do not implement in this task:

- full market calendar
- all listed company discovery
- price parser
- normalizer
- latest price API
- chart API
- index collector
- AMMC collector
- news collector
- scheduler daemon
- Redis queue
- Kafka/NATS
- Playwright crawler
- TradeHub backend changes
- frontend UI

---

## 42. Future Files That Will Extend This Work

This collector will later connect with:

```txt
05_COMPANY_DATA_COLLECTOR.md
08_NORMALIZATION_PIPELINE.md
09_API_DESIGN.md
10_SCHEDULER_AND_WORKERS.md
12_TRADEHUB_INTEGRATION.md
```

Do not try to solve those future phases here.

---

## 43. Final Reminder for Codex

This task is about reliable raw data collection, not financial interpretation.

The correct first milestone is:

```txt
fetch safely -> store raw payload -> record ingestion status -> test behavior
```

Everything else comes later.
