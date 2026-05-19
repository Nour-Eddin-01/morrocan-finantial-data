# 15_BVC_LIVE_MISSING_PRICE_REVIEW.md

# TradeHub Data - BVC Live Missing Price Review

## 1. Purpose

This document reviews the 9 `missing_price` row-level normalization errors from the first controlled BVC live JSON collection run after the HTTP timeout fix.

The goal is to classify the affected rows, decide a safe normalization policy, and keep scheduler approval blocked until the policy is implemented and tested.

This document is investigation-only. It does not add scheduler/workers or TradeHub integration.

## 2. Context

The live HTTP timeout blocker was fixed by adding the safe non-secret header:

```txt
Accept-Language: fr-FR,fr;q=0.9,en;q=0.8
```

The controlled live command succeeded at collection level:

```txt
pages_found = 2
pages_processed = 2
pagination_complete = true
total_rows_detected = 80
duplicate_symbols_count = 0
```

Normalization was partial:

```txt
total_rows_normalized = 71
row-level normalization errors = 9
all errors = missing_price
```

## 3. Latest Live Pagination Group

Latest live group inspected:

```txt
pagination_group_id = bvc_price_snapshot:live_json:33771eb2-42b7-4d1b-9e50-06c25ea8c0d6
```

Raw payload pages:

```txt
page 1 raw_payload_id = e72cca4b-684e-4519-a112-c1e21f938a31
page 1 page_size = 50
page 1 status = normalized

page 2 raw_payload_id = c79bcea5-c10b-4c33-84a7-0bcbc74a7f9b
page 2 page_size = 30
page 2 status = normalized
```

## 4. Affected Rows

Only safe fields are shown. Full raw JSON payloads are intentionally not included.

| Page | Row index | Symbol | Current stored name | JSON status | lastTradedPrice | coursCourant | Previous/reference price | Volume | Traded value | Trades | Source timestamp |
| --- | ---: | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 0 | AFM | AFMA | `N.T` | null | `1240.0000000000` | `1240.0000000000` | null | null | null | `2026-05-19T10:45:55+00:00` |
| 1 | 11 | NEJ | AUTO NEJMA | `N.T` | null | `4134.0000000000` | `4134.0000000000` | null | null | null | `2026-05-19T10:29:47+00:00` |
| 1 | 25 | CTM | CTM | `N.T` | null | `895.9000000000` | `895.9000000000` | null | null | null | `2026-05-19T10:46:11+00:00` |
| 1 | 26 | DRI | DARI COUSPATE | `N.T` | null | `4190.0000000000` | `4190.0000000000` | null | null | null | `2026-05-19T09:30:00+00:00` |
| 1 | 27 | DLM | DELATTRE LEVIVIER MAROC | `S` | null | `40.0000000000` | `40.0000000000` | null | null | null | `2026-05-19T08:10:00+00:00` |
| 1 | 29 | DIS | DIAC SALAF | `S` | null | `26.2500000000` | `26.2500000000` | null | null | null | `2026-05-19T08:10:00+00:00` |
| 1 | 49 | MLE | MAROC LEASING | `N.T` | null | `370.0000000000` | `370.0000000000` | null | null | null | `2026-05-19T09:57:41+00:00` |
| 2 | 12 | SAM | SAMIR | `S` | null | `127.8000000000` | `127.8000000000` | null | null | null | `2026-05-19T08:10:00+00:00` |
| 2 | 29 | ZDJ | ZELLIDJA S.A | `N.T` | null | `205.0000000000` | `205.0000000000` | null | null | null | `2026-05-19T10:49:48+00:00` |

Status field observed:

```txt
etatCotVal = N.T or S
```

Likely meaning:

```txt
N.T = not traded / no transaction during the session
S = suspended or stopped quotation state
```

The exact source semantics should be confirmed before production scheduler approval.

## 5. Comparison With Previous Manual Fixtures

All 9 symbols already have normalized records from the previous manual HTML fixture run dated `2026-05-18`.

