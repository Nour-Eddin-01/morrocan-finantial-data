# 10_BVC_LIVE_MULTIPAGE_COLLECTOR.md

# TradeHub Data - BVC Live Multi-Page Collector Specification

## 1. Purpose

This document defines the safe implementation plan for live multi-page BVC market price collection using the discovered JSON endpoint.

Manual multi-page validation already works with saved real HTML fixtures:

```txt
page 1 rows_detected = 50
page 2 rows_detected = 30
total rows = 80
source_trading_date = 2026-05-18
pagination_complete = true
duplicate_symbols_count = 0
tests pass
```

The next step is to collect the same complete market listing from the official JSON endpoint while preserving the project rule:

```txt
collect raw data first, normalize later
```

This specification does not implement scheduler, API, or TradeHub integration.

## 2. Why This Comes Before Scheduler

Scheduler/workers should not run until complete live collection is proven manually.

This phase comes before scheduler because:

- page 1 alone is incomplete
- scheduled partial collection would create incomplete `latest_prices`
- scheduler retries could amplify bad pagination behavior
- SSL verification issues must be handled safely before automation
- JSON payload shape must be validated before parser/normalizer trust it
- the runner must prove live collection can collect all pages and normalize idempotently

The scheduler must remain blocked until:

```txt
all live JSON pages are collected
all JSON raw payloads are stored
diagnostics pass for each page
normalization is idempotent
SSL verification remains enabled
```

## 3. Confirmed Endpoint and Parameters

Browser DevTools showed page 2 is loaded from this endpoint:

```txt
https://www.casablanca-bourse.com/api/proxy/fr/api/bourse_data/last_market_watches/action?page%5Blimit%5D=50&page%5Boffset%5D=50
```

Decoded query:

```txt
page[limit] = 50
page[offset] = 50
```

Observed request:

```txt
method = GET
status = 200 OK
content-type = application/json
accept = application/vnd.api+json
referer = https://www.casablanca-bourse.com/fr/live-market/marche-actions-listing
```

Manual JSON validation showed the page 2 payload contains expected instruments, including:

```txt
MED PAPER
MICRODATA
MINIERE TOUISSIT
MUTANDIS SCA
```

Likely pagination:

```txt
page 1: page[limit]=50&page[offset]=0
page 2: page[limit]=50&page[offset]=50
```

## 4. Request Rules

The live collector must be conservative.

Required rules:

- use `GET` only
- run only through explicit manual commands for this phase
- use low-frequency requests
- keep SSL verification enabled by default
- reuse existing HTTP client and collector configuration where possible
- support `BVC_PRICE_COLLECTOR_CA_BUNDLE_PATH`
- send `accept: application/vnd.api+json`
- send a normal, non-secret `referer` if required by the endpoint
- do not commit cookies, CSRF tokens, session IDs, or private headers
- do not hardcode transient browser security tokens
- do not fetch unknown endpoints automatically

Allowed default request headers:

```txt
Accept: application/vnd.api+json
Referer: https://www.casablanca-bourse.com/fr/live-market/marche-actions-listing
User-Agent: configured BVC collector user agent
```

Forbidden committed headers:

```txt
Cookie
Authorization
X-CSRF-Token
CSRF token query values
browser session IDs
```

## 5. SSL Handling

A direct curl request to the JSON endpoint failed with:

```txt
curl: (60) SSL certificate problem: unable to get local issuer certificate
```

Therefore:

- do not disable SSL verification by default
- do not ignore SSL errors silently
- reuse `BVC_PRICE_COLLECTOR_VERIFY_SSL=true`
- support `BVC_PRICE_COLLECTOR_CA_BUNDLE_PATH`
- surface SSL failures clearly in collector results and ingestion run metadata

Expected configuration:

```env
BVC_PRICE_COLLECTOR_VERIFY_SSL=true
BVC_PRICE_COLLECTOR_CA_BUNDLE_PATH=/path/in/container/bvc-ca-bundle.pem
```

If a development-only SSL bypass exists later, it must remain disabled by default and clearly documented as unsafe for production validation.

## 6. Pagination Strategy

The collector should use offset pagination with a safety guard.

Default parameters:

```txt
limit = 50
start_offset = 0
next_offset = current_offset + limit
max_pages = configurable safety guard
```

Stop when:

- returned row count is `0`
- returned row count is less than `limit`
- `max_pages` is reached
- a page fails and the configured failure policy says to stop

Recommended first implementation:

```txt
offset 0  -> page_number 1
offset 50 -> page_number 2
stop because row_count 30 < limit
```

