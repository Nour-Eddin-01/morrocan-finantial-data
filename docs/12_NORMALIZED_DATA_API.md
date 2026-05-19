# 12_NORMALIZED_DATA_API.md

# TradeHub Data - Normalized BVC Data API Specification

## 1. Purpose

This document defines read-only FastAPI endpoints for serving normalized BVC market data already stored in `tradehub-data`.

The API must read from normalized database tables created by the existing manual BVC pipeline:

```txt
raw_payloads
    -> parser diagnostics
    -> BVC price normalizer
    -> instruments
    -> latest_prices
    -> price_bars
```

The API must not collect, parse, normalize, schedule, or push data to TradeHub.

## 2. Why This Can Happen Before Scheduler

Scheduler remains blocked because live BVC collection from Docker/terminal still times out even after TLS verification is fixed with a CA bundle.

However, the manual multi-page fixture pipeline works and normalized records can already exist in:

```txt
instruments
latest_prices
price_bars
normalization_errors
raw_payloads metadata
```

Read-only endpoints can be added before scheduler because they:

- expose already-normalized data for local inspection
- help verify database shape and query ergonomics
- do not fetch live BVC data
- do not mutate raw or normalized tables
- do not approve scheduled collection
- do not integrate TradeHub yet

These endpoints are an internal data-access milestone, not a production publishing milestone.

## 3. Endpoint Summary

Initial BVC endpoints:

```txt
GET /api/v1/markets/bvc/instruments
GET /api/v1/markets/bvc/latest-prices
GET /api/v1/markets/bvc/instruments/{symbol}
GET /api/v1/markets/bvc/instruments/{symbol}/price-bars
GET /api/v1/markets/bvc/diagnostics/summary
```

All endpoints are read-only.

All endpoints must return normalized data or safe metadata only. Do not expose `raw_payloads.payload_text` or full raw payload JSON/HTML.

## 4. Data Sources

Allowed read tables:

```txt
exchanges
instruments
latest_prices
price_bars
raw_payloads metadata only
normalization_errors summarized or redacted
data_sources
```

Do not expose:

```txt
raw_payloads.payload_text
raw_payloads.payload
raw source HTML
raw source JSON
private headers
cookies
tokens
full raw error fragments when they may contain source payload content
```

## 5. Query Parameters

Shared parameters where relevant:

```txt
symbol
limit
offset
trading_date
timeframe
```

### 5.1 `symbol`

Optional on collection endpoints. Required as a path parameter on instrument-specific endpoints.

Rules:

- normalize to uppercase for lookup
- trim whitespace
- reject empty symbols
- return `404` if no matching BVC instrument exists

### 5.2 `limit`

Pagination limit.

Recommended defaults:

```txt
default = 100
minimum = 1
maximum = 500
```

### 5.3 `offset`

Pagination offset.

Recommended defaults:

```txt
default = 0
minimum = 0
```

### 5.4 `trading_date`

Optional ISO date filter:

```txt
YYYY-MM-DD
```

Applicable to:

```txt
latest_prices
price_bars
diagnostics summary
```

Invalid date formats must return `422`.

### 5.5 `timeframe`

Optional price bar timeframe filter.

Initial supported value:

```txt
1d
```

Unsupported values should return `422` or a clear validation error.

## 6. Response Contracts

### 6.1 `GET /api/v1/markets/bvc/instruments`

Returns BVC instruments.

Query parameters:

```txt
symbol
limit
offset
```

Response:

```json
{
  "items": [
    {
      "id": "uuid",
      "symbol": "ATW",
      "isin": "MA0000012445",
      "name": "ATTIJARIWAFA BANK",
      "instrument_type": "equity",
      "currency_code": "MAD",
      "is_active": true,
      "last_seen_at": "2026-05-18T12:00:00Z",
      "source_id": "uuid",
      "raw_payload_id": "uuid"
    }
  ],
  "limit": 100,
  "offset": 0,
  "count": 1
}
```

