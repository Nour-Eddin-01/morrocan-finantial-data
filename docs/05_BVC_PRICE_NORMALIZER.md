# 05_BVC_PRICE_NORMALIZER.md

# TradeHub Data - BVC Price Normalizer Specification

## 1. Purpose

This document defines the BVC price parser and normalizer for `tradehub-data`.

The goal is to transform stored raw BVC market listing payloads into normalized market price tables.

The required pipeline is:

```txt
raw_payloads
    -> BVC price parser
    -> parsed BVC price rows
    -> BVC price normalizer
    -> instruments
    -> latest_prices
    -> price_bars
    -> validation / normalization errors
```

This module must not fetch live BVC data. It only consumes raw payloads already stored by the BVC price collector or by the manual raw fixture loader.

The parser and database write logic must stay separate:

- parser: raw HTML -> parsed DTOs
- normalizer: parsed DTOs -> normalized database writes

## 2. Input Tables

### 2.1 `raw_payloads`

The normalizer reads BVC price payloads from `raw_payloads`.

Required filters:

```txt
payload_type = "bvc_price_snapshot"
status IN ("collected", "failed") only when explicitly retrying failed payloads
payload_text IS NOT NULL
```

Recommended default behavior:

```txt
process only status = "collected"
```

Required input columns:

```txt
id
source_id
ingestion_run_id
source_url
source_endpoint
payload_type
payload_text
payload_hash
collected_at
source_published_at
status
metadata
```

The normalizer must preserve traceability by writing `raw_payload_id` and `source_id` into normalized rows where supported.

### 2.2 `data_sources`

The normalizer uses the payload `source_id`.

Expected source:

```txt
code = "bvc_prices"
name = "Bourse de Casablanca Prices"
```

### 2.3 `exchanges`

The normalizer must ensure the Casablanca Stock Exchange exists before upserting instruments.

Expected exchange:

```txt
code = "BVC"
name = "Bourse de Casablanca"
country_code = "MA"
currency_code = "MAD"
timezone = "Africa/Casablanca"
website_url = "https://www.casablanca-bourse.com"
```

If a helper/seed already creates this exchange, reuse it. Do not create duplicate exchange rows.

### 2.4 Optional Retry Inputs

Manual commands may accept:

```txt
--raw-payload-id <uuid>
--payload-hash <sha256>
--limit <n>
--retry-failed
```

The default command must not process every historical payload blindly without an explicit limit or status filter.

## 3. Output Tables

### 3.1 `instruments`

The normalizer may upsert instruments when a parsed BVC row contains enough identity information.

Preferred match order:

1. `exchange_id + isin`, when ISIN is available.
2. `exchange_id + symbol`, when symbol is available.
3. Reject row as incomplete if neither ISIN nor symbol is available.

Required fields for new v0.1 instruments:

```txt
exchange_id
symbol
isin
name
instrument_type = "equity"
currency_code = "MAD"
source_id
raw_payload_id
is_active = true
last_seen_at
metadata
```

If `companies` are not available yet, `company_id` may remain null. Do not invent company records from price rows unless a later company/instrument normalizer specification allows it.

### 3.2 `latest_prices`

The normalizer must upsert one latest price row per instrument.

Constraint:

```txt
UNIQUE(instrument_id)
```

Required fields:

```txt
instrument_id
price
open_price
high_price
low_price
previous_close
change_value
change_percent
volume
traded_value
market_cap
price_timestamp
trading_date
source_id
raw_payload_id
data_quality_status
metadata
```

Update rule:

- If there is no `latest_prices` row for the instrument, insert one.
- If the parsed row has a newer or equal `price_timestamp`, update the row.
- If the parsed row has an older `price_timestamp`, do not overwrite the latest price. Record the skipped reason in normalizer result metadata.

### 3.3 `price_bars`

The normalizer must insert or update daily price bars for historical tracking.

Initial timeframe:

```txt
timeframe = "1d"
```

Idempotency constraint:

