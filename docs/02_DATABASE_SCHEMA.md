# 02_DATABASE_SCHEMA.md

# TradeHub Data — Database Schema Specification

## 1. Purpose

This document defines the database design for `tradehub-data`.

`tradehub-data` is the dedicated financial data infrastructure layer for TradeHub. Its database must support reliable collection, raw archival, normalization, validation, historical analysis, and API access for Moroccan stock market data.

This schema must be designed for correctness first.

The database is not only a storage layer for scraped pages. It is the durable financial data model that TradeHub will eventually consume instead of using its internal market worker.

---

## 2. Context From TradeHub

The current TradeHub project already has a `Stock` model, `PriceHistory`, market APIs, trading simulation, portfolio valuation, and a market worker.

The goal of `tradehub-data` is to become the external source of truth for market data.

TradeHub needs clean market data for:

- market pages
- stock detail pages
- trading simulation
- order execution prices
- portfolio valuation
- watchlists
- charts
- future alerts and analytics

So this schema must expose clean normalized data that can later map into TradeHub's existing stock and price-history expectations.

---

## 3. Core Database Principles

### 3.1 Raw First, Normalize Later

Collectors must never write directly to final business tables.

Correct flow:

```txt
external source
    ↓
collector
    ↓
raw_payloads / raw files
    ↓
parser
    ↓
normalizer
    ↓
normalized financial tables
    ↓
API / TradeHub integration
```

This gives us:

- debugging power
- reprocessing ability
- audit history
- source-change detection
- protection from parser bugs

---

### 3.2 Financial Values Must Not Use Float

Money, prices, percentages, market caps, and traded values must use decimal/numeric database types.

Use PostgreSQL:

```sql
NUMERIC(20, 6)
```

or a more specific precision where needed.

Never use floating-point types for financial values.

---

### 3.3 Historical Data Must Be Append-Friendly

Historical prices and snapshots should not be silently overwritten.

For time-series data, use unique constraints such as:

```txt
instrument_id + timeframe + timestamp
```

or:

```txt
instrument_id + trading_date
```

Use upserts only when the same source republishes the same official time period with corrected values.

---

### 3.4 Idempotency Is Mandatory

Collectors and normalizers will run repeatedly.

The same source response may be collected multiple times.

The schema must prevent duplicate logical records using:

- payload hashes
- source identifiers
- unique constraints
- normalized natural keys
- ingestion run IDs

---

### 3.5 Source Traceability Is Mandatory

Every normalized record should be traceable to its source when practical.

Important normalized tables should include:

```txt
source_id
raw_payload_id
last_seen_at
created_at
updated_at
```

This allows us to answer:

- where did this value come from?
- when was it collected?
- which raw payload produced it?
- when did we last confirm it?

---

### 3.6 Database Is Internal Source of Truth

The database should be the internal source of truth for `tradehub-data`.

The API should read from normalized tables.

Collectors should only create raw records and ingestion metadata.

---

## 4. Technology Requirements

Initial database stack:

- PostgreSQL
- SQLAlchemy ORM
- Alembic migrations
- Pydantic schemas for API serialization
- pytest for repository and migration tests

Do not use Prisma in `tradehub-data` unless explicitly requested later.

TradeHub currently uses Prisma, but `tradehub-data` should use Python tooling because the data pipeline is planned around Python, FastAPI, SQLAlchemy, and Alembic.

---

## 5. Naming Conventions

Use lowercase snake_case for tables and columns.

Examples:

```txt
companies
instruments
price_bars
raw_payloads
ingestion_runs
```

Primary keys:

```txt
id UUID PRIMARY KEY
```

Foreign keys:

```txt
company_id UUID REFERENCES companies(id)
instrument_id UUID REFERENCES instruments(id)
source_id UUID REFERENCES data_sources(id)
```

Timestamps:

```txt
created_at TIMESTAMPTZ NOT NULL DEFAULT now()
updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
collected_at TIMESTAMPTZ
last_seen_at TIMESTAMPTZ
```

Use timezone-aware timestamps everywhere.

---

## 6. Suggested Schema Groups

The database should be grouped mentally into these layers:

```txt
1. Source and ingestion metadata
2. Raw data storage
3. Reference data
4. Market instruments
5. Prices and time series
6. Indices
7. Corporate information
8. Reports and filings
9. News
10. Macro data
11. Data quality and monitoring
12. TradeHub integration views
```

For version 0.1, implement only the minimum reliable foundation.

---

# PART A — VERSION 0.1 REQUIRED TABLES

The following tables should be implemented first.

---

## 7. `data_sources`

Stores external data sources.

Examples:

- Bourse de Casablanca prices source
- Bourse de Casablanca listed companies source
- AMMC filings source
- news source
- macroeconomic source

### Columns

