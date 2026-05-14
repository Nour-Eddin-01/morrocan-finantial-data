# 03_SOURCES_AND_COLLECTORS.md

# TradeHub Data — Sources and Collectors Specification

## 1. Purpose

This document defines how `tradehub-data` should think about external data sources and collectors.

The goal is to build a clean, reliable, and maintainable ingestion system for Moroccan financial market data.

This file does **not** define one specific scraper implementation.

It defines the general rules and structure that every future collector must follow.

Future source-specific documents should extend this file, for example:

```txt
04_BVC_PRICE_COLLECTOR.md
05_COMPANY_DATA_COLLECTOR.md
06_NEWS_COLLECTOR.md
07_AMMC_FILINGS_COLLECTOR.md
08_NORMALIZATION_PIPELINE.md
```

---

## 2. Core Rule

The project must never be designed as random scraping scripts.

Every data source must be handled through a clear collector contract.

The required pipeline is:

```txt
external source
    ↓
collector
    ↓
raw storage
    ↓
parser
    ↓
normalizer
    ↓
validator
    ↓
normalized database
    ↓
API
    ↓
TradeHub
```

A collector is only responsible for collecting and storing raw source data.

A collector must not directly write final business records such as `price_bars`, `companies`, `news_articles`, or `financial_reports` unless the source-specific document explicitly allows it for a temporary prototype.

Default rule:

```txt
collector -> raw_payloads only
```

---

## 3. Why Sources Need Structure

Financial data sources are unstable.

They may change:

- page structure
- table columns
- file names
- date formats
- number formats
- encoding
- URLs
- pagination
- anti-bot behavior
- publication schedule
- data corrections

So the system must preserve the original source response before trying to transform it.

This allows us to:

- debug parsing bugs
- reprocess historical payloads
- compare source changes
- audit where a value came from
- prove when a value was collected
- protect the normalized database from broken scraper logic

---

## 4. Source Categories

The project should organize sources by financial domain.

### 4.1 Market Data Sources

Purpose:

- listed instruments
- latest prices
- daily OHLCV
- traded volume
- traded value
- market indices
- market status
- sector performance

Candidate source types:

- official Casablanca Stock Exchange pages
- official downloadable files if available
- structured APIs if discovered and legally usable
- public market pages
- partner/vendor feeds in the future

Expected raw payload examples:

```txt
raw_market_price_snapshot
raw_market_indices_snapshot
raw_market_session_snapshot
raw_instrument_list_snapshot
```

---

### 4.2 Company and Instrument Sources

Purpose:

- listed companies
- tickers/symbols
- ISIN codes
- sector classification
- instrument names
- listing status
- instrument type
- market segment
- company profile

Candidate source types:

- Casablanca Stock Exchange company pages
- issuer pages
- official market documents
- AMMC issuer records
- annual reports

Expected raw payload examples:

```txt
raw_company_profile_snapshot
raw_instrument_metadata_snapshot
raw_issuer_list_snapshot
```

---

### 4.3 AMMC and Regulatory Sources

Purpose:

- financial statements
- issuer publications
- financial operations
- registered documents
- regulatory press releases
- warnings and official communications

Candidate source types:

- AMMC publications
- AMMC financial information pages
- issuer financial statements
- official PDF documents

Expected raw payload examples:

```txt
raw_ammc_filing_page
raw_ammc_financial_statement
raw_ammc_publication
raw_regulatory_pdf
```

---

### 4.4 News Sources

Purpose:

- company news
- market news
- macroeconomic news
- earnings news
- dividend announcements
- corporate actions
- analyst-style articles

Candidate source types:

- official company press releases
- Moroccan financial news websites
- economic newspapers
- public RSS feeds if available
- official regulator/exchange news pages

Expected raw payload examples:

```txt
raw_news_article
raw_news_listing_page
raw_press_release
raw_rss_item
```

Important:

News collection must respect publisher rights. Do not store full copyrighted article bodies unless the source allows it. Prefer storing metadata, summary, canonical URL, title, publication date, and extracted financial entities.

---

### 4.5 Macroeconomic Sources

Purpose:

- interest rates
- exchange rates
- inflation
- monetary statistics
- economic indicators
- GDP-related data
- labor and demographic indicators

Candidate source types:

- Bank Al-Maghrib statistics
- HCP statistics
- World Bank open data
- IMF data
- official government open-data portals

Expected raw payload examples:

```txt
raw_macro_indicator_snapshot
raw_exchange_rate_snapshot
raw_interest_rate_snapshot
raw_inflation_snapshot
```

---

### 4.6 Document Sources

