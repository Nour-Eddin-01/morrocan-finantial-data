# AGENTS.md

# TradeHub Data — Agent Instructions

This file defines the rules that AI coding agents must follow when working on the `tradehub-data` repository.

The goal is to make Codex and other coding agents useful without letting them damage architecture, create messy code, or implement too much at once.

---

## 1. Project Identity

`tradehub-data` is the dedicated financial data infrastructure project for TradeHub.

It collects, stores, normalizes, validates, and serves Moroccan stock market data.

This project is not a simple scraper.

It is a data engineering system that will eventually replace the market-data worker currently inside the main TradeHub application.

---

## 2. Main Mission

The mission of `tradehub-data` is to provide clean, reliable, structured financial data to TradeHub.

The project should eventually support:

- listed companies
- stocks and instruments
- latest prices
- historical OHLCV data
- market indices
- company metadata
- dividends
- corporate actions
- AMMC filings
- financial reports
- news
- macroeconomic data
- data APIs for TradeHub

---

## 3. Core Architecture Rule

The most important architecture rule is:

```txt
collect raw data first, normalize later
```

Agents must not write scraped or collected source data directly into final business tables.

Use this pipeline:

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
validated normalized tables
    ↓
API
    ↓
TradeHub
```

This rule is mandatory.

---

## 4. Agent Behavior Rules

When working in this repository, agents must:

1. Read the relevant Markdown specification before coding.
2. Implement only the requested module or task.
3. Avoid changing unrelated files.
4. Keep changes small and reviewable.
5. Preserve project architecture boundaries.
6. Add tests for non-trivial logic.
7. Use environment variables for configuration.
8. Never hardcode secrets, API keys, credentials, or tokens.
9. Prefer simple, maintainable code over clever abstractions.
10. Log meaningful failures instead of hiding errors.
11. Make collectors retry-safe and idempotent.
12. Store raw source responses before normalization.
13. Keep API routes thin; put logic in services.
14. Keep database access in repositories or data-access modules.
15. Avoid introducing new dependencies unless they are clearly justified.
16. Update documentation when architecture or behavior changes.
17. Do not remove tests or weaken validation to make code pass.
18. Do not silently skip errors in financial-data pipelines.

---

## 5. Forbidden Actions

Agents must not:

- build the full project from a single prompt
- mix multiple milestones in one uncontrolled change
- implement scraping logic without a source-specific specification
- bypass raw storage and write directly to normalized tables
- expose raw scraped payloads through public APIs by default
- hardcode credentials
- commit `.env` files
- remove architecture boundaries
- create frontend UI inside this repository unless explicitly requested
- implement TradeHub user authentication here
- implement TradeHub trading simulation logic here
- implement portfolio ownership, watchlist ownership, chat, social feed, or gamification here
- aggressively crawl websites or bypass protections
- change unrelated modules while working on a specific task

---

## 6. Expected Technology Stack

The initial stack is:

- Python
- FastAPI
- PostgreSQL
- SQLAlchemy
- Alembic
- Pydantic
- Redis
- httpx
- BeautifulSoup
- Playwright only when necessary
- Docker
- Docker Compose
- pytest

Agents should not replace this stack without explicit instruction.

---

## 7. Repository Structure Rules

The expected repository structure is:

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

Agents should follow this structure unless a later architecture document changes it.

---

## 8. Module Responsibility Rules

### 8.1 Collectors

Collectors are responsible only for collecting raw data from one external source.

A collector may:

- call an API
- download a file
- fetch HTML
- save raw payloads
- save collection metadata
- report errors

A collector must not:

- contain TradeHub business logic
- update final normalized records directly
- decide frontend behavior
- mix several unrelated sources

---

### 8.2 Parsers

Parsers transform raw payloads into intermediate structured data.

A parser may:

- parse HTML
- parse JSON
- parse CSV
- extract fields from source data
- validate required fields exist

A parser must not:

- write to the database directly
- call external sources
- decide final business entity relationships alone

---

### 8.3 Normalizers

Normalizers convert parsed source data into canonical internal models.

A normalizer may:

- deduplicate data
- map source fields to internal fields
- validate business meaning
- link records to companies, instruments, prices, indices, or reports
- create or update normalized tables safely

A normalizer must be idempotent.

Running the same normalizer multiple times must not create duplicate final records.

---

### 8.4 API Layer

The API layer serves clean normalized data to consumers.

The API may expose:

- stocks
- companies
- prices
- historical data
- indices
- news
- reports
- health checks

The API must not expose raw scraped payloads publicly by default.

Routes should stay thin.

Business logic belongs in services.

Database access belongs in repositories or data-access modules.

---

### 8.5 Scheduler and Workers

Schedulers and workers are responsible for running collectors and pipelines at the correct time.

They must:

- use configurable intervals
- log execution results
- handle failures
- avoid duplicate concurrent runs where harmful
- respect market hours when implemented
- support manual execution where useful

---

## 9. Data Quality Rules

Financial data must be handled carefully.

Agents must prioritize:

- correctness over speed
- auditability over convenience
- source traceability over anonymous data
- deduplication over repeated inserts
- historical preservation over overwriting
- explicit errors over silent failures
- validation over blind trust in source data

Every normalized record should be traceable back to a raw source record when possible.

---

## 10. Idempotency Rules

Collectors and normalizers must be safe to run multiple times.

Use natural uniqueness where possible:

- stock symbol
- ISIN
- source name + source external ID
- symbol + trading date
- company + report period
- source URL hash
- document hash
- response hash

Do not rely only on random IDs for financial records that need deduplication.

---

## 11. Database Rules

Agents should design the database around financial entities, not web pages.

Important entity groups:

- source
- raw collection run
- raw payload
- company
- instrument
- stock
- price snapshot
- historical price bar
- index
- index value
- news item
- filing
- report
- corporate action

Use migrations for schema changes.

Do not manually edit database state as a substitute for proper migrations.

---

## 12. Configuration Rules

All runtime configuration must come from environment variables or config files.

Examples:

- database URL
- Redis URL
- source URLs
- API keys
- scheduler intervals
- log level
- environment name
- market timezone

Secrets must never be committed.

`.env.example` should document required environment variables without real secrets.

---

## 13. Logging Rules

Collectors and pipelines must log:

- start time
- end time
- source name
- URL or source identifier
- status code when applicable
- number of records fetched
- number of records inserted
- number of records updated
- number of records skipped
- errors and exception context
- runtime duration

Logs should be structured enough to support monitoring later.

---

## 14. Testing Rules

Agents should add tests for:

- parsers
- normalizers
- repository behavior
- API endpoints
- scheduler-safe logic
- idempotency rules
- data validation

Tests should avoid depending on live external websites unless explicitly requested.

Prefer fixtures for source payloads.

Do not make tests pass by weakening production validation.

---

## 15. Error Handling Rules

Agents must not hide errors.

For collectors:

- failed requests should be recorded
- invalid payloads should be logged
- parser failures should include source context
- normalization failures should not corrupt existing data

For APIs:

- return clear error responses
- avoid leaking internal stack traces
- use proper HTTP status codes

---

## 16. Source Collection Rules

When implementing a source collector:

1. Prefer official APIs or downloadable files when available.
2. Respect rate limits.
3. Avoid aggressive crawling.
4. Avoid bypassing protections.
5. Store source metadata.
6. Store raw payloads.
7. Keep source-specific logic isolated.
8. Add fixtures and parser tests.

Source-specific behavior must be described in a dedicated Markdown file before implementation.

---

## 17. TradeHub Integration Rules

`tradehub-data` should serve TradeHub through clean APIs.

TradeHub owns:

- users
- authentication
- portfolios
- simulated trading
- watchlists
- social features
- chat
- notifications
- gamification

`tradehub-data` owns:

- market data collection
- raw data storage
- normalization
- financial data APIs
- data quality monitoring

Do not move TradeHub product features into `tradehub-data`.

---

## 18. Initial API Contract Direction

Initial endpoints may include:

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

Agents should not implement all endpoints at once unless explicitly instructed.

---

## 19. Coding Style

Use clear, boring, maintainable code.

Prefer:

- explicit names
- small functions
- typed parameters
- typed return values
- Pydantic schemas for API/data validation
- SQLAlchemy models for persistence
- service classes for business logic
- repository classes or modules for database operations

Avoid:

- clever one-liners
- hidden global state
- large functions
- circular imports
- magic constants
- mixed responsibilities

---

## 20. Documentation Rules

Whenever a meaningful module is added, update or create documentation explaining:

- purpose
- responsibilities
- how to run it
- environment variables
- expected inputs
- expected outputs
- known limitations
- testing approach

Markdown specs in `docs/` are part of the development process.

Agents should treat them as source-of-truth instructions.

---

## 21. Recommended Codex Workflow

Use this project in small steps.

Example workflow:

```txt
1. Read AGENTS.md.
2. Read docs/00_PROJECT_OVERVIEW.md.
3. Read the specific task document.
4. Explain the planned change before coding.
5. Implement only the requested task.
6. Run tests or provide commands to run tests.
7. Summarize changed files and reasoning.
```

Example Codex instruction:

```txt
Read AGENTS.md, docs/00_PROJECT_OVERVIEW.md, and docs/02_DATABASE_SCHEMA.md.
Implement only the database foundation described in docs/02_DATABASE_SCHEMA.md.
Do not implement collectors, API endpoints, or scheduler logic yet.
Keep the change small and add tests where relevant.
```

---

## 22. Definition of Done

A task is done only when:

- it follows the relevant Markdown spec
- it respects architecture boundaries
- it has clear ownership
- it can run locally
- it includes tests where needed
- it logs important failures
- it is idempotent where applicable
- it does not duplicate normalized records
- it does not introduce secrets
- it does not change unrelated modules
- it is understandable by another developer or agent

---

## 23. Final Agent Reminder

Do not optimize for generating a lot of code.

Optimize for building a reliable financial data platform step by step.

Small correct modules are better than large fragile implementations.