| Column | Type | Required | Notes |
|---|---:|---:|---|
| `id` | UUID | yes | Primary key |
| `code` | VARCHAR(80) | yes | Stable internal code, unique |
| `name` | VARCHAR(255) | yes | Human readable source name |
| `source_type` | VARCHAR(50) | yes | `exchange`, `regulator`, `news`, `macro`, `manual`, `other` |
| `base_url` | TEXT | no | Base URL if applicable |
| `country_code` | CHAR(2) | no | Usually `MA` |
| `is_active` | BOOLEAN | yes | Default true |
| `priority` | INTEGER | yes | Used when multiple sources provide same data |
| `metadata` | JSONB | no | Source-specific config metadata |
| `created_at` | TIMESTAMPTZ | yes | Default now |
| `updated_at` | TIMESTAMPTZ | yes | Default now |

### Constraints

```txt
UNIQUE(code)
```

### SQLAlchemy notes

Model name:

```txt
DataSource
```

---

## 8. `ingestion_runs`

Stores each collector execution.

A collector run should create one `ingestion_runs` record before collecting data.

### Columns

| Column | Type | Required | Notes |
|---|---:|---:|---|
| `id` | UUID | yes | Primary key |
| `source_id` | UUID | yes | FK to `data_sources.id` |
| `collector_name` | VARCHAR(120) | yes | Example: `bvc_price_collector` |
| `run_type` | VARCHAR(50) | yes | `scheduled`, `manual`, `backfill`, `retry` |
| `status` | VARCHAR(30) | yes | `running`, `success`, `partial_success`, `failed` |
| `started_at` | TIMESTAMPTZ | yes | When run started |
| `finished_at` | TIMESTAMPTZ | no | When run ended |
| `records_collected` | INTEGER | yes | Default 0 |
| `records_inserted` | INTEGER | yes | Default 0 |
| `records_updated` | INTEGER | yes | Default 0 |
| `records_failed` | INTEGER | yes | Default 0 |
| `error_message` | TEXT | no | Human readable error |
| `metadata` | JSONB | no | Extra info, timings, params |
| `created_at` | TIMESTAMPTZ | yes | Default now |

### Indexes

```txt
INDEX(source_id)
INDEX(collector_name)
INDEX(status)
INDEX(started_at DESC)
```

### SQLAlchemy notes

Model name:

```txt
IngestionRun
```

---

## 9. `raw_payloads`

Stores raw data collected from external sources.

This is one of the most important tables in the project.

A raw payload can be:

- JSON API response
- HTML page
- CSV text
- XML response
- small document metadata
- extracted PDF text
- any raw source payload that is practical to store in PostgreSQL

Large files such as PDFs may later be stored in object storage or filesystem, with only metadata stored here.

### Columns

| Column | Type | Required | Notes |
|---|---:|---:|---|
| `id` | UUID | yes | Primary key |
| `source_id` | UUID | yes | FK to `data_sources.id` |
| `ingestion_run_id` | UUID | no | FK to `ingestion_runs.id` |
| `source_url` | TEXT | no | Exact URL used |
| `source_endpoint` | TEXT | no | Logical endpoint name |
| `payload_type` | VARCHAR(50) | yes | `json`, `html`, `csv`, `xml`, `text`, `pdf_metadata`, `other` |
| `payload` | JSONB | no | Use for JSON or structured extracted payload |
| `payload_text` | TEXT | no | Use for HTML, CSV, text when needed |
| `payload_hash` | CHAR(64) | yes | SHA-256 hash of normalized raw payload |
| `http_status` | INTEGER | no | HTTP status if collected through HTTP |
| `content_type` | VARCHAR(120) | no | HTTP content type |
| `collected_at` | TIMESTAMPTZ | yes | Collection timestamp |
| `source_published_at` | TIMESTAMPTZ | no | When source says data was published |
| `status` | VARCHAR(30) | yes | `collected`, `parsed`, `normalized`, `failed`, `ignored` |
| `error_message` | TEXT | no | If collection/parsing failed |
| `metadata` | JSONB | no | Extra source-specific info |
| `created_at` | TIMESTAMPTZ | yes | Default now |

### Constraints

```txt
UNIQUE(source_id, payload_hash)
```

This prevents storing duplicate raw payloads from the same source.

### Indexes

```txt
INDEX(source_id)
INDEX(ingestion_run_id)
INDEX(payload_hash)
INDEX(collected_at DESC)
INDEX(status)
```

### SQLAlchemy notes

Model name:

```txt
RawPayload
```

---

## 10. `exchanges`

Stores financial exchanges.

For v0.1, this will likely contain one row for the Casablanca Stock Exchange.

### Columns