Safety rules:

- default `max_pages` should be small, for example `5`
- never brute-force arbitrary offsets
- never continue indefinitely
- record why pagination stopped
- record pages attempted and pages stored

Recommended environment variables:

```env
BVC_PRICE_COLLECTOR_JSON_ENABLED=true
BVC_PRICE_COLLECTOR_JSON_PATH=/api/proxy/fr/api/bourse_data/last_market_watches/action
BVC_PRICE_COLLECTOR_PAGE_LIMIT=50
BVC_PRICE_COLLECTOR_MAX_PAGES=5
```

## 7. Raw Payload Storage

Each JSON page must be stored as a separate `raw_payloads` row.

Required fields:

```txt
payload_type = "bvc_price_snapshot"
source_endpoint = "bvc_price_snapshot_json_page"
source_url = full URL including page parameters
payload = parsed JSON when safe
payload_text = raw JSON text
payload_hash = sha256(source_url + normalized body)
content_type = application/json
status = collected
```

Required metadata:

```txt
metadata.page_number
metadata.page_offset
metadata.page_limit
metadata.page_size
metadata.pagination_group_id
metadata.pagination_total_pages
metadata.pagination_stop_reason
metadata.source_trading_date
metadata.source_timestamp
metadata.source_timestamp_policy
metadata.collection_mode = "live_json"
```

`pagination_group_id` should be shared by all pages from one collector run.

Recommended format:

```txt
bvc_price_snapshot:<source_trading_date or unknown>:live:<ingestion_run_id>
```

The collector must not write directly to:

```txt
instruments
latest_prices
price_bars
```

## 8. Parser and Normalizer Impact

The existing HTML parser must remain intact.

The implementation may choose one of two safe approaches.

### 8.1 Direct JSON Parser

Add a JSON parser that maps BVC JSON records into the existing parsed DTO shape.

Recommended path:

```txt
src/tradehub_data/parsers/bvc_prices/json_parser.py
```

This parser should:

- parse raw JSON text or dict
- extract instrument identity and price fields
- use `Decimal` for financial values
- use integer parsing only for quantities/counts
- expose `source_trading_date` if available
- expose `source_timestamp` only if truly available
- return the same normalized parser DTOs used by the normalizer

### 8.2 JSON-to-Parser DTO Layer

Alternatively, add a JSON-to-DTO adapter that produces the current parser DTOs without changing HTML parsing.

This adapter must still be parser code, not normalizer code.

### 8.3 Normalizer Rules

The normalizer may branch by raw payload content type or metadata:

```txt
application/json -> JSON parser
text/html        -> HTML parser
```

The normalizer must:

- keep one raw page as one normalization unit
- preserve `raw_payload_id`
- preserve `source_id`
- continue using existing upserts
- not duplicate instruments/latest_prices/price_bars
- not silently normalize unknown JSON shapes

## 9. Diagnostics

Diagnostics should support JSON payloads in addition to HTML.

JSON diagnostics should report:

```txt
payload_format = "json"
raw_payload_id or file_path
page_number
page_offset
page_limit
tables_found = 0
rows_detected
parseable_rows_count
row_parse_errors_count
source_trading_date
source_timestamp
source_timestamp_raw
source_timestamp_policy
mapped_fields
unmapped_fields
missing_required_fields
status
```

Group diagnostics should continue reporting:

```txt
pagination_group_id
pages_found
expected_pages
missing_pages
pagination_complete
total_rows_detected
total_parseable_rows
duplicate_symbols
duplicate_symbols_count
per_page_summaries
```

Malformed JSON must return `status = failed` and include an explicit error message.

## 10. Pipeline Runner Behavior

The manual pipeline runner should use the live collector only when explicitly requested:

```bash
docker compose run --rm api python -m tradehub_data.pipelines.bvc_prices.runner --collect-live
```

For this phase, `--collect-live` should:

```txt
1. Run the existing BVC collector in JSON multi-page mode.
2. Store each JSON page in raw_payloads.
3. Build a pagination group from collected raw_payload IDs.
4. Run diagnostics for each page.
5. Run group diagnostics.
6. Normalize only pages whose diagnostics pass.
7. Return JSON group summary.
```

Runner group output should include:

```txt
status
mode = "collect_live"
pagination_group_id
pages_found
pages_processed
expected_pages
missing_pages
pagination_complete
source_trading_date
source_timestamp
total_rows_detected
total_rows_normalized
duplicate_symbols_count
duplicate_symbols
errors_count
per_page_summaries
```