```txt
UNIQUE(instrument_id, timeframe, bar_timestamp)
```

Required fields:

```txt
instrument_id
timeframe
bar_timestamp
trading_date
open_price
high_price
low_price
close_price
volume
traded_value
number_of_trades
source_id
raw_payload_id
is_adjusted = false
data_quality_status
metadata
```

For BVC latest market listings, use:

```txt
close_price = parsed last price
bar_timestamp = parsed source timestamp if available, otherwise raw_payload.collected_at
trading_date = parsed trading date if available, otherwise date(raw_payload.collected_at in Africa/Casablanca)
```

Do not create intraday bars unless a future source confirms reliable intraday timestamps.

### 3.4 `normalization_errors`

The normalizer must create `normalization_errors` rows for parse or validation failures that prevent a row from being normalized.

Recommended fields:

```txt
raw_payload_id
ingestion_run_id
source_id
entity_type = "bvc_price_row"
error_type
error_message
raw_fragment
status = "open"
```

Error examples:

```txt
missing_instrument_identifier
missing_price
invalid_decimal
invalid_volume
invalid_ohlc
unexpected_table_shape
ambiguous_instrument_match
storage_error
```

### 3.5 `raw_payloads` Status

The current schema supports `raw_payloads.status` and `raw_payloads.error_message`.

The normalizer must update raw payload status:

```txt
normalized  -> all valid rows were normalized, with no blocking errors
failed      -> payload could not be parsed at all, or no usable rows were normalized
parsed      -> parser succeeded but normalization was not run, if parser-only mode is implemented
collected   -> not processed yet
ignored     -> explicitly skipped by operator/config
```

If some rows are normalized and some rows fail validation, use:

```txt
raw_payloads.status = "normalized"
raw_payloads.metadata.normalization_errors_count = <n>
```

and create `normalization_errors` records for the failed rows.

The schema does not currently have a dedicated `processed_at` or `normalized_at` column on `raw_payloads`. Store processing timestamps in `raw_payloads.metadata` until a migration adds a first-class column.

## 4. Parsing Rules

### 4.1 Parser Input

Parser input:

```python
raw_payload_id: UUID
source_url: str | None
payload_text: str
collected_at: datetime
source_published_at: datetime | None
```

The parser must not access the database and must not call external websites.

### 4.2 Parser Output DTO

Recommended parsed DTO:

```python
class BvcParsedPriceRow(BaseModel):
    raw_payload_id: UUID
    row_index: int
    source_symbol: str | None
    source_name: str | None
    isin: str | None
    last_price: Decimal | None
    open_price: Decimal | None
    high_price: Decimal | None
    low_price: Decimal | None
    previous_close: Decimal | None
    change_value: Decimal | None
    change_percent: Decimal | None
    volume: int | None
    traded_value: Decimal | None
    market_cap: Decimal | None
    number_of_trades: int | None
    source_timestamp: datetime | None
    trading_date: date | None
    raw_values: dict[str, str | None]
```

The parser should return:

```python
class BvcPriceParseResult(BaseModel):
    raw_payload_id: UUID
    rows: list[BvcParsedPriceRow]
    errors: list[BvcParseError]
    source_timestamp: datetime | None
    trading_date: date | None
```

### 4.3 HTML Table Detection

The parser should target the BVC market listing table shape from:

```txt
https://www.casablanca-bourse.com/fr/live-market/marche-actions-listing?amp=1
```

It must not depend on one brittle selector only.

Recommended approach:

1. Parse HTML with an HTML parser such as BeautifulSoup if available in dependencies.
2. Identify candidate tables by headers.
3. Normalize header text by lowercasing, trimming whitespace, removing repeated spaces, and removing accents only for matching.
4. Select the table containing a sufficient set of price headers.

Header aliases should be centralized in one parser module.

Expected header concepts:

```txt
instrument name
symbol
ISIN
reference price / previous close
open
last price
high
low
variation
volume
traded value
market capitalization
number of trades
source timestamp
```

