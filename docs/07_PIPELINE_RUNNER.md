# 07_PIPELINE_RUNNER.md

# TradeHub Data - Manual BVC Pipeline Runner Specification

## 1. Purpose

This document defines a manual BVC pipeline runner for `tradehub-data`.

The runner connects existing, already-scoped components into one manual command:

```txt
raw payload source
    -> raw_payloads
    -> parser diagnostics
    -> BVC price normalizer
    -> JSON pipeline summary
```

The runner must not introduce new parsing, collection, or normalization rules. It is an orchestration layer over the existing BVC collector, fixture loader, parser diagnostics, and normalizer.

## 2. Why This Comes Before Scheduler

The BVC collector, fixture loader, parser diagnostics, and normalizer now work independently. A real BVC market listing HTML payload has been validated and normalized successfully:

```txt
50 instruments inserted
50 latest_prices inserted
50 price_bars inserted
second run created no duplicates
```

Before adding scheduler or worker execution, the project needs a repeatable manual pipeline command that proves the full sequence is safe.

This phase exists to verify:

- raw data is always stored before normalization
- diagnostics are always run before normalization
- failed diagnostics block normalization
- the normalizer remains idempotent
- final command output is easy to inspect
- operators can test a payload without scheduler side effects

Scheduler work should only begin after the manual runner has clear behavior for success, partial success, failure, and repeated runs.

## 3. Supported Input Modes

The runner should support three input modes.

### 3.1 Existing Raw Payload

Use an existing `raw_payloads.id`.

Example:

```bash
docker compose run --rm api python -m tradehub_data.pipelines.bvc_prices.runner --raw-payload-id 7c8cf106-b5c3-43d9-8532-be53d4250705
```

Behavior:

- read the existing `raw_payloads` row
- run diagnostics from `raw_payload.payload_text`
- normalize only if diagnostics pass
- preserve the existing `raw_payload_id` and `source_id`

### 3.2 Local HTML Fixture Path

Use a local HTML file path inside the container.

Example:

```bash
docker compose run --rm api python -m tradehub_data.pipelines.bvc_prices.runner --fixture-path /app/fixtures/bvc_prices/real/bvc_market_listing_20260515_1200.html
```

Behavior:

- store the file through the existing fixture-loader behavior
- create or reuse a `raw_payloads` row based on payload hash
- run diagnostics against the stored raw payload
- normalize only if diagnostics pass
- return the stored `raw_payload_id`

This mode must not bypass raw storage by passing fixture text directly into the normalizer.

### 3.3 Existing Live Collector Configuration

Live collection may be supported only through the existing BVC collector configuration.

Example:

```bash
docker compose run --rm api python -m tradehub_data.pipelines.bvc_prices.runner --collect-live
```

Behavior:

- run the existing BVC collector once
- use raw payload IDs returned by the collector or ingestion run metadata
- run diagnostics for each collected or reused raw payload
- normalize only payloads whose diagnostics pass

Rules:

- SSL verification must remain enabled by default
- CA bundle behavior must use `BVC_PRICE_COLLECTOR_CA_BUNDLE_PATH`
- the runner must not add a second HTTP client
- live fetching must not happen unless `--collect-live` is explicitly passed

## 4. Pipeline Flow

The runner flow must be:

```txt
1. Resolve input mode
2. Get or create raw_payload_id
3. Load raw payload by ID
4. Run parser diagnostics
5. If diagnostics fail: stop before normalization
6. Run normalizer for the raw_payload_id
7. Reload final raw_payload status
8. Print JSON summary
```

Required boundaries:

- fixture loading belongs to the existing fixture loader
- live collection belongs to the existing BVC collector
- table/header inspection belongs to parser diagnostics
- database writes to normalized tables belong to the normalizer
- the runner only coordinates these steps

The runner must not duplicate parser logic or normalizer logic.

## 5. JSON Output Contract

The runner must print JSON by default.

Minimum output fields:

```json
{
  "status": "success",
  "mode": "raw_payload_id",
  "raw_payload_id": "7c8cf106-b5c3-43d9-8532-be53d4250705",
  "source_id": "78d0d120-4b73-4d30-8f42-f047f8191132",
  "payload_hash": "sha256...",
  "diagnostics_status": "success",
  "rows_detected": 50,
  "rows_parseable": 50,
  "row_parse_errors_count": 0,
  "normalization_status": "success",
  "rows_normalized": 50,
  "rows_failed": 0,
  "instruments_inserted": 50,
  "instruments_updated": 0,
  "latest_prices_inserted": 50,
  "latest_prices_updated": 0,
  "price_bars_inserted": 50,
  "price_bars_updated": 0,
  "errors_count": 0,
  "final_raw_payload_status": "normalized",
  "message": null
}
```

Allowed top-level statuses:

```txt
success
partial_success
failed
skipped
```

For multi-payload live collection mode, the runner may return:

```json
{
  "status": "partial_success",
  "mode": "collect_live",
  "payloads_found": 2,
  "payloads_processed": 1,
  "payloads_failed": 1,
  "results": []
}
```

Each item in `results` must follow the single-payload output contract.

## 6. Failure Behavior

The runner must fail safely.

### 6.1 Invalid Arguments

If the user provides more than one input mode, fail before any work starts.

Allowed modes are mutually exclusive:

```txt
--raw-payload-id
--fixture-path
--collect-live
```

### 6.2 Missing Raw Payload

If `--raw-payload-id` is not found:

- do not run diagnostics
- do not run normalizer
- return `status = "failed"`
- include a clear message

### 6.3 Missing Payload Text

If the raw payload has no `payload_text`:

- diagnostics fail
- normalization is skipped
- raw payload status must not be silently changed by the runner