| Symbol | Existing latest price date | Existing latest price | Existing latest bar date |
| --- | --- | ---: | --- |
| AFM | `2026-05-18` | `1240.000000` | `2026-05-18` |
| CTM | `2026-05-18` | `895.900000` | `2026-05-18` |
| DIS | `2026-05-18` | `26.250000` | `2026-05-18` |
| DLM | `2026-05-18` | `40.000000` | `2026-05-18` |
| DRI | `2026-05-18` | `4190.000000` | `2026-05-18` |
| MLE | `2026-05-18` | `370.000000` | `2026-05-18` |
| NEJ | `2026-05-18` | `4134.000000` | `2026-05-18` |
| SAM | `2026-05-18` | `127.800000` | `2026-05-18` |
| ZDJ | `2026-05-18` | `205.000000` | `2026-05-18` |

The values in `coursCourant` from the live JSON match the existing latest/reference prices for these symbols.

This suggests the 9 rows are not malformed source rows. They are valid listed instruments in a not-traded or suspended state where the JSON field `lastTradedPrice` is null while `coursCourant` remains populated.

## 6. Root Cause Classification

Primary classification:

```txt
parser mapping issue
```

The current JSON parser maps:

```txt
last_price = first of lastTradedPrice, coursCourant, closingPrice
```

But the helper currently stops when the first alias key exists, even if that value is null.

For these rows:

```txt
lastTradedPrice = null
coursCourant = populated
```

Therefore the parser records:

```txt
last_price = null
```

and the normalizer correctly raises:

```txt
missing latest price
```

Secondary classification:

```txt
valid listed instruments with no current trade/quote activity
```

The source still provides a display/reference price through `coursCourant`, and the previous fixture normalized the same prices.

## 7. Expected Handling Policy

Recommended policy:

1. JSON parser should skip null/empty alias values and continue to the next alias.
2. `last_price` should use `coursCourant` when `lastTradedPrice` is null and `coursCourant` is present.
3. Parser should expose the source status field, at minimum:

```txt
etatCotVal
```

4. Normalizer should store source status in metadata for latest prices and price bars.
5. Instruments should still be upserted for valid rows with a symbol/name even when no trade occurred.
6. `latest_prices` may be updated when `coursCourant` is present because it is the source's displayed current/reference price.
7. `price_bars` may be created only with clear metadata marking the source status and timestamp policy.
8. If both `lastTradedPrice` and `coursCourant` are missing, then:

```txt
instrument may be upserted if identity is valid
latest_prices must not be overwritten
price_bars must not be created
normalization_errors should record missing_price
```

9. Diagnostics/runner may report `partial_success` only for truly unpriceable rows. Rows with populated `coursCourant` should not fail as `missing_price`.

## 8. Code Changes Needed

Yes, code changes are needed before scheduler approval.

Required changes:

- update JSON parser alias selection to skip null/empty values
- add parser tests proving:
  - `lastTradedPrice = null`
  - `coursCourant = populated`
  - parsed `last_price = coursCourant`
- add regression tests using representative `N.T` and `S` rows
- preserve Decimal parsing
- do not use float
- keep full raw JSON out of public API responses

Recommended follow-up changes:

- add `etatCotVal` or equivalent source status to parser DTO metadata or raw values
- include source status in latest price/bar metadata
- add normalizer test proving no duplicate records and successful normalization for these rows

No schema migration is required for the parser fix because source status can initially live in metadata/raw values.

## 9. Scheduler Status

Scheduler remains blocked.

Blocking reasons:

- live collection now works, but live normalization is still partial
- 9 rows failed because the JSON parser does not fall back from null `lastTradedPrice` to populated `coursCourant`
- source status handling for `N.T` and `S` rows must be explicit and tested
- a second controlled live validation should confirm:

```txt
pages_found = 2
total_rows_detected = 80
total_rows_normalized = 80
missing_price errors = 0 for rows with coursCourant
duplicate_symbols_count = 0
idempotent second run
```

## 10. Out Of Scope

Do not implement in this review:

- scheduler/workers
- TradeHub integration
- new data sources
- live scraping beyond explicit controlled validation
- SSL bypass
- cookies, CSRF tokens, WAF tokens, session IDs, or authorization headers
- public exposure of raw payload JSON/HTML

## 11. Codex Implementation Checklist

For the next implementation task:

1. Read `AGENTS.md`.
2. Read this document.
3. Read the JSON parser and normalizer validation code.
4. Implement only the parser/status handling fix.
5. Add tests for null alias fallback and `coursCourant`.
6. Add tests for `N.T` and `S` rows.
7. Run the full test suite.
8. Run one controlled `--collect-live` validation with CA bundle.
9. Do not approve scheduler until live normalization reaches the acceptance criteria above.