Do not hardcode parser assumptions inside repository or normalizer modules.

### 4.4 Locale Number Parsing

Use `Decimal`, never `float`.

The parser must support Moroccan/French formats:

```txt
123,45
1 234,56
1.234,56
+0,94 %
-0,94 %
MAD
DH
-
N/A
```

Rules:

- Empty, dash, and `N/A` values become `None`.
- Strip percent signs for percentages.
- Strip currency labels for money fields.
- Remove grouping separators.
- Convert decimal comma to decimal point.
- Raise a parse error for unrecognized numeric values instead of silently returning zero.

### 4.5 Timestamp and Trading Date

Preferred source timestamp order:

1. Timestamp explicitly shown in the source payload.
2. `raw_payload.source_published_at`.
3. `raw_payload.collected_at`.

Preferred trading date order:

1. Trading date explicitly shown in the source payload.
2. Date part of source timestamp in `Africa/Casablanca`.
3. Date part of `raw_payload.collected_at` in `Africa/Casablanca`.

Do not invent market open or close times.

### 4.6 Symbol and Name Cleanup

The parser should preserve source values in `raw_values`.

The normalizer may derive:

```txt
symbol = uppercase trimmed source symbol
name = trimmed source instrument name
isin = uppercase trimmed ISIN
```

Do not guess a ticker from a company name unless a controlled mapping table exists.

## 5. Validation Rules

Validation must run before database writes for each parsed row.

### 5.1 Required Fields

A row is normalizable only if it has:

```txt
last_price
source_symbol or isin
source_name or source_symbol
trading_date
price_timestamp
```

If `last_price` is missing, create a validation error and do not write `latest_prices` or `price_bars` for that row.

If both `source_symbol` and `isin` are missing, create a validation error and do not upsert an instrument.

### 5.2 Decimal and Integer Rules

Reject rows with:

```txt
last_price < 0
open_price < 0
high_price < 0
low_price < 0
previous_close < 0
volume < 0
traded_value < 0
market_cap < 0
number_of_trades < 0
```

Zero volume is valid.

### 5.3 OHLC Rules

If all relevant fields are present:

```txt
high_price >= low_price
high_price >= open_price
high_price >= last_price
low_price <= open_price
low_price <= last_price
```

If OHLC values conflict, write the row with `data_quality_status = "suspect"` only if the critical latest price is valid and traceable. Otherwise reject the row and record a `normalization_errors` entry.

### 5.4 Data Quality Status

Use:

```txt
valid
suspect
missing
stale
```

Initial v0.1 rules:

- `valid`: required fields are present and validation checks pass.
- `suspect`: latest price is usable but secondary validation is inconsistent.
- `missing`: critical fields are missing; do not publish latest price or bar.
- `stale`: source timestamp is older than an operator-defined threshold, if configured later.

### 5.5 No Invented Values

The normalizer must not invent:

- prices
- volume
- traded value
- market cap
- ISIN
- symbol
- source timestamp

Fallback timestamps from the raw payload are allowed only for traceability and must be recorded in metadata:

```json
{
  "timestamp_source": "raw_payload.collected_at"
}
```

## 6. Idempotency Rules

Running the normalizer on the same raw payload multiple times must be safe.

### 6.1 Instruments

Use upsert semantics:

```txt
match by exchange_id + isin when available
else match by exchange_id + symbol
```

Do not create duplicate instruments for the same BVC security.

If an existing instrument has a different symbol for the same ISIN, do not silently overwrite. Record an `ambiguous_instrument_match` or `instrument_identity_conflict` error.

### 6.2 Latest Prices

Use upsert semantics on:

```txt
instrument_id
```

Repeated normalization of the same raw payload should update the same latest row with the same values and traceability.

Do not downgrade latest price to an older timestamp.

### 6.3 Price Bars

Use upsert semantics on:

```txt
instrument_id + timeframe + bar_timestamp
```

For the same raw payload and same parsed timestamp, the normalizer must not create duplicate bars.