| Column | Type | Required | Notes |
|---|---:|---:|---|
| `id` | UUID | yes | Primary key |
| `code` | VARCHAR(30) | yes | Internal code, example `BVC` |
| `name` | VARCHAR(255) | yes | Exchange name |
| `country_code` | CHAR(2) | yes | `MA` |
| `currency_code` | CHAR(3) | yes | `MAD` |
| `timezone` | VARCHAR(80) | yes | Example: `Africa/Casablanca` |
| `website_url` | TEXT | no | Exchange website |
| `metadata` | JSONB | no | Extra details |
| `created_at` | TIMESTAMPTZ | yes | Default now |
| `updated_at` | TIMESTAMPTZ | yes | Default now |

### Constraints

```txt
UNIQUE(code)
```

### SQLAlchemy notes

Model name:

```txt
Exchange
```

---

## 11. `sectors`

Stores sector classification.

### Columns

| Column | Type | Required | Notes |
|---|---:|---:|---|
| `id` | UUID | yes | Primary key |
| `code` | VARCHAR(80) | no | Internal or source sector code |
| `name` | VARCHAR(255) | yes | Sector name |
| `description` | TEXT | no | Optional description |
| `source_id` | UUID | no | FK to `data_sources.id` |
| `metadata` | JSONB | no | Extra classification info |
| `created_at` | TIMESTAMPTZ | yes | Default now |
| `updated_at` | TIMESTAMPTZ | yes | Default now |

### Constraints

```txt
UNIQUE(name)
```

### SQLAlchemy notes

Model name:

```txt
Sector
```

---

## 12. `companies`

Stores listed companies and issuer metadata.

A company can have one or more instruments/securities.

### Columns

| Column | Type | Required | Notes |
|---|---:|---:|---|
| `id` | UUID | yes | Primary key |
| `name` | VARCHAR(255) | yes | Normalized display name |
| `legal_name` | VARCHAR(255) | no | Official legal name if known |
| `slug` | VARCHAR(255) | yes | Stable URL/API slug |
| `sector_id` | UUID | no | FK to `sectors.id` |
| `country_code` | CHAR(2) | yes | Usually `MA` |
| `website_url` | TEXT | no | Company website |
| `description` | TEXT | no | Company summary |
| `logo_url` | TEXT | no | Optional logo URL |
| `source_id` | UUID | no | FK to source used to create/update company |
| `raw_payload_id` | UUID | no | FK to raw payload |
| `is_active` | BOOLEAN | yes | Default true |
| `metadata` | JSONB | no | Extra source-specific fields |
| `last_seen_at` | TIMESTAMPTZ | no | Last source confirmation |
| `created_at` | TIMESTAMPTZ | yes | Default now |
| `updated_at` | TIMESTAMPTZ | yes | Default now |

### Constraints

```txt
UNIQUE(slug)
```

Optional later:

```txt
UNIQUE(legal_name)
```

Only add `UNIQUE(legal_name)` if the normalizer guarantees clean legal names.

### Indexes

```txt
INDEX(sector_id)
INDEX(is_active)
INDEX(name)
```

### SQLAlchemy notes

Model name:

```txt
Company
```

---

## 13. `instruments`

Stores tradable securities.

For v0.1, the main instrument type is equity stock.

This table is the closest equivalent to TradeHub's current `Stock` model.

### Columns

| Column | Type | Required | Notes |
|---|---:|---:|---|
| `id` | UUID | yes | Primary key |
| `company_id` | UUID | no | FK to `companies.id` |
| `exchange_id` | UUID | yes | FK to `exchanges.id` |
| `symbol` | VARCHAR(30) | yes | Trading ticker/symbol |
| `isin` | VARCHAR(20) | no | ISIN code |
| `name` | VARCHAR(255) | yes | Display name |
| `instrument_type` | VARCHAR(50) | yes | `equity`, `bond`, `fund`, `index`, `other` |
| `currency_code` | CHAR(3) | yes | Usually `MAD` |
| `market_segment` | VARCHAR(80) | no | Main market, alternative, etc. |
| `listing_date` | DATE | no | If known |
| `delisting_date` | DATE | no | If delisted |
| `shares_outstanding` | BIGINT | no | Number of shares if available |
| `free_float_percent` | NUMERIC(10, 6) | no | If available |
| `source_id` | UUID | no | FK to source used to create/update instrument |
| `raw_payload_id` | UUID | no | FK to raw payload |
| `is_active` | BOOLEAN | yes | Default true |
| `metadata` | JSONB | no | Extra source-specific data |
| `last_seen_at` | TIMESTAMPTZ | no | Last source confirmation |
| `created_at` | TIMESTAMPTZ | yes | Default now |
| `updated_at` | TIMESTAMPTZ | yes | Default now |

### Constraints

```txt
UNIQUE(exchange_id, symbol)
UNIQUE(exchange_id, isin)
```

Important:

- `isin` can be nullable.
- PostgreSQL unique constraints allow multiple null values.
- This is acceptable for v0.1.

### Indexes

```txt
INDEX(company_id)
INDEX(exchange_id)
INDEX(symbol)
INDEX(isin)
INDEX(is_active)
```