Purpose:

- annual reports
- semi-annual reports
- quarterly reports if available
- prospectuses
- operation notes
- official PDFs
- corporate presentations

Candidate source types:

- AMMC document pages
- issuer websites
- exchange publication pages

Expected raw payload examples:

```txt
raw_document_metadata
raw_pdf_file
raw_pdf_text_extract
```

Documents should be stored as files/object storage entries plus metadata in PostgreSQL.

---

## 5. Source Registry

Every external source must be registered before building a collector.

The source registry should be stored in the database table defined in `02_DATABASE_SCHEMA.md`:

```txt
data_sources
```

Each source should have:

```txt
id
name
code
source_type
base_url
official_status
requires_auth
allowed_collection_method
rate_limit_per_minute
terms_review_status
is_active
created_at
updated_at
```

Example source records:

```txt
code: bvc_official
name: Casablanca Stock Exchange
source_type: market_data
official_status: official
allowed_collection_method: http_html_or_api_if_available
rate_limit_per_minute: conservative
```

```txt
code: ammc_official
name: Moroccan Capital Market Authority
source_type: regulatory
official_status: official
allowed_collection_method: http_html_pdf
rate_limit_per_minute: conservative
```

```txt
code: bam_official
name: Bank Al-Maghrib
source_type: macroeconomic
official_status: official
allowed_collection_method: http_html_file_download
rate_limit_per_minute: conservative
```

```txt
code: hcp_official
name: Haut-Commissariat au Plan
source_type: macroeconomic
official_status: official
allowed_collection_method: http_html_file_download
rate_limit_per_minute: conservative
```

---

## 6. Collector Definition

A collector is a source-specific module that fetches data and stores the raw result.

A collector must have one clear responsibility.

Good collector examples:

```txt
BvcLatestPricesCollector
BvcInstrumentListCollector
AmmcFinancialStatementsCollector
BamExchangeRatesCollector
NewsListingCollector
CompanyPressReleaseCollector
```

Bad collector examples:

```txt
MoroccoEverythingCollector
ScrapeAllSitesCollector
MarketAndNewsAndReportsCollector
```

Each collector should answer:

```txt
What source does it collect from?
What exact data does it collect?
How often should it run?
What raw table does it write to?
What parser will consume the raw payload?
How does it avoid duplicates?
How does it report errors?
```

---

## 7. Collector Contract

Every collector must follow this contract.

### 7.1 Input

A collector receives:

```txt
source configuration
run context
optional date range
optional instrument identifier
optional pagination cursor
```

Example:

```python
CollectorRunContext(
    source_code="bvc_official",
    run_id="uuid",
    started_at="2026-05-14T15:00:00Z",
    requested_by="scheduler",
)
```

---

### 7.2 Output

A collector outputs raw payload records.

Minimum output fields:

```txt
source_id
collector_name
ingestion_run_id
source_url
http_status
content_type
payload_type
payload_hash
payload_json or payload_text or file_path
collected_at
status
error_message
```

Collector success does not mean the data is already valid.

Success only means:

```txt
The source was reached and the raw payload was stored.
```

Validation happens later.

---

### 7.3 Side Effects

Collectors are allowed to:

- create ingestion run logs
- create raw payloads
- save downloaded files
- update collector health metrics
- record retry failures

Collectors are not allowed to:

- create final stock records directly
- create final price bars directly
- calculate indicators
- modify TradeHub user data
- call TradeHub trading endpoints
- send notifications to users
- contain frontend logic

---

## 8. Collector Folder Structure

Recommended structure:

```txt
src/tradehub_data/collectors/
├── base.py
├── registry.py
├── bvc/
│   ├── __init__.py
│   ├── latest_prices.py
│   ├── instruments.py
│   ├── indices.py
│   └── config.py
├── ammc/
│   ├── __init__.py
│   ├── filings.py
│   ├── financial_statements.py
│   └── config.py
├── bam/
│   ├── __init__.py
│   ├── exchange_rates.py
│   ├── interest_rates.py
│   └── config.py
├── hcp/
│   ├── __init__.py
│   ├── macro_indicators.py
│   └── config.py
└── news/
    ├── __init__.py
    ├── rss.py
    ├── article_pages.py
    └── config.py
```

---

## 9. Base Collector Interface

Codex should implement a shared base collector before implementing many source-specific collectors.

Suggested interface:

```python
from abc import ABC, abstractmethod

class BaseCollector(ABC):
    name: str
    source_code: str

    @abstractmethod
    async def collect(self, context):
        """Fetch source data and store raw payloads."""
        raise NotImplementedError
```

