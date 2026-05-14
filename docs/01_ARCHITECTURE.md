# 01_ARCHITECTURE.md

# TradeHub Data Architecture

## 1. Purpose

`tradehub-data` is the dedicated financial data infrastructure project for TradeHub.

Its role is to collect, store, normalize, validate, and expose financial market data related to the Moroccan stock market.

The long-term goal is to replace the current market data worker inside the main TradeHub application with a separate, reliable, always-running data platform.

TradeHub should not be responsible for scraping or collecting market data directly.

Instead:

```txt
tradehub-data collects and prepares the data
tradehub consumes the data through APIs or database integration
```

---

## 2. High-Level Architecture

The system should follow this architecture:

```txt
External Sources
      |
      v
Collectors
      |
      v
Raw Data Storage
      |
      v
Parsers / Normalizers
      |
      v
Validated Financial Database
      |
      v
Internal API / Data Feed
      |
      v
TradeHub Application
```

This means collectors should not write directly to final production tables.

The correct flow is:

```txt
collect -> raw store -> normalize -> validate -> publish
```

---

## 3. Main Architecture Principle

The project must be designed as a **data engineering system**, not just a scraper.

The main goal is not only to fetch web pages.

The real goal is to create a clean, structured, historical, and reusable financial database.

Core principles:

- Data correctness is more important than speed.
- Historical data must never be silently overwritten.
- Raw data should be saved before normalization.
- Every collector must be retry-safe.
- Every ingestion process must be idempotent.
- The API should expose clean data, not raw scraped data.
- TradeHub should consume only validated data.

---

## 4. Main Components

The project should be split into the following components.

```txt
tradehub-data/
├── collectors/
├── parsers/
├── normalizers/
├── validators/
├── storage/
├── api/
├── workers/
├── scheduler/
├── monitoring/
├── config/
├── tests/
├── docs/
└── scripts/
```

---

## 5. Component Responsibilities

## 5.1 Collectors

Collectors are responsible only for fetching data from external sources.

Examples:

```txt
collectors/bvc_prices/
collectors/bvc_companies/
collectors/ammc_filings/
collectors/news/
collectors/macro/
```

Collectors should:

- fetch data from a source,
- handle HTTP requests,
- handle pagination if needed,
- handle retries,
- save raw responses,
- record collection metadata,
- avoid business logic.

Collectors should NOT:

- calculate indicators,
- decide if a stock is good or bad,
- write directly to final normalized tables,
- expose API endpoints,
- contain UI logic,
- contain TradeHub-specific business rules.

Expected collector output:

```txt
RawMarketSnapshot
RawCompanyData
RawNewsArticle
RawFilingDocument
RawIndexSnapshot
```

---

## 5.2 Raw Storage

Raw storage keeps the original collected data before transformation.

This is important because external sources may change format, break, or return inconsistent data.

Raw storage allows us to:

- debug collector failures,
- reprocess old data,
- audit what was collected,
- compare source changes over time,
- avoid losing information.

Raw data can be stored in:

- PostgreSQL raw tables for structured raw data,
- object storage or filesystem for PDFs, HTML, CSV, JSON, and screenshots if needed.

Example raw tables:

```txt
raw_bvc_price_snapshots
raw_bvc_company_snapshots
raw_ammc_filings
raw_news_articles
raw_macro_snapshots
```

Raw records should include metadata:

```txt
id
source_name
source_url
payload
payload_hash
collected_at
status
error_message
created_at
```

---

## 5.3 Parsers

Parsers convert source-specific raw payloads into intermediate structured objects.

A parser understands the format of one source.

Example:

```txt
BVC HTML table -> parsed price rows
AMMC page -> parsed filing metadata
News HTML -> parsed title, date, body, source
```

Parsers should be isolated from collectors.

Collectors fetch.

Parsers interpret.

---

## 5.4 Normalizers

Normalizers convert parsed data into the final internal TradeHub Data model.

Example:

```txt
Source company name: "ATTIJARIWAFA BANK"
Normalized company: Attijariwafa Bank
Ticker: ATW
Market: Casablanca Stock Exchange
Currency: MAD
```

Normalizers should handle:

- ticker mapping,
- ISIN mapping,
- company name cleanup,
- date normalization,
- number normalization,
- currency normalization,
- duplicate detection,
- missing value handling,
- source priority rules.

Normalizers write to final normalized tables.

Example final tables:

```txt
companies
securities
exchanges
market_prices
price_history
market_indices
index_prices
financial_reports
corporate_actions
news_articles
macro_indicators
```

---

## 5.5 Validators

Validators check if normalized data is safe to publish.

Validation examples:

- price cannot be negative,
- volume cannot be negative,
- trade date must be valid,
- ticker must exist,
- ISIN format should be valid when available,
- duplicate price rows should be ignored,
- large unexpected price moves should be flagged,
- missing critical fields should be marked as incomplete.

Validators should produce warnings and errors.

Not every validation failure should crash the system.

Some records can be stored as rejected or incomplete for later review.

---

## 5.6 API Layer

The API exposes clean validated data to TradeHub and future clients.

Recommended API framework:

```txt
FastAPI
```

Main API responsibilities:

- expose latest stock prices,
- expose historical OHLCV data,
- expose company metadata,
- expose indices,
- expose news,
- expose filings,
- expose market status,
- expose collector health,
- expose sync status.

The API should not scrape data directly.

The API should only read from normalized tables and system status tables.

Example endpoints:

```txt
GET /health
GET /v1/stocks
GET /v1/stocks/{ticker}
GET /v1/stocks/{ticker}/prices/latest
GET /v1/stocks/{ticker}/prices/history
GET /v1/indices
GET /v1/news
GET /v1/filings
GET /v1/sync/status
GET /v1/sources/status
```

---

## 5.7 Scheduler

The scheduler decides when collectors and normalizers run.

For the first version, use a simple scheduler.

Recommended initial options:

```txt
APScheduler
or
Celery Beat + Celery workers
```

Start simple.

Do not introduce Kafka, Airflow, or complex orchestration in the first version unless the project truly needs it.

Initial scheduling targets:

```txt
prices: every 1 minute during market hours
indices: every 1 minute during market hours
news: every 5 minutes
company metadata: once per day
filings: every 10 to 30 minutes
macro data: once per day or once per week
```

The scheduler must know market hours.

It should avoid unnecessary high-frequency scraping when the market is closed.

---

## 5.8 Workers

Workers execute collection and normalization jobs.

Worker responsibilities:

- run collectors,
- process raw data,
- normalize parsed data,
- update sync status,
- emit logs and metrics,
- retry failed jobs safely.

Workers should be stateless where possible.

The database should be the source of truth.

---

## 5.9 Monitoring

Monitoring is required because this project must run continuously.

The system should track:

- last successful sync time,
- last failed sync time,
- number of records collected,
- number of records normalized,
- number of rejected records,
- source response time,
- source failure rate,
- API latency,
- worker crashes,
- database errors.

Recommended monitoring stack:

```txt
Prometheus
Grafana
structured logs
```

At minimum, every collector must record status in the database.

Example status table:

```txt
source_sync_status
```

Fields:

```txt
id
source_name
job_name
status
last_success_at
last_failure_at
last_error_message
records_collected
records_processed
duration_ms
created_at
updated_at
```

---

## 6. Recommended Technology Stack

Initial stack:

```txt
Language: Python
API: FastAPI
Database: PostgreSQL
ORM: SQLAlchemy or SQLModel
Migrations: Alembic
Cache: Redis
Scheduler: APScheduler first, Celery later if needed
HTTP client: httpx
HTML parsing: BeautifulSoup or selectolax
Browser automation: Playwright only when needed
Testing: pytest
Containers: Docker + Docker Compose
Monitoring: Prometheus + Grafana later
```

Do not over-engineer the first version.

Start with a modular monolith.

Move to distributed services only when the data volume or reliability requirements justify it.

---

## 7. Database Architecture

The database should be split logically into four layers.

```txt
1. Reference data
2. Raw data
3. Normalized market data
4. System/operations data
```

---

## 7.1 Reference Data

Reference data describes stable entities.

Examples:

```txt
exchanges
companies
securities
sectors
industries
currencies
sources
```

---

## 7.2 Raw Data

Raw data contains original collected payloads.

Examples:

```txt
raw_bvc_price_snapshots
raw_news_articles
raw_ammc_documents
raw_company_snapshots
```

---

## 7.3 Normalized Market Data

This layer powers the TradeHub product.

Examples:

```txt
market_prices
price_history
index_prices
market_indices
company_profiles
financial_reports
corporate_actions
news_articles
```

---

## 7.4 System Data

System data tracks jobs, failures, source health, and audit logs.

Examples:

```txt
collection_jobs
source_sync_status
data_quality_issues
normalization_runs
api_keys
```

---

## 8. Data Flow Example: Price Collection

Example flow for price data:

```txt
1. Scheduler triggers bvc_price_collector every 1 minute.
2. Collector fetches latest price table from source.
3. Raw response is saved in raw_bvc_price_snapshots.
4. Parser extracts rows from raw payload.
5. Normalizer maps rows to known securities.
6. Validator checks price, volume, date, and duplicate rules.
7. Valid rows are upserted into market_prices and price_history.
8. source_sync_status is updated.
9. TradeHub reads latest prices from the API.
```

Important:

The collector should succeed even if some rows fail normalization.

Failed rows should be stored and reported.

---

## 9. Data Flow Example: News Collection

```txt
1. Scheduler triggers news collectors every 5 minutes.
2. Collector fetches articles from configured sources.
3. Raw article HTML or JSON is stored.
4. Parser extracts title, body, publication date, URL, author, and source.
5. Normalizer detects related companies or tickers if possible.
6. Duplicates are removed using URL hash and content hash.
7. Clean articles are stored in news_articles.
8. TradeHub API exposes recent news to frontend.
```

Later, news can support:

```txt
sentiment analysis
AI summaries
company mention detection
market event extraction
```

But those should not be part of the first implementation.

---

## 10. TradeHub Integration Architecture

The main TradeHub application should consume data from `tradehub-data`.

Recommended integration approach for the first version:

```txt
TradeHub backend -> tradehub-data API -> tradehub-data PostgreSQL
```

This keeps TradeHub independent from the internal database structure of `tradehub-data`.

Later, if performance requires it, TradeHub can use:

```txt
read replica
shared database views
message events
websocket feed
```

But the first clean integration should be through an HTTP API.

---

## 11. Replacement of Current Market Worker

The current TradeHub market worker should eventually be replaced by `tradehub-data`.

Target transition:

```txt
Before:
TradeHub backend market-worker -> provider -> TradeHub database

After:
tradehub-data collectors -> tradehub-data database -> tradehub-data API -> TradeHub backend
```

TradeHub modules that will likely consume this data:

```txt
stocks
trading
portfolio
watchlist
dashboard
notifications
```

The first integration goal should be to make the existing TradeHub stock endpoints use `tradehub-data` as the source of truth.

---

## 12. Runtime Architecture

Initial Docker services:

```txt
postgres
redis
api
worker
scheduler
```

Example:

```txt
tradehub-data-postgres
tradehub-data-redis
tradehub-data-api
tradehub-data-worker
tradehub-data-scheduler
```

Later services:

```txt
prometheus
grafana
object-storage
message-queue
```

---

## 13. Suggested Folder Structure

```txt
tradehub-data/
├── AGENTS.md
├── README.md
├── docker-compose.yml
├── .env.example
├── pyproject.toml
│
├── docs/
│   ├── 00_PROJECT_OVERVIEW.md
│   ├── 01_ARCHITECTURE.md
│   ├── 02_DATABASE_SCHEMA.md
│   ├── 03_SOURCES_AND_COLLECTORS.md
│   └── ...
│
├── app/
│   ├── main.py
│   ├── config.py
│   ├── api/
│   ├── db/
│   ├── models/
│   ├── schemas/
│   ├── services/
│   └── utils/
│
├── collectors/
│   ├── base.py
│   ├── bvc_prices/
│   ├── bvc_companies/
│   ├── ammc_filings/
│   ├── news/
│   └── macro/
│
├── parsers/
│   ├── bvc_prices.py
│   ├── bvc_companies.py
│   ├── ammc_filings.py
│   └── news.py
│
├── normalizers/
│   ├── prices.py
│   ├── companies.py
│   ├── filings.py
│   └── news.py
│
├── validators/
│   ├── prices.py
│   ├── companies.py
│   └── news.py
│
├── workers/
│   ├── run_worker.py
│   └── jobs/
│
├── scheduler/
│   └── scheduler.py
│
├── migrations/
│   └── alembic/
│
├── tests/
│   ├── unit/
│   ├── integration/
│   └── fixtures/
│
└── scripts/
    ├── seed_reference_data.py
    └── run_collector_once.py
```