### SQLAlchemy notes

Model name:

```txt
Instrument
```

---

## 14. `latest_prices`

Stores latest known market price for each instrument.

This table is optimized for fast reads by TradeHub.

It should contain only the latest current state, not historical bars.

Historical data goes to `price_bars`.

### Columns

| Column | Type | Required | Notes |
|---|---:|---:|---|
| `id` | UUID | yes | Primary key |
| `instrument_id` | UUID | yes | FK to `instruments.id` |
| `price` | NUMERIC(20, 6) | yes | Latest price |
| `open_price` | NUMERIC(20, 6) | no | Session open |
| `high_price` | NUMERIC(20, 6) | no | Session high |
| `low_price` | NUMERIC(20, 6) | no | Session low |
| `previous_close` | NUMERIC(20, 6) | no | Previous close |
| `change_value` | NUMERIC(20, 6) | no | Absolute change |
| `change_percent` | NUMERIC(12, 6) | no | Percentage change |
| `volume` | BIGINT | no | Shares traded |
| `traded_value` | NUMERIC(24, 6) | no | Traded value |
| `market_cap` | NUMERIC(24, 6) | no | If available or computed |
| `price_timestamp` | TIMESTAMPTZ | yes | When source says price is valid |
| `trading_date` | DATE | yes | Market date |
| `source_id` | UUID | no | FK to `data_sources.id` |
| `raw_payload_id` | UUID | no | FK to `raw_payloads.id` |
| `data_quality_status` | VARCHAR(30) | yes | `valid`, `suspect`, `stale`, `missing` |
| `metadata` | JSONB | no | Extra price fields |
| `created_at` | TIMESTAMPTZ | yes | Default now |
| `updated_at` | TIMESTAMPTZ | yes | Default now |

### Constraints

```txt
UNIQUE(instrument_id)
```

### Indexes

```txt
INDEX(instrument_id)
INDEX(price_timestamp DESC)
INDEX(trading_date DESC)
INDEX(data_quality_status)
```

### SQLAlchemy notes

Model name:

```txt
LatestPrice
```

---

## 15. `price_bars`

Stores historical OHLCV bars for instruments.

This table supports daily history first.

Later, it can support intraday bars if reliable source data exists.

### Columns

| Column | Type | Required | Notes |
|---|---:|---:|---|
| `id` | UUID | yes | Primary key |
| `instrument_id` | UUID | yes | FK to `instruments.id` |
| `timeframe` | VARCHAR(20) | yes | `1d`, `1h`, `15m`, `5m`, `1m` |
| `bar_timestamp` | TIMESTAMPTZ | yes | Bar timestamp |
| `trading_date` | DATE | yes | Trading date |
| `open_price` | NUMERIC(20, 6) | no | Open |
| `high_price` | NUMERIC(20, 6) | no | High |
| `low_price` | NUMERIC(20, 6) | no | Low |
| `close_price` | NUMERIC(20, 6) | yes | Close |
| `volume` | BIGINT | no | Volume |
| `traded_value` | NUMERIC(24, 6) | no | Traded value |
| `number_of_trades` | INTEGER | no | If available |
| `source_id` | UUID | no | FK to `data_sources.id` |
| `raw_payload_id` | UUID | no | FK to `raw_payloads.id` |
| `is_adjusted` | BOOLEAN | yes | Default false |
| `data_quality_status` | VARCHAR(30) | yes | `valid`, `suspect`, `stale`, `missing` |
| `metadata` | JSONB | no | Extra fields |
| `created_at` | TIMESTAMPTZ | yes | Default now |
| `updated_at` | TIMESTAMPTZ | yes | Default now |

### Constraints

```txt
UNIQUE(instrument_id, timeframe, bar_timestamp)
```

### Indexes

```txt
INDEX(instrument_id, timeframe, bar_timestamp DESC)
INDEX(trading_date DESC)
INDEX(data_quality_status)
```

### SQLAlchemy notes

Model name:

```txt
PriceBar
```

---

## 16. `market_indices`

Stores index definitions.

Examples:

- MASI
- MASI 20
- sector indices

### Columns

| Column | Type | Required | Notes |
|---|---:|---:|---|
| `id` | UUID | yes | Primary key |
| `exchange_id` | UUID | yes | FK to `exchanges.id` |
| `symbol` | VARCHAR(50) | yes | Index symbol/code |
| `name` | VARCHAR(255) | yes | Display name |
| `currency_code` | CHAR(3) | yes | Usually `MAD` |
| `description` | TEXT | no | Optional description |
| `source_id` | UUID | no | FK to source |
| `raw_payload_id` | UUID | no | FK to raw payload |
| `is_active` | BOOLEAN | yes | Default true |
| `metadata` | JSONB | no | Extra fields |
| `last_seen_at` | TIMESTAMPTZ | no | Last source confirmation |
| `created_at` | TIMESTAMPTZ | yes | Default now |
| `updated_at` | TIMESTAMPTZ | yes | Default now |

