# 13_BVC_DIAGNOSTICS_GROUP_TOTALS_FIX.md

# TradeHub Data - BVC Diagnostics Group Totals Fix

## 1. Purpose

This document defines a small diagnostics-only fix for the read-only BVC normalized data API.

Runtime validation showed that the API endpoints work, but the diagnostics summary can under-report raw payload coverage for multi-page BVC snapshots.

The goal is to make:

```txt
GET /api/v1/markets/bvc/diagnostics/summary
```

report group-level raw payload coverage for the latest BVC pagination group, without changing collectors, normalizers, schedulers, or API contracts.

## 2. Current Issue

The current diagnostics repository implementation finds the latest BVC `raw_payloads` row, then derives:

```txt
raw_payloads.latest_total_rows_detected
```

from that single row's metadata:

```txt
metadata.normalization_rows_found
or
metadata.page_size
```

This is correct for a single-page payload, but wrong for a multi-page pagination group.

Example:

```txt
page 1 page_size = 50
page 2 page_size = 30
group total = 80
latest raw payload = page 2
current diagnostics result = 30
expected diagnostics result = 80
```

The diagnostics response is safe and redacted, but its coverage count is not group-aware.

## 3. Scope

In scope:

- diagnostics summary repository logic
- diagnostics summary API behavior only through improved repository data
- tests for group-level raw payload totals

Out of scope:

- collector changes
- parser changes
- normalizer changes
- pipeline runner changes unless a test fixture helper needs no runtime behavior change
- live BVC fetch
- scheduler/workers
- TradeHub integration
- schema migration

## 4. Expected Behavior

For the latest BVC `pagination_group_id`, diagnostics summary should expose:

```txt
raw_payloads.latest_pagination_group_id
raw_payloads.latest_pages_found
raw_payloads.latest_total_rows_detected
raw_payloads.latest_collection_mode
raw_payloads.latest_collected_at
raw_payloads.latest_normalized_at
```

The fields should remain backward-compatible with `docs/12_NORMALIZED_DATA_API.md`.

### 4.1 Latest Group Selection

The latest group should be selected from BVC `raw_payloads` rows by:

```txt
DataSource.code = "bvc_prices"
metadata.pagination_group_id exists
latest collected_at / created_at
```

If no `pagination_group_id` exists, diagnostics may fall back to the current single-payload behavior.

### 4.2 Group Total Rows

For the selected group:

```txt
latest_total_rows_detected = sum(page row counts across all raw_payloads in the group)
```

Preferred metadata fields, in order:

```txt
metadata.normalization_rows_found
metadata.rows_detected
metadata.page_size
```

If a page has none of these fields, it should contribute `0` and the implementation should not expose raw payload content.

### 4.3 Pages Found

`latest_pages_found` should represent group-level coverage.

Preferred behavior:

```txt
latest_pages_found = count(distinct metadata.page_number) within group
```

Fallback behavior:

```txt
metadata.pagination_total_pages
```

If page numbers are missing, count raw payload rows in the group.

### 4.4 Latest Collected And Normalized Times

For the selected group:

```txt
latest_collected_at = max(raw_payloads.collected_at)
latest_normalized_at = max(metadata.normalized_at) when present
```

If `normalized_at` is not parseable as a timestamp, compare safely as a string only as a fallback or return the latest non-null value from the latest payload.

### 4.5 Collection Mode

`latest_collection_mode` should remain safe and should be derived from group metadata:

```txt
metadata.collection_mode
or metadata.loaded_by
```

Prefer the latest page's value if all pages agree. If pages differ, use the latest page value and do not expose full metadata.

## 5. Safety Requirements

The diagnostics summary must not expose:

```txt
raw_payloads.payload_text
raw_payloads.payload
raw HTML
raw JSON
private headers
cookies
tokens
authorization headers
raw fragments
stack traces
```

The response should remain an aggregate, redacted summary.

## 6. Implementation Notes

The fix should live in the diagnostics repository helper, currently:

```txt
src/tradehub_data/repositories/bvc_market.py
```

Recommended helper functions:

```txt
_latest_bvc_raw_payload(db)
_raw_payload_group_summary(db, pagination_group_id)
_metadata_int(metadata, keys)
_safe_latest_normalized_at(payloads)
```

Keep `src/tradehub_data/api/bvc_market.py` thin. The API route should continue calling the repository and returning the existing schema.

No schema migration is required because the needed values already exist in `raw_payloads.metadata`.

## 7. Tests Required

Update or add tests in:

```txt
tests/api/test_bvc_market.py
```

Required tests:

1. Seed two BVC raw payload rows with the same `pagination_group_id`:

```txt
page 1: page_number = 1, page_size or normalization_rows_found = 50
page 2: page_number = 2, page_size or normalization_rows_found = 30
pagination_total_pages = 2
```

Assert diagnostics summary returns:

```txt
latest_pagination_group_id = group id
latest_pages_found = 2
latest_total_rows_detected = 80
latest_collection_mode = expected safe mode
latest_collected_at = latest page collected_at
latest_normalized_at = latest group normalized_at
```

2. Add a regression test where the latest payload is page 2 with `30` rows and verify the API does not return `30` as the group total.

3. Keep the existing redaction assertions:

```txt
payload_text not exposed
raw JSON not exposed
cookie not exposed
private_headers not exposed
raw_fragment not exposed
```

4. Keep single-page fallback behavior covered, either through the existing diagnostics test or a small dedicated test.

## 8. Acceptance Criteria

The fix is complete when:

- diagnostics summary reports group-level totals for the latest BVC pagination group
- single-page diagnostics behavior still works
- response fields remain backward-compatible
- no raw payload content or private metadata is exposed
- all API tests pass
- full test suite passes

## 9. Out Of Scope

Do not implement:

- live BVC fetching
- collector changes
- parser changes
- normalizer changes
- scheduler/workers
- API contract expansion beyond corrected diagnostics values
- TradeHub integration
- schema migration