### 6.2 `GET /api/v1/markets/bvc/latest-prices`

Returns latest normalized prices for BVC instruments.

Query parameters:

```txt
symbol
trading_date
limit
offset
```

Response:

```json
{
  "items": [
    {
      "symbol": "ATW",
      "instrument_id": "uuid",
      "price": "500.000000",
      "open_price": "495.000000",
      "high_price": "505.000000",
      "low_price": "490.000000",
      "previous_close": "492.000000",
      "change_value": "8.000000",
      "change_percent": "1.626000",
      "volume": 1234,
      "traded_value": "617000.000000",
      "market_cap": "1000000000.000000",
      "price_timestamp": "2026-05-18T16:00:00Z",
      "trading_date": "2026-05-18",
      "data_quality_status": "valid",
      "source_id": "uuid",
      "raw_payload_id": "uuid",
      "metadata": {
        "timestamp_policy": "source_timestamp",
        "source_trading_date": "2026-05-18",
        "source_timestamp_policy": "source_timestamp"
      }
    }
  ],
  "limit": 100,
  "offset": 0,
  "count": 1,
  "freshness": {
    "latest_collected_at": "2026-05-18T12:00:00Z",
    "latest_price_timestamp": "2026-05-18T16:00:00Z",
    "latest_trading_date": "2026-05-18"
  }
}
```

Financial numeric values must be serialized as strings to preserve decimal precision.

### 6.3 `GET /api/v1/markets/bvc/instruments/{symbol}`

Returns one BVC instrument with its latest price when available.

Response:

```json
{
  "id": "uuid",
  "symbol": "ATW",
  "isin": "MA0000012445",
  "name": "ATTIJARIWAFA BANK",
  "instrument_type": "equity",
  "currency_code": "MAD",
  "is_active": true,
  "last_seen_at": "2026-05-18T12:00:00Z",
  "latest_price": {
    "price": "500.000000",
    "price_timestamp": "2026-05-18T16:00:00Z",
    "trading_date": "2026-05-18",
    "data_quality_status": "valid",
    "raw_payload_id": "uuid"
  },
  "source_id": "uuid",
  "raw_payload_id": "uuid"
}
```

If the instrument exists but no latest price exists, return the instrument with:

```json
{
  "latest_price": null
}
```

### 6.4 `GET /api/v1/markets/bvc/instruments/{symbol}/price-bars`

Returns historical price bars for one instrument.

Query parameters:

```txt
timeframe
trading_date
limit
offset
```

Response:

```json
{
  "symbol": "ATW",
  "timeframe": "1d",
  "items": [
    {
      "bar_timestamp": "2026-05-18T00:00:00+01:00",
      "trading_date": "2026-05-18",
      "open_price": "495.000000",
      "high_price": "505.000000",
      "low_price": "490.000000",
      "close_price": "500.000000",
      "volume": 1234,
      "traded_value": "617000.000000",
      "number_of_trades": 42,
      "data_quality_status": "valid",
      "source_id": "uuid",
      "raw_payload_id": "uuid",
      "metadata": {
        "timestamp_policy": "trading_date_start_of_day"
      }
    }
  ],
  "limit": 100,
  "offset": 0,
  "count": 1
}
```

### 6.5 `GET /api/v1/markets/bvc/diagnostics/summary`

Returns a safe summary of normalized data quality and ingestion freshness.

Query parameters:

```txt
trading_date
```

Response:

```json
{
  "market": "BVC",
  "latest_trading_date": "2026-05-18",
  "instruments_count": 80,
  "latest_prices_count": 80,
  "price_bars_count": 80,
  "open_normalization_errors_count": 0,
  "raw_payloads": {
    "latest_collected_at": "2026-05-18T12:00:00Z",
    "latest_normalized_at": "2026-05-18T12:01:00Z",
    "latest_pagination_group_id": "bvc_price_snapshot:2026-05-18:manual",
    "latest_pages_found": 2,
    "latest_total_rows_detected": 80,
    "latest_collection_mode": "manual_fixture"
  },
  "live_collection": {
    "scheduler_blocked": true,
    "blocker": "live Docker/terminal HTTP requests time out"
  }
}
```