A better implementation can return a structured result:

```python
class CollectorResult:
    collector_name: str
    source_code: str
    run_id: str
    status: str
    raw_payload_ids: list[str]
    collected_count: int
    failed_count: int
    error_message: str | None
```

---

## 10. Ingestion Run Tracking

Every collector execution should create an `ingestion_runs` record.

The run should track:

```txt
id
source_id
collector_name
status
started_at
finished_at
items_collected
items_failed
error_message
metadata
```

Statuses:

```txt
pending
running
success
partial_success
failed
cancelled
```

This allows the system to answer:

- when did the collector last run?
- did it succeed?
- how many records did it collect?
- did the source break?
- which raw payloads came from this run?

---

## 11. Raw Payload Rules

Every raw payload must have a stable hash.

Use a normalized hash strategy:

```txt
sha256(source_code + source_url + normalized_payload_body)
```

or for files:

```txt
sha256(file_bytes)
```

The goal is to avoid duplicate raw records when the same payload is collected more than once.

Raw payloads should support:

```txt
payload_json
payload_text
file_path
payload_hash
content_type
http_status
source_url
collected_at
```

Do not store huge PDFs directly inside PostgreSQL as JSON/text.

For large documents:

```txt
PostgreSQL -> metadata
filesystem/object storage -> file content
```

---

## 12. Idempotency Rules

Collectors must be safe to run repeatedly.

If a collector runs every minute, it must not create duplicate logical records every minute.

Use:

- payload hashes
- source URL
- source publication ID if available
- trading date
- instrument code
- document URL
- canonical article URL

Examples:

```txt
same source_url + same payload_hash = duplicate raw payload
same instrument + same timestamp + same timeframe = duplicate price bar
same document_url + same document_hash = duplicate filing document
same canonical_url = duplicate news article
```

---

## 13. Scheduling Strategy

Not all sources should run every minute.

Each source needs a realistic update frequency.

Suggested initial schedule:

| Data Type | Suggested Frequency | Notes |
|---|---:|---|
| Latest market prices | Every 1 minute during market hours | Only if source allows it and system is stable |
| Listed instruments | Daily | Usually changes rarely |
| Indices | Every 1 minute during market hours | Same as prices |
| Financial statements | Daily or every few hours | Documents do not update every minute |
| AMMC publications | Daily or every few hours | Depends on source behavior |
| News listing pages | Every 5 to 15 minutes | Avoid aggressive crawling |
| Macroeconomic indicators | Daily or weekly | Depends on publication frequency |
| Static company metadata | Daily or weekly | Rarely changes |

The scheduler must support source-specific intervals.

Do not force every collector to run every minute.

---

## 14. Market Hours Awareness

Market price collectors should be market-hours aware.

They should support:

```txt
market_open_time
market_close_time
trading_days
holidays
timezone
```

Initial timezone:

```txt
Africa/Casablanca
```

Outside market hours, price collectors can either:

- stop running
- run less frequently
- collect only end-of-day confirmation data

Do not hardcode market hours forever.

Market calendar should eventually become a database table:

```txt
market_calendar
```

---

## 15. Access and Compliance Rules

Collectors must respect source websites and legal boundaries.

Before implementing a collector, check:

- whether an official API exists
- whether downloads are public
- whether robots.txt allows access
- whether terms of use allow automated collection
- whether the collection frequency is reasonable
- whether full article/document content may be stored

Forbidden behavior:

- bypassing authentication
- bypassing paywalls
- bypassing anti-bot protection
- aggressive crawling
- hiding identity through suspicious behavior
- storing copyrighted article bodies without permission
- collecting personal data unrelated to financial market data

Use conservative request rates by default.

---

## 16. HTTP Client Rules

Use `httpx` for normal HTTP collectors.

Use Playwright only when:

- the source requires JavaScript rendering
- the source cannot be collected through HTTP requests
- no official API or file endpoint is available

Default HTTP behavior:

```txt
timeout: 20 seconds
retries: 3
backoff: exponential
user_agent: clear project user agent
rate_limit: source-specific
```

Recommended user agent format:

```txt
TradeHubDataBot/0.1 (+contact-email-or-project-url)
```

Do not fake a browser user agent unless explicitly justified in the source-specific spec.

---

## 17. Error Handling Rules

Collectors must not silently fail.

Every failure should be recorded in:

```txt
ingestion_runs
collector_errors
logs
metrics
```

Error categories:

```txt
network_error
http_error
parse_error
validation_error
rate_limited
source_changed
empty_response
unexpected_format
storage_error
unknown_error
```

A failed collector should not crash the whole data platform.