### Constraints

```txt
UNIQUE(exchange_id, symbol)
```

### SQLAlchemy notes

Model name:

```txt
MarketIndex
```

---

## 17. `latest_index_values`

Stores latest values for indices.

### Columns

| Column | Type | Required | Notes |
|---|---:|---:|---|
| `id` | UUID | yes | Primary key |
| `index_id` | UUID | yes | FK to `market_indices.id` |
| `value` | NUMERIC(20, 6) | yes | Latest index value |
| `open_value` | NUMERIC(20, 6) | no | Open |
| `high_value` | NUMERIC(20, 6) | no | High |
| `low_value` | NUMERIC(20, 6) | no | Low |
| `previous_close` | NUMERIC(20, 6) | no | Previous close |
| `change_value` | NUMERIC(20, 6) | no | Absolute change |
| `change_percent` | NUMERIC(12, 6) | no | Percentage change |
| `value_timestamp` | TIMESTAMPTZ | yes | Source timestamp |
| `trading_date` | DATE | yes | Market date |
| `source_id` | UUID | no | FK to source |
| `raw_payload_id` | UUID | no | FK to raw payload |
| `data_quality_status` | VARCHAR(30) | yes | `valid`, `suspect`, `stale`, `missing` |
| `metadata` | JSONB | no | Extra fields |
| `created_at` | TIMESTAMPTZ | yes | Default now |
| `updated_at` | TIMESTAMPTZ | yes | Default now |

### Constraints

```txt
UNIQUE(index_id)
```

### SQLAlchemy notes

Model name:

```txt
LatestIndexValue
```

---

## 18. `index_bars`

Stores historical OHLC values for indices.

### Columns

| Column | Type | Required | Notes |
|---|---:|---:|---|
| `id` | UUID | yes | Primary key |
| `index_id` | UUID | yes | FK to `market_indices.id` |
| `timeframe` | VARCHAR(20) | yes | `1d`, `1h`, `15m`, etc. |
| `bar_timestamp` | TIMESTAMPTZ | yes | Bar timestamp |
| `trading_date` | DATE | yes | Trading date |
| `open_value` | NUMERIC(20, 6) | no | Open |
| `high_value` | NUMERIC(20, 6) | no | High |
| `low_value` | NUMERIC(20, 6) | no | Low |
| `close_value` | NUMERIC(20, 6) | yes | Close |
| `source_id` | UUID | no | FK to source |
| `raw_payload_id` | UUID | no | FK to raw payload |
| `data_quality_status` | VARCHAR(30) | yes | `valid`, `suspect`, `stale`, `missing` |
| `metadata` | JSONB | no | Extra fields |
| `created_at` | TIMESTAMPTZ | yes | Default now |
| `updated_at` | TIMESTAMPTZ | yes | Default now |

### Constraints

```txt
UNIQUE(index_id, timeframe, bar_timestamp)
```

### Indexes

```txt
INDEX(index_id, timeframe, bar_timestamp DESC)
INDEX(trading_date DESC)
```

### SQLAlchemy notes

Model name:

```txt
IndexBar
```

---

## 19. `normalization_errors`

Stores parsing and normalization failures.

Errors should not disappear inside logs only.

Financial data failures need durable visibility.

### Columns

| Column | Type | Required | Notes |
|---|---:|---:|---|
| `id` | UUID | yes | Primary key |
| `raw_payload_id` | UUID | no | FK to `raw_payloads.id` |
| `ingestion_run_id` | UUID | no | FK to `ingestion_runs.id` |
| `source_id` | UUID | no | FK to `data_sources.id` |
| `entity_type` | VARCHAR(80) | no | Example: `instrument`, `price_bar`, `company` |
| `error_type` | VARCHAR(80) | yes | Example: `missing_symbol`, `invalid_number` |
| `error_message` | TEXT | yes | Human readable message |
| `raw_fragment` | JSONB | no | Problematic input row/object |
| `status` | VARCHAR(30) | yes | `open`, `ignored`, `fixed` |
| `created_at` | TIMESTAMPTZ | yes | Default now |
| `resolved_at` | TIMESTAMPTZ | no | When fixed/ignored |

### Indexes

```txt
INDEX(raw_payload_id)
INDEX(ingestion_run_id)
INDEX(source_id)
INDEX(error_type)
INDEX(status)
INDEX(created_at DESC)
```

### SQLAlchemy notes

Model name:

```txt
NormalizationError
```

---

## 20. `sync_states`

Stores current state of collectors, normalizers, and scheduled jobs.

This table powers health checks and debug endpoints.

### Columns

