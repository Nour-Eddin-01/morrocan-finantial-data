# 09_BVC_MULTIPAGE_COLLECTION.md

# TradeHub Data - BVC Multi-Page Collection Specification

## 1. Purpose

This document defines the safe multi-page collection and normalization strategy for BVC market listing payloads before scheduler or worker execution.

The current BVC pipeline can parse, diagnose, normalize, and manually run a real BVC HTML payload. The validated real fixture shows:

```txt
50 rows detected
50 rows parseable
source_trading_date = 2026-05-15
source_timestamp = null
pagination_detected = true
visible pages = 1, 2
runner status = partial_success
```

This means the current saved payload may represent only page 1 of the full market listing. Before scheduler work, the system must prove complete listing coverage.

## 2. Why Multi-Page Coverage Is Required Before Scheduler

Schedulers and workers would repeatedly collect and normalize BVC data. If the collector only stores page 1, scheduled runs would produce incomplete market data while appearing successful.

Multi-page coverage must be validated first because:

- missing pages can omit listed instruments
- `latest_prices` would show a partial market snapshot
- daily `price_bars` would preserve incomplete historical records
- downstream TradeHub consumers would eventually receive incomplete market data
- incomplete scheduled runs are harder to detect after automation starts
- duplicate symbols across pages must be treated as a data quality signal

Scheduler work must remain blocked until the project can answer:

```txt
How many BVC listing pages exist for a trading date?
Which raw payload belongs to each page?
Were all known pages collected and diagnosed?
Were all pages normalized without duplicate final rows?
```

## 3. Page Discovery

Page discovery must be conservative and based on saved HTML or manually observed browser requests first.

### 3.1 Inspect Saved HTML Pagination Controls

Diagnostics should continue inspecting saved HTML for:

- visible page buttons
- current page indicators
- next/previous controls
- page-size controls
- link `href` values
- data attributes on pagination elements
- embedded AMP or Next.js state that references page data

Required discovered fields:

```txt
pagination_detected
visible_page_numbers
current_page
next_page_hint
page_size_hint
pagination_controls
pagination_warnings
```

### 3.2 Identify Page Numbers

The first validated real fixture has visible pages:

```txt
1
2
```

This must be treated as a strong signal that at least two pages may exist. It must not be treated as proof that only two pages exist unless the source provides an explicit total-page value or browser-observed behavior confirms it.

Diagnostics should report:

```txt
page_number
pagination_total_pages
pagination_total_pages_source
```

Allowed `pagination_total_pages_source` values:

```txt
visible_controls
source_metadata
manual_operator_input
unknown
```

### 3.3 Identify Next-Page URLs or Request Parameters

Before any code fetches page 2 automatically, an operator must identify how page navigation works.

Possible mechanisms:

- URL query parameter such as `page=2`
- form or POST parameter
- AJAX request
- AMP state update
- embedded Next.js data
- separate structured endpoint
- client-side-only state

Discovery should use:

- browser developer tools
- manually saved page 1 and page 2 HTML
- copied request URLs and headers when safe
- saved response samples

Do not guess endpoint parameters in production code.

### 3.4 No Aggressive Discovery

The project must not:

- brute-force page numbers
- crawl unknown endpoints
- use high-frequency retries
- bypass protections
- disable SSL verification
- infer missing pages without source evidence

If page 2 cannot be acquired safely, keep runner output as `partial_success` and document the blocker.

## 4. Safe Acquisition Workflow

Initial multi-page validation must use manual fixtures.

### 4.1 Manual Page Fixtures

An operator should manually save each visible page from the official BVC listing.

Recommended path:

```txt
fixtures/bvc_prices/real/
```

Recommended filenames:

```txt
bvc_market_listing_YYYYMMDD_HHMM_page_1.html
bvc_market_listing_YYYYMMDD_HHMM_page_2.html
```

If the existing page-1 fixture is reused, page metadata must still be supplied when storing or grouping it.

### 4.2 Browser-Observed Request Notes

Alongside fixtures, document the observed page-2 mechanism in a local note or future doc update:

```txt
source_url_page_1
source_url_page_2
request_method
query_parameters
requires_ajax
requires_amp_state
requires_next_data
observed_at
operator_notes
```

Do not commit secrets, cookies, private tokens, or authentication material.

### 4.3 SSL Verification

SSL verification remains enabled by default.

If live validation is later needed, use the existing collector SSL configuration:

```env
BVC_PRICE_COLLECTOR_VERIFY_SSL=true
BVC_PRICE_COLLECTOR_CA_BUNDLE_PATH=/path/in/container/bvc-ca-bundle.pem
```

Do not add a committed default that disables verification.

## 5. Raw Payload Storage Strategy

Each listing page must be stored as a separate `raw_payloads` row.

Required row behavior:

```txt
payload_type = "bvc_price_snapshot"
status = "collected"
payload_text = full page HTML
payload_hash = sha256(full page HTML)
source_url = page-specific URL if known
source_endpoint = bvc_price_snapshot_page
```

Required metadata:

```txt
metadata.page_number
metadata.page_size
metadata.pagination_group_id
metadata.pagination_total_pages
metadata.source_trading_date
metadata.source_timestamp
metadata.source_timestamp_policy
metadata.pagination_discovery
```

`pagination_group_id` groups pages from the same logical market listing snapshot. It should be deterministic when possible:

```txt
bvc_price_snapshot:<source_trading_date>:<collection_batch_id>
```

If the trading date is not known yet, use a UUID generated by the manual runner or fixture loader and preserve it across all pages in the group.

The database schema does not need a migration for this phase unless a later audit determines page fields must become first-class columns.

## 6. Diagnostics Behavior

Diagnostics must stay read-only.

For each page, diagnostics should report:

```txt
raw_payload_id or file_path
page_number
pagination_group_id
pagination_total_pages
tables_found
rows_detected
parseable_rows_count
row_parse_errors_count
source_trading_date
source_timestamp
pagination_detected
pagination_controls
pagination_warnings
```

For a page group, diagnostics should report:

```txt
pagination_group_id
pages_found
expected_pages
missing_pages
pagination_complete
total_rows_detected
total_parseable_rows
symbols_detected
duplicate_symbols
duplicate_symbols_count
per_page_summaries
status
```

Duplicate instruments across pages must be visible as warnings. They should not be silently ignored because duplicates may indicate:

- overlapping pagination
- reused page content
- source-side duplication
- incorrect page request parameters

Allowed group diagnostic statuses:

```txt
success
partial_success
failed
```

Suggested status rules:

- `success`: all expected pages are present and all page diagnostics pass.
- `partial_success`: pages parse, but pagination is incomplete or duplicate symbols exist.
- `failed`: any page has no valid market table or no parseable rows.

## 7. Normalization Behavior

The normalizer should continue processing one `raw_payloads` row at a time.

For multi-page groups:

- normalize each page independently
- preserve each page's `raw_payload_id`
- preserve each page's `source_id`
- keep parser logic separate from database writes
- rely on existing upsert/idempotency constraints
- keep raw page metadata in normalized record metadata where useful

Final tables must remain duplicate-safe:

```txt
instruments: exchange_id + isin or exchange_id + symbol
latest_prices: instrument_id
price_bars: instrument_id + timeframe + bar_timestamp
```

If the same symbol appears on multiple pages with the same trading date:

- do not create duplicate instruments
- do not create duplicate latest_prices
- do not create duplicate price_bars
- report duplicate symbols as a warning in group output
- preserve the `raw_payload_id` that last updated the normalized record according to existing timestamp/update rules

The normalizer must not merge raw payloads before parsing. Raw traceability must remain page-specific.

## 8. Pipeline Runner Behavior

The manual pipeline runner should keep current single-page behavior unchanged and add explicit multi-page modes.

### 8.1 Supported Multi-Page Inputs

Recommended input options:

```bash
--fixture-path /app/fixtures/bvc_prices/real/page_1.html
--fixture-path /app/fixtures/bvc_prices/real/page_2.html
```

or:

```bash
--fixture-dir /app/fixtures/bvc_prices/real/20260515_1200/
```

or:

```bash
--raw-payload-id <page-1-uuid>
--raw-payload-id <page-2-uuid>
```

The implementation may choose one input style first, but it must not break:

```bash
--fixture-path <single-file>
--raw-payload-id <single-uuid>
--collect-live
```

### 8.2 Page Group Processing

Runner flow for a fixture group:

```txt
1. Resolve input pages.
2. Assign or read pagination_group_id.
3. Store each page through raw payload storage.
4. Run diagnostics for each page.
5. Run group diagnostics.
6. If any page diagnostics fail, do not normalize failed pages.
7. Normalize passing pages one at a time.
8. Report per-page and group results as JSON.
```

The runner must not duplicate parser or normalizer logic.

### 8.3 JSON Output Contract

Group output should include:

```json
{
  "status": "success",
  "mode": "fixture_group",
  "pagination_group_id": "bvc_price_snapshot:2026-05-15:manual-20260515-1200",
  "pages_found": 2,
  "pages_processed": 2,
  "expected_pages": 2,
  "missing_pages": [],
  "pagination_complete": true,
  "source_trading_date": "2026-05-15",
  "source_timestamp": null,
  "total_rows_detected": 74,
  "total_rows_normalized": 74,
  "duplicate_symbols_count": 0,
  "errors_count": 0,
  "per_page_summaries": []
}
```

Each per-page summary should include:

```txt
page_number
raw_payload_id
source_id
payload_hash
diagnostics_status
rows_detected
parseable_rows_count
normalization_status
rows_normalized
errors_count
final_raw_payload_status
pagination_warnings
```

If only page 1 is provided while `pagination_total_pages = 2`, return:

```txt
status = "partial_success"
pagination_complete = false
missing_pages = [2]
```

Normalization may still run for provided pages if diagnostics pass, but output must clearly show incomplete coverage.

## 9. Acceptance Criteria

This phase is complete when:

- page 1 and page 2 fixtures can be diagnosed
- both pages can be stored as separate `raw_payloads`
- both pages share a `pagination_group_id`
- per-page metadata includes page number and total pages when known
- group diagnostics report page counts and missing pages
- duplicate symbols across pages are detected and reported
- both pages can be normalized independently
- repeated group runs create no duplicate instruments, latest prices, or price bars
- runner reports complete or incomplete pagination state
- existing single-page runner behavior remains compatible
- tests cover multi-page fixture storage, diagnostics, normalization, duplicate detection, and idempotency

## 10. Out-of-Scope Items

This phase must not implement:

- scheduler or worker execution
- TradeHub integration
- API endpoints
- aggressive scraping
- automated crawling without a confirmed request pattern
- SSL verification disabled by default
- invented missing pages
- invented financial values
- silent normalization of unknown table shapes
- company master-data normalization
- index parsing
- intraday bars

## 11. Codex Implementation Checklist

When implementing this phase, Codex should:

1. Read `AGENTS.md` and all BVC specs through this document.
2. Inspect the current collector, fixture loader, parser diagnostics, normalizer, runner, and tests.
3. Preserve current single-page behavior.
4. Add page metadata support in fixture/raw payload workflows.
5. Support a manual multi-page fixture input mode.
6. Store each page as its own `raw_payloads` row.
7. Assign or accept a shared `pagination_group_id`.
8. Run parser diagnostics per page.
9. Add group diagnostics for completeness and duplicate symbols.
10. Normalize each passing page independently.
11. Do not normalize pages whose diagnostics fail.
12. Keep duplicate final rows prevented by existing repository upserts.
13. Include per-page and group summaries in runner JSON.
14. Add fixtures for page 1 and page 2.
15. Add tests for missing page behavior.
16. Add tests for duplicate symbols across pages.
17. Add idempotency tests for running the group twice.
18. Run:

```bash
python3 -m compileall -q src tests
docker compose config
docker compose build api
docker compose run --rm api sh -c "pip install -e '.[dev]' && pytest"
```

19. Report:

```txt
page discovery findings
raw payload page metadata behavior
group diagnostics behavior
normalization/idempotency behavior
runner JSON examples
commands passed
remaining blockers before scheduler
```