If a same-key bar already exists with different values:

- update only when the incoming source is same or higher priority and the correction is traceable
- preserve `raw_payload_id` of the latest source used
- record correction metadata:

```json
{
  "corrected_from_raw_payload_id": "...",
  "correction_reason": "same source republished daily bar"
}
```

### 6.4 Raw Payload Status

Repeated successful runs should leave `raw_payloads.status = "normalized"` and should not create duplicate normalized rows.

Repeated failed runs may create duplicate `normalization_errors` unless an error de-duplication helper is implemented. Prefer de-duplication by:

```txt
raw_payload_id + row_index + error_type
```

if the schema/repository supports it later.

## 7. Error Handling

The normalizer must not silently ignore parsing or validation failures.

### 7.1 Payload-Level Errors

Payload-level errors mark the raw payload as `failed`.

Examples:

```txt
payload_text missing
HTML cannot be parsed
market listing table not found
no rows parsed
database transaction failed
```

Required behavior:

- create a `normalization_errors` record
- set `raw_payloads.status = "failed"`
- set `raw_payloads.error_message`
- return a failed normalizer result

### 7.2 Row-Level Errors

Row-level errors do not fail the whole payload if at least one row is normalized.

Required behavior:

- create `normalization_errors` entries for invalid rows
- continue processing valid rows
- include counts in result metadata
- mark payload as `normalized` with error count in metadata if at least one valid row was normalized

### 7.3 Transaction Boundaries

Recommended v0.1 transaction boundary:

```txt
one database transaction per raw payload
```

If the payload-level transaction fails, roll back all writes for that payload and mark the payload failure in a separate transaction if possible.

### 7.4 Normalizer Result

Recommended result DTO:

```python
class BvcPriceNormalizationResult(BaseModel):
    status: Literal["success", "partial_success", "failed", "skipped"]
    raw_payload_id: UUID | None
    rows_found: int
    rows_normalized: int
    rows_failed: int
    instruments_inserted: int
    instruments_updated: int
    latest_prices_upserted: int
    price_bars_inserted: int
    price_bars_updated: int
    errors_count: int
    message: str | None = None
```

## 8. Expected Folder Structure

Recommended implementation layout:

```txt
src/tradehub_data/
├── parsers/
│   └── bvc_prices/
│       ├── __init__.py
│       ├── html_parser.py
│       ├── models.py
│       ├── number_parsing.py
│       └── errors.py
│
├── normalizers/
│   └── bvc_prices/
│       ├── __init__.py
│       ├── normalizer.py
│       ├── models.py
│       ├── validation.py
│       └── errors.py
│
├── repositories/
│   ├── exchanges.py
│   ├── instruments.py
│   ├── prices.py
│   ├── raw_payloads.py
│   └── normalization_errors.py
│
└── core/
    └── decimal_parsing.py
```

Tests:

```txt
tests/
├── parsers/
│   └── test_bvc_price_parser.py
└── normalizers/
    └── test_bvc_price_normalizer.py
```

Fixtures:

```txt
fixtures/
└── bvc_prices/
    └── sample_market_listing.html
```

Keep parsing rules in parser modules. Keep database upsert logic in repository modules. Keep orchestration in the normalizer service.

## 9. Tests Required

Tests must not fetch live BVC data.

Use the existing sample fixture:

```txt
fixtures/bvc_prices/sample_market_listing.html
```

Minimum parser tests:

1. Parses the sample fixture into at least one parsed row.
2. Preserves raw cell values in `raw_values`.
3. Parses French/Moroccan decimals correctly:

```txt
123,45 -> Decimal("123.45")
1 234,56 -> Decimal("1234.56")
-0,94 % -> Decimal("-0.94")
```

4. Converts empty/dash values to `None`.
5. Raises or returns explicit parse errors for unrecognized numeric values.
6. Returns a payload-level error when no market listing table is found.

Minimum validation tests:

1. Rejects row with missing symbol and missing ISIN.
2. Rejects row with missing latest price.
3. Rejects negative price.
4. Marks inconsistent OHLC as `suspect` or rejects according to the implemented rule.
5. Does not invent missing financial values.

Minimum normalizer tests:

1. Reads a raw payload with `payload_type = "bvc_price_snapshot"`.
2. Creates or reuses the `BVC` exchange.
3. Upserts an instrument from a parsed row.
4. Upserts `latest_prices`.
5. Inserts a `price_bars` daily row.
6. Running the same raw payload twice does not duplicate price bars.
7. Running the same raw payload twice does not duplicate instruments.
8. Invalid rows create `normalization_errors`.
9. Payload-level parse failure sets `raw_payloads.status = "failed"`.
10. Successful normalization sets `raw_payloads.status = "normalized"`.
11. Partial row failures still normalize valid rows and record error count.
12. Older price timestamps do not overwrite newer `latest_prices`.

Use SQLite only if the repository code supports equivalent constraints in tests. Prefer PostgreSQL for integration tests when database-specific upsert behavior is used.

## 10. Manual Commands

### 10.1 Store a Local Raw Fixture

Use the existing raw fixture loader:

```bash
docker compose run --rm api python -m tradehub_data.collectors.bvc_prices.fixtures /app/fixtures/bvc_prices/sample_market_listing.html
```

With source URL:

```bash
docker compose run --rm api python -m tradehub_data.collectors.bvc_prices.fixtures /app/fixtures/bvc_prices/sample_market_listing.html --source-url "https://www.casablanca-bourse.com/fr/live-market/marche-actions-listing?amp=1"
```

### 10.2 Run the Normalizer Once

Future command:

```bash
docker compose run --rm api python -m tradehub_data.normalizers.bvc_prices.normalizer
```

Recommended options:

```bash
docker compose run --rm api python -m tradehub_data.normalizers.bvc_prices.normalizer --limit 10
docker compose run --rm api python -m tradehub_data.normalizers.bvc_prices.normalizer --raw-payload-id <uuid>
docker compose run --rm api python -m tradehub_data.normalizers.bvc_prices.normalizer --retry-failed --limit 5
```

The command should:

- load settings
- connect to the database
- select eligible raw payloads
- parse and normalize each payload once
- print JSON summary
- exit non-zero only when no requested payload could be normalized or when a fatal configuration/storage error occurs

### 10.3 Run Tests

```bash
docker compose run --rm api sh -c "pip install -e '.[dev]' && pytest"
```

Parser-only focused tests:

```bash
docker compose run --rm api sh -c "pip install -e '.[dev]' && pytest tests/parsers/test_bvc_price_parser.py"
```

Normalizer focused tests:

```bash
docker compose run --rm api sh -c "pip install -e '.[dev]' && pytest tests/normalizers/test_bvc_price_normalizer.py"
```

## 11. Out-of-Scope Items

Do not implement in this normalizer task:

- live BVC fetching
- collector URL discovery
- scheduler or worker daemon
- TradeHub integration
- public API endpoints
- index normalization
- company profile collection
- AMMC data
- market calendar table
- intraday bars
- technical indicators
- price adjustment for dividends, splits, or corporate actions
- automatic crawling of per-instrument pages
- Playwright/browser automation
- external vendor feeds

The normalizer must focus only on:

```txt
stored BVC raw payload -> parsed rows -> instruments/latest_prices/price_bars
```

## 12. Acceptance Criteria

The BVC price normalizer implementation is complete when:

- it reads stored BVC raw payloads without fetching live data
- parser logic is isolated from database writes
- it parses the sample fixture into structured rows
- it validates required identity and price fields
- it upserts instruments safely
- it upserts latest prices safely
- it inserts or updates daily price bars idempotently
- it records validation and parse errors in `normalization_errors`
- it updates `raw_payloads.status`
- running the same raw payload twice does not duplicate normalized rows
- tests pass without external network calls
- no scheduler, API, or TradeHub integration is added