It should mark the run as failed and allow other collectors to continue.

---

## 18. Parser Responsibility

Parsers consume raw payloads and produce intermediate structured objects.

Parser examples:

```txt
BVC latest price HTML -> ParsedPriceRow[]
AMMC filings page -> ParsedFiling[]
News listing page -> ParsedArticleLink[]
PDF document -> ParsedDocumentText
```

Parsers should not write final database rows directly unless mediated by a normalizer service.

Recommended flow:

```txt
raw_payload -> parser -> parsed DTO -> normalizer -> normalized database
```

---

## 19. Normalizer Responsibility

Normalizers map parsed source-specific objects into canonical internal models.

Example:

```txt
ParsedBvcPriceRow
    ↓
Instrument lookup by symbol / ISIN
    ↓
PriceBar upsert
    ↓
LatestInstrumentPrice update
```

Normalizers must handle:

- number formatting
- date formatting
- source-specific names
- ticker mapping
- ISIN matching
- duplicate records
- missing values
- source corrections

---

## 20. Validator Responsibility

Validators check whether normalized data is reasonable before publication.

Validation examples:

```txt
price must be non-negative
volume must be non-negative
high >= low
high >= open/close if all available
low <= open/close if all available
trading date must be valid
instrument must exist
source must be active
```

For suspicious values, use status flags instead of blindly publishing.

Example statuses:

```txt
valid
suspicious
rejected
needs_review
```

---

## 21. Initial Collector Roadmap

Build collectors in this order.

### Phase 1 — Foundation Collectors

1. `BvcInstrumentListCollector`
2. `BvcLatestPricesCollector`
3. `BvcIndicesCollector`

Reason:

TradeHub needs instruments, prices, and indices before everything else.

---

### Phase 2 — Market History

4. `BvcDailyHistoryCollector`
5. `BvcIntradaySnapshotCollector` if available and legally usable

Reason:

Historical data is needed for charts, portfolio performance, and analysis.

---

### Phase 3 — Regulatory Data

6. `AmmcFinancialStatementsCollector`
7. `AmmcPublicationsCollector`
8. `AmmcOperationsCollector`

Reason:

Regulatory documents are important for fundamental analysis and event detection.

---

### Phase 4 — News and Events

9. `MarketNewsCollector`
10. `CompanyPressReleaseCollector`
11. `CorporateActionsCollector`

Reason:

News and events are useful for alerts, sentiment, and user engagement.

---

### Phase 5 — Macro Data

12. `BamRatesCollector`
13. `BamExchangeRatesCollector`
14. `HcpMacroIndicatorsCollector`
15. `WorldBankMoroccoIndicatorsCollector`

Reason:

Macroeconomic context is useful later for advanced analytics.

---

## 22. First Implementation Target

The first real implementation should not build all collectors.

The first implementation should build only the collector framework and one simple collector.

Recommended first Codex task:

```txt
Implement the collector foundation only:

- BaseCollector interface
- CollectorResult model
- ingestion_runs repository/service
- raw_payloads repository/service
- source registry model support
- basic HTTP client wrapper
- collector registry
- unit tests

Do not implement a real external scraper yet.
```

Then second task:

```txt
Implement BvcInstrumentListCollector only.

It must:
- read source config
- fetch the configured source URL
- store raw payload
- create an ingestion run
- avoid duplicate raw payloads using hash
- not write to normalized companies/instruments directly
- include tests with mocked HTTP responses
```

---

## 23. Collector Configuration

Collectors should be configurable through environment variables and/or database source records.

Example environment variables:

```txt
TRADEHUB_DATA_USER_AGENT=TradeHubDataBot/0.1
HTTP_TIMEOUT_SECONDS=20
COLLECTOR_DEFAULT_RETRIES=3
COLLECTOR_DEFAULT_BACKOFF_SECONDS=2
BVC_COLLECTOR_ENABLED=true
AMMC_COLLECTOR_ENABLED=true
NEWS_COLLECTOR_ENABLED=false
```

Example collector config object:

```python
CollectorConfig(
    source_code="bvc_official",
    base_url="https://www.casablanca-bourse.com",
    timeout_seconds=20,
    retries=3,
    rate_limit_per_minute=30,
    enabled=True,
)
```

---

## 24. Collector Registry

The system should have a collector registry.

Purpose:

- discover available collectors
- run collector by name
- enable/disable collectors
- schedule collectors
- expose collector status through admin/debug API

Example:

```python
collector_registry = {
    "bvc_instruments": BvcInstrumentListCollector,
    "bvc_latest_prices": BvcLatestPricesCollector,
    "ammc_filings": AmmcFilingsCollector,
}
```