---

## 14. API Versioning

All public/internal API routes should be versioned.

Use:

```txt
/v1/...
```

Do not expose unversioned business endpoints except:

```txt
/health
/metrics
```

Good examples:

```txt
GET /v1/stocks
GET /v1/stocks/ATW
GET /v1/stocks/ATW/history
GET /v1/news
GET /v1/sync/status
```

Avoid:

```txt
GET /stocks
GET /prices
```

---

## 15. Idempotency Rules

Every job must be safe to run multiple times.

This is critical because workers may retry after failure.

Examples:

- Price history should use unique constraints on security, timestamp, and source.
- News articles should deduplicate by URL hash and content hash.
- Filings should deduplicate by source URL, title, company, and publication date.
- Company records should deduplicate by ticker or ISIN.
- Raw payloads should deduplicate by payload hash when useful.

Never assume a job runs only once.

---

## 16. Error Handling Strategy

The system should not crash completely because one source fails.

Error handling principles:

- one failed source should not stop other collectors,
- one invalid row should not reject the entire batch,
- errors should be logged with context,
- failed jobs should update source_sync_status,
- failed records should be stored when useful,
- retries should use backoff,
- permanent failures should be visible in monitoring.

---

## 17. Security Architecture

The API should not expose admin or raw-data endpoints publicly without protection.

Security rules:

- internal endpoints should require an API key or internal network access,
- secrets must come from environment variables,
- never commit credentials,
- avoid exposing raw scraped payloads to frontend users,
- validate all query parameters,
- rate-limit public endpoints if they are exposed externally.

---

## 18. Development Strategy With Codex

This project should be built file by file and module by module.

Codex should not be asked to build the entire system at once.

Correct workflow:

```txt
1. Read AGENTS.md
2. Read 00_PROJECT_OVERVIEW.md
3. Read 01_ARCHITECTURE.md
4. Read the specific task file
5. Implement only that task
6. Add tests
7. Do not modify unrelated modules
```

Example prompt for Codex:

```txt
Read AGENTS.md, docs/00_PROJECT_OVERVIEW.md, and docs/01_ARCHITECTURE.md.
Do not code yet.
Explain the architecture you understand and propose the initial file structure.
```

Then:

```txt
Now implement only the initial project skeleton.
Do not implement collectors yet.
Do not add scraping logic yet.
```

---

## 19. First Milestone Architecture

The first milestone should NOT include all collectors.

The first milestone should build the foundation.

Milestone 1 scope:

```txt
Docker Compose
FastAPI app
PostgreSQL connection
Alembic migrations
base models
health endpoint
source_sync_status table
base collector interface
basic scheduler shell
tests setup
```

Do not include:

```txt
full scraping
AI analysis
sentiment analysis
complex dashboards
Kafka
Airflow
microservices
real broker integration
```

---

## 20. Second Milestone Architecture

Milestone 2 should implement the first real source.

Recommended first source:

```txt
Bourse de Casablanca listed companies and price data
```

Build only one collector first.

Expected flow:

```txt
collector -> raw table -> parser -> normalizer -> final tables -> API endpoint
```

---

## 21. Future Architecture Evolution

The project may later evolve into a more advanced data platform.

Possible future additions:

```txt
Kafka or Redpanda for streaming data
TimescaleDB for time-series optimization
object storage for PDFs and reports
AI news summarization
sentiment analysis
company financial statement extraction
portfolio risk engine
public paid API
institutional dashboard
websocket market feed
```

These should be future phases, not initial implementation requirements.

---

## 22. Final Architecture Rule

Keep the first version simple, clean, and correct.

The correct priority is:

```txt
1. Good data model
2. Reliable ingestion
3. Clear normalization
4. Clean API
5. Monitoring
6. Performance optimization
```

Do not optimize before the data model and ingestion flow are correct.