Single-page fixture and multi-page fixture behavior must remain intact.

## 11. Error Handling

Errors must be explicit and traceable.

### 11.1 SSL Errors

If SSL verification fails:

- return collector status `failed`
- record the SSL error message
- record the affected URL
- do not retry with SSL disabled
- include CA bundle guidance in the error message or docs

### 11.2 Page 1 Success but Later Page Fails

If page 1 succeeds and a later page fails:

- store successful pages
- record failed page metadata in ingestion run
- return `partial_success` or `failed` according to the implemented policy
- do not pretend pagination is complete
- do not normalize failed or missing pages

### 11.3 Empty Page

An empty page is a normal stop condition when previous pages contained rows.

Record:

```txt
pagination_stop_reason = "empty_page"
```

Do not store an empty page as a normal price snapshot unless it is useful for audit and clearly marked.

### 11.4 Malformed JSON

Malformed JSON must:

- fail diagnostics
- record a parsing error
- block normalization for that page
- keep raw payload traceability if the response was stored

### 11.5 Duplicate Symbols

Duplicate symbols across pages must:

- be reported in group diagnostics
- appear in runner JSON
- not create duplicate final records
- not be silently ignored

## 12. Tests

Tests must not depend on live network calls.

Required tests:

- mocked page 1 JSON response with 50 rows
- mocked page 2 JSON response with 30 rows
- collector stops when row count is less than limit
- collector stops on empty page
- collector respects `max_pages`
- SSL/network failure is recorded clearly
- raw payload metadata includes page offset, limit, number, group ID
- JSON diagnostics reports rows detected and parseable rows
- JSON parser maps source fields to parser DTOs using `Decimal`
- normalizer handles JSON raw payloads idempotently
- runner `--collect-live` returns group JSON summary with all pages
- duplicate symbol detection across JSON pages
- existing HTML fixture multi-page behavior remains unchanged

Manual JSON fixtures saved from browser DevTools are acceptable for parser and diagnostics tests.

## 13. Acceptance Criteria

This phase is complete when:

- live JSON collector can fetch offset `0` and offset `50` with SSL verification enabled
- collector stores each JSON page as a separate `raw_payload`
- raw payload metadata includes page offset, page limit, page number, and pagination group
- collector stops safely when rows returned are less than limit or empty
- mocked tests cover pagination and error cases
- JSON diagnostics work without live network
- JSON payloads can be normalized without breaking HTML normalization
- runner `--collect-live` collects all pages, diagnoses them, normalizes passing pages, and returns a group summary
- repeated live/group runs do not duplicate instruments/latest_prices/price_bars
- manual fixture multi-page behavior still works
- no scheduler, API endpoint, or TradeHub integration is added

## 14. Out-of-Scope Items

This phase must not implement:

- scheduler or worker execution
- TradeHub integration
- public API endpoints
- aggressive crawling
- unknown endpoint discovery
- disabled SSL verification by default
- committed cookies, CSRF tokens, or private headers
- browser automation unless a later spec requires it
- company master-data normalization
- index parsing
- intraday bars

## 15. Codex Implementation Checklist

When implementing this phase, Codex should:

1. Read `AGENTS.md` and all BVC specs through this document.
2. Inspect the current BVC collector, client, config, fixture loader, parser, diagnostics, normalizer, runner, and tests.
3. Add JSON endpoint configuration without removing existing HTML/manual fixture support.
4. Reuse the existing HTTP client SSL verification and CA bundle behavior.
5. Add explicit JSON page request construction for `page[limit]` and `page[offset]`.
6. Add `max_pages` guard.
7. Store each JSON page in `raw_payloads` with `source_endpoint = "bvc_price_snapshot_json_page"`.
8. Add page metadata to each raw payload.
9. Add JSON parser or JSON-to-DTO adapter in parser modules.
10. Add JSON diagnostics.
11. Update normalizer dispatch for JSON vs HTML payloads.
12. Update runner `--collect-live` to process collected JSON pages as a group.
13. Add mocked HTTP tests for page 1/page 2/empty/error cases.
14. Add parser/diagnostics tests using saved JSON fixtures.
15. Add idempotency tests.
16. Run:

```bash
python3 -m compileall -q src tests
docker compose config
docker compose build api
docker compose run --rm api sh -c "pip install -e '.[dev]' && pytest"
```

17. Report:

```txt
endpoint configuration
pagination behavior
raw payload metadata
JSON diagnostics behavior
normalization behavior
runner collect-live output
commands passed
remaining blockers before scheduler
```