The scheduler should call collectors through the registry, not by importing random classes everywhere.

---

## 25. API Visibility

The API should expose normalized data to TradeHub.

The API may expose internal collector health to admins or internal services.

Public/internal market API examples:

```txt
GET /api/v1/instruments
GET /api/v1/instruments/{symbol}
GET /api/v1/instruments/{symbol}/prices/latest
GET /api/v1/instruments/{symbol}/prices/history
GET /api/v1/indices
```

Internal/admin collector API examples:

```txt
GET /api/v1/admin/sources
GET /api/v1/admin/collectors
GET /api/v1/admin/ingestion-runs
GET /api/v1/admin/ingestion-runs/{id}
```

Do not expose raw payload bodies publicly by default.

---

## 26. Testing Requirements

Every collector should have tests.

Minimum tests:

```txt
collector creates ingestion run
collector stores raw payload
collector computes stable hash
collector does not duplicate same payload
collector records HTTP errors
collector respects disabled config
collector handles empty response
```

Parser tests:

```txt
valid source fixture parses correctly
missing fields handled safely
unexpected format raises clear error
number formats are parsed correctly
date formats are parsed correctly
```

Normalizer tests:

```txt
creates new normalized record
updates existing record idempotently
rejects invalid values
maps source symbols to instruments
tracks source/raw payload traceability
```

Use fixture files for sample source responses.

Recommended test folder:

```txt
tests/
├── fixtures/
│   ├── bvc/
│   ├── ammc/
│   ├── bam/
│   └── news/
├── collectors/
├── parsers/
├── normalizers/
└── repositories/
```

---

## 27. Logging and Metrics

Collectors must log:

```txt
collector started
collector finished
source URL
status
items collected
items failed
run duration
error category
```

Metrics should eventually include:

```txt
collector_run_total
collector_run_success_total
collector_run_failed_total
collector_duration_seconds
raw_payloads_collected_total
collector_last_success_timestamp
collector_last_failure_timestamp
```

This is important because the system is expected to run continuously.

---

## 28. Data Quality Flags

Collectors and normalizers should support quality/status flags.

Examples:

```txt
raw_collected
parsed
normalized
validated
published
failed_parse
failed_validation
needs_review
```

The pipeline should make it clear where a piece of data is currently blocked.

---

## 29. TradeHub Integration Impact

The current TradeHub application has market-related features that depend on stock data and price history.

`tradehub-data` should eventually replace the internal market worker by exposing clean APIs or a controlled data feed.

The collector system must therefore prioritize:

```txt
instrument identity stability
latest price accuracy
historical price consistency
low-latency update path
API compatibility
monitoring and health checks
```

TradeHub should not care where the data came from.

TradeHub should only consume clean normalized data.

---

## 30. What Codex Should Do With This File

When Codex reads this file, it should understand:

1. Sources must be registered.
2. Collectors must be small and source-specific.
3. Raw data must be stored first.
4. Collectors must not directly write final business tables.
5. Every run must be tracked.
6. Duplicate raw payloads must be avoided.
7. Errors must be visible.
8. Tests are required.
9. Playwright is a fallback, not the default.
10. The first implementation should build the collector foundation before real scraping.

---

## 31. Recommended Codex Prompt For The Next Step

Use this prompt after `00_PROJECT_OVERVIEW.md`, `01_ARCHITECTURE.md`, `02_DATABASE_SCHEMA.md`, and this file exist in the repository.

```txt
Read these files first:

- AGENTS.md
- docs/00_PROJECT_OVERVIEW.md
- docs/01_ARCHITECTURE.md
- docs/02_DATABASE_SCHEMA.md
- docs/03_SOURCES_AND_COLLECTORS.md

Do not implement real scraping yet.

Create the initial collector foundation only:

- BaseCollector interface
- CollectorConfig model
- CollectorResult model
- CollectorRunContext model
- collector registry
- HTTP client wrapper with timeout/retry support
- raw payload hashing helper
- ingestion run service/repository skeleton
- raw payload service/repository skeleton
- pytest tests for hashing and collector result behavior

Follow the architecture rules.
Do not touch unrelated modules.
Do not implement BVC, AMMC, news, or macro collectors yet.
```

---

## 32. Final Note

The quality of `tradehub-data` will depend more on source discipline than on scraping speed.

A fast scraper with messy data is not valuable.

A slower but traceable, normalized, and reliable ingestion system is much more powerful.

The goal is not to collect everything immediately.

The goal is to build a foundation that can safely collect financial data forever.