Diagnostics summary must not expose full raw payload text, full raw JSON, full raw HTML, private request headers, cookies, tokens, or sensitive internal stack traces.

## 7. Data Freshness Fields

Responses that include price data should expose freshness metadata when available:

```txt
latest_collected_at
latest_price_timestamp
latest_trading_date
source_trading_date
source_timestamp
source_timestamp_policy
raw_payload_id
source_id
data_quality_status
```

Freshness should be derived from normalized tables and safe `raw_payloads.metadata`.

Do not infer freshness from current wall-clock time except for response generation timestamps if explicitly needed later.

## 8. Error Behavior

### 8.1 Validation Errors

Invalid query parameters return `422`.

Examples:

```txt
limit < 1
limit > 500
offset < 0
invalid trading_date format
unsupported timeframe
empty symbol
```

### 8.2 Not Found

Missing instruments return `404`:

```json
{
  "detail": "BVC instrument not found: ATW"
}
```

### 8.3 Empty Results

Collection endpoints return an empty `items` list with `count = 0`.

Do not return `404` for an empty collection result.

### 8.4 Internal Errors

Unexpected server errors should return a generic `500`.

Do not expose:

```txt
SQL statements
stack traces
raw payload fragments
source HTML
source JSON
private headers
```

Log internal details server-side using existing logging behavior.

## 9. Implementation Boundaries

API routes must stay thin.

Recommended implementation shape:

```txt
src/tradehub_data/api/routes/bvc_market.py
src/tradehub_data/schemas/bvc_market.py
src/tradehub_data/repositories/market_data.py
```

Allowed responsibilities:

- route layer validates request parameters and returns schemas
- repository layer reads normalized tables
- schemas serialize Decimals safely as strings

Forbidden responsibilities:

- fetching live BVC data
- storing raw payloads
- parsing payloads
- normalizing payloads
- running the pipeline runner
- triggering scheduler behavior
- integrating with TradeHub

## 10. Tests Required

Add tests for:

```txt
GET /api/v1/markets/bvc/instruments
GET /api/v1/markets/bvc/latest-prices
GET /api/v1/markets/bvc/instruments/{symbol}
GET /api/v1/markets/bvc/instruments/{symbol}/price-bars
GET /api/v1/markets/bvc/diagnostics/summary
pagination limit/offset
symbol filtering
trading_date filtering
timeframe validation
404 for missing symbol
Decimal serialization as strings
raw_payloads.payload_text is not exposed
diagnostics errors are summarized/redacted
```

Tests must use local database fixtures or seeded in-memory data.

Tests must not:

```txt
hit the live BVC network
run collectors
run normalizers unless explicitly seeding test data through existing helpers
depend on scheduler
depend on TradeHub
```

## 11. Out-of-Scope Items

Do not implement in this API phase:

- scheduler or workers
- live BVC collection
- collector behavior changes
- parser changes
- normalizer changes
- TradeHub integration
- authentication
- frontend UI
- public raw payload browsing
- raw HTML or raw JSON response endpoints
- mutation endpoints
- admin repair tools

## 12. Codex Implementation Checklist

When implementing this spec later:

```txt
1. Re-read AGENTS.md and this specification.
2. Inspect current FastAPI app structure.
3. Inspect SQLAlchemy models for instruments, latest_prices, price_bars, raw_payloads, and normalization_errors.
4. Add read-only repository helpers for normalized BVC data.
5. Add Pydantic response schemas that serialize Decimal values as strings.
6. Add thin FastAPI routes under /api/v1/markets/bvc.
7. Ensure no endpoint exposes raw_payloads.payload_text or raw payload JSON/HTML.
8. Add focused API tests using local fixture data.
9. Run compile and test commands.
10. Do not add scheduler, workers, live fetching, or TradeHub integration.
```

