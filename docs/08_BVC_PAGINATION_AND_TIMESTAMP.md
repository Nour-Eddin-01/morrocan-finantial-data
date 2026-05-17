# 08_BVC_PAGINATION_AND_TIMESTAMP.md

# TradeHub Data - BVC Pagination and Source Timestamp Specification

## 1. Purpose

This document defines the validation and implementation plan for BVC market listing pagination and source date/timestamp extraction before scheduler or worker execution.

The current manual BVC pipeline can:

```txt
raw payload / fixture
    -> parser diagnostics
    -> normalizer
    -> instruments / latest_prices / price_bars
```

A real BVC market listing HTML fixture has been parsed and normalized successfully:

```txt
50 rows detected
50 rows parseable
50 instruments/latest_prices/price_bars inserted on first run
second run was idempotent
tests pass
```

Before scheduled collection is safe, the system must know whether those 50 rows are complete and which source date or timestamp should be used for normalized price records.

## 2. Why This Must Happen Before Scheduler

Scheduler/workers would repeatedly collect and normalize BVC payloads. If pagination or timestamps are wrong, scheduled runs could create incomplete or misleading market data.

This phase must happen first because:

- a 50-row payload may be only page 1 of the full market listing
- missing pages would silently omit instruments
- daily `price_bars` need a stable trading date
- `latest_prices` should use a trustworthy source timestamp when available
- inventing intraday timestamps would make historical records look more precise than the source allows
- pagination and timestamp rules affect idempotency keys for `price_bars`

The scheduler must not run until the project can clearly answer:

```txt
Did we collect the complete market listing?
What official trading date does this payload represent?
Does the source provide a real timestamp, or only a date?
```

## 3. Pagination Discovery

Pagination discovery must be conservative and non-aggressive.

### 3.1 Detect Pagination Controls

Parser diagnostics should inspect the real BVC HTML for visible pagination controls such as:

- numbered page buttons
- next/previous buttons
- links with page parameters
- form controls that select page size
- embedded data attributes used by client-side pagination

Diagnostics should report:

```txt
pagination_detected
pagination_controls
current_page
visible_page_numbers
next_page_hint
page_size_hint
rows_detected
```

### 3.2 Detect 50-Row Limit

The current real fixture has 50 detected rows. This may mean:

- the full market has exactly 50 displayed rows in that payload
- the page size is 50
- page 1 was saved without page 2
- the site renders only the first page server-side and loads more rows client-side

Diagnostics should flag a possible limit when:

```txt
rows_detected == 50
pagination controls are present
visible page numbers include more than one page
```

The runner should report this as a warning, not silently treat the payload as complete.

### 3.3 Identify Additional Page Mechanism

Investigation should identify whether additional pages require:

- query parameters in the URL
- POST or AJAX request
- embedded Next.js/AMP data
- client-side JavaScript state
- separate API endpoint

Discovery must prefer reading saved HTML and browser-observed requests over automated crawling.

Do not add page fetching until:

- the request pattern is understood
- rate limits/source behavior are documented
- SSL verification remains enabled
- collection stays low-frequency and explicit

### 3.4 Raw Payload Strategy for Pages

If pagination exists, each fetched page must be stored as its own `raw_payloads` row.

Required raw metadata:

```txt
payload_type = "bvc_price_snapshot"
source_url = page-specific URL or endpoint
source_endpoint = bvc_price_snapshot_page
metadata.page_number
metadata.page_size
metadata.pagination_group_id
metadata.pagination_discovery
```

The normalizer must be able to process one raw payload at a time. A future page-group runner may coordinate multiple page payloads, but it must still preserve per-page raw traceability.

## 4. Source Timestamp Extraction

The parser must extract source date/timestamp information only when it is visible or explicitly present in source payload data.

### 4.1 Trading Date

The parser should detect visible French trading dates such as:

```txt
vendredi 15 mai 2026
15 mai 2026
Séance du vendredi 15 mai 2026
```

The parsed value should be exposed as:

```txt
source_trading_date = 2026-05-15
```

French month names must be parsed explicitly:

```txt
janvier
février
mars
avril
mai
juin
juillet
août
septembre
octobre
novembre
décembre
```

Accent-insensitive parsing is allowed for month names, but the original raw string must remain visible in diagnostics.

### 4.2 Source Timestamp

If the source contains a real time, the parser may expose:

```txt
source_timestamp = 2026-05-15T12:34:00+01:00
```

Accepted examples:

```txt
15 mai 2026 12:34
Dernière mise à jour : 15/05/2026 12:34
Mise à jour le vendredi 15 mai 2026 à 12:34
```

If no time is visible, the parser must not invent an intraday timestamp.

### 4.3 Safe Behavior When Only Trading Date Exists

If only `source_trading_date` exists:

- parser returns `source_trading_date`
- parser returns `source_timestamp = null`
- normalizer uses `source_trading_date` for `trading_date`
- normalizer uses a deterministic daily bar timestamp policy

Recommended deterministic daily bar timestamp:

```txt
bar_timestamp = source_trading_date at 00:00:00 Africa/Casablanca
metadata.timestamp_policy = "trading_date_start_of_day"
```

For `latest_prices`, if `source_timestamp` is missing:

```txt
price_timestamp = raw_payload.collected_at
metadata.timestamp_policy = "raw_payload_collected_at_no_source_time"
```

This keeps `latest_prices` operational without pretending the source published an intraday timestamp.

## 5. Parser Changes

Parser output should expose:

```txt
source_trading_date
source_timestamp
source_timestamp_raw
source_timestamp_policy
pagination_metadata
```

For each parsed row:

- `trading_date` should prefer `source_trading_date`
- `source_timestamp` should be set only when a true source timestamp is found
- row `raw_values` must remain unchanged and traceable

