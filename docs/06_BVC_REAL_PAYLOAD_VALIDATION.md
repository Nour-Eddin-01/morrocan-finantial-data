# 06_BVC_REAL_PAYLOAD_VALIDATION.md

# TradeHub Data - BVC Real Payload Validation Specification

## 1. Purpose

This document defines the validation phase for real BVC production HTML payloads before adding scheduler, API, or TradeHub integration.

The current BVC flow works with local fixtures:

```txt
manual fixture / collector
    -> raw_payloads
    -> BVC price parser
    -> BVC price normalizer
    -> instruments / latest_prices / price_bars
```

This phase verifies that the parser and normalizer handle real Casablanca Stock Exchange market listing HTML safely and transparently.

The main goal is not to fetch more data. The goal is to prove that real production payload shapes are understood before normalized data is trusted downstream.

## 2. Why This Phase Exists

The BVC collector, fixture loader, parser, and normalizer are already separated according to the project rule:

```txt
collect raw data first, normalize later
```

However, production BVC HTML coverage is still missing. The parser currently supports the known fixture table shape and header aliases, but real pages may differ by:

- table structure
- header names
- hidden columns
- localized labels
- date and timestamp placement
- number formatting
- empty-value conventions
- server-side rendering differences
- maintenance or error pages

Live Docker fetching may also fail when SSL verification is enabled if the BVC server or local environment requires a trusted intermediate certificate bundle. SSL verification must remain enabled by default. This phase must support safe manual validation without weakening TLS defaults.

No scheduler or TradeHub integration should depend on BVC production data until this validation phase passes.

## 3. Real Payload Acquisition Options

Use one of these safe acquisition paths.

### 3.1 Operator-Provided HTML Fixture

An operator may manually download the BVC market listing HTML from a browser or approved internal tool and place it under a local fixture path.

Recommended location:

```txt
fixtures/bvc_prices/real/
```

Recommended filename pattern:

```txt
bvc_market_listing_YYYYMMDD_HHMM.html
```

Real fixtures may be committed only if they contain public source data and are acceptable for repository storage. If there is any uncertainty, keep them local and untracked.

### 3.2 Manual Raw Payload Loader

The existing fixture loader should be used to store manually downloaded HTML in `raw_payloads` with:

```txt
payload_type = "bvc_price_snapshot"
status = "collected"
payload_text = full HTML body
source_url = original BVC page URL or manual-fixture URL
```

This preserves the normal pipeline and traceability without live fetching.

### 3.3 Collector With Trusted CA Bundle

If live collection is needed for validation, use the collector only with SSL verification enabled.

Default:

```env
BVC_PRICE_COLLECTOR_VERIFY_SSL=true
```

If Docker cannot verify the BVC certificate chain, provide a trusted CA or intermediate bundle:

```env
BVC_PRICE_COLLECTOR_CA_BUNDLE_PATH=/path/in/container/bvc-ca-bundle.pem
```

Do not set SSL verification to false in committed defaults. A development-only bypass, if ever implemented, must remain disabled by default and clearly documented as unsafe for production validation.

## 4. Manual Fixture Workflow

1. Download the BVC market listing HTML manually from the official market listing page.
2. Save the file locally, for example:

```txt
fixtures/bvc_prices/real/bvc_market_listing_20260515_1200.html
```

3. Store it as a raw payload:

```bash
docker compose run --rm api python -m tradehub_data.collectors.bvc_prices.fixtures /app/fixtures/bvc_prices/real/bvc_market_listing_20260515_1200.html
```

4. Record the returned `raw_payload_id`.
5. Run parser diagnostics before normalization.
6. Run a safe normalization trial only after diagnostics show the expected market listing table and mapped fields.

The fixture loader must not parse or normalize the HTML. It only stores the raw payload.

## 5. Parser Diagnostic Command

Add a parser diagnostic command before expanding production use.

Recommended command:

```bash
docker compose run --rm api python -m tradehub_data.parsers.bvc_prices.diagnostics /app/fixtures/bvc_prices/real/bvc_market_listing_20260515_1200.html
```

Optional raw payload form:

```bash
docker compose run --rm api python -m tradehub_data.parsers.bvc_prices.diagnostics --raw-payload-id <uuid>
```

The command must not write normalized data.

The diagnostic output should be JSON by default and include:

```txt
file_path or raw_payload_id
payload_hash when available
tables_found
candidate_tables
headers_detected
normalized_headers
rows_detected
mapped_fields
unmapped_headers
missing_required_fields
parseable_rows_count
row_parse_errors_count
row_parse_errors_sample
selected_table_index
selected_table_reason
status
```

