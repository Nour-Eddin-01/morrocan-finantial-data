# 00_PROJECT_OVERVIEW.md

# TradeHub Data — Project Overview

## 1. Purpose

`tradehub-data` is the dedicated financial data infrastructure project that will power TradeHub.

The goal is to collect, clean, normalize, store, and serve financial data related to the Moroccan stock market in a reliable and structured way.

This project is not just a scraper.

It is a market data platform designed to become the main data source for TradeHub.

---

## 2. Background

TradeHub currently has its own backend, frontend, database, market worker, trading simulation logic, portfolio system, watchlist, social features, notifications, and monitoring stack.

The existing TradeHub backend already contains a market worker responsible for synchronizing stock data, indices, and price history.

The long-term goal is to move market-data responsibilities out of the main TradeHub application and into this new project:

```txt
tradehub-data
```

Then TradeHub will consume market data from `tradehub-data` instead of managing market collection logic internally.

---

## 3. Vision

The vision is to build the central Moroccan financial data layer for TradeHub.

`tradehub-data` should eventually provide:

- listed company data
- stock prices
- historical OHLCV data
- market indices
- company financial reports
- dividends
- corporate actions
- AMMC filings
- news
- macroeconomic data
- metadata about sectors, industries, and instruments
- normalized APIs for TradeHub
- stable internal data models
- historical archives
- future analytics and AI-ready datasets

The project should be designed as a serious financial data infrastructure system, not a temporary script.

---

## 4. Main Objective

The main objective is to create a clean and reliable database of Moroccan stock market data that can be updated continuously and served to TradeHub.

The data should be:

- structured
- normalized
- deduplicated
- historical
- queryable
- API-accessible
- reliable enough for trading simulation and analytics
- extensible for future real-money brokerage workflows

---

## 5. Relationship With TradeHub

TradeHub is the product layer.

`tradehub-data` is the data layer.

```txt
+-------------------+        +----------------------+
|     TradeHub      |        |    tradehub-data     |
|-------------------|        |----------------------|
| UI / UX           |        | collectors           |
| trading simulator |        | raw data storage     |
| portfolio         | <----> | normalization        |
| watchlist         |  API   | market database      |
| charts            |        | financial APIs       |
| alerts            |        | scheduled workers    |
| community         |        | monitoring           |
+-------------------+        +----------------------+
```

TradeHub should not scrape or collect market data directly.

TradeHub should request clean data from `tradehub-data`.

---

## 6. Product Boundary

### `tradehub-data` is responsible for:

- collecting raw data from external sources
- storing raw snapshots
- parsing collected data
- normalizing financial entities
- validating data quality
- keeping historical records
- exposing clean APIs
- running scheduled updates
- monitoring ingestion health
- providing data to TradeHub

### `tradehub-data` is NOT responsible for:

- user authentication for TradeHub users
- social feed
- chat
- gamification
- portfolio ownership logic
- executing trades
- user watchlists
- frontend UI
- broker account management

Those responsibilities stay in the main TradeHub project.

---

## 7. First Version Scope

The first version should be intentionally limited.

Do not try to build everything at once.

### Version 0.1 should focus on:

- project structure
- Docker development environment
- PostgreSQL database
- database migrations
- raw data tables
- normalized core market tables
- one working collector
- one normalization pipeline
- one API endpoint group
- basic scheduler
- basic logging
- basic health checks

### Suggested first data domain:

Start with listed companies and daily/latest price data.

This is the foundation needed by TradeHub market pages, trading simulation, portfolio valuation, and watchlists.

---

## 8. Data Domains

The project should eventually support these domains.

### 8.1 Market Instruments

Examples:

- listed companies
- stocks
- ISIN codes
- tickers/symbols
- sectors
- industries
- market segments

### 8.2 Prices

Examples:

- last price
- open price
- high price
- low price
- close price
- volume
- traded value
- percentage change
- daily price history
- intraday snapshots if available

### 8.3 Indices

Examples:

- MASI
- MASI 20
- sector indices
- index values
- index historical data
- index constituents if available

### 8.4 Corporate Data

Examples:

- company name
- legal name
- sector
- industry
- website
- description
- listing date
- share count
- market capitalization
- free float if available

### 8.5 Financial Reports

Examples:

- annual reports
- quarterly reports
- financial statements
- balance sheet
- income statement
- cash flow statement
- notes and PDFs

### 8.6 Corporate Actions

Examples:

- dividends
- stock splits
- capital increases
- rights issues
- suspensions
- announcements

### 8.7 News and Filings

Examples:

- company news
- AMMC filings
- press releases
- official announcements
- market notices

### 8.8 Macroeconomic Data

Examples:

- interest rates
- inflation
- exchange rates
- GDP indicators
- Bank Al-Maghrib data
- World Bank data