### 6.4 Diagnostics Failure

If diagnostics return `status = "failed"`:

- do not run the normalizer
- return `normalization_status = "skipped"`
- include diagnostics fields and message

Diagnostics `partial_success` may be handled conservatively:

- default behavior should skip normalization unless explicitly allowed by a future flag
- no such permissive flag should be added in the first implementation unless requested

### 6.5 Normalization Failure

If the normalizer returns `failed`:

- preserve normalizer-created errors and raw payload status
- return `status = "failed"`
- include `errors_count` and `final_raw_payload_status`

### 6.6 Live Collector Failure

If `--collect-live` is used and the collector fails:

- report collector status and error message
- do not fabricate a raw payload ID
- do not run diagnostics or normalization without a raw payload

## 7. Idempotency Expectations

The runner must be safe to run repeatedly on the same payload.

Expected repeated-run behavior:

- raw payload is reused by hash for fixture mode
- parser diagnostics can run any number of times
- normalizer does not duplicate instruments
- normalizer does not duplicate latest_prices
- normalizer does not duplicate price_bars
- older timestamps do not overwrite newer latest prices
- repeated normalization may report updates or no-op behavior according to repository logic

The runner should report inserted/updated counts from the normalizer without hiding no-op results.

## 8. Command Examples

### 8.1 Run From Existing Raw Payload

```bash
docker compose run --rm api python -m tradehub_data.pipelines.bvc_prices.runner --raw-payload-id 7c8cf106-b5c3-43d9-8532-be53d4250705
```

### 8.2 Run From Real Fixture

```bash
docker compose build api
docker compose run --rm api python -m tradehub_data.pipelines.bvc_prices.runner --fixture-path /app/fixtures/bvc_prices/real/bvc_market_listing_20260515_1200.html
```

The `docker compose build api` step is needed when the Compose setup copies fixtures into the image rather than bind-mounting the repository.

### 8.3 Run Diagnostics Only

The runner does not replace diagnostics-only validation.

```bash
docker compose run --rm api python -m tradehub_data.parsers.bvc_prices.diagnostics /app/fixtures/bvc_prices/real/bvc_market_listing_20260515_1200.html
```

### 8.4 Run Normalizer Only

The runner does not replace direct normalizer execution.

```bash
docker compose run --rm api python -m tradehub_data.normalizers.bvc_prices.normalizer --raw-payload-id 7c8cf106-b5c3-43d9-8532-be53d4250705
```

### 8.5 Run Existing Collector Only

The runner does not replace direct collector execution.

```bash
docker compose run --rm api python -m tradehub_data.collectors.bvc_prices.collector
```

## 9. Acceptance Criteria

The manual BVC pipeline runner is complete when:

- it supports `--raw-payload-id`
- it supports `--fixture-path`
- it optionally supports `--collect-live` using only the existing collector
- it runs parser diagnostics before normalization
- it does not normalize when diagnostics fail
- it produces JSON output matching the contract
- it preserves `raw_payload_id` and `source_id` traceability
- it reuses existing fixture loader behavior for local HTML files
- it reuses existing diagnostics behavior for parser inspection
- it reuses existing normalizer behavior for database writes
- it is idempotent for repeated runs on the same real fixture
- tests cover success, diagnostics failure, invalid arguments, missing raw payload, and repeated runs
- no scheduler, worker, API endpoint, or TradeHub integration is added

Required implementation verification:

```bash
python3 -m compileall -q src tests
docker compose config
docker compose build api
docker compose run --rm api sh -c "pip install -e '.[dev]' && pytest"
docker compose run --rm api python -m tradehub_data.pipelines.bvc_prices.runner --fixture-path /app/fixtures/bvc_prices/real/bvc_market_listing_20260515_1200.html
docker compose run --rm api python -m tradehub_data.pipelines.bvc_prices.runner --fixture-path /app/fixtures/bvc_prices/real/bvc_market_listing_20260515_1200.html
```

The second runner command verifies idempotency.

## 10. Out-of-Scope Items

This phase must not implement:

- scheduler or worker execution
- API endpoints
- TradeHub integration
- new live-fetching logic
- disabled SSL verification by default
- aggressive scraping
- parser rule changes unless required by existing diagnostics
- normalizer behavior changes unless required for runner correctness
- public raw payload exposure
- company master-data normalization
- index normalization
- pagination handling beyond existing raw payloads

## 11. Codex Implementation Checklist

When implementing this runner, Codex should:

1. Read `AGENTS.md` and all BVC pipeline specs through this document.
2. Inspect the existing BVC collector, fixture loader, diagnostics command, normalizer, and repositories.
3. Create a small runner module, recommended path:

```txt
src/tradehub_data/pipelines/bvc_prices/runner.py
```

4. Add package `__init__.py` files only where needed.
5. Keep the runner as orchestration code only.
6. Enforce exactly one input mode.
7. For `--fixture-path`, call or reuse existing fixture-loader logic to create/reuse a raw payload.
8. For `--raw-payload-id`, load the existing raw payload and do not duplicate it.
9. For `--collect-live`, call the existing collector only if this mode is included.
10. Run diagnostics before normalization.
11. Block normalization when diagnostics fail.
12. Call the existing normalizer by `raw_payload_id`.
13. Reload final raw payload state after normalization.
14. Return a clear JSON summary.
15. Add tests for success, failure, and idempotency.
16. Do not add scheduler, workers, API endpoints, or TradeHub integration.
17. Run the required verification commands.
18. Report:

```txt
files created/modified
input modes implemented
JSON fields implemented
diagnostics gate behavior
idempotency result
commands passed
remaining work before scheduler
```