Safe human-readable output may be added, but JSON should remain available for repeatable review.

## 6. Header/Table-Shape Validation Rules

Parser diagnostics must inspect all HTML tables before choosing a market listing table.

For each table, report:

- table index
- raw headers
- normalized headers
- row count
- mapped header fields
- unmapped headers
- whether required market fields are present
- why the table was accepted or rejected

Minimum required fields for a candidate BVC price table:

```txt
last_price
volume
```

Required fields for a row to normalize:

```txt
last_price
source_timestamp
trading_date
source_symbol or isin
source_name or source_symbol
```

Header aliases must be expanded only after observing real payloads. Do not hardcode a production-only assumption from one page if the meaning is ambiguous.

Unknown headers must be reported. They must not be silently discarded from diagnostics. The parser may ignore unknown headers for row DTOs, but diagnostics must expose them so aliases can be reviewed.

Rows with invalid numeric values must produce explicit parse errors. Missing optional values such as open price, high price, low price, traded value, market cap, or number of trades must remain `None`; do not invent or derive them.

## 7. Safe Normalization Trial Workflow

Only run the normalizer after parser diagnostics have been reviewed.

Recommended command:

```bash
docker compose run --rm api python -m tradehub_data.normalizers.bvc_prices.normalizer --raw-payload-id <uuid>
```

Validation trial requirements:

- process one payload at a time by explicit `raw_payload_id`
- preserve `raw_payload_id` and `source_id` traceability
- update `raw_payloads.status` according to the normalizer result
- create `normalization_errors` for invalid rows
- keep `latest_prices` timestamp-safe
- keep `price_bars` idempotent
- use `Decimal` for all financial values
- never normalize unknown table shapes silently

If diagnostics show unexpected headers or multiple plausible market tables, update parser diagnostics and tests first. Do not broaden normalization behavior blindly.

## 8. Acceptance Criteria

This phase is complete when:

- at least one operator-provided real BVC market listing payload can be stored in `raw_payloads`
- parser diagnostics report all tables, headers, mapped fields, unmapped fields, detected rows, and parse errors
- parser tests cover any new real header aliases using saved or anonymized real-like fixtures
- the parser can identify the intended market listing table without selecting unrelated tables
- invalid rows produce explicit parse or normalization errors
- a normalization trial on a known real payload writes only expected `instruments`, `latest_prices`, and `price_bars`
- repeating the same normalization trial does not duplicate normalized records
- older payloads do not overwrite newer `latest_prices`
- SSL verification remains enabled by default
- a CA bundle workflow is documented and tested at configuration level if needed
- no scheduler, API endpoint, or TradeHub integration depends on this data yet

## 9. Out-of-Scope Items

This phase must not implement:

- scheduler or worker execution
- TradeHub integration
- API endpoints
- aggressive live scraping
- SSL verification disabled by default
- bypassing source protections
- parser assumptions based on one unreviewed production page
- invented financial values
- company master-data normalization
- index parsing
- intraday bars beyond the current daily `1d` price bar behavior
- public exposure of raw payloads

## 10. Codex Implementation Checklist

When implementing this phase, follow this checklist:

1. Read `AGENTS.md`, `docs/00_PROJECT_OVERVIEW.md`, `docs/01_ARCHITECTURE.md`, `docs/02_DATABASE_SCHEMA.md`, `docs/03_SOURCES_AND_COLLECTORS.md`, `docs/04_BVC_PRICE_COLLECTOR.md`, and `docs/05_BVC_PRICE_NORMALIZER.md`.
2. Inspect the current BVC collector, fixture loader, parser, normalizer, and repository helpers.
3. Add parser diagnostics without changing normalizer write behavior.
4. Keep diagnostics read-only unless explicitly storing a fixture through the existing fixture loader.
5. Support diagnostics from both file path and `raw_payload_id`.
6. Report table shape details clearly: tables found, headers detected, mapped fields, unmapped fields, row counts, and parse errors.
7. Add anonymized or real-like fixtures for new table/header shapes.
8. Add parser tests for every new header alias and table selection rule.
9. Add normalizer tests only for validated real-like payload handoff behavior.
10. Keep SSL verification enabled by default and document the CA bundle path workflow.
11. Do not add scheduler, workers, API endpoints, or TradeHub integration.
12. Run:

```bash
python3 -m compileall -q src tests
docker compose config
docker compose run --rm api sh -c "pip install -e '.[dev]' && pytest"
```

13. Report:

```txt
real fixture source used
diagnostic fields added
headers/table shapes observed
parser aliases added
tests added
commands passed
remaining unknowns before scheduler work
```