---

## 9. Core Architecture Principle

The most important architecture principle is:

```txt
collect raw data first, normalize later
```

Do not write scraped data directly into final business tables.

Use this pipeline:

```txt
external source
    ↓
collector
    ↓
raw data storage
    ↓
parser
    ↓
normalizer
    ↓
validated normalized tables
    ↓
API
    ↓
TradeHub
```

This gives the project the ability to:

- reprocess old data
- debug broken collectors
- compare source changes
- preserve audit history
- improve normalization logic without losing original data
- handle website/API format changes

---

## 10. Raw Data vs Normalized Data

### Raw data

Raw data is the original collected data.

It may be:

- HTML
- JSON
- CSV
- PDF metadata
- downloaded file references
- API responses
- screenshots or snapshots if needed

Raw data should be stored with:

- source name
- source URL
- collection timestamp
- request status
- response hash
- raw payload
- collector version
- error metadata if collection failed

### Normalized data

Normalized data is clean structured data used by TradeHub.

It should be:

- typed
- deduplicated
- validated
- linked to known entities
- query optimized
- safe for API consumption

---

## 11. Recommended Initial Stack

This project should start simple but production-minded.

### Backend/data stack

- Python
- FastAPI
- PostgreSQL
- SQLAlchemy
- Alembic
- Redis
- Pydantic
- httpx
- BeautifulSoup
- Playwright only when necessary

### Workers/scheduling

Initial option:

- APScheduler or Celery

Later scalable options:

- Celery + Redis
- Temporal
- Kafka or Redpanda
- NATS

### Infrastructure

- Docker
- Docker Compose
- Prometheus-ready metrics later
- structured logs
- environment-based config

---

## 12. Suggested Repository Structure

```txt
tradehub-data/
├── AGENTS.md
├── README.md
├── pyproject.toml
├── .env.example
├── docker-compose.yml
│
├── docs/
│   ├── 00_PROJECT_OVERVIEW.md
│   ├── 01_ARCHITECTURE.md
│   ├── 02_DATABASE_SCHEMA.md
│   ├── 03_SOURCES_AND_COLLECTORS.md
│   ├── 04_BVC_PRICE_COLLECTOR.md
│   ├── 05_COMPANY_DATA_COLLECTOR.md
│   ├── 06_NEWS_COLLECTOR.md
│   ├── 07_AMMC_FILINGS_COLLECTOR.md
│   ├── 08_NORMALIZATION_PIPELINE.md
│   ├── 09_API_DESIGN.md
│   ├── 10_SCHEDULER_AND_WORKERS.md
│   ├── 11_MONITORING_AND_LOGGING.md
│   ├── 12_TRADEHUB_INTEGRATION.md
│   └── 13_DEPLOYMENT.md
│
├── src/
│   └── tradehub_data/
│       ├── api/
│       ├── core/
│       ├── collectors/
│       ├── parsers/
│       ├── normalizers/
│       ├── models/
│       ├── schemas/
│       ├── repositories/
│       ├── services/
│       ├── workers/
│       ├── scheduler/
│       ├── monitoring/
│       └── utils/
│
├── migrations/
├── tests/
└── scripts/
```

---

## 13. Collector Design Rules

Every collector should be treated as an independent component.

A collector must:

- have one clear source
- have one clear responsibility
- be retry-safe
- be idempotent
- store raw responses before normalization
- log success and failure
- expose collection metadata
- avoid duplicating normalized records
- not contain business logic for TradeHub
- not directly control frontend behavior

A collector should not silently fail.

Failures must be stored or logged with enough context to debug later.

---

## 14. Scheduling Strategy

The project should support different update frequencies depending on the data type.

Not all data should update every minute.

Suggested frequencies:

| Data Type | Suggested Frequency |
|---|---:|
| latest prices | every 1 minute during market hours |
| indices | every 1 minute during market hours |
| company metadata | daily or weekly |
| financial reports | every few hours or daily |
| AMMC filings | every few hours |
| news | every 5 to 15 minutes |
| macroeconomic data | daily, weekly, or monthly |

The scheduler should eventually understand Moroccan market hours and avoid unnecessary high-frequency scraping when the market is closed.

---

## 15. API Strategy

`tradehub-data` should expose clean APIs that TradeHub can consume.

Initial API groups may include:

```txt
GET /health
GET /v1/stocks
GET /v1/stocks/{symbol}
GET /v1/stocks/{symbol}/prices/latest
GET /v1/stocks/{symbol}/prices/history
GET /v1/indices
GET /v1/indices/{symbol}/history
GET /v1/companies
GET /v1/companies/{id}
GET /v1/news
```

The API should return normalized data only.