Parser diagnostics should expose:

```txt
source_trading_date
source_timestamp
source_timestamp_raw
source_timestamp_policy
pagination_detected
pagination_controls
pagination_warnings
raw_date_candidates
```

The parser must remain separate from database writes.

## 6. Normalizer Changes

The normalizer should use parser-provided date/timestamp values safely.

### 6.1 Daily Price Bars

Daily `price_bars` should use:

```txt
trading_date = parsed source_trading_date if available
bar_timestamp = parsed source_timestamp if available
bar_timestamp = source_trading_date at 00:00 Africa/Casablanca if only date is available
bar_timestamp = raw_payload.collected_at only if no source date exists
```

The timestamp policy must be stored in `price_bars.metadata`.

### 6.2 Latest Prices

`latest_prices.price_timestamp` should use:

```txt
source_timestamp if available
raw_payload.collected_at if only source_trading_date is available
raw_payload.collected_at if no source date exists
```

The timestamp policy must be stored in `latest_prices.metadata`.

The normalizer must continue to avoid overwriting newer `latest_prices` with older timestamps.

### 6.3 Raw Payload Metadata

After normalization, `raw_payloads.metadata` should include:

```txt
source_trading_date
source_timestamp
source_timestamp_policy
pagination_detected
pagination_warning
normalized_at
processed_at
```

No schema migration is required unless a later audit shows these fields need first-class columns.

## 7. Pipeline Runner Behavior

The manual BVC pipeline runner should include date/timestamp and pagination information in JSON output.

Additional output fields:

```txt
source_trading_date
source_timestamp
source_timestamp_policy
pagination_detected
pagination_warnings
rows_detected
parseable_rows_count
normalization_status
final_raw_payload_status
```

If pagination is detected and only one page was processed, the runner should return:

```txt
status = "partial_success"
message includes pagination warning
```

The runner may still normalize the provided page if diagnostics pass, but it must clearly report that the payload may not represent the full market listing.

## 8. Tests

Add focused fixtures and tests.

### 8.1 Fixture With Visible Trading Date

Create a fixture containing a market listing table plus visible date text:

```txt
Séance du vendredi 15 mai 2026
```

Expected:

```txt
source_trading_date = 2026-05-15
source_timestamp = null
```

### 8.2 Fixture Without Timestamp

Use a fixture with rows but no visible date/time.

Expected:

```txt
source_trading_date = null
source_timestamp = null
timestamp policy falls back safely
```

### 8.3 Fixture With Pagination Indicators

Create a fixture with pagination controls and 50 rows or a simplified representative table.

Expected diagnostics:

```txt
pagination_detected = true
pagination_warnings includes possible_incomplete_listing
```

### 8.4 French Date Parsing Tests

Test French date parsing:

```txt
vendredi 15 mai 2026 -> 2026-05-15
15 mai 2026 -> 2026-05-15
15/05/2026 12:34 -> date + timestamp
Mise à jour le vendredi 15 mai 2026 à 12:34 -> date + timestamp
```

### 8.5 Regression Tests

The existing real fixture must continue to parse and normalize:

```txt
rows_detected = 50
parseable_rows_count = 50
row_parse_errors_count = 0
second run creates no duplicate instruments/latest_prices/price_bars
```

## 9. Acceptance Criteria

This phase is complete when:

- diagnostics reports whether pagination controls are present
- diagnostics reports possible 50-row/page-limit warnings
- parser extracts French trading dates when visible
- parser extracts source timestamp only when real time is visible
- parser never invents intraday timestamps
- normalizer uses extracted trading date for daily bars
- normalizer uses safe timestamp policy for latest prices
- timestamp policy is stored in metadata
- pipeline runner reports source date/timestamp and pagination warnings
- tests cover date parsing, missing timestamp, pagination indicators, and existing real fixture regression
- no scheduler, worker, API endpoint, or TradeHub integration is added

## 10. Out-of-Scope Items

This phase must not implement:

- scheduler or worker execution
- TradeHub integration
- API endpoints
- aggressive live scraping
- SSL verification disabled by default
- automated pagination crawling without a documented request pattern
- intraday bars
- invented source timestamps
- company master-data normalization
- index parsing
- public raw payload exposure

## 11. Codex Implementation Checklist

When implementing this phase, Codex should:

1. Read `AGENTS.md` and all BVC specs through this document.
2. Inspect current parser, diagnostics, normalizer, runner, and tests.
3. Add source date/timestamp parsing helpers in parser code, not normalizer code.
4. Keep parser output separate from database writes.
5. Add diagnostics fields for date/timestamp and pagination.
6. Add pagination detection from saved HTML only.
7. Do not fetch additional pages until the request pattern is explicitly specified.
8. Update normalizer timestamp selection policy.
9. Store timestamp policy in normalized record metadata.
10. Update runner JSON output with source date/timestamp and pagination warnings.
11. Add fixtures for visible date, missing timestamp, and pagination indicators.
12. Add French date parsing tests.
13. Preserve current real fixture behavior.
14. Run:

```bash
python3 -m compileall -q src tests
docker compose config
docker compose build api
docker compose run --rm api sh -c "pip install -e '.[dev]' && pytest"
docker compose run --rm api python -m tradehub_data.parsers.bvc_prices.diagnostics /app/fixtures/bvc_prices/real/bvc_market_listing_20260515_1200.html
docker compose run --rm api python -m tradehub_data.pipelines.bvc_prices.runner --fixture-path /app/fixtures/bvc_prices/real/bvc_market_listing_20260515_1200.html
```

15. Report:

```txt
pagination findings
source date/timestamp findings
timestamp policy implemented
files created/modified
tests added
commands passed
remaining blockers before scheduler
```