| Column | Type | Required | Notes |
|---|---:|---:|---|
| `id` | UUID | yes | Primary key |
| `component_name` | VARCHAR(120) | yes | Example: `bvc_price_collector` |
| `component_type` | VARCHAR(50) | yes | `collector`, `normalizer`, `scheduler`, `api` |
| `status` | VARCHAR(30) | yes | `healthy`, `degraded`, `failed`, `unknown` |
| `last_success_at` | TIMESTAMPTZ | no | Last successful execution |
| `last_failure_at` | TIMESTAMPTZ | no | Last failed execution |
| `last_run_id` | UUID | no | FK to `ingestion_runs.id` if relevant |
| `message` | TEXT | no | Human readable status |
| `metadata` | JSONB | no | Extra diagnostic info |
| `created_at` | TIMESTAMPTZ | yes | Default now |
| `updated_at` | TIMESTAMPTZ | yes | Default now |

### Constraints

```txt
UNIQUE(component_name)
```

### SQLAlchemy notes

Model name:

```txt
SyncState
```

---

# PART B — VERSION 0.2+ TABLES

The following tables are important, but should not be implemented in the first database milestone unless explicitly requested.

---

## 21. `corporate_actions`

Future table for stock splits, capital increases, mergers, name changes, delistings, and other corporate events.

Suggested columns:

```txt
id
instrument_id
company_id
action_type
action_date
effective_date
title
description
old_value
new_value
source_id
raw_payload_id
metadata
created_at
updated_at
```

Suggested unique key:

```txt
UNIQUE(instrument_id, action_type, effective_date, title)
```

---

## 22. `dividends`

Future table for dividend events.

Suggested columns:

```txt
id
instrument_id
company_id
fiscal_year
announcement_date
ex_dividend_date
payment_date
amount_per_share
currency_code
dividend_type
source_id
raw_payload_id
metadata
created_at
updated_at
```

Suggested unique key:

```txt
UNIQUE(instrument_id, fiscal_year, dividend_type)
```

---

## 23. `financial_reports`

Future table for annual, semi-annual, quarterly, and other company financial reports.

Suggested columns:

```txt
id
company_id
instrument_id
report_type
period_start
period_end
fiscal_year
published_at
title
language
document_url
file_path
file_hash
source_id
raw_payload_id
metadata
created_at
updated_at
```

Suggested unique key:

```txt
UNIQUE(company_id, report_type, period_end, file_hash)
```

---

## 24. `regulatory_filings`

Future table for AMMC and other regulatory filings.

Suggested columns:

```txt
id
company_id
instrument_id
filing_type
published_at
title
summary
document_url
file_path
file_hash
source_id
raw_payload_id
metadata
created_at
updated_at
```

Suggested unique key:

```txt
UNIQUE(source_id, document_url)
```

If `document_url` is missing, use file hash or source-specific filing ID.

---

## 25. `news_articles`

Future table for market and company news.

Suggested columns:

```txt
id
source_id
company_id
instrument_id
title
slug
summary
body
url
published_at
language
sentiment_score
sentiment_label
raw_payload_id
metadata
created_at
updated_at
```

Suggested unique keys:

```txt
UNIQUE(source_id, url)
UNIQUE(source_id, title, published_at)
```

---

## 26. `macro_indicators`

Future table for macroeconomic indicator definitions.

Suggested columns:

```txt
id
code
name
country_code
unit
frequency
description
source_id
metadata
created_at
updated_at
```

Suggested unique key:

```txt
UNIQUE(code, country_code)
```

---

## 27. `macro_observations`

Future table for macroeconomic time series.

Suggested columns:

```txt
id
indicator_id
period_start
period_end
value
source_id
raw_payload_id
metadata
created_at
updated_at
```

Suggested unique key:

```txt
UNIQUE(indicator_id, period_start, period_end)
```

---

# PART C — IMPLEMENTATION DETAILS

## 28. SQLAlchemy Model Organization

Codex should create models in this structure:

```txt
src/tradehub_data/models/
├── __init__.py
├── base.py
├── mixins.py
├── source.py
├── raw.py
├── reference.py
├── instrument.py
├── price.py
├── index.py
├── quality.py
└── sync.py
```

Suggested grouping:

```txt
source.py      -> DataSource, IngestionRun
raw.py         -> RawPayload
reference.py   -> Exchange, Sector
instrument.py  -> Company, Instrument
price.py       -> LatestPrice, PriceBar
index.py       -> MarketIndex, LatestIndexValue, IndexBar
quality.py     -> NormalizationError
sync.py        -> SyncState
```

---

## 29. Base Model Requirements

Create a declarative base in:

```txt
src/tradehub_data/models/base.py
```

All models should use:

- UUID primary keys
- timezone-aware timestamps
- SQLAlchemy 2.0 style if possible
- explicit relationships
- explicit indexes and constraints

Example concept:

```python
id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
```

Use this as guidance, not a copy-paste requirement.

---

## 30. Mixins

Create reusable mixins in:

```txt
src/tradehub_data/models/mixins.py
```