Raw data should not be exposed publicly by default.

---

## 16. TradeHub Integration Goal

The final integration goal is:

```txt
TradeHub backend stocks module
        ↓
tradehub-data API
        ↓
normalized market database
        ↓
collectors and workers
```

TradeHub should eventually replace its current market worker with API calls to `tradehub-data`.

TradeHub should continue to own trading simulation, portfolio logic, user features, social features, and notifications.

---

## 17. Data Quality Principles

Financial data must be treated carefully.

The project should prioritize:

- correctness over speed
- auditability over convenience
- consistency over quick hacks
- deduplication over repeated inserts
- explicit errors over silent failures
- historical preservation over overwriting
- source traceability over anonymous data

Every normalized record should ideally be traceable back to a raw source record.

---

## 18. Idempotency Rules

Collectors and normalizers must be safe to run multiple times.

Running the same collector twice should not create duplicate final records.

Use natural uniqueness where possible:

- stock symbol
- ISIN
- source name + source external ID
- symbol + trading date
- company + report period
- news URL hash
- document hash

---

## 19. Logging and Monitoring Requirements

The project should log:

- collector start
- collector success
- collector failure
- number of records fetched
- number of records inserted
- number of records updated
- normalization errors
- source response status
- runtime duration

Later, the project should expose metrics such as:

- last successful collection time
- failed collection count
- records collected per source
- normalization error count
- API latency
- scheduler health

---

## 20. Security and Compliance Notes

The project should avoid unsafe scraping behavior.

Rules:

- respect source rate limits
- prefer official APIs or downloadable files when available
- avoid aggressive crawling
- identify sources clearly in config
- do not store secrets in code
- use environment variables for credentials
- do not expose raw scraped payloads publicly
- do not bypass website protections

---

## 21. Development Workflow With Codex

This project will be built using Codex in terminal.

Codex should not be asked to build the full system at once.

The workflow should be:

```txt
1. Write a small Markdown specification.
2. Ask Codex to read the relevant specs.
3. Ask Codex to implement only one module.
4. Review the result.
5. Run tests.
6. Fix issues.
7. Move to the next module.
```

Each `.md` file should describe one part of the system.

Example:

```txt
04_BVC_PRICE_COLLECTOR.md
```

should only describe the BVC price collector.

It should not describe news collection, AMMC filings, frontend UI, or deployment.

---

## 22. Codex Rules For This Project

When Codex reads this file, it must follow these rules:

1. Do not implement the whole project from this file alone.
2. Do not invent source-specific scraping logic unless a source-specific `.md` file provides details.
3. Do not mix collectors, normalizers, API routes, and database models in one uncontrolled change.
4. Prefer small commits and small modules.
5. Keep collectors isolated.
6. Store raw data before normalization.
7. Add tests for any non-trivial logic.
8. Use typed models and schemas.
9. Keep configuration environment-based.
10. Never hardcode secrets.
11. Do not change unrelated files.
12. Do not remove architecture boundaries without explicit instruction.
13. Ask for clarification when source behavior is unknown.
14. Keep the system easy to run locally with Docker.

---

## 23. First Milestones

### Milestone 0 — Foundation

Deliver:

- repository structure
- `AGENTS.md`
- `README.md`
- `.env.example`
- Docker Compose
- Python project setup
- formatting/linting/test setup

### Milestone 1 — Database Foundation

Deliver:

- PostgreSQL connection
- SQLAlchemy models
- Alembic migrations
- raw source tables
- normalized stock/company/price tables
- seed/demo data if needed

### Milestone 2 — First Collector

Deliver:

- one collector for listed companies or prices
- raw response storage
- parser
- normalizer
- tests

### Milestone 3 — API

Deliver:

- FastAPI app
- health endpoint
- stocks endpoint
- latest prices endpoint
- history endpoint
- OpenAPI docs

### Milestone 4 — Scheduler

Deliver:

- scheduled collector execution
- configurable intervals
- logging
- failure handling
- manual trigger command

### Milestone 5 — TradeHub Integration

Deliver:

- documented API contract
- TradeHub backend adapter plan
- replacement plan for the current market worker
- compatibility with current stock/price needs

---

## 24. Definition of Done

A module is done only when:

- it has a clear responsibility
- it is documented
- it can run locally
- it has tests where needed
- it logs failures
- it does not break architecture boundaries
- it does not duplicate normalized data
- it handles repeated execution safely
- it can be understood by another developer or agent

---

## 25. Final Mental Model

Think of `tradehub-data` as:

```txt
the financial data engine behind TradeHub
```

TradeHub is the trading and community product.

`tradehub-data` is the trusted source of Moroccan market data.

The long-term value of TradeHub depends heavily on the quality, reliability, and structure of this data layer.