Suggested mixins:

```txt
UUIDPrimaryKeyMixin
TimestampMixin
SourceTraceMixin
```

`SourceTraceMixin` can include:

```txt
source_id
raw_payload_id
last_seen_at
```

Use it only where appropriate.

---

## 31. Enums

Use Python enums or SQLAlchemy enum-compatible strings for stable statuses.

Required enum-like values:

### Source Type

```txt
exchange
regulator
news
macro
manual
other
```

### Ingestion Run Status

```txt
running
success
partial_success
failed
```

### Run Type

```txt
scheduled
manual
backfill
retry
```

### Raw Payload Type

```txt
json
html
csv
xml
text
pdf_metadata
other
```

### Raw Payload Status

```txt
collected
parsed
normalized
failed
ignored
```

### Instrument Type

```txt
equity
bond
fund
index
other
```

### Data Quality Status

```txt
valid
suspect
stale
missing
```

### Sync Status

```txt
healthy
degraded
failed
unknown
```

### Normalization Error Status

```txt
open
ignored
fixed
```

Important: keep enum values lowercase strings.

---

## 32. Alembic Migration Requirements

Create an initial migration that creates all v0.1 tables.

Migration should include:

- UUID support if needed
- JSONB columns
- foreign keys
- unique constraints
- indexes
- timestamp columns
- numeric precision columns

Do not create future v0.2 tables in the initial migration unless explicitly requested.

---

## 33. PostgreSQL Extensions

Use PostgreSQL UUID generation carefully.

The application can generate UUIDs in Python.

So the migration does not strictly need database-side UUID generation.

If using database-generated UUIDs, use a standard PostgreSQL extension such as:

```sql
CREATE EXTENSION IF NOT EXISTS "pgcrypto";
```

But prefer Python-generated UUIDs for simplicity unless the project chooses otherwise.

---

## 34. Indexing Strategy

Prioritize indexes for expected access patterns.

Common API queries:

```txt
GET latest stocks
GET stock by symbol
GET stock by ISIN
GET price history by instrument and timeframe
GET indices
GET index history
GET sync health
GET ingestion errors
```

Required important indexes:

```txt
instruments(exchange_id, symbol)
instruments(exchange_id, isin)
latest_prices(instrument_id)
price_bars(instrument_id, timeframe, bar_timestamp DESC)
market_indices(exchange_id, symbol)
index_bars(index_id, timeframe, bar_timestamp DESC)
raw_payloads(source_id, payload_hash)
ingestion_runs(source_id, started_at DESC)
normalization_errors(status, created_at DESC)
sync_states(component_name)
```

---

## 35. TradeHub Integration Mapping

The normalized `instruments` and `latest_prices` tables should provide enough data to generate TradeHub-compatible stock responses.

Possible TradeHub stock response mapping:

```txt
TradeHub Stock.id              <- instruments.id
TradeHub Stock.symbol          <- instruments.symbol
TradeHub Stock.name            <- instruments.name
TradeHub Stock.isin            <- instruments.isin
TradeHub Stock.sector          <- sectors.name
TradeHub Stock.price           <- latest_prices.price
TradeHub Stock.change          <- latest_prices.change_value
TradeHub Stock.changePercent   <- latest_prices.change_percent
TradeHub Stock.volume          <- latest_prices.volume
TradeHub Stock.marketCap       <- latest_prices.market_cap
TradeHub PriceHistory          <- price_bars where timeframe = '1d'
```

Do not create TradeHub user, portfolio, watchlist, social, chat, or gamification tables in `tradehub-data`.

Those stay in the main TradeHub database.

---

## 36. Views for TradeHub API

Later, create database views or API serializers for TradeHub.

Possible view:

```txt
v_tradehub_stocks
```

Possible fields:

```txt
instrument_id
symbol
isin
name
company_name
sector_name
currency_code
latest_price
change_value
change_percent
volume
market_cap
trading_date
price_timestamp
data_quality_status
```

Do not implement this view in v0.1 unless the API milestone requests it.

---

## 37. Seed Data Requirements

For local development, create minimal seed data:

```txt
1 exchange: BVC
1 data source: bvc_market_data
several sectors if known or placeholder sectors
several sample companies
several sample instruments
sample latest prices
sample daily price bars
```

Seed data must be clearly fake or demo unless it comes from a documented source.

Do not mix fake data with real collected data without marking it.

Suggested metadata flag:

```json
{"demo": true}
```

---

## 38. Repository Layer Requirements

Database access should be isolated in repositories.

Suggested structure:

```txt
src/tradehub_data/repositories/
├── __init__.py
├── sources.py
├── raw_payloads.py
├── instruments.py
├── prices.py
├── indices.py
├── quality.py
└── sync.py
```

Repositories should contain functions like:

```txt
create_ingestion_run
finish_ingestion_run
insert_raw_payload_if_new
get_instrument_by_symbol
upsert_company
upsert_instrument
upsert_latest_price
upsert_price_bar
record_normalization_error
update_sync_state
```

Do not put database write logic directly inside API routes.

---

## 39. Testing Requirements

Add tests for database behavior.

Required test areas:

### Raw payload idempotency

- inserting same source + payload hash twice should not create duplicates

### Instrument uniqueness

- same exchange + symbol should be unique
- same exchange + ISIN should be unique when ISIN exists

### Latest price behavior

- only one latest price per instrument
- upsert should update latest price, not create duplicates

### Price history behavior

- same instrument + timeframe + timestamp should be unique
- different timeframes for same timestamp are allowed

### Ingestion run lifecycle

- run starts as `running`
- run can finish as `success`, `partial_success`, or `failed`
- counts are updated correctly

### Sync state behavior

- one sync state per component
- update should overwrite component state

---

## 40. Data Validation Rules

The database should allow imperfect raw data, but normalized data should be strict.

### Raw layer

Raw layer can store incomplete, messy, or failed payloads.

### Normalized layer

Normalized layer should reject or flag invalid values.

Examples:

- price cannot be negative
- volume cannot be negative
- symbol cannot be empty
- currency code should be 3 letters
- country code should be 2 letters
- `high_price` should not be lower than `low_price`
- `close_price` should generally be between low and high when all values exist

Some validations can be app-level instead of database-level.

Do not overcomplicate the first migration with too many CHECK constraints.

Start with essential constraints and add more after real data behavior is known.

---

## 41. Decimal Precision Guidelines

Use these defaults:

```txt
price fields:          NUMERIC(20, 6)
percentage fields:     NUMERIC(12, 6)
traded value fields:   NUMERIC(24, 6)
market cap fields:     NUMERIC(24, 6)
shares/volume fields:  BIGINT
```

Never use float for financial values.

---

## 42. Deletion Policy

Avoid hard deletion for important financial data.

For entities that can disappear from source data, prefer:

```txt
is_active = false
last_seen_at
```

Examples:

- company no longer listed
- instrument delisted
- index retired

Raw payloads, price bars, ingestion runs, and normalization errors should generally not be deleted.

---

## 43. Version 0.1 Acceptance Criteria

The database milestone is complete when:

1. SQLAlchemy models exist for all v0.1 tables.
2. Alembic migration creates all v0.1 tables.
3. Relationships and foreign keys are defined.
4. Important unique constraints are defined.
5. Important indexes are defined.
6. Money fields use `Numeric`, not float.
7. Volume/share fields use integer/big integer types.
8. Raw payloads support JSONB and text payload storage.
9. Ingestion runs support lifecycle tracking.
10. Latest prices support one row per instrument.
11. Price bars support historical OHLCV data.
12. Basic repository helpers exist.
13. Tests validate idempotency and uniqueness.
14. Seed script creates minimal demo reference data.
15. No TradeHub user/social/trading tables are created.

---

## 44. What Codex Should Implement First

When instructed to implement this document, Codex should implement only v0.1.

Implementation order:

```txt
1. Create SQLAlchemy base and mixins
2. Create enums/constants
3. Create v0.1 models
4. Create Alembic setup if missing
5. Create initial migration
6. Create repository helpers
7. Create seed script
8. Create tests
9. Update README with migration commands
```

Do not implement collectors yet.

Do not implement API endpoints yet unless explicitly requested.

Do not implement v0.2 future tables yet unless explicitly requested.

---

## 45. Codex Prompt For This Milestone

Use this prompt when asking Codex to implement the database milestone:

```txt
Read these files first:
- AGENTS.md
- docs/00_PROJECT_OVERVIEW.md
- docs/01_ARCHITECTURE.md
- docs/02_DATABASE_SCHEMA.md

Implement only the v0.1 database schema milestone.

Requirements:
- Use PostgreSQL, SQLAlchemy, and Alembic.
- Create SQLAlchemy models for the v0.1 tables only.
- Create an Alembic initial migration.
- Use UUID primary keys.
- Use timezone-aware timestamps.
- Use Numeric/Decimal for all financial values.
- Use BigInteger for volume and share count values.
- Add foreign keys, unique constraints, and important indexes.
- Add repository helpers for ingestion runs, raw payloads, instruments, prices, indices, errors, and sync state.
- Add tests for idempotency and uniqueness.
- Add a minimal seed script for local development.

Do not implement collectors.
Do not implement API endpoints.
Do not implement frontend code.
Do not create TradeHub user, portfolio, watchlist, chat, social, or gamification tables.
Keep the change small and focused.
```

---

## 46. Important Reminder

This database is the foundation of the whole `tradehub-data` project.

Bad schema decisions will create pain later.

Prioritize:

```txt
correctness > speed
traceability > convenience
idempotency > quick inserts
history > latest-only data
clean boundaries > shortcuts
```
