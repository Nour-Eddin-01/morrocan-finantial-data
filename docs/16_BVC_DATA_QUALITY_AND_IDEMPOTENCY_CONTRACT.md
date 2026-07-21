# 16_BVC_DATA_QUALITY_AND_IDEMPOTENCY_CONTRACT.md

# TradeHub Data — BVC Data Quality and Idempotency Contract

## Purpose and authority

This document defines the canonical data-quality, idempotency, raw-audit, pipeline-state, freshness, and readiness rules for the BVC price vertical slice.

It is a specification for later implementation. It does not implement collectors, parsers, normalizers, repositories, migrations, API endpoints, schedulers, or workers.

For the BVC price vertical slice, this contract supersedes conflicting or ambiguous guidance in:

- `docs/02_DATABASE_SCHEMA.md`
- `docs/05_BVC_PRICE_NORMALIZER.md`
- `docs/09_BVC_MULTIPAGE_COLLECTION.md`
- `docs/10_BVC_LIVE_MULTIPAGE_COLLECTOR.md`
- `docs/12_NORMALIZED_DATA_API.md`

The core architecture remains:

```txt
collect raw data first, normalize later
```

The following words are normative:

- **MUST**: required for conformance.
- **MUST NOT**: prohibited.
- **SHOULD**: expected unless a documented reason justifies an exception.
- **MAY**: optional behavior that must still satisfy all invariants.

This contract does not assign undocumented financial meaning to source fields or status codes. Where source confirmation is missing, the safe storage and publication rule is decided, while the business meaning remains an explicit open question.

## Shared terminology

| Term | Meaning |
|---|---|
| Raw content | Immutable bytes received from a source, identified by an exact body hash and stored independently of when or why they were received |
| Collection occurrence | One request attempt and its transport/response outcome, including run, page, timestamps, status, and safe headers; it may reference already-known raw content |
| Logical page | One required page position in a pagination group; it may have several retry occurrences but exactly zero or one selected successful occurrence |
| Processing attempt | An immutable parser/normalizer execution against content in a declared occurrence/group/rule-version context |
| Data page | A structurally valid page containing one or more source market rows |
| Terminal sentinel | A valid later response that contains zero rows and proves pagination ended; it is not a market-data page and is not normalized |
| Pagination group | All occurrences/pages belonging to one logical market snapshot collection attempt |
| Accepted group | A production-eligible group with proven pagination and instrument coverage, successful processing of every data page, no blocking quality conflict, and completed atomic publication as trusted current data |
| Publication scope | The one current-snapshot namespace `(exchange_id, dataset_code, publication_channel)`; for this slice the production key is `(BVC, bvc_equity_prices, production)` |
| Material fields | Fields whose change alters the canonical financial or identity state, excluding audit timestamps and counters |
| Direct source timestamp | A timezone-aware timestamp explicitly supplied by the source for the row/value |
| Fallback timestamp | A source publication timestamp or collection-response timestamp used because no direct source timestamp exists |
| Scheduler acceptance | Eligibility of a collected group to be promoted as trusted data by future automation; it does not mean permission to retry a failed collection |

### Canonical material-fingerprint protocol

Every material fingerprint named by this contract MUST use a stored algorithm identifier and this common canonical protocol. The initial identifiers are `bvc-latest-price-material-v1` and `bvc-daily-bar-material-v1`; the corresponding ordered field lists are defined in sections 2 and 3. Implementations MUST NOT hash an ORM object's incidental serialization.

For each ordered field, construct a JSON array entry `[field_name, type_tag, canonical_value]`. The closed type tags and values are:

- `null`: JSON `null`; null is distinct from an empty string, zero, and a missing field, and every listed field is always present;
- `decimal`: a finite exact `Decimal` rendered in plain base-10 notation, with no exponent or leading plus, trailing fractional zeros and a trailing decimal point removed, and every signed zero rendered as `"0"`;
- `integer`: a base-10 string with no leading plus or redundant leading zeros;
- `boolean`: JSON `true` or `false`;
- `date`: canonical `YYYY-MM-DD`;
- `timestamp`: the instant converted to UTC and rendered as `YYYY-MM-DDTHH:MM:SS.ffffffZ` at PostgreSQL microsecond precision; a timestamp with nonzero excess precision is rejected before fingerprinting;
- `uuid`: a parsed UUID rendered as 36 lowercase hexadecimal characters with hyphens in `8-4-4-4-12` form; accepted equivalent input spellings are parsed and re-rendered, and invalid UUID text is rejected;
- `string`: the validated canonical string required by that field's rule, without another locale-dependent transformation;
- `string_set`: unique canonical strings sorted by UTF-8 byte order and represented as a JSON array.

Each algorithm below declares the one non-null tag for every field. A nullable field uses tag `null` and value null when absent, otherwise exactly its declared tag; substituting another tag is invalid even if its printed value looks similar. Non-null fields reject null. No implementation may infer tags from runtime Python/ORM types.

Wrap the entries as `[algorithm_identifier, entries]`, serialize them as canonical UTF-8 JSON using RFC 8785 string/array/boolean/null rules with no insignificant whitespace, and store lowercase hexadecimal SHA-256. Because every financial number is encoded from `Decimal` as a string before JSON serialization, this protocol never invokes binary floating-point formatting. Equivalent Decimal scales (`1.0`, `1.00`), equivalent timezone offsets for the same instant, and input order of reason-code sets produce the same digest; null versus blank, a changed material value, or a changed algorithm version does not. The algorithm identifier and digest MUST be stored together. A future field-list or canonicalization change requires a new identifier and MUST NOT silently reinterpret an old digest.

## 1. Instrument merge policy

### 1.1 Decision

Instrument updates MUST use field-aware merge rules. A repository MUST NOT assign every incoming field blindly.

Required principle:

```txt
A weaker row must not erase a previously known valid value.
```

#### Identity matching

For BVC instruments, identity matching MUST follow this sequence:

1. Normalize `exchange_id`, source symbol, and ISIN using the canonical rules below.
2. When both symbol and ISIN are present, look up both keys.
3. If both keys resolve to the same instrument, that instrument is the match.
4. If one key resolves and the other is unused, the resolved instrument is the candidate and a valid missing canonical field MUST be enriched under the merge table.
5. If symbol and ISIN resolve to different instruments, the row has a blocking `instrument_identity_conflict` and MUST NOT update an instrument, latest price, or bar.
6. When only an ISIN is present and uniquely resolves, it MUST match that existing instrument; it MUST NOT create a new instrument because `Instrument.symbol` is required.
7. When only a source-provided symbol is present, match by `(exchange_id, symbol)`.
8. An ISIN, company name, record ID, or URL fragment MUST NOT be substituted as a canonical symbol.

A new instrument requires a valid, nonblank, source-provided symbol. ISIN remains optional.

Canonical identity syntax for this contract is deterministic:

- symbol: Unicode NFKC, trim surrounding whitespace, uppercase, reject internal whitespace/control characters, retain non-whitespace punctuation, and require 1–30 characters;
- ISIN: Unicode NFKC, trim, uppercase, and require exactly 12 ASCII alphanumeric characters with the ISO-style shape `[A-Z]{2}[A-Z0-9]{9}[0-9]`;
- blank values after normalization are missing, not identity keys;
- checksum validation is not required until the authoritative checksum policy is selected; a syntactically valid but checksum-unverified ISIN remains labeled as such.

Canonical stored symbol/ISIN values MUST use these normalized forms. Raw tokens remain immutable evidence.

#### Deterministic field precedence

| Field | Merge rule |
|---|---|
| `symbol` | Canonical identity field. A missing symbol never erases it. A different non-null symbol for the same ISIN is a blocking identity conflict; do not auto-rename. Symbol changes require a separately confirmed authoritative mapping or corporate/lifecycle event. |
| `isin` | Missing incoming ISIN never erases a known ISIN. A valid ISIN MUST fill a null field when the symbol match is unambiguous and authority is not lower. A different non-null ISIN for the same symbol is a blocking identity conflict. |
| `name` | The parser MUST emit `name_origin=explicit_source \| derived_symbol_fallback \| conflict_fallback \| missing` and `name_quality=explicit_descriptive \| explicit_short_label \| derived_symbol_fallback \| missing`. An explicit token whose canonical text equals the symbol is `explicit_short_label`, not parser-derived; every other nonblank explicit source token is `explicit_descriptive`. Rank values in the listed order. A higher-quality value MUST replace a lower-quality value. A short/fallback value MUST NOT replace a descriptive name, even when its source has higher generic priority. Different descriptive non-null names at the same authority rank enter the deterministic conflict rule below. |
| `instrument_type` | The BVC equity-price adapter supplies canonical `equity`. Missing input cannot erase it. A conflicting non-null type is recorded as an attribute conflict and does not change the field. |
| `currency_code` | The BVC exchange contract supplies `MAD`. Missing input cannot erase it. A conflicting non-null currency is recorded and does not change the field. |
| `is_active` | A new instrument created from the configured official BVC listed-equity source MUST start active. Price/trading statuses such as `N.T` or `S` MUST NOT deactivate it. A price row MUST NOT reactivate an instrument that an authoritative lifecycle process marked inactive; it records a conflict for review. Only a confirmed listing/delisting lifecycle source or explicit operator action may change established activity state. |
| `source_id` | Identifies the single source responsible for the latest accepted unconflicted material instrument-attribute transition, not merely the latest price sighting. A weak/no-op row does not replace it. A system-derived conflict fallback has no single supplying source; conflict contributors are stored separately and the legacy projection remains on the prior unconflicted transition, or null when none exists. |
| `raw_payload_id` | Identifies the raw content paired with `source_id` for that same accepted unconflicted material transition. It MUST NOT be changed when all canonical fields are retained from older richer content. Conflict candidates retain their own raw provenance separately. |
| occurrence provenance | A future material `collection_occurrence_id` is paired with that source/raw transition. A separate last-seen occurrence and confirmation links record sightings without claiming they supplied retained fields. Conflict candidates each retain occurrence provenance. |
| `first_seen_at` | Minimum response-received/authentic observation time among qualifying occurrences that established or confirmed the identity. It may move earlier when older authentic evidence is first ingested, but MUST never move later. Reprocessing is not a new sighting. |
| `last_seen_at` | Monotonic maximum observation time among qualifying occurrences that unambiguously match the instrument. It MUST advance on a later identical or weaker qualifying confirmation even when material fields and material provenance do not change. It MUST never move backward. |

For sightings, a **qualifying occurrence** is a preserved external live response or an explicitly approved replay with an authentic declared observation time, containing a structurally parseable row whose normalized symbol/ISIN matches unambiguously. A local fixture without authentic observation evidence and a mere reprocessing attempt are not qualifying occurrences. Group acceptance is not required merely to prove the source showed the identity, but failed/ambiguous identity rows do not qualify.

On each qualifying occurrence, repositories MUST calculate:

```txt
first_seen_at = min(existing first_seen_at, qualifying observation time)
last_seen_at  = max(existing last_seen_at, qualifying observation time)
```

Field-specific non-erasure and conflict rules in the table are hard gates and override generic ranking. Within candidates in the same field-quality class, precedence is evaluated in this order:

1. identity consistency;
2. syntactic validity;
3. field completeness/quality class;
4. explicitly configured field-specific source authority/priority;
5. temporal evidence;
6. preserve existing value and record a conflict if still tied.

Source authority MUST be an explicit field-specific integer configured by source adapter/contract; higher integers win and an absent configuration is rank zero. A generic source rank cannot make a fallback name outrank a descriptive name. Static-field temporal evidence is the qualifying occurrence observation time, not processing time. Temporal evidence MUST NOT resolve symbol, ISIN, instrument-type, currency, activity, or same-authority descriptive-name conflicts. For these hard-gated fields, different valid values remain conflicts regardless of arrival/observation order. Temporal evidence applies only when a field-specific rule explicitly permits time-based replacement.

For a same-authority descriptive-name conflict, neither tied name is canonical truth. Persist the unique canonical candidate values and provenance in an instrument-name-conflict set, set `name_resolution_state=conflicted`, and use the canonical symbol as the non-null public/storage placeholder with `name_origin=conflict_fallback` and fallback quality until a strictly higher field-specific authority or an explicitly audited resolution supplies one name. This transition is not a weaker-row overwrite: the equal-strength contradiction invalidates both candidates' authority while preserving both as evidence. It applies whether the instrument already existed, candidates arrive sequentially in either order, or inserts race from an empty database. The repository MUST atomically converge to the same conflict state; it MUST NOT retain whichever descriptive candidate committed first. A later weak or identical row cannot clear the conflict. If another allowed comparison remains tied with different values and has no rule-specific conflict state, retain the prior accepted value and record a conflict rather than use transaction order.

The source/raw/material-occurrence fields always move as one provenance tuple and MUST NOT mix contributors. When equivalent candidates race to establish the same initial unconflicted material state, the deterministic representative is the candidate with the minimum qualifying `(observation_time, occurrence_sequence)`; reverse processing may reconcile that provenance tuple without changing canonical values. A fixture without authentic observation time uses occurrence sequence only within fixture scope and cannot become production provenance. A multi-candidate conflict transition has no representative supplier: its candidate links are authoritative audit evidence, while any nullable singular projection follows the rule in the table above.

HTML is not globally more authoritative than JSON, and JSON is not globally more authoritative than HTML. Completeness is evaluated per field. In the current source shape, descriptive HTML values commonly enrich JSON fallbacks, while JSON price recency is handled independently by the latest-price policy.

An older payload MUST fill a canonical null or explicitly classified fallback when identity is unambiguous, the candidate is syntactically valid, and no valid non-null conflict exists, provided it has higher field quality or the same quality with non-lower field-specific authority. Otherwise it MUST be a no-op or a recorded conflict. It MUST NOT replace a different valid non-null identity value merely because it was processed later. Static identity conflicts are never resolved by processing order alone.

Instrument metadata MUST record, at minimum, whether a name is descriptive or fallback and the provenance of accepted material fields. Raw source values remain trace evidence and are not canonical merge instructions.

### 1.2 Rationale

Current `upsert_instrument()` matches by ISIN then symbol and assigns every incoming value. The JSON parser also uses symbol as a name fallback. Consequently, a newer weak JSON row can erase an HTML-derived ISIN/name, reset provenance, force `is_active=true`, or move `last_seen_at` backward when old content is reprocessed.

Instrument identity and descriptive master data change much less frequently than price snapshots. Field-aware merge rules prevent a high-frequency weak price feed from degrading stable identity data while still allowing nonconflicting enrichment.

### 1.3 Example

Existing instrument:

```txt
symbol = ATW
isin = MA0000012445
name = ATTIJARIWAFA BANK
name_quality = descriptive
```

New JSON row:

```txt
symbol = ATW
isin = null
name = ATW        # parser fallback
source_status = N.T
```

Required result:

```txt
symbol = ATW
isin = MA0000012445
name = ATTIJARIWAFA BANK
is_active = unchanged
material source/raw provenance = unchanged
last_seen_at = max(existing last_seen_at, qualifying observation time)
```

The price observation MUST continue to latest-price evaluation because the symbol identity is unambiguous; section 2 still decides whether it writes. `N.T` does not alter instrument activity.

### 1.4 Edge cases

- Same ISIN, different symbol: blocking identity conflict; preserve existing instrument and do not publish the row's price.
- Same symbol, different ISIN: blocking identity conflict; preserve existing instrument and do not publish the row's price.
- Both keys resolve to different records: blocking conflict requiring data repair before normalization.
- Existing null ISIN, incoming valid ISIN, unambiguous symbol: enrich the existing instrument.
- Existing fallback name, incoming descriptive name: upgrade the name and its provenance even if the content is older, provided identity is unambiguous.
- Existing descriptive name, incoming different same-authority descriptive name: retain both in conflict evidence, expose the symbol conflict fallback, and allow price normalization only if symbol/ISIN identity is otherwise secure.
- Reprocessing the same occurrence: no material change and no first/last-seen change.
- Duplicate content in a new qualifying occurrence: material fields remain unchanged; `last_seen_at` advances when its authentic observation time is later.
- Inactive instrument appears in a price page: retain inactive state and record an activity conflict; do not infer relisting.
- Whitespace/case-only differences after normalization are confirmations, not changes.

### 1.5 Required tests

Later implementation MUST add tests proving:

1. rich HTML followed by weak JSON preserves ISIN and descriptive name;
2. weak JSON followed by rich HTML enriches the instrument;
3. missing incoming values never erase known valid values;
4. older content cannot regress canonical fields or material provenance;
5. late arrival of older authentic evidence moves first-seen earlier but never last-seen backward;
6. reprocessing is not counted as a new sighting;
7. same ISIN/different symbol blocks all canonical writes for the row;
8. same symbol/different ISIN blocks all canonical writes for the row;
9. different same-authority descriptive names create a nonblocking attribute conflict, retain both candidates, and expose the deterministic symbol conflict fallback;
10. `N.T`, `S`, blank, and unknown source statuses do not change `is_active`;
11. a price row does not automatically reactivate an inactive instrument;
12. concurrent inserts for the same symbol/ISIN converge on one instrument under PostgreSQL;
13. symbol/ISIN normalization rejects whitespace/control/invalid-length variants and converges case variants;
14. `name_origin` distinguishes a source-provided short label from a parser-derived fallback, and deterministic `name_quality` prevents either from replacing a descriptive name;
15. lower-quality names cannot win through generic source priority;
16. local fixtures/reprocessing do not change sightings without authentic observation evidence.
17. an ISIN-only row matches an existing unique instrument without changing its canonical symbol;
18. an unmatched ISIN-only row is rejected and never creates an ISIN-as-symbol instrument;
19. sequential same-authority descriptive-name conflicts in both arrival orders converge to the same candidate set and symbol conflict fallback;
20. concurrent creation from an empty database with two tied descriptive names converges under PostgreSQL to one instrument in the same conflict state as sequential processing;
21. a weak or identical later row cannot clear a name conflict, while a strictly higher field-authority resolution can clear it with audit provenance;
22. source/raw/material-occurrence provenance moves as one tuple, conflict contributors never overwrite it, and equivalent initial candidates select the same earliest authentic representative in forward, reverse, and concurrent processing.

### 1.6 Database implications

- Retain PostgreSQL uniqueness for `(exchange_id, symbol)` and `(exchange_id, isin)`.
- Enforce stored canonical symbol/ISIN form with checks or normalized expression indexes as detailed in section 11; application normalization still owns the friendly validation error.
- Add a first-class `first_seen_at` if source observation time must differ from persistence `created_at`. `created_at` remains database insertion time and MUST NOT be relabeled as historical source first-seen.
- Add occurrence provenance for last material change and last confirmation. The existing single `raw_payload_id` cannot represent both meanings.
- Store validated field-quality/provenance metadata and a uniqueness-protected name-conflict candidate set (for example `(instrument_id, canonical_name, authority_rank)`), or use a dedicated field-provenance/history design with equivalent deterministic conflict state.
- Repository writes MUST use PostgreSQL conflict handling/locking and retry semantics; application prechecks alone are not concurrency enforcement.

### 1.7 Open questions

- Which BVC source, if any, is authoritative for official symbol/name changes and delisting/relisting events?
- Does an existing source expose an official instrument master record that should outrank price-page HTML/JSON?
- Should exact per-field provenance live in validated JSON metadata or a dedicated instrument history table?
- Which manually captured/backfilled occurrences have authentic observation timestamps and may affect first/last-seen values?
- Should syntactically valid ISINs also require ISO 6166/Luhn checksum validation before becoming canonical?

## 2. Latest-price stale-data policy

### 2.1 Decision

`latest_prices` contains one coherent latest source-displayed price snapshot per instrument. `price_kind=last_trade` means only that the source field is labeled `lastTradedPrice` until `price_semantics_confirmed=true`; it is not independently verified execution evidence.

Each parsed price MUST retain:

```txt
trading_date
trading_date_source = explicit_source_date | derived_direct_timestamp | source_published | collection_received
effective price_timestamp
timestamp_source = direct_source | source_published | collection_received
price_kind = last_trade | displayed_reference | unknown
selected_price_field
price_semantics_confirmed
source_status_raw
source_status_normalized
raw content provenance
collection occurrence provenance
```

Timestamp selection is:

1. valid timezone-aware row source timestamp;
2. valid timezone-aware `source_published_at`;
3. collection occurrence `response_received_at`.

Naive source timestamps MUST be rejected unless a source-specific contract explicitly declares the timezone. They MUST NOT silently be treated as UTC. Trading dates MUST be derived in `Africa/Casablanca`, not by taking the date of an arbitrary source offset without conversion.

Trading-date selection is:

1. a syntactically valid explicit source trading date;
2. Casablanca date derived from a direct source timestamp;
3. Casablanca date derived from `source_published_at`;
4. Casablanca date derived from collection response time only under the restricted fallback rule below.

An explicit date that disagrees with the Casablanca date of a direct source timestamp is a blocking `timestamp_trading_date_conflict`.

A `collection_received` trading date may advance canonical latest data only when all of these are true:

- the occurrence is an external live response, not a fixture, replay, backfill, or reprocessing attempt;
- the source adapter has an explicitly verified `current_snapshot_date_eligible=true` contract;
- the response time falls within the configured applicable Casablanca market-session/date policy;
- the content is not known to carry older explicit/direct/publication date evidence;
- identity and all other acceptance rules pass.

Until that source eligibility and market-session policy are verified, a collection-derived date MUST NOT insert or advance `latest_prices`; record `untrusted_trading_date_fallback`. Fixture/replay/backfill input requires an explicit source date, direct timestamp, source publication time, or approved authentic observation time and MUST never use processing/current wall-clock time as market date.

#### Update ordering

The repository MUST make the following atomic decision:

| Incoming relationship | Required behavior |
|---|---|
| No existing row | Insert the complete incoming snapshot if identity and required price validation pass |
| Older trading date | Do not update canonical latest price or its provenance |
| Newer trusted trading date | Update with the complete incoming snapshot; fallback evidence remains explicitly lower-confidence. A collection-derived date must first pass the restricted eligibility rule above. |
| Same trading date, both direct source timestamps | Older timestamp is skipped; newer timestamp replaces the complete snapshot; equal timestamp follows correction rules below |
| Same trading date, existing direct timestamp and incoming fallback timestamp | Preserve the direct-timestamp row at every configured source-authority rank |
| Same trading date, existing fallback and incoming direct timestamp | The direct-timestamp snapshot outranks the fallback at every configured source-authority rank, even if its clock time is earlier |
| Same trading date, existing `source_published` and incoming `collection_received` | Preserve the source-published row at every configured source-authority rank |
| Same trading date, existing `collection_received` and incoming `source_published` | The source-published candidate outranks the collection fallback at every configured source-authority rank if all validation passes |
| Same trading date, both use the same fallback class | Compare their effective timestamps; later timestamp wins; equal timestamp follows correction rules |

Accepted newer snapshots replace all market snapshot fields as one unit. Missing optional OHLC/volume/value fields become null; values from an older date or snapshot MUST NOT be carried forward because that would fabricate a mixed-time snapshot.

On the same trading date, timestamp-source rank is a hard first comparison: a lower class MUST NOT overwrite a higher class at any configured source authority. Configured source authority is compared only within the same timestamp class. Within one class, a genuinely newer effective event timestamp wins before equal-time correction rules.

#### Equal-timestamp corrections

An equal-timestamp incoming row is handled as follows:

1. If all material snapshot fields, quality classification, price kind, and selected source field are identical, it is a confirmation/no-op. Canonical financial values and their supplying raw provenance do not churn. The new occurrence/group MUST receive a confirmation/publication-membership link to the reused value revision so a complete unchanged group can be accepted and prove new collection freshness without pretending it supplied different values.
2. Compute the total evidence tuple below lexicographically; a higher value wins at the first differing element.
3. If material values differ and the incoming evidence tuple is lower, preserve the existing row and record `latest_price_conflict`.
4. If the incoming evidence tuple is higher because of an explicitly configured authority/rank, it MUST replace the row as a correction and write correction audit.
5. If material values differ, the source/adapter/record identity is the same, the incoming occurrence order is strictly later, and the evidence tuple is equal, the incoming row MUST replace the row as a source republication/correction.
6. If the same source/adapter/record identity and evidence tuple arrive from an occurrence strictly earlier than the occurrence supplying the stored equal-event revision, preserve the later stored revision and record `superseded_equal_event_observation`. The earlier candidate is not a confirmation and receives no publication-membership link.
7. If equal-rank conflicting candidates come from different source/adapter/record identities, neither candidate is eligible for group publication. Preserve the last accepted row, or publish no row when none existed, and record `latest_price_conflict`; transaction/processing order MUST NOT choose a winner.
8. If the same occurrence and rule version produces a different material fingerprint on reprocessing, preserve canonical state and record `nondeterministic_reprocessing`.
9. An accepted correction replaces the complete snapshot and updates canonical raw/occurrence provenance. Metadata MUST record the prior raw/occurrence IDs, a previous-value fingerprint, correction time, and reason `equal_timestamp_source_republication` or `higher_rank_correction`.

The evidence tuple, from most significant to least significant, is:

```txt
(
  timestamp_source_rank,
  configured_price_source_authority,
  price_kind_rank
)
```

Timestamp ranks are `direct_source=3`, `source_published=2`, `collection_received=1`. Higher configured authority integers win within the same timestamp class; absent configuration is zero. Price-kind ranks are `last_trade=2`, `displayed_reference=1`, `unknown=0`. This is a lexicographic order, not a score: source priority or price kind cannot make a collection fallback outrank direct source-time evidence on the same trading date.

For response-bearing external occurrences, strict occurrence order is `(response_received_at, occurrence_sequence)`, where `occurrence_sequence` is an immutable unique database-assigned ordering value used only to break equal clocks. Request sequence and retry attempt remain separate page-attempt identity. This same definition governs provisional daily-bar comparisons in section 3.

A fixture load or synthetic reprocessing event has no `response_received_at` and MUST NOT become a same-event source correction merely because it was loaded later. Reprocessing the same occurrence follows the nondeterminism rule. A replay/backfill of distinct occurrences may use preserved authentic original response ordering or an explicit source revision order declared by its adapter contract; without either, equal-event different-fingerprint candidates are a conflict and are ineligible for publication. Local load time and transaction order are never correction evidence.

The `bvc-latest-price-material-v1` fingerprint MUST use the shared canonical protocol and this exact ordered field/type map (`?` means the value may instead use the `null` tag):

```txt
instrument_id: uuid
trading_date: date
trading_date_source: string
price_timestamp: timestamp
timestamp_source: string
price: decimal
open: decimal?
high: decimal?
low: decimal?
previous_close: decimal?
change_value: decimal?
change_percent: decimal?
volume: integer?
traded_value: decimal?
market_capitalization: decimal?
number_of_trades: integer?
price_kind: string
selected_price_field: string
price_semantics_confirmed: boolean
source_status_normalized: string?
data_quality_status: string
quality_reason_codes: string_set
```

It excludes only audit row IDs, ingestion/occurrence IDs, persistence times, and counters. A status-only or quality-only material change therefore follows correction/conflict rules instead of silently leaving stale metadata attached to a new observation.

Temporal recency takes precedence for genuinely newer source events. Therefore a newer source-displayed reference value becomes the latest snapshot when all rules pass, but it MUST remain clearly labeled and MUST NOT be described as a last trade.

#### Source price alias and status

- `lastTradedPrice` selected with a non-null/nonblank value is stored as `price_kind=last_trade` only as shorthand for **source-labeled last trade**. Until BVC documentation confirms execution semantics, set `price_semantics_confirmed=false`, and APIs MUST NOT describe it as an independently verified trade execution.
- `coursCourant` used only because the primary field is null/blank is `price_kind=displayed_reference` until BVC semantics are confirmed.
- A `displayed_reference` value that passes identity, date, timestamp, ordering, and numeric rules MUST populate/update `latest_prices`; its `data_quality_status` MUST be at least `suspect` and metadata MUST state that no trade is confirmed.
- `N.T`, `S`, or any other opaque source status does not waive missing-price validation and does not alter instrument activity.
- If every approved price alias is null/blank, create a `missing_price` normalization error and do not insert/update `latest_prices`.

Staleness caused by wall-clock age is a freshness concern defined in section 9. An older incoming row being skipped and an accepted row later becoming operationally stale are different concepts.

### 2.2 Rationale

Current code compares only `price_timestamp`, treats every equal timestamp as updateable, and replaces all values/provenance without recording correction history. It also uses `collected_at` when a source timestamp is absent, allowing later collection of older source data to displace a more authoritative row.

The selected rules preserve one coherent snapshot, prevent fallback timestamps from outranking direct source evidence on the same session, and allow traceable same-time corrections without accepting arbitrary last-write-wins behavior.

### 2.3 Example

Existing row:

```txt
trading_date = 2026-05-18
price_timestamp = 2026-05-18T16:00:00Z
timestamp_source = direct_source
price = 500.00
price_kind = last_trade
```

A fixture for the same trading date is loaded on 2026-05-19 with no source time and price 490.00. Its fallback timestamp is later in wall-clock time.

Required result: preserve 500.00. The later collection timestamp does not outrank an existing direct source timestamp for the same trading date. Preserve the fixture occurrence and record the skip reason; do not change latest-price provenance.

If the same source/adapter/record later returns the same direct timestamp with 501.00 in a strictly later occurrence and equal evidence rank, the repository MUST accept it as an equal-time source republication and preserve correction provenance.

### 2.4 Edge cases

- Newer trading date with no direct time: update only when a source-publication time or the verified live collection-date fallback rule supplies trusted date/time; tag the applicable source and at least `suspect` timestamp confidence.
- Existing fallback and incoming direct timestamp on the same date: direct timestamp wins.
- Equal timestamp, identical values, different raw occurrence: no financial/provenance churn; confirmation remains in occurrence audit.
- Equal timestamp, different value, same occurrence reprocessing: no correction; deterministic reprocessing must converge.
- Equal timestamp, source-labeled `lastTradedPrice` versus displayed reference at equal higher-order rank: the source-labeled field wins while its semantics flag remains explicit.
- Newer displayed reference following an older source-labeled last trade: it becomes the current displayed snapshot when all ordering/eligibility rules pass, while its price kind and suspect quality remain visible.
- An older timestamp is evaluated independently for a daily bar under section 3 eligibility; it never changes current latest price.
- Source timestamp date and supplied trading date disagree after market-timezone conversion: block latest update with `timestamp_trading_date_conflict` until resolved.
- Incoming optional values are null: do not carry old optional financial values into the new snapshot.
- An undated duplicate payload replayed on the next day cannot derive a new trading date from replay/processing time.
- Equal-rank cross-source conflicts remain staged/quarantined and cannot replace the last accepted publication.
- A same-source equal-event candidate from an earlier occurrence is an audited superseded observation, not a correction or confirmation.

### 2.5 Required tests

Later implementation MUST test:

1. insert with direct timestamp;
2. strictly older direct timestamp is skipped without provenance change;
3. strictly newer direct timestamp replaces the complete snapshot;
4. equal timestamp and identical data is a no-op apart from occurrence evidence;
5. accepted equal-time correction records prior provenance and fingerprint;
6. unqualified equal-time conflict is retained as an error without overwrite;
7. same-date collection fallback cannot displace an existing direct timestamp;
8. direct timestamp displaces a same-date fallback row;
9. newer trading date with a source-publication fallback is accepted and marked suspect, while an unverified collection-date fallback is blocked;
10. optional financial fields do not carry forward from an older snapshot;
11. `coursCourant` fallback is labeled `displayed_reference` and suspect;
12. missing all price aliases remains a blocking `missing_price` error;
13. timestamps are converted to `Africa/Casablanca` before trading-date comparison;
14. naive source timestamps are rejected without an explicit source-timezone contract;
15. concurrent older/newer/equal corrections converge deterministically under PostgreSQL.
16. `source_published` outranks `collection_received` in both directions on the same trading date;
17. all cross-dimension evidence-tuple combinations follow the declared lexicographic order;
18. a same-source equal-rank later occurrence is a mandatory audited correction, while a same-occurrence difference is rejected as nondeterministic;
19. the material fingerprint detects status-only, quality-only, and optional-field differences;
20. fixture, replay, reprocessing, and ineligible live content cannot manufacture a new collection-derived trading date;
21. an eligible verified live-current-snapshot fallback date is accepted and labeled with its source;
22. equal-rank cross-source candidates produce no order-dependent publication.
23. an identical later group reuses the financial value revision but creates confirmation/publication membership and can advance accepted collection freshness.
24. a higher-authority collection fallback cannot beat lower-authority direct/source-published evidence on the same trading date;
25. an older trading date with a later collection/effective timestamp never replaces the current row;
26. explicit source date disagreement with the Casablanca-converted direct timestamp produces `timestamp_trading_date_conflict` and no canonical write.
27. a same-source/equal-evidence/equal-event older occurrence processed after a newer occurrence preserves the newer revision and records a superseded observation;
28. fingerprint tests prove equal digests for equivalent Decimal scales, equivalent UTC instants, equivalent UUID spellings, and reordered reason codes, and different digests for null/blank, each changed material field, and a different algorithm version;
29. same-occurrence different fingerprints remain `nondeterministic_reprocessing` in both processing orders.
30. every v1 field rejects an undeclared type tag, invalid UUIDs are rejected, and stored UUID text is lowercase/hyphenated.
31. fixture load order cannot authorize an equal-event correction; replay/backfill requires preserved authentic occurrence or source-revision order.

### 2.6 Database implications

- Retain `UNIQUE(instrument_id)` as the basic latest-price invariant.
- Add first-class or schema-validated fields for trading-date source, timestamp source, price kind, price-semantics confirmation, selected price field, source status, quality-reason codes, and occurrence provenance. These values affect conflict resolution and must not be arbitrary unvalidated metadata.
- Repository updates require an atomic PostgreSQL upsert/locking strategy with timestamp/evidence predicates; a query followed by a blind update is insufficient.
- Add nonnegative database checks for price, open, high, low, previous close, volume, traded value, and market cap. Do not require change value or change percent to be nonnegative.
- Preserve correction audit information outside mutable free-form messages; a correction-history table is preferred if complete value history is required.
- Separate immutable value-revision provenance from group confirmation/publication membership so unchanged accepted groups do not churn values or lose freshness evidence.
- Store the fingerprint algorithm identifier and digest on immutable latest-price revisions; repository comparisons MUST use the declared canonical protocol rather than ad hoc serialization.

### 2.7 Open questions

- Does BVC formally define `coursCourant` as a last price, reference price, previous close, or display-only value under each source status?
- Is `transactTime` a row update time, trade time, or another event time?
- Which future source-priority rules should apply when distinct BVC sources publish equal-time conflicts?
- Should correction value history be stored in a dedicated price revision table or reconstructed from immutable raw content and occurrence records?
- Can BVC documentation verify that the listing endpoint represents the current market date when no row date/timestamp is present?

## 3. Daily price-bar identity

### 3.1 Decision

The canonical identity of a BVC `1d` price bar is:

```txt
instrument_id + timeframe + trading_date
```

For `timeframe = "1d"`, exact source timestamp MUST NOT participate in logical identity.

`bar_timestamp` for a daily bar is a deterministic period anchor: midnight at the start of `trading_date` in `Africa/Casablanca`. It is not represented as a source event or official market close time. The actual source observation timestamp and occurrence provenance MUST be stored separately.

Daily bars and intraday market snapshots are different entities:

- `latest_prices` represents the latest source-displayed market snapshot.
- one `1d` bar represents the aggregate state for one trading session/date;
- future intraday bars, if ever supported by a suitable source, use their own timeframe and event/window identity and are outside this contract.

#### Provisional and final daily state

A daily bar MUST have a system state:

```txt
provisional | final
```

- Each source adapter MUST declare `daily_aggregate_eligible=true|false` with a contract/version reference. Missing or unverified eligibility is treated as `false`.
- When eligibility is `true` and the row passes required daily-field/date validation, the repository MUST upsert exactly one provisional bar for its trading date. When eligibility is `false`, it MUST NOT create/update a daily bar from that row.
- A provisional observation MUST NOT be described as an official close.
- Promotion to `final` requires an independently verified finalization signal or a source contract that explicitly defines the payload as the official daily close.
- Page completion, HTTP receipt time, `N.T`, `S`, or market-clock assumptions alone are not finalization evidence.
- Until current BVC JSON/HTML bar semantics are confirmed, valid rows follow section 2 and MUST update/confirm/skip `latest_prices` exactly as ordered there; they MUST NOT be promoted to final daily bars. If provisional-bar eligibility is also unconfirmed, no bar is created from that source.

#### Same-date merge and correction precedence

1. Several eligible intraday observations for the same date target the same provisional daily row; they never create multiple `1d` rows.
2. A verified final candidate always outranks a provisional candidate for the same identity. A provisional candidate MUST NOT overwrite a final row.
3. Provisional candidates compare configured daily-bar source authority first (higher integer wins), then observation-source rank (`direct_source > source_published > collection_received`), then effective source observation time. A greater candidate MUST replace the complete provisional observation coherently.
4. If those three values tie, compare the material fingerprint, source identity, and strict occurrence order. Same source plus identical fingerprint is a confirmation with no value or supplying-provenance churn. Same source plus a different fingerprint and strictly later occurrence MUST replace the provisional row with an audited same-event correction. The same source with a different fingerprint from a strictly earlier occurrence is an audited `superseded_equal_event_observation` and MUST preserve the later revision; the same occurrence/rule version producing a different fingerprint is `nondeterministic_reprocessing`. Different source plus identical fingerprint creates confirmation/publication membership without value/provenance churn. Different source plus different fingerprint preserves the last accepted state (or publishes none), records `daily_bar_conflict`, and prohibits group acceptance.
5. A verified final observation MUST replace a provisional bar and record finalization provenance.
6. An existing final row may be corrected only when a source contract identifies the candidate as an official correction/revision, its configured authority is at least the existing final authority, and its source observation/revision occurrence is strictly later. A qualifying candidate MUST update the same daily row with revision audit; every other final conflict MUST preserve the existing final row and record `daily_bar_conflict`. Generic provisional precedence never bypasses this final-correction gate.
7. An older occurrence or weaker reference/display price MUST NOT replace a confirmed final close.
8. Missing optional OHLCV fields in an accepted replacement do not silently inherit values from a different observation unless the source contract explicitly defines cumulative field merging. Default behavior is coherent whole-observation replacement.

The `bvc-daily-bar-material-v1` fingerprint MUST use the shared canonical protocol and this exact ordered field/type map (`?` means the value may instead use the `null` tag):

```txt
instrument_id: uuid
timeframe: string
trading_date: date
bar_timestamp: timestamp
bar_state: string
open: decimal?
high: decimal?
low: decimal?
close: decimal
volume: integer?
traded_value: decimal?
number_of_trades: integer?
adjusted: boolean
daily_aggregate_eligible: boolean
adapter_contract_version: string
daily_bar_source_authority: integer
timestamp_source: string
source_observed_at: timestamp
selected_close_field: string
price_kind: string
price_semantics_confirmed: boolean
source_status_normalized: string?
data_quality_status: string
quality_reason_codes: string_set
finalization_signal: string?
finalization_authority: integer?
source_revision_identifier: string?
```

It excludes audit IDs, persistence times, and occurrence/group membership. A confirmation links the new occurrence/group to the reused bar revision without claiming that it supplied different values.

Rows using a `coursCourant` fallback or an opaque source status such as `N.T`/`S` MUST NOT establish or finalize an official close. They remain latest/display observations. They MUST NOT affect a provisional bar unless the adapter contract explicitly verifies daily-aggregate eligibility and selected-field semantics; once both are verified, a valid eligible row follows the mandatory provisional upsert rules above.

Provisional bars are persisted audit/operational state, not accepted official history. The ordinary public historical API MUST exclude them. A future explicitly named preview/operational scope may include them only with `bar_state=provisional`, unconfirmed-close semantics, quality/provenance fields, and the incomplete/finalization behavior in section 9. A preview-visible revision must come from a pagination-complete group, have a successful row normalization result with no blocking identity/value conflict, and retain its exact group/occurrence provenance; other provisional observations remain diagnostics-only. Publishing the latest-price snapshot does not implicitly publish a provisional bar as final history.

### 3.2 Rationale

Current code labels every bar `1d` but uses the row's intraday JSON timestamp as `bar_timestamp`, while the database uniqueness key is `(instrument_id, timeframe, bar_timestamp)`. Several updates during one session therefore create several nominal daily bars. HTML date-only fixtures and JSON data for the same date also create different rows.

Trading-date identity provides one deterministic daily record. A separate provisional/final state prevents an intraday snapshot from being misrepresented as an official daily close.

### 3.3 Example

For instrument `ATW`, three observations from a verified daily-aggregate-eligible adapter arrive for 2026-05-18 at 10:00, 12:00, and 16:00.

Required storage:

```txt
one row
identity = ATW + 1d + 2026-05-18
bar_timestamp = 2026-05-18T00:00:00 Africa/Casablanca
latest source_observed_at = 16:00 observation, if eligible
bar_state = provisional unless finalization is independently verified
```

There must not be three `1d` rows keyed by 10:00, 12:00, and 16:00.

### 3.4 Edge cases

- HTML fixture then JSON live row for the same date: they target the same daily identity, but neither writes a bar unless its adapter is verified eligible; eligible candidates follow the explicit state/authority/observation order above.
- JSON then richer HTML for the same date: field richness is not a daily-bar precedence dimension. The declared eligibility/state/authority/observation order decides, and tied cross-source conflicts are quarantined.
- A row carrying a code suspected to mean suspended/not-traded and a displayed/reference value MUST become/confirm/skip the latest snapshot exactly under section 2; without authoritative code semantics it is never proof of final close.
- Late official correction after finalization: update the same daily row with revision provenance; do not insert a second bar.
- Different trading dates with the same UTC instant near timezone boundaries: market-local trading date controls daily identity.
- Future `1h`/`1m` rows: not subject to the daily-date unique rule; their window/event identity requires a separate source-specific contract.
- Pre-existing duplicate daily rows created by intraday timestamps: a future migration must audit and reconcile them before adding the new unique constraint.
- A same-source tied candidate processed newest first and oldest second preserves the newer provisional revision and records the older occurrence as superseded.

### 3.5 Required tests

Later implementation MUST test:

1. multiple observations from a verified-eligible JSON adapter create exactly one `1d` row per instrument/date;
2. verified-eligible HTML and JSON rows with identical material fingerprints converge on exactly one daily row;
3. market-timezone conversion determines the correct trading date and period anchor;
4. a newer provisional observation updates one provisional row;
5. a provisional observation cannot replace a final bar;
6. a verified final observation promotes/replaces a provisional row;
7. a traceable late final correction updates the same row and records revision provenance;
8. an unqualified final conflict does not overwrite;
9. `coursCourant`, `N.T`, and `S` do not establish an official close;
10. concurrent same-date writes converge under the PostgreSQL daily unique constraint;
11. different daily dates and different timeframes remain independently storable;
12. a pre-migration duplicate audit reports all conflicting same-date rows;
13. an adapter with false/missing eligibility never creates a daily bar;
14. a verified eligible row deterministically upserts a provisional bar;
15. provisional precedence compares authority, timestamp class, and observation time in the declared order;
16. tied same-source candidates use later occurrence order, while tied cross-source conflicts never use transaction order;
17. a qualified official final correction updates with revision audit and every unqualified final conflict is preserved/reported.
18. same-source identical fingerprint confirms without value/provenance churn;
19. same-source different fingerprint with later occurrence mandates provisional correction audit;
20. different-source identical fingerprint confirms through membership without churn;
21. different-source different fingerprint conflicts and cannot publish;
22. false/missing eligibility asserts zero bars separately from the exactly-one eligible cases;
23. a same-source/equal-precedence/equal-event older occurrence arriving after the newer one preserves the newer provisional revision;
24. same-occurrence different fingerprints are rejected as nondeterministic reprocessing;
25. daily fingerprint equivalence/change/version/type-tag/UUID cases follow the shared protocol without float conversion;
26. ordinary public history excludes provisional bars, while an explicitly authorized preview labels them provisional and incomplete rather than as official close;
27. fixture load order cannot choose between different tied provisional fingerprints without authentic occurrence/source-revision order.

### 3.6 Database implications

- Add a PostgreSQL unique constraint or partial unique index enforcing `(instrument_id, timeframe, trading_date)` for `timeframe='1d'`.
- Retain timestamp/window uniqueness appropriate for future non-daily timeframes; do not replace all timeframe identity with trading date.
- Add a validated `bar_state`, adapter eligibility/contract version, authority, source observation class/time, and correction/finalization provenance.
- Keep `bar_timestamp` as a deterministic daily period anchor, not the daily unique key.
- A migration must define how current same-date timestamp-keyed duplicates are selected, merged, quarantined, or rejected. It MUST report conflicts rather than discard rows silently.
- Repository writes require atomic conflict handling on the daily key.
- Store the daily fingerprint algorithm identifier and digest on immutable bar revisions, and separate provisional operational visibility from accepted final-history publication membership.

### 3.7 Open questions

- Do BVC listing JSON/HTML open/high/low/volume fields represent session-to-date aggregates suitable for a provisional daily bar?
- What source field or endpoint, if any, proves the official daily close/finalization event?
- Is there a later official historical endpoint that should be the only authority for final bars?
- What operator review policy applies to pre-existing same-date bars with conflicting values?

## 4. Exact Decimal handling

### 4.1 Decision

Required principle:

```txt
No financial value may pass through binary float before becoming Decimal.
```

All canonical JSON decode paths MUST use a shared strict loader equivalent to:

```python
json.loads(
    payload_text,
    parse_float=Decimal,
    parse_int=int,
    parse_constant=reject_non_finite_constant,
)
```

`Decimal(str(float_value))` is prohibited because precision already lost in the float cannot be recovered.

Rules by input form:

| Input | Required behavior |
|---|---|
| Quoted JSON decimal string | Trim and parse directly with the strict decimal text parser |
| Unquoted JSON fractional number | Decode directly as `Decimal` through `parse_float=Decimal`; never create a float |
| Unquoted JSON integer | Decode as Python `int`, which is exact; convert directly to `Decimal` for financial fields or validate as integer for quantity/count fields |
| French-formatted HTML text | Normalize spaces/NBSP, currency/percent labels, grouping separators, and decimal comma, then construct `Decimal` from the normalized string |
| JSON `null` | `None` |
| Blank string or documented blank token | `None` |
| Numeric zero | Preserve `Decimal("0")` or integer zero; never treat it as missing |
| Invalid/non-finite token | Explicit parse/normalization error; never coerce to zero or null silently |

Quoted JSON financial strings use a distinct non-locale grammar. After trimming surrounding whitespace and applying the declared missing-token rule, a value MUST fully match:

```txt
[+-]?[0-9]+(?:\.[0-9]+)?
```

The default BVC JSON missing-string tokens are empty/whitespace and `-`; JSON `null` is handled structurally. `N/A` or another token becomes missing only when a versioned adapter contract explicitly adds it. Quoted JSON rejects decimal comma, grouping separators, internal whitespace, currency/percent labels, underscores, and exponent notation. French locale normalization applies only to HTML/text adapters. A leading plus/minus and numeric zero are preserved exactly.

Raw entity-body bytes under section 7 are the ultimate content evidence. After strict charset decoding without Unicode/newline normalization, `payload_text` is the authoritative JSON token stream used by the parser and retains declared encoding provenance; it does not replace the bytes. If a decoded JSONB copy cannot preserve `Decimal` values without conversion, the collector MUST keep bytes/text and either omit the decoded financial representation or encode numeric tokens losslessly as strings. A raw convenience object MUST NOT become the canonical numeric input.

#### Precision and scale

Values MUST be validated and normalized before database persistence. The database driver MUST NOT be the first component to decide rounding.

For a target `NUMERIC(precision, scale)` validation order is fixed:

1. reject non-finite values before arithmetic;
2. inspect `Decimal.as_tuple()`/adjusted exponent without quantizing and reject integer-digit overflow first with `decimal_precision_exceeded`;
3. inspect coefficient/trailing digits and reject any nonzero information beyond target scale with `decimal_scale_exceeded`; excess zero digits are allowed;
4. only then quantize under an explicit local Decimal context whose precision is derived to cover the input coefficient and target precision/scale, independent of ambient process context;
5. map every remaining `InvalidOperation`/context failure deterministically to the already-established precision or scale rule, never expose a context-dependent exception;
6. preserve the original token in raw trace metadata.

Extreme positive exponents that exceed integer capacity fail precision before quantization. Extreme negative exponents with nonzero information below target scale fail scale before quantization. Implementations MUST NOT allocate an unbounded context merely to discover either outcome.

An unquoted JSON exponent token MUST be accepted when the strict Decimal result is finite, exact, and within declared precision/scale; otherwise it MUST be rejected. Quoted JSON/HTML exponent text remains invalid unless a later source-text grammar explicitly permits it.

API serialization MUST use strings from `Decimal`. The API MUST NOT repair an impossible null required price/close with the string `"0"`; corruption is an error/readiness-quality condition.

### 4.2 Rationale

The current text parser constructs `Decimal` safely, but the JSON parser, collector, and diagnostics use ordinary `json.loads()`. An unquoted fractional token therefore becomes binary float before it is stringified and parsed as Decimal. Current fixtures hide this gap because financial decimals are quoted strings.

Explicit scale validation also prevents PostgreSQL or a driver from silently rounding source values that exceed the schema's six decimal places.

### 4.3 Example

Input:

```json
{"lastTradedPrice": 0.100000000000000005}
```

Required parser value:

```python
Decimal("0.100000000000000005")
```

The value must never first become Python `float(0.1)`. If the target scale is six and nonzero digits would be lost, normalization records `decimal_scale_exceeded` instead of silently persisting `0.100000`.

Input `"123.4500000000"` is acceptable for scale six because quantization to `123.450000` does not change its numeric value.

### 4.4 Edge cases

- JSON `null`, `""`, whitespace, and `"-"` become `None`; `N/A` does so only under a versioned adapter missing-token declaration.
- JSON `0`, `0.0`, quoted `"+0.00"`, and HTML `"0,00"` remain zero.
- JSON `NaN`, `Infinity`, and `-Infinity` are rejected.
- Unquoted integer quantities remain exact and must be integral/nonnegative where required.
- A fractional value in an integer field is an explicit `invalid_integer` error.
- Very large exponents or integer parts are rejected before database flush.
- Decimal comma/grouping rules apply to HTML/text, not unquoted JSON grammar.
- A quoted string containing exponent notation follows the source-specific text grammar; it is rejected unless explicitly supported.

### 4.5 Required tests

Later implementation MUST test:

1. high-precision unquoted JSON fraction remains the exact expected Decimal;
2. quoted JSON decimal parses exactly;
3. unquoted JSON integer remains exact;
4. French decimal comma, dot/space grouping, NBSP, percent, and currency labels;
5. null and blank tokens become `None`, while zero remains zero;
6. invalid text and fractional integer values create explicit errors;
7. non-finite JSON constants are rejected;
8. exponent notation follows the declared policy;
9. excess trailing zero scale is accepted;
10. nonzero scale loss and precision overflow are rejected before persistence;
11. raw JSON storage does not require Decimal-to-float conversion;
12. API Decimal serialization never uses float and never invents zero for corruption.
13. quoted JSON accepts only the declared dot-decimal grammar and rejects comma/grouping, labels, internal whitespace, underscore, and exponent forms;
14. quoted plus/minus values and default missing tokens behave exactly as declared, while undeclared `N/A` is rejected;
15. the JSON parser, parser-diagnostics path, and collector decoded-convenience path (when retained) each preserve the same high-precision unquoted Decimal with no float object;
16. extreme positive exponent maps to `decimal_precision_exceeded`, extreme negative/nonzero fractional exponent maps to `decimal_scale_exceeded`, and results are independent of ambient Decimal context;
17. excess-zero quantization succeeds under the explicit local context without value change.

### 4.6 Database implications

- Existing `NUMERIC(20,6)`, `NUMERIC(12,6)`, and `NUMERIC(24,6)` columns remain appropriate only when pre-persistence precision/scale validation is enforced.
- Numeric database columns are a final exact-storage backstop, not the parsing policy.
- No float-typed intermediate column, DTO, serializer, or JSON conversion may be introduced for financial fields.
- If lossless decoded raw JSON is required, its storage representation must support exact numeric tokens; otherwise exact raw text is sufficient and canonical.

### 4.7 Open questions

- Does the live BVC endpoint ever emit unquoted fractional financial numbers or exponent notation?
- Does BVC define an official rounding rule for values with more than the target scale, or should all nonzero excess precision remain rejected?
- Should raw exact HTTP bytes be stored in PostgreSQL or object storage when text decoding cannot reproduce the original byte sequence?

## 5. Source-status semantics

### 5.1 Decision

Source status is opaque source evidence until BVC documentation confirms its meaning.

Values such as:

```txt
etatCotVal = N.T
etatCotVal = S
```

MUST be preserved without assigning an undocumented business interpretation.

The parser contract MUST expose first-class fields rather than hiding the information only inside `raw_values`:

```txt
source_status_raw
source_status_normalized
source_status_semantics_confirmed
source_status_mapping_version
selected_price_field
price_kind
```

`source_status_raw` preserves the exact source token. `source_status_normalized` is syntactic only: Unicode NFKC, trim surrounding whitespace, collapse each internal whitespace run to one ASCII space, uppercase, and preserve punctuation exactly. Blank becomes null. Thus `N.T` and `NT` remain distinct until an authoritative dictionary explicitly relates them. Syntactic normalization MUST NOT map a code to words such as “suspended,” “not traded,” or “active” without an authoritative BVC code dictionary.

`source_status_semantics_confirmed` is tri-state: null when status is absent; false when a nonblank code is present but has no authoritative mapping; true only when an authoritative mapping is applied and its nonblank `source_status_mapping_version` is stored. Present/unmapped alone adds `source_status_unconfirmed`; absent status does not. No code in this document is confirmed/mapped yet.

#### Instrument activity

- A row's trading/status code MUST NOT set `Instrument.is_active=false`.
- A row's presence MUST NOT override an authoritative inactive/delisted state.
- `N.T`, `S`, blank, and unknown codes are observations, not listing lifecycle events.

#### Latest price

- A numerically valid `lastTradedPrice` that passes the other section 2 rules MUST produce/update the latest snapshot with `price_kind=last_trade`, meaning source-labeled last trade, and `price_semantics_confirmed=false` until authoritative confirmation; the opaque status is retained as a quality reason/field.
- A fallback to `coursCourant` produces `price_kind=displayed_reference` and `data_quality_status` of at least `suspect` until its semantics are confirmed.
- Source status alone cannot make a missing price publishable. If all approved price aliases are absent, record `missing_price` and write neither latest price nor bar.
- Source status MUST NOT cause an old value to bypass the latest-price temporal policy.

#### Daily bars

- No opaque status code is proof that a daily bar is final.
- A `coursCourant` fallback under `N.T`, `S`, or an unknown code MUST NOT be labeled an official close.
- Such an observation affects a provisional daily bar only when its adapter explicitly verifies both daily-aggregate eligibility and the selected field's bar semantics under section 3; if verified and all validation passes it MUST upsert the provisional row, otherwise it MUST NOT create/update a daily bar.

#### Data quality and metadata

For publishable canonical rows, quality is deterministic:

```txt
no nonblocking quality reason -> valid
one or more nonblocking quality reasons -> suspect
any blocking/missing/invalid rule -> no canonical row; normalization error
```

Reason codes form a sorted deterministic set produced by numeric/OHLC validation, timestamp confidence, selected price kind, and source-semantic confidence. Operational `stale_data` is computed only by section 9 and is never folded into row quality.

- A present opaque/unmapped status adds quality reason `source_status_unconfirmed`, makes the canonical snapshot at least `suspect`, and adds a diagnostics warning.
- A blank/absent status is counted diagnostically but does not by itself downgrade an otherwise direct, valid price unless a later source contract makes the field mandatory.
- `price_semantics_confirmed=false` adds `source_price_semantics_unconfirmed` and makes the snapshot at least `suspect`, including a source-labeled `lastTradedPrice`. This qualifies semantics, not numeric fidelity, and is nonblocking for latest-snapshot storage.
- The opaque status alone does not turn an otherwise direct, numerically valid last-trade snapshot into `missing` or a normalization error.
- A displayed/reference fallback is `suspect` regardless of whether the source code is known syntactically.
- Any workflow that requires a business interpretation of the status, such as daily-bar finalization, MUST remain blocked until the code meaning is confirmed.

The normalized model MUST retain first-class/schema-validated `source_status_normalized`, `selected_price_field`, `price_kind`, `price_semantics_confirmed`, `source_status_semantics_confirmed`, timestamp/trading-date source, quality reasons, and occurrence provenance. The exact raw status token may remain in bounded safe trace metadata/provenance. Arbitrary JSON alone is nonconforming for fields used in precedence or API filtering. No representation may expose unrelated raw row fragments or private HTTP metadata.

#### Diagnostics and errors

Diagnostics MUST report aggregate counts by normalized source status and selected price field, plus counts of blank/unmapped statuses. A previously unseen code is a nonblocking diagnostic warning, not automatically a normalization error.

Normalization errors remain tied to actual violated rules:

- missing all price aliases -> `missing_price`;
- invalid numeric value -> numeric parse error;
- identity conflict -> instrument error;
- status-dependent finalization attempted without confirmed semantics -> block the finalization with a stable rule code.

### 5.2 Rationale

Current code preserves `etatCotVal` only in raw-value metadata and does not use it for quality, activity, price kind, or bar semantics. Tests currently accept `N.T` and `S` rows through `coursCourant`, create active instruments, latest prices, and daily bars, and often label them `valid`.

Preserving the source code is correct. Inferring that it means not traded, suspended, delisted, or final close without authoritative documentation is not. The chosen rule keeps usable displayed values available while preventing them from being misrepresented as trades or official closes.

### 5.3 Example

Source row:

```txt
lastTradedPrice = null
coursCourant = 1240.00
etatCotVal = N.T
transactTime = 2026-05-19T10:00:00Z
```

Required outcome:

```txt
instrument activity = unchanged
latest price = 1240.00
price_kind = displayed_reference
selected_price_field = coursCourant
data_quality_status = suspect
quality reason includes source_status_unconfirmed
source_status_raw = N.T
official final daily bar = not created or finalized from this evidence
normalization error = none, provided all other required fields are valid
```

### 5.4 Edge cases

- Unknown nonblank status with a direct valid last price: preserve it, emit an unmapped-status warning, and do not invent meaning.
- Blank status with a direct valid price: the price MUST normalize when all other rules pass; record status as absent.
- Known status token changes between two occurrences: preserve both occurrences; update latest snapshot only according to timestamp/evidence rules.
- Status suggests a state that conflicts with a valid direct price: do not infer which field is wrong; retain raw evidence and flag a semantic warning.
- Status present but all price aliases missing: blocking `missing_price` remains.
- HTML `Statut` field: preserve raw/normalized token under the same opaque policy once the HTML parser maps it.

### 5.5 Required tests

Later implementation MUST test:

1. raw and normalized status tokens are preserved separately;
2. `N.T`, `S`, unknown, and blank statuses never change instrument activity;
3. direct last-traded price retains `price_kind=last_trade` and opaque-status metadata;
4. null/blank primary price falling back to `coursCourant` is labeled displayed/reference and suspect;
5. missing every price alias is a blocking error regardless of status;
6. opaque status cannot finalize a daily bar;
7. status aggregates appear in diagnostics without raw fragment exposure;
8. a new/unmapped code produces a warning rather than a fabricated mapping;
9. HTML and JSON status tokens use the same safe storage contract;
10. public API metadata exposes only explicitly allowlisted status/price semantics.
11. NFKC/whitespace/case normalization is deterministic, punctuation is preserved, and `N.T` remains distinct from `NT`;
12. a direct source-labeled price with any present unconfirmed `N.T`, `S`, or unknown code is `suspect`, while a blank status adds no status-specific downgrade;
13. any nonblocking reason produces `suspect`, any blocking rule writes no canonical row, and operational staleness never changes row quality;
14. source-labeled `last_trade` is never presented as independently confirmed execution while its semantics flag is false.
15. an unconfirmed source-price semantics flag adds the deterministic suspect reason even when status is blank and numeric validation passes.
16. absent status stores null confirmation/version, present-unmapped stores false/null and adds its reason, and a future mapped fixture stores true/nonblank mapping version without inventing an unmapped warning.

### 5.6 Database implications

- Source status MUST be a bounded string, not a PostgreSQL enum, because its vocabulary is controlled externally and not yet confirmed.
- Canonical system fields such as `price_kind`, `bar_state`, and quality reasons require validated internal vocabularies and database checks.
- Source status, selected price field, price kind/semantics flags, timestamp/trading-date source, bar state, quality reasons, and occurrence provenance MUST be first-class columns or strictly schema-validated structured fields covered by checks where system-owned; arbitrary JSON is nonconforming.
- Add a cross-field check/validated schema for the status tri-state: absent -> confirmation/version null; present-unmapped -> false/version null; present-mapped -> true/nonblank version.
- Source status values must never be used as an instrument-activity database constraint.

### 5.7 Open questions

- What are the authoritative BVC meanings of `N.T`, `S`, `T`, `NT`, and any other observed codes?
- Is `coursCourant` a trade, previous/reference price, theoretical value, or display-only value under each code?
- Does the HTML `Statut` column use the same vocabulary and semantics as JSON `etatCotVal`?
- Is there an authoritative status/finalization signal suitable for daily-bar promotion?

## 6. Page and group status

### 6.1 Decision

Collector outcome, raw-content storage, diagnostics, normalization, page result, group result, pagination completeness, and scheduler acceptance are separate state dimensions. One field MUST NOT stand in for all of them.

#### Status meanings

```txt
success
```

The phase completed all of its requirements without row/page/integrity errors.

```txt
partial_success
```

At least one usable row/page completed, but one or more nonfatal row, page, duplication, or completeness problems occurred.

```txt
failed
```

No usable normalized row exists for the applicable page/group, or a blocking structural/identity/integrity failure prevents trusting the result.

`skipped` remains valid only for an intentionally disabled/no-op operation or a terminal sentinel whose normalization is intentionally not applicable. It is not a substitute for a hidden failure.

#### Page rules

- `collection_page_outcome=success` requires a selected acceptable 2xx occurrence, successful raw storage, and structural diagnosis sufficient to establish a data-page or terminal-sentinel role. Malformed/unexpected shape, empty HTTP body, unresolved multiple successful versions, exhausted non-2xx/transport failure, or unknown page role is `collection_page_outcome=failed`. Row-level normalization quality does not change this acquisition/structure outcome.
- A diagnostic `partial_success` MUST mean row boundaries and the source structure are trustworthy, at least one row is parseable, and invalid rows can be isolated. Valid rows MUST then proceed to normalization; the page result is `partial_success`.
- Structural ambiguity, malformed JSON, unexpected top-level shape, or inability to identify safe row boundaries is diagnostic `failed`, not partial.
- A normalizer `partial_success` always makes the page result `partial_success`.
- A raw data page with all rows normalized and no blocking warning is page `success`.
- A publishable `suspect` row with only declared nonblocking reason codes is still normalized successfully and does not by itself make the page partial. A blocking rule/error does. Any reason code without a declared severity is blocking until classified.
- A terminal zero-row sentinel after valid contiguous pages is page-role `terminal_sentinel`, page result `success`, and normalization `skipped`. It does not count as a processed data page.

For this BVC offset collector, every group declares one positive `page_limit`. Logical pages start at 1 and require `page_offset=(logical_page_number-1)*page_limit`. A future `group_pages` record MUST be unique on both `(pagination_group_id, logical_page_number)` and `(pagination_group_id, page_offset)`. Every page/retry/redirect occurrence references its owning `group_page_id`.

Selection uses a `group_page_selections` record with primary/unique `group_page_id`, unique `occurrence_id`, and a composite FK `(occurrence_id, group_page_id)` to a unique matching pair on `collection_occurrences`. This structurally guarantees that one occurrence cannot be selected for two pages and a page cannot select an occurrence owned by another group/page/offset. Retry attempts remain separate occurrences for the same logical page; success/role eligibility remains repository logic.

Retry selection is deterministic:

1. the first occurrence in strict occurrence order that satisfies the successful page/terminal rules becomes the selected occurrence and ends normal retry behavior;
2. earlier HTTP/transport failures remain occurrence evidence but do not make a recovered logical page or group partial;
3. if an implementation nevertheless obtains several successful occurrences with identical raw content for one logical page, select the earliest and record a nonblocking duplicate-attempt diagnostic;
4. if several successful occurrences for one logical page contain different content, the page is `failed` with `multiple_successful_page_versions`, no candidate is selected for publication, and scheduler acceptance is prohibited;
5. a failed attempt after an already selected page is an operational anomaly and cannot replace/unselect the page. If it belongs to the same group, that group is permanently production-ineligible and `partial_success`; no review flag can waive the anomaly. Recovery requires a new group with a new sequence and clean request history. An attempt proven to belong to another group is evaluated only in that other group; moving an occurrence between groups is prohibited.

#### Group rules

- Group processing result `success` requires proven pagination completeness, every selected data page result `success`, zero cross-page duplicate symbols, and zero blocking identity/value conflicts. It is computed before and independently of publication.
- Group `partial_success` requires at least one normalized usable row and any row/page error, duplicate, missing page, unknown completeness, later failure, or other nonblocking group issue.
- Group `failed` means zero usable normalized rows or a blocking group-level integrity conflict that makes all rows unsafe.
- Duplicate symbols may leave `pagination_complete=true` because coverage and quality are distinct, but group status is partial and scheduler acceptance is false.
- A cross-page identity conflict in which one normalized symbol/ISIN resolves to incompatible instruments is a blocking group-level integrity failure: group `failed`, publication prohibited, even if unrelated rows were individually usable. An identical duplicate symbol without identity conflict remains group `partial_success`.

Acceptance and publication are separate deterministic states:

```txt
group processing result -> acceptance eligibility -> atomic publication state
```

Production acceptance eligibility requires all of:

- group processing result `success`;
- `pagination_complete=true` with live/source-authoritative evidence;
- `instrument_coverage_status=proven` under an approved coverage policy;
- eligible production collection mode and source contract;
- no active blocking quality/error waiver;
- all current-snapshot value revisions/reuse memberships staged and ready for one atomic publication.

`instrument_coverage_status` is one of `proven | violated | unknown | not_configured`. Only `proven` permits production acceptance. `violated`, `unknown`, and `not_configured` set acceptance eligibility false. The evidence may be an authoritative source total/universe or another separately approved deterministic policy; a directory page count or absence of errors is not proof.

Publication state is `not_evaluated | ineligible | eligible | published | publication_failed | superseded`. A group becomes an **accepted group** only at `published`, when the current-snapshot pointer for publication scope `(BVC, bvc_equity_prices, production)` is changed atomically with all staged latest-price revisions. Publication failure does not change the already-computed group processing result, but it sets the authoritative end-to-end run to `failed` and scheduler acceptance false. A validation/manual run may end with publication `not_evaluated`; its processing status does not make it accepted.

Legal publication transitions are:

```txt
not_evaluated -> ineligible | eligible
eligible -> published | publication_failed
publication_failed -> eligible          # only after the same staged revision is verified retry-safe
published -> superseded                  # atomically when a newer eligible group is published
```

`ineligible` and `superseded` are terminal for that publication evaluation. A failed/partial processing result cannot transition to eligible without creating a new processing revision and recomputing eligibility; terminal run/group history is never rewritten.

Every publication try is an immutable `publication_attempt` linked to its own ingestion/retry run. If the first attempt fails, that attempt and original end-to-end run remain `failed`. Retrying the same unchanged staged revision creates a new publication attempt/run; after re-verifying retry safety it produces the derived `publication_failed -> eligible -> published` group transition. It MUST NOT rewrite the prior attempt/run to success. If staged content changed, it is a new processing revision and eligibility evaluation, not a publication retry.

#### Pagination completeness

`pagination_complete=true` requires positive evidence and contiguous required pages. Accepted evidence is one of:

1. an authoritative total row/page count with every required page present;
2. a verified source `next` indicator proving no next page;
3. offset pagination ending in a nonempty short page (`row_count < limit`);
4. offset pagination ending in a valid zero-row terminal sentinel after one or more contiguous full pages;
5. explicit operator-supplied expected pages for a manual fixture group, with all expected pages present, proving only bounded fixture-scope completeness.

Every completeness result MUST include `completion_scope=live_source | manual_fixture | approved_replay` and a machine-readable evidence kind. A bare boolean without scope/evidence is nonconforming.

The following are never completion evidence by themselves:

- highest observed page number;
- number of files in a directory;
- all pages collected before a fetch/parse failure;
- `max_pages` reached;
- manual stop;
- no known page count;
- absence of an error.

A later fetch failure or `max_pages` stop MUST set `pagination_complete=false` unless independent authoritative evidence proves every required page was already obtained.

Fixture-scope completeness MUST NOT be relabeled as live/source completeness, production data freshness, or scheduler acceptance. A fixture/replay can become production-eligible only under a separate approved replay policy with preserved authoritative source coverage and observation evidence; an operator-entered file list alone is insufficient.

#### Raw-content and processing state

A received body has immutable raw-content storage status `stored`. Contextual outcomes such as `diagnostic_failed`, `normalized`, `normalized_partial`, or `terminal_sentinel` belong to the selected page/processing attempt, never to deduplicated raw content. No raw-content row exists when no response was received. If a response arrives but raw persistence fails, the collector/group fails and no parsing or normalization may proceed.

The existing mutable `RawPayload.status` values (`collected`, `parsed`, `normalized`, `ignored`, `failed`) are a legacy non-authoritative projection to be deprecated/migrated. In the target model, parser/normalizer state lives on immutable processing attempts; the same content may validly have different outcomes under different occurrence roles or rule versions without mutation or contradiction.

#### Required scenario matrix

"Raw content" below reports only whether immutable response content was stored. "Processing outcome" is contextual to the logical page/attempt. Transport outcome lives on each collection occurrence. "Run status" is the authoritative end-to-end ingestion/pipeline run, not merely the HTTP phase. "Scheduler acceptance" means eligibility; actual acceptance additionally requires successful atomic publication.

| Scenario | Collector status | Raw content | Processing outcome | Page result | Group result | `pagination_complete` | Ingestion-run status | Scheduler acceptance |
|---|---|---|---|---|---|---:|---|---:|
| All rows valid; terminal/total evidence and instrument coverage are proven | `success` | every received body `stored` | data pages `normalized`; sentinel `terminal_sentinel` | data pages `success`; sentinel `success/skipped` | `success` | `true` | `success` after publication | allowed |
| All rows valid and pagination complete, but instrument coverage is `unknown`/`not_configured` | `success` | `stored` | `normalized` | `success` | processing `success` | `true` | authoritative production run `partial_success` (ineligible) | prohibited |
| Some row-level normalization errors; other rows valid | `success` | `stored` | `normalized_partial` with error counts | `partial_success` | `partial_success` | based only on pagination evidence | `partial_success` | prohibited |
| Parser diagnostic partial but rows safely isolated | `success` | `stored` | `diagnosed_partial`, then safe rows `normalized_partial` | `partial_success` | `partial_success` | based only on pagination evidence | `partial_success` | prohibited |
| Normalizer partial success | `success` | `stored` | `normalized_partial` | `partial_success` | `partial_success` | based only on pagination evidence | `partial_success` | prohibited |
| Malformed/unexpected first page | `failed` | received body `stored` | `diagnostic_failed` | `failed` | `failed` | `false` | `failed` | prohibited |
| Malformed/unexpected later page after usable pages | `partial_success` | all received bodies `stored` | bad page `diagnostic_failed` | bad page `failed` | `partial_success` | `false` | `partial_success` | prohibited |
| Empty HTTP body on first request | `failed` | zero-byte body `stored` | `empty_body_failed` | `failed` | `failed` | `false` | `failed` | prohibited |
| Empty HTTP body on a later request | `partial_success` if prior pages usable, otherwise `failed` | zero-byte body `stored` | `empty_body_failed`; not a JSON sentinel | `failed` | `partial_success` or `failed` | `false` | same as group | prohibited |
| Structurally valid zero-row first page | `failed` by safe default | body `stored` | `empty_first_page_failed` | `failed` | `failed` | `false` | `failed` | prohibited |
| Valid zero-row later page after contiguous full valid pages | `success` | sentinel body `stored` | `terminal_sentinel`; normalization skipped | `success`, role sentinel | `success` only if prior pages succeed | `true` | same as final publication result | allowed only when all acceptance checks pass |
| Valid zero-row later page conflicts with authoritative expected pages | `partial_success` | body `stored` | `terminal_conflict` | `failed` | `partial_success` if prior usable rows exist, otherwise `failed` | `false` | same as group | prohibited |
| First-page non-2xx after retries exhausted, body present or empty | `failed` | received body, including zero bytes, `stored` | occurrence `http_error_response`; no normal parsing | `failed` | `failed` | `false` | `failed` | prohibited |
| Later-page non-2xx after retries exhausted | `partial_success` if prior pages usable, otherwise `failed` | response body, including zero bytes, `stored` | occurrence `http_error_response`; no normal parsing | `failed` | `partial_success` or `failed` | `false` | same as group | prohibited |
| Retry receives 503, then one valid 200 for the same logical page and all other pages succeed | `success` | both bodies `stored` | 503 attempt retained; 200 selected and normalized | `success` with retry diagnostics | `success` | based on final coverage evidence | `success` after all acceptance/publication checks | allowed; failed attempt remains operational evidence |
| Failed response/transport attempt occurs after that logical page was already selected in the same group | `partial_success` | response body `stored`, or no content for transport failure | selected result retained; extra attempt is a blocking operational anomaly | selected page remains `success` with anomaly | `partial_success` | determined only by coverage evidence and may remain `true` | `partial_success` | prohibited permanently for that group; retry as a new group |
| Several successful retry responses for one page have different bodies | `partial_success` if other pages usable, otherwise `failed` | every body `stored` | `multiple_successful_page_versions` | `failed` | `partial_success` or `failed` | `false` | same as group | prohibited |
| Missing expected page | `partial_success` if other pages usable, otherwise `failed` | no content for missing page | no selected occurrence/processing attempt; group records gap | missing page `failed` | `partial_success` or `failed` | `false` | same as group | prohibited |
| Later-page transport failure after retries exhausted | `partial_success` if prior pages usable, otherwise `failed` | no raw content for no-response attempt | occurrence `transport_failure`; no processing | failed logical page | `partial_success` or `failed` | `false` | same as group | prohibited |
| Duplicate symbols across otherwise valid pages | `success` | bodies `stored` | pages `normalized` | pages `success` | `partial_success` | determined independently by coverage evidence and can be `true` | `partial_success` | prohibited |
| Same symbol/ISIN keys resolve to incompatible identities across pages | `success` acquisition | bodies `stored` | affected rows blocked with identity conflict | affected pages `failed`/`partial_success` | `failed` integrity result | determined independently by coverage evidence and can be `true` | `failed` | prohibited |
| `max_pages` reached without authoritative completion | `partial_success` if pages usable, otherwise `failed` | received bodies `stored` | valid received pages normalize | valid received pages succeed | `partial_success` or `failed` | `false` | same as group | prohibited |
| `max_pages` exactly covers authoritative total | `success` if all logical pages succeed | bodies `stored` | `normalized` | `success` | `success` | `true` | `success` after publication | allowed if instrument coverage and other checks pass |
| Page count unknown; last page is short | `success` if all logical pages succeed | bodies `stored` | `normalized` | `success` | `success` | `true` | `success` after publication | allowed only if separate instrument coverage is proven |
| Page count unknown; last observed page is full and no terminal evidence exists | `partial_success` if pages usable | bodies `stored` | valid received pages normalize | valid received pages succeed | `partial_success` | `false` | `partial_success` | prohibited |
| Data page has no usable rows after a successful response/storage | acquisition `success`; processing fails | body `stored` | `normalization_failed` | `failed` | `failed` if no other usable page, otherwise `partial_success` | pagination-evidence-dependent | same as group | prohibited |
| Operator-declared manual fixture pages are all valid | `success` in fixture mode | fixture contents `stored` with fixture occurrence | `normalized` | `success` | `success` for fixture scope | `true` only for declared fixture scope | validation run `success` | prohibited for production |
| Group processing succeeds but atomic publication fails | `success` | bodies `stored` | `normalized` | `success` | processing result `success`; publication `publication_failed` | `true` | `failed` | prohibited until publication is safely retried |

An ingestion run that represents the authoritative production pipeline MUST be finalized only after group diagnostics, normalization, eligibility, and attempted publication. It is `success` only when publication is `published`; it is `partial_success` when usable processing exists but quality/coverage makes the group ineligible; it is `failed` when no usable rows exist, a blocking integrity failure occurs, or atomic publication fails. A separately typed `validation`/fixture run can be `success` without publication, but it is never scheduler-accepted for production. If collection uses a separate run record, that record reflects acquisition only and MUST NOT be presented as the authoritative pipeline run.

### 6.2 Rationale

Current behavior treats diagnostic partial success as page failure, can treat normalizer partial success as group success, and defines completeness as “no numerically missing page.” It may therefore report `pagination_complete=true` after `max_pages` or a later fetch failure. The collector also reports success for an empty first page or max-page stop in some paths.

Separating acquisition, processing, quality, and completeness prevents a partial market snapshot from appearing fully successful.

### 6.3 Example

Page 1 returns 50 valid rows. Fetching page 2 times out.

Required outcome:

```txt
collector = partial_success
page 1 raw content = stored; page 1 processing = normalized if processing succeeds
page 2 occurrence = failed, with no raw content if no response arrived
page 1 result = success
page 2 result = failed
group = partial_success
pagination_complete = false
authoritative ingestion/pipeline run = partial_success
scheduler acceptance = false
```

The 50 usable rows and page-1 processing result remain auditable, but the group is not eligible to replace the last accepted complete market snapshot. In the target model, `normalized` is page-processing state, not mutable state on the deduplicated raw body.

### 6.4 Edge cases

- A diagnostic with one valid and one invalid row is partial only if row boundaries are trustworthy.
- A malformed response received after complete authoritative page coverage is still a failed extra attempt; it does not retroactively change coverage, but the group/run policy must explain why the extra request occurred and scheduler acceptance remains conservative unless the attempt is outside the group.
- Duplicate symbol with identical values is still a group anomaly; do not silently ignore it.
- Duplicate symbol with conflicting identity/value may become a blocking identity conflict.
- An operator provides pages 1 and 2 but no declared expected count and both are full: incomplete, despite directory exhaustion.
- A short first page with valid rows is valid terminal evidence and makes a complete one-page group when all other page rules pass.
- An authoritative total of zero produces a successful empty market only when an explicit source contract confirms that zero is legitimate and supplies authoritative evidence; otherwise empty-first remains failed.
- The same zero-byte/raw JSON content can be an empty-first failure in one occurrence and a terminal sentinel in another; its immutable content row stays `stored`, while processing/page outcomes differ.
- A recovered retry does not hide the failed occurrence and does not make a successfully selected logical page partial.
- A fixture can be complete for its declared files without being complete for the live source.

### 6.5 Required tests

Later implementation MUST have one focused test for every row in the scenario matrix, plus tests proving:

1. diagnostic partial rows continue through row-isolated normalization;
2. structural ambiguity is failed, not partial;
3. normalizer partial makes page, group, and run partial;
4. `pagination_complete` is independent of duplicate/error status;
5. later fetch failure always forces incomplete without independent completion evidence;
6. `max_pages` is incomplete without authoritative evidence and complete with it;
7. terminal empty page is preserved and does not count as a normalized data page;
8. no usable rows makes the group failed;
9. collector-only success is not scheduler acceptance;
10. group/run aggregation is deterministic regardless of page processing order.
11. every additional HTTP/retry/fixture/publication scenario in the expanded matrix;
12. one logical page selects the first successful occurrence after failed retries;
13. several identical successful attempts select the earliest, while different successful bodies fail the logical page;
14. raw content remains `stored` while the same content receives different contextual processing outcomes;
15. fixture completeness never enables production acceptance/freshness;
16. `instrument_coverage_status` values other than `proven` block production acceptance;
17. processing success, eligibility, and publication state transition independently and publication failure fails the end-to-end run.
18. incompatible cross-page symbol/ISIN identity fails the group, while an identical duplicate leaves it partial.
19. any failed same-group attempt after page selection leaves the selected page immutable but makes the group permanently partial/ineligible; only a distinct new group can recover.

### 6.6 Database implications

- Store phase statuses separately or in a validated group/run model: collection, diagnostics, normalization, group outcome, completeness, coverage, eligibility, and publication.
- Add a first-class pagination/group record with stop reason, completion evidence/scope, expected/found pages, totals, coverage state, and final processing result.
- Add `group_pages` with unique `(pagination_group_id, logical_page_number)` and `(pagination_group_id, page_offset)` and fixed positive group page limit. Add occurrence-to-page FKs plus the unique/composite `group_page_selections` binding above. Occurrences remain one-to-many for retries.
- Add database checks for each internal status vocabulary. Source statuses remain opaque external strings.
- Status-transition legality belongs in repository/pipeline logic; a finite-value check alone cannot enforce transitions.
- Page/group processing records should reference collection occurrences and raw content, not copy mutable raw metadata.
- Add one atomic publication pointer unique on `(exchange_id, dataset_code, publication_channel)`, with the production BVC price scope defined above. Partial/ineligible groups MUST NOT replace the last accepted snapshot.

### 6.7 Open questions

- Does the BVC JSON response expose an authoritative total count, page count, or reliable next link?
- Can a legitimate official first page contain zero listed instruments, and what evidence would distinguish that from a source failure?
- What authoritative instrument-universe/coverage evidence will make `instrument_coverage_status=proven` for production BVC collections?

## 7. Raw-first and collection-occurrence policy

### 7.1 Decision

Every HTTP response that reaches the client MUST be audit-preserved before JSON/HTML parsing, row counting, shape validation, or normalization.

Required principle:

```txt
Identical content may be deduplicated without losing evidence that a new collection attempt occurred.
```

Raw content identity and collection occurrence identity are different concepts and MUST be represented separately.

#### Raw content identity

Raw content is immutable and identified by:

```txt
source_id + sha256(exact client-visible entity-body bytes)
```

For this contract, **entity-body bytes** are the bytes exposed by the HTTP client after HTTP transfer/content decoding (for example gzip decompression) but before charset decoding, Unicode normalization, JSON parsing, newline normalization, or text re-encoding. Raw on-wire compressed bytes are outside the current client capability and MUST NOT be claimed as stored. Safe `content-encoding` evidence is retained on the occurrence.

Request URL, endpoint, run ID, page number, HTTP status, timestamps, headers, and pagination metadata MUST NOT participate in the exact content hash. They belong to the occurrence. A separate semantic hash MAY later support equivalence checks, but it MUST NOT replace exact entity-body identity.

The current hash of `source_url + normalized text body` is not the target content identity because it prevents deduplication of identical bytes received from different URLs and cannot represent exact bytes.

#### Collection occurrence identity

Every logical request attempt, including each retry response, has a unique occurrence. At minimum it records:

```txt
id
occurrence_sequence
source_id
ingestion_run_id
raw_payload_id nullable
request_sequence
attempt_number
redirect_hop
logical_request_url
requested_url
response_url nullable
source_endpoint
requested_at
response_received_at nullable
finished_at
http_status nullable
content_type nullable
body_length nullable
outcome
safe_error_type nullable
safe_error_message nullable
safe_response_headers
group_page_id
pagination/stop evidence
```

`group_page_id` is the sole stored ownership key for a BVC price occurrence. `pagination_group_id`, logical page number, page offset, and page limit MUST be obtained from the referenced `group_pages`/pagination-group records and MUST NOT also be copied into independently writable occurrence columns. Redirect hops and retries for one logical request share the same `group_page_id`; their request, attempt, and redirect-hop identity remains distinct. If a future storage design intentionally denormalizes any page field, a composite foreign key covering every copied ownership value MUST make a mismatch impossible in direct SQL.

`ingestion_run_id`, `request_sequence`, `attempt_number`, and `redirect_hop` are non-null. Numbering is one-based for request/attempt and zero-based for redirect hop. `occurrence_sequence` is a unique immutable database ordering value. Manual fixtures use request sequence 1..n, attempt 1, and redirect hop 0 rather than null sentinels.

Transport failure before any response creates an occurrence with `raw_payload_id=null`. Any received response, including zero bytes, redirect responses, or non-2xx status, creates or reuses a raw content row and links it to the occurrence.

Every redirect hop is a separate occurrence/exchange. A 3xx response body is preserved, its occurrence outcome is `redirect_response`, and the following request increments `redirect_hop`. Only the final qualifying 2xx occurrence may be selected as the logical data page. If redirect following is disabled or a redirect chain exceeds policy, the logical attempt fails after preserving all responses received. URL fields MUST pass a source-specific sanitizer: remove userinfo/fragments and reject or redact query names/values outside the public allowlist (`offset`, `limit`, and other explicitly documented BVC pagination keys). Redirect `Location` values are not stored as arbitrary headers.

Occurrence error fields use stable bounded error codes and sanitized messages. Raw exception text MUST NOT be persisted/logged when it can contain URLs, query values, request/response headers, cookies, tokens, proxy data, or body excerpts.

#### Required response behavior

| Received outcome | Raw-first behavior |
|---|---|
| Successful normal JSON | Store/reuse exact content, create occurrence, then diagnose/normalize |
| Empty body | Store/reuse zero-byte content, create occurrence, classify failed; never discard because it is empty |
| Malformed JSON | Store/reuse exact content, create occurrence, then mark processing failure |
| JSON with unexpected shape | Store/reuse exact content, create occurrence, then mark structural failure |
| Non-2xx with a body | Store/reuse exact content and occurrence with HTTP failure; do not normalize as a normal data page |
| Non-2xx without a body | Store/reuse zero-byte content and occurrence with HTTP failure |
| Transport exception/no response | Create failed occurrence without raw content |
| Duplicate body in a later run | Reuse immutable content and create a new occurrence containing the new run/page/timestamps/outcome |
| Redirect response | Store/reuse its entity body and safe headers as its own hop occurrence; continue only within redirect policy |

Occurrence outcome/evidence combinations are closed and database-checked:

| Outcome | Required evidence |
|---|---|
| `success_response` | `raw_payload_id`, `response_received_at`, `response_url`, and `http_status` non-null; status 200–299 |
| `redirect_response` | same response evidence non-null; status in `{301, 302, 303, 307, 308}`; redirect policy/next-hop validity remains repository diagnostics |
| `http_error_response` | same response evidence non-null; status 100–599 but neither 200–299 nor an allowed redirect status; never parsed as a normal page |
| `transport_failure` | `raw_payload_id`, `response_received_at`, `response_url`, and `http_status` all null; requested/finished/error evidence non-null |
| `fixture_loaded` | `raw_payload_id` non-null; HTTP response time/URL/status null; fixture mode and load/declared observation evidence explicit |

All non-null HTTP status values are 100–599. Response outcomes require `requested_at <= response_received_at <= finished_at`; transport/fixture outcomes require `requested_at <= finished_at`. A zero-byte response still has a raw-content FK. Direct SQL combinations outside this table are invalid.

Raw content must be stored with storage state `stored` before calling `json.loads()`, extracting row count, or deciding an empty/malformed stop reason. Diagnosis/normalization state is written to processing attempts, not back to raw content.

Page/group/stop metadata belongs to the collection occurrence or group record. A later duplicate occurrence MUST NOT mutate the old raw content row's ingestion run, collection time, page group, stop reason, or headers.

Normalized records that claim source traceability SHOULD reference both:

- the immutable raw content that supplied the values; and
- the collection occurrence whose timing/group context justified the canonical update.

Operational freshness is derived from occurrences and group state, never from the creation/collection timestamp of a deduplicated content row.

The existing `raw_payloads` model is not sufficient by itself because it stores only one ingestion run, URL, collection time, HTTP status, mutable metadata, and contextual status for deduplicated content. A future `collection_occurrences` table or equivalent is required. Diagnosis and normalization MUST use immutable processing-attempt/event records rather than overwriting a raw-content lifecycle state.

### 7.2 Rationale

Current JSON collection decodes and counts rows before inserting raw data. Empty, malformed, and unexpected-shape bodies can be lost. The HTTP client raises for non-2xx and empty bodies before returning response metadata. Duplicate content returns an old raw row, and later code can mutate its pagination stop metadata, making audit and freshness misleading.

Separating immutable content from occurrences provides both content deduplication and a faithful audit of every attempt.

### 7.3 Example

Run A receives page 1 body hash `abc` at 12:00. Run B receives identical bytes at 12:05.

Required storage:

```txt
raw content rows = 1
collection occurrences = 2

occurrence A -> raw abc, run A, received 12:00, group A, page 1
occurrence B -> raw abc, run B, received 12:05, group B, page 1
```

Run B proves a new collection occurred and advances latest attempt/collected time. It advances published collection freshness only if its full group later becomes accepted, without changing raw content creation time or falsely rewriting occurrence A's provenance.

### 7.4 Edge cases

- Retry receives 503 with body, then 200 with another body: preserve two occurrences and both content bodies.
- Two pages return identical bytes: preserve two occurrences with different page contexts; duplicate-page diagnostics can then identify the anomaly.
- Same bytes arrive from HTML and JSON endpoints: source and content type/endpoint evidence remains occurrence metadata; parser dispatch must not rely on mutable content metadata alone.
- Gzip and identity transfer encodings that yield identical entity bytes reuse raw content while retaining different safe encoding headers on their occurrences.
- A redirect chain preserves every hop response; only its final successful data response can satisfy a logical page.
- Body decoding fails: preserve exact bytes, encoding/content-type evidence, and a safe decode error.
- Empty body repeats frequently: one zero-byte content row may serve many occurrences.
- Reprocessing old raw content without a new HTTP request: create a processing attempt, not a collection occurrence.
- Manual fixture load: create an occurrence with mode `manual_fixture`; it must not masquerade as a live source observation.

### 7.5 Required tests

Later implementation MUST test:

1. normal JSON content is stored before diagnostics;
2. empty body creates content plus occurrence and fails explicitly;
3. malformed and unexpected-shape JSON are preserved exactly;
4. non-2xx bodies are preserved;
5. transport failure creates an occurrence with no content;
6. every retry response receives its own attempt number/occurrence;
7. duplicate content in a later run creates a new occurrence but not a new content row;
8. duplicate pages can share content while preserving distinct page occurrence metadata;
9. later occurrences never mutate old content/run/group metadata;
10. freshness advances from occurrences rather than content creation time;
11. reprocessing creates processing evidence without a fake collection occurrence;
12. concurrent duplicate-content inserts converge safely under PostgreSQL.
13. gzip-decoded entity bytes are hashed before charset/JSON decoding and the contract never claims on-wire-byte preservation;
14. every redirect response/body becomes a hop occurrence and only the final valid response is page-selectable;
15. occurrence identity components are non-null with declared numbering for live, retry, redirect, and fixture cases;
16. URL sanitization strips userinfo/private query fields and retains only approved pagination keys;
17. one raw body receives distinct parser-version/page-role outcomes without changing its immutable storage row.
18. transport/HTTP error persistence uses safe codes/messages and never leaks private URL/header/body data.
19. direct PostgreSQL checks enforce every occurrence outcome/evidence combination, status range, and timestamp ordering, including fixture and zero-byte cases.
20. direct PostgreSQL cannot bind an occurrence or page selection to page A while storing or deriving group/page/offset/limit provenance from page B.

### 7.6 Database implications

- Retain or refactor `raw_payloads` as immutable content storage with unique `(source_id, entity_body_sha256)`.
- Add `collection_occurrences` with a unique `(ingestion_run_id, request_sequence, attempt_number, redirect_hop)`, a unique immutable `occurrence_sequence`, non-null identity components, a non-null `group_page_id` FK for this slice, and a nullable FK to raw content. Page/group/offset/limit ownership is derived from that parent rather than copied.
- Add cross-field `CHECK` constraints (or structurally separate response/failure tables with equivalent guarantees) for the complete outcome/evidence table above.
- Add indexes for source/time, run, group/page, outcome, HTTP status, and raw content.
- Move URL, received time, request attempt, group/page, transport outcome, and safe response headers to occurrence/group storage.
- Add occurrence provenance to canonical records or a canonical-revision link.
- Add a separate processing-attempt table keyed by immutable attempt ID, raw content, parser/normalizer version, and occurrence/group context so reprocessing history is not overwritten.
- Migration/backfill must preserve existing raw rows and synthesize one occurrence for each legacy raw row/run relationship where evidence exists. It MUST label missing occurrence history as unknown rather than invent it.

### 7.7 Open questions

- Should exact response bytes live in PostgreSQL or object storage when payload size/encoding makes text insufficient?
- What parser/normalizer version identifier is required for processing-attempt idempotency?
- How should legacy duplicate-run metadata be backfilled when one raw row was referenced by several ingestion runs but only one run FK exists?
- Is preserving client-visible entity bytes sufficient for regulatory/audit needs, or is a lower-level transport client required later to preserve on-wire compressed bytes too?
- Which redirect hosts and public BVC query parameters are allowlisted for collection URL sanitization/following?

## 8. Safe response-header retention

### 8.1 Decision

Response headers MUST be filtered through a strict, case-insensitive allowlist before persistence or structured logging.

Allowed response header names are:

```txt
content-type
content-length
content-encoding
etag
last-modified
date
cache-control
```

Rules:

1. Normalize allowed names to lowercase.
2. Denylist rules take precedence over an allowlist.
3. `content-type`, `content-length`, `content-encoding`, `etag`, `last-modified`, and `date` are singleton fields. Exactly one valid instance is required to retain that name; two or more instances, even identical, drop the whole name with a safe diagnostic. `cache-control` is repeatable and retains its received value order.
4. Store values as `lowercase-name -> array of strings`, including a one-element array for every retained singleton. Sort field names lexicographically for persistence; retain only `cache-control` value order.
5. Before aggregate sizing, drop a field name if any candidate value contains control characters/newlines, a value exceeds 2,048 UTF-8 bytes, or the repeatable name has more than 16 values. Do not truncate evidence.
6. Compute aggregate size from a canonical minified UTF-8 JSON encoding with lexicographically sorted names and `ensure_ascii=false`. If the candidate object exceeds 8,192 bytes, persist an empty header object, set `response_headers_overflow=true`, and count every candidate name as dropped. Do not keep an iteration-order-dependent subset. Otherwise persist the object with `response_headers_overflow=false`.
7. Store safe headers only on the collection occurrence, never on immutable content identity.
8. Safe headers remain internal and are not automatically exposed by public APIs.
9. Unknown headers are dropped by default. Record only `dropped_response_header_name_count`, defined as the number of distinct normalized rejected/dropped names across denylist, unknown, invalid, duplicate-singleton, per-field-bound, and aggregate-overflow decisions; repeated instances of one name count once. Do not persist rejected names, values, or instance counts.
10. Header values MUST NOT participate in raw content identity.

The following MUST never be retained or logged:

```txt
set-cookie
cookie
authorization
proxy-authorization
csrf headers or tokens
waf identifiers
session identifiers
browser security tokens
private request identifiers
unknown security headers
```

Prefixes and vendor-specific variants that contain authentication, cookie, CSRF, WAF, session, or private security data are denied even if a future allowlist accidentally overlaps them.

Request behavior SHOULD be identified by a safe named request-profile/version rather than storing complete request headers. Explicitly approved non-secret request values may be stored separately only when operationally necessary.

### 8.2 Rationale

Current collection copies every response header into raw metadata. Public API redaction prevents external exposure but does not prevent sensitive cookies, WAF values, or session identifiers from being persisted or logged.

A small allowlist retains useful content/cache diagnostics without turning raw storage into a secret/session archive.

### 8.3 Example

Received headers:

```txt
Content-Type: application/json
ETag: "abc123"
Set-Cookie: session=secret
X-WAF-Token: private
X-Unknown: value
```

Stored occurrence metadata:

```json
{
  "response_headers": {
    "content-type": ["application/json"],
    "etag": ["\"abc123\""]
  },
  "dropped_response_header_name_count": 3,
  "response_headers_overflow": false
}
```

No dropped name or value appears in storage, logs, diagnostics, or API output.

### 8.4 Edge cases

- Mixed-case `Set-Cookie` is denied.
- Repeated `cache-control` values retain received order; every repeated singleton name is dropped even when values are identical.
- Oversized or newline-containing `etag` is dropped.
- A vendor header that looks useful but is not allowlisted is dropped until this contract is intentionally revised.
- An allowed `date` header is transport metadata and MUST NOT become the market source timestamp without a separate source contract.
- `content-length` disagreement with received byte length is retained as diagnostic evidence and flagged; received bytes remain authoritative.

### 8.5 Required tests

Later implementation MUST test:

1. all seven allowed headers are retained case-insensitively as arrays;
2. cookie, auth, proxy-auth, CSRF, WAF, session, and private identifiers are removed;
3. unknown headers are dropped and only the count is stored;
4. denylist precedence is enforced;
5. control-character and oversized values are dropped;
6. safe headers attach to occurrences, not raw content rows;
7. safe headers do not affect raw hashes;
8. no stored/logged/API representation contains rejected names or values;
9. `content-length` mismatch produces safe diagnostics without discarding raw bytes;
10. all six singleton fields reject every repeated instance and repeated `cache-control` retains received order;
11. per-value/per-name bounds drop the whole name without truncation;
12. canonical aggregate sizing is independent of input/map iteration order and overflow stores `{}` plus `response_headers_overflow=true`;
13. dropped-header count means distinct normalized rejected names, includes overflow-dropped candidate names, and never reveals names.

### 8.6 Database implications

- Store allowlisted response headers in a bounded JSON object plus `response_headers_overflow` and dropped-name count on `collection_occurrences`, or in normalized columns where querying is required.
- Do not copy legacy unrestricted header dictionaries during migration without filtering.
- Add a migration scrub plan before production use if existing databases may already contain sensitive header metadata. The scrub operation requires separate explicit authorization and is not part of this document.
- API schemas continue to omit raw/occurrence headers by default.

### 8.7 Open questions

- Does the BVC `ETag` contain only cache identity, or could it include a per-session tracking value that warrants removing it from the allowlist?
- Are any additional standard cache/representation headers operationally necessary enough to justify a future reviewed allowlist change?

## 9. API freshness semantics

### 9.1 Decision

Freshness metadata MUST describe distinct collection, processing, publication, and market times. One timestamp MUST NOT be labeled generically as "last updated" without identifying which event it represents.

All freshness timestamps MUST be timezone-aware UTC values at the API boundary. Each aggregate MUST state its scope, including source, collection mode, market, and any instrument filter. A value with no supporting evidence MUST be `null`/unknown; it MUST NOT default to application start time, raw-content creation time, or the current clock.

#### Canonical freshness definitions

| Term | Exact meaning |
|---|---|
| **Latest attempt time** | Select the greatest `(requested_at, occurrence_sequence)` among collection occurrences in scope, including transport failures and non-2xx responses; return its `requested_at` plus associated outcome. |
| **Latest collected time** | Select the greatest `(response_received_at, occurrence_sequence)` among occurrences in scope for which a response, including empty/non-2xx, was preserved; return its response time/outcome. A no-response failure affects latest attempt only. |
| **Latest successful collection time** | Select the greatest `group_sequence` whose selected logical pages all have successful collection-page outcomes and positive completion evidence, then return its `collection_completed_at`. Malformed/unexpected 2xx content does not qualify. It says nothing about row normalization or publication. |
| **Latest normalized time** | Select the greatest `(completed_at, processing_attempt_sequence)` among immutable processing attempts/revisions in scope with terminal `success` or `partial_success` and at least one usable row. Return its time, rule version, group/occurrence scope, and status. Reprocessing old content may advance this value without rewriting its old group. |
| **Latest price timestamp** | The greatest canonical `price_timestamp` among currently published latest-price rows in scope. It is a source/fallback observation time, not a database update time. Its timestamp kind and price kind MUST be exposed or derivable. |
| **Latest price trading date** | The greatest canonical Casablanca `trading_date` among currently published latest-price rows in scope. For compatibility, an API field named only `latest_trading_date` MUST mean this value, not bar date. |
| **Latest finalized-bar trading date** | The greatest Casablanca trading date among published accepted `1d` rows with `bar_state=final`. Provisional/ineligible observations do not advance it. |
| **Historical-range collection provenance** | For the returned final bars, the minimum and maximum `collection_completed_at` across their accepted supplying groups, plus the distinct supplying group IDs/count. These values describe provenance only; the maximum MUST NOT make older dates or missing range coverage appear freshly collected. An empty range returns null bounds and zero groups. |
| **Expected finalized-through date** | For a requested historical scope, the greatest Casablanca trading date within the request that should have a final bar under the configured calendar and finalization SLA as of evaluation time. It is null/unknown until both policy inputs are configured. |
| **Latest pagination group** | The greatest immutable `group_sequence`, regardless of failure, completeness, normalization, or acceptance; group timestamps are display evidence, not ordering tie-breakers. |
| **Latest complete group** | The greatest `group_sequence` with `pagination_complete=true` based on section 6, regardless of row-level normalization quality. |
| **Latest normalized group** | The greatest immutable group sequence having a completed processing revision for every selected data page and at least one usable row. It may be incomplete or `partial_success`; status fields MUST make that visible. Reprocessing an older group never changes group sequence or makes it the latest group. |
| **Latest accepted group** | The group currently referenced by the atomic publication pointer for the applicable current-snapshot publication scope. It necessarily passed section 6 eligibility and reached publication `published`. |

These identifiers may refer to different groups. An API MUST NOT substitute one for another merely because the preferred group is missing.

`occurrence_sequence`, `processing_attempt_sequence`, and `group_sequence` are unique positive database-assigned immutable order values. Equal-clock selection MUST use these exact secondary keys; UUID, transaction commit order, or arbitrary query order is nonconforming.

`collection_completed_at` is immutable and equals the maximum `response_received_at` among the selected required logical-page occurrences, including the selected response that supplies terminal completion evidence. It is never run-finalization, normalization, acceptance, or current clock time.

#### Publication and freshness anchor

Current-snapshot/latest-price BVC responses MUST be anchored atomically to the accepted group for `(BVC, bvc_equity_prices, production)`, not the latest attempted page, latest raw content row, or latest partially normalized group. A newer incomplete or partial group MUST NOT overwrite that snapshot. The API may continue serving the prior accepted group, but MUST expose that a newer attempt exists and did not become accepted.

Other endpoint scopes follow different provenance rules:

- instrument master responses may combine field-level canonical values from several accepted/qualified occurrences and MUST expose their canonical/last-seen provenance semantics rather than claim one snapshot group supplied every field;
- historical bar responses may span many accepted groups/dates; every returned canonical bar revision MUST trace to an accepted/finalized source group, and completeness is evaluated for the requested date/instrument range;
- operational diagnostics may describe attempted, failed, partial, complete, normalized, and accepted groups without publishing their rows as trusted current data.

The accepted group's collection freshness anchor is its `collection_completed_at`, derived from its selected required-page occurrences. `accepted_at` and processing-attempt completion times MUST NOT make old observations appear newly collected.

The following freshness views are required:

1. **Operational attempt freshness** uses latest attempt time and outcome.
2. **Acquisition freshness** uses latest successful collection time/latest complete group.
3. **Processing freshness** uses latest normalized time/latest normalized group.
4. **Published current-snapshot freshness** uses the accepted publication group collection time together with latest price timestamp and latest price trading date.
5. **Published daily-history freshness** uses per-bar accepted/final provenance, requested-range coverage, historical-range collection provenance, expected finalized-through date, and latest finalized-bar trading date. It never substitutes the current-snapshot publication pointer.

A successful individual page is never sufficient evidence of dataset freshness.

#### Stale and incomplete data

Staleness is market-aware, endpoint-specific, and three-valued (`true | false | unknown`), with `not_applicable` permitted for a dimension that the endpoint contract explicitly excludes:

- **current snapshot/latest prices**: `collection_stale` compares that scope's accepted-group `collection_completed_at` with the configured current-collection SLA; `market_timestamp_stale` compares its published price timestamp/trading-date evidence with the market-data SLA after applying the Casablanca calendar;
- **final historical daily bars**: `collection_stale=not_applicable`. Collection recency is not proof of historical correctness; expose the range provenance bounds above instead. `market_timestamp_stale` is true when any required session at or before `expected_finalized_through_date` lacks an accepted final bar, false when such coverage is proven, and unknown while calendar/finalization policy is unavailable;
- **provisional preview scope**: every returned provisional date is explicitly not final. It makes `incomplete_data=true`. `collection_stale` is evaluated per returned provisional revision from its qualified complete supplying group's `collection_completed_at` against the applicable preview/current-session collection SLA: true if any required revision is too old, false only if every required revision is within SLA, and unknown if any required provenance/SLA is unavailable and none is already true. `market_timestamp_stale` is false while that date is still inside its configured finalization window, true after finalization is overdue, and unknown without the calendar/SLA. Provisional rows never advance latest finalized-bar date;
- `stale_data` is true if any applicable required dimension is true, false only when every applicable required dimension is false, and unknown when none is true but any required dimension is unknown. A `not_applicable` dimension is excluded from aggregation.

Until an SLA and trading calendar are configured and tested, the API MUST report staleness as `unknown`, not `false`.

`incomplete_data` is endpoint-scope-specific:

- **current snapshot/latest prices**: true when its accepted publication pointer is absent; the response cannot be proven wholly from that publication; or any newer production group with a started collection attempt has not itself become the atomic accepted publication. The latter includes in-progress/`not_evaluated`, incomplete, partial, failed, ineligible, eligible-but-unpublished, and `publication_failed` states. A merely planned group with no request/fixture-load attempt is not collection evidence and is excluded;
- **historical daily bars**: true when requested date/instrument coverage is missing/unknown, any ordinary-history revision lacks accepted/finalized provenance, or an explicit preview response contains a provisional row. Ordinary public history excludes provisional rows. Absence of the current latest-price publication pointer does not make otherwise complete final history incomplete;
- **instrument master**: true when required identity/field provenance is missing or conflicting under its endpoint contract. Absence of a current price publication does not by itself make master data incomplete;
- **operational diagnostics**: reports underlying statuses directly and MUST label the scope if it also derives an incomplete flag.

`incomplete_data` is independent of `stale_data`: recently attempted data can be incomplete, and an old complete snapshot can be stale.

Freshness MUST be computed from occurrences, immutable processing attempts, groups, and publications. When an identical body is received in a later run, its new occurrence may support a new complete/accepted group and newer collection freshness; the old deduplicated raw-content timestamp MUST NOT be reused. Conversely, merely reprocessing old raw content may advance latest normalized time but MUST NOT change the original group's collection/processing timestamps, group ordering, accepted publication, price timestamp, or trading-date freshness.

### 9.2 Rationale

The current model associates collection timing and pagination metadata with a deduplicated `RawPayload`. Reusing the same content can therefore make a new observation invisible or can mutate old audit context. The current normalized tables also contain one mutable latest-price row per instrument and do not identify an atomically promoted group.

Explicit occurrence-, group-, and publication-based meanings prevent a healthy HTTP request, late reprocessing job, partial page set, or unchanged response body from being mistaken for fresh, complete market data.

### 9.3 Example

Group A is complete, normalized, and accepted at 10:00. Group B starts at 10:05, receives page 1, and fails fetching page 2. Page 1 happens to contain the same bytes as Group A.

Required API state:

```txt
latest_attempt_time = Group B request time
latest_collected_time = Group B page 1 response time
latest_successful_collection_time = Group A collection completion
latest_pagination_group = Group B
latest_complete_group = Group A
latest_normalized_group = Group A, unless Group B reaches the defined terminal normalization state
latest_accepted_group = Group A
served_data_group = Group A
incomplete_data = true
```

Group B's duplicate content does not hide its occurrence, does not make Group A's raw row newer, and does not partially replace the published prices from Group A.

### 9.4 Edge cases

- A complete accepted group receives identical market values to its predecessor: collection freshness advances from its new occurrences; market price timestamps may remain unchanged and MUST be shown separately.
- An old raw payload is normalized after a software deployment: latest normalized time advances from the new processing attempt, but latest normalized group, collection, publication, and market freshness do not.
- A manual fixture group MUST be labeled by mode and MUST NOT satisfy live operational freshness unless an explicit non-production policy requests fixture scope.
- A complete acquisition with row errors advances latest successful collection time but not latest accepted group.
- A partial group that finishes normalization may become latest normalized group; it cannot become latest accepted group.
- An accepted group containing only safely stored `N.T`/`S` observations may be collection-fresh while direct-trade timestamps remain old or absent. The API must expose both dimensions rather than invent a trade.
- If clocks are invalid or occurrence timestamps are naive, freshness is unknown and a quality diagnostic is required.
- A late correction processed today from a response actually received yesterday uses yesterday's occurrence time for collection freshness.
- Aggregate latest price trading date or finalized-bar date does not prove per-instrument/range coverage; coverage diagnostics remain necessary.
- A history response spanning accepted Groups A through Z is valid when every returned final revision is accepted and requested-range coverage is proven; it is not forced into one group.
- That multi-group history response reports the minimum/maximum supplying-group collection completion and group count for provenance, but `collection_stale=not_applicable`; requested-range finalization/coverage decides its incomplete and market-stale dimensions.
- A preview containing today's eligible provisional bar labels it provisional and incomplete. Its collection staleness comes from the complete group that supplied that revision, not the current final-history range or raw-content creation time. It is not market-stale before the configured finalization deadline, becomes market-stale if finalization is overdue, and remains unknown when no calendar/SLA exists.
- Instrument master data enriched by a later qualified occurrence retains field provenance and is not falsely attributed wholly to the current price publication group.

### 9.5 Required tests

Later implementation MUST test:

1. every definition in the canonical freshness table using groups with deliberately different times and states;
2. latest attempt advancing after a no-response transport failure while latest collected does not;
3. complete acquisition with normalization errors advancing collection but not acceptance freshness;
4. a newer incomplete group leaving the prior accepted snapshot published and setting `incomplete_data=true`;
5. duplicate content in a later occurrence advancing collection evidence without mutating old raw content;
6. reprocessing old content advancing only latest normalized time, never latest normalized group or immutable group timestamps;
7. accepted-at time never substituting for collection or market time;
8. latest price timestamp, latest price trading date (`latest_trading_date` alias), and latest finalized-bar trading date are computed separately from accepted data and serialize precisely;
9. manual fixtures are excluded from live scope;
10. staleness is unknown before an SLA/calendar is configured;
11. collection-stale and market-timestamp-stale are evaluated independently across weekends, holidays, and market closures once the calendar exists;
12. no accepted group yields null freshness anchors, `incomplete_data=true`, and no false freshness claim;
13. a successful page cannot make a failed/incomplete group appear fresh and complete;
14. freshness aggregation is deterministic under equal timestamps by using immutable `group_sequence` only;
15. `collection_completed_at` is exactly the maximum selected required-page response time, including terminal evidence;
16. malformed 2xx content never qualifies as a successful collection page;
17. current snapshots use one publication pointer, historical ranges accept multiple traced groups, and instrument fields use field provenance;
18. history completeness is evaluated for requested range rather than single-group membership.
19. coverage-ineligible, eligible-but-unpublished, and publication-failed newer groups all set `incomplete_data=true`;
20. publication retry creates a new immutable attempt/run, leaves the original run failed, and cannot publish changed staged content as a retry.
21. equal requested/received/processing clocks select by occurrence/processing sequence, and groups select only by group sequence;
22. complete accepted/finalized historical bars remain `incomplete_data=false` even when no current latest-price publication pointer exists.
23. every newer started production group that is not atomically published, including in-progress/`not_evaluated`, sets current-snapshot `incomplete_data=true`, while an unstarted planned record does not.
24. a multi-group final-history range reports deterministic min/max collection provenance and group count while keeping `collection_stale=not_applicable`;
25. ordinary history excludes provisional bars; explicit preview includes them with provisional/unconfirmed labels, `incomplete_data=true`, supplying-group/SLA collection staleness, and deadline-driven true/false/unknown market staleness;
26. expected finalized-through coverage uses the Casablanca calendar/SLA and never uses the newest supplying group's collection time to hide an older missing date.

### 9.6 Database implications

- Add first-class immutable group timestamps/state: `started_at`, `collection_completed_at`, completion evidence, phase statuses, and immutable ordering identity. Do not overwrite a group with later reprocessing time.
- Store unique positive `group_sequence`, `occurrence_sequence`, and `processing_attempt_sequence`. Store immutable processing attempts/revisions with their own `completed_at`, rule version, outcome, and group/occurrence scope; latest normalized time is derived from these events.
- Link occurrences to groups and canonical revisions/publication provenance.
- Add an atomic accepted-group pointer/versioned publication table unique on `(exchange_id, dataset_code, publication_channel)`. Updating mutable `latest_prices` page by page is insufficient to guarantee that readers see only one accepted current snapshot.
- Add immutable publication-attempt records with run, staged revision fingerprint, started/finished times, outcome, and failure/retry linkage; group publication state is derived from their ordered events.
- Daily bars and latest-price revisions need group/occurrence provenance if freshness and rollback are to be queryable without ambiguity.
- Historical range queries need bounded indexes/joins from each final bar revision to its accepted supplying group so min/max collection provenance and group count are derived without a single-group fiction.
- Index group state/time, source/mode/time, and accepted publication identity for bounded diagnostics queries.
- Database timestamps store UTC instants; canonical daily-bar trading dates remain Casablanca calendar dates.
- API serialization MUST expose Decimal values as exact strings and timestamps as timezone-aware ISO 8601 values; it MUST NOT infer missing freshness in the schema layer.

### 9.7 Open questions

- What are the collection and market-data freshness SLAs during open sessions, pre-open, post-close, weekends, and Moroccan exchange holidays?
- Which authoritative trading calendar and market-hours source will define expected freshness?
- Which collection modes are eligible for operational acceptance: live only, or explicitly approved replay/backfill modes as well?
- Does the source provide a trustworthy market-wide timestamp, or must freshness remain a combination of occurrence time and per-row timestamps?
- Should APIs expose one conservative `stale_data` field plus detailed dimensions, or only the detailed dimensions?
- What immutable publication/version design best fits historical bars and the one-row-per-instrument latest-price projection?

## 10. Normalization-error idempotency

### 10.1 Decision

One logical normalization error is identified by stable source-content, row, rule, field, stage, and rule-version identity. Human-readable messages and processing occurrences are evidence about that error, not its identity.

The future PostgreSQL unique key MUST be:

```txt
(
  raw_payload_id,
  processing_stage,
  entity_type,
  row_locator,
  rule_code,
  field_name_key,
  rule_version
)
```

Required meanings:

- `raw_payload_id`: immutable exact-content identity, not ingestion-run or collection-occurrence identity;
- `processing_stage`: a controlled internal value such as `parser`, `normalizer`, or `repository_validation`;
- `entity_type`: the canonical entity being evaluated, such as `instrument`, `latest_price`, `price_bar`, or `payload`;
- `row_locator`: a deterministic locator within the immutable raw content;
- `rule_code`: a stable machine identifier such as `missing_symbol` or `instrument_identity_conflict`, not an exception class/message chosen incidentally;
- `field_name_key`: the canonical field name, or the non-null sentinel `__record__` for a row/payload-wide rule;
- `rule_version`: an explicit version of the rule semantics, not the application deployment timestamp.

All identity columns MUST be non-null. PostgreSQL's ordinary unique semantics for nullable fields MUST NOT be allowed to create duplicates; use canonical non-null sentinels or an explicitly reviewed `NULLS NOT DISTINCT` constraint.

#### Stable row location

The parser MUST preserve a zero-based source row index. `row_locator` is selected deterministically:

1. `record:<source_record_id>` when the source ID is nonblank and unique within that raw content;
2. otherwise `row:<zero_based_source_row_index>`;
3. `payload` for a payload-wide error with no safe row boundary.

A symbol, ISIN, company name, raw fragment, or error message alone is not a safe locator because it may be missing, duplicated, corrected, reformatted, or sensitive. Those values MAY be stored separately as redacted diagnostic context.

#### Repeated processing and occurrences

- Reprocessing the same raw row under the same rule version and emitting the same logical key MUST reuse one error row.
- The insert MUST be an atomic PostgreSQL upsert (`INSERT ... ON CONFLICT ...` or equivalent), never a select-then-insert race.
- Each processing attempt that observes the error MUST create or reuse a separate observation link keyed by `(normalization_error_id, processing_attempt_id)`.
- The observation records the applicable collection occurrence/group when one exists. Reprocessing without a request has no fabricated collection occurrence.
- Identical content received in separate collection occurrences produces one logical error plus distinct occurrence/processing evidence.
- A changed human-readable message with the same logical key updates `latest_message`, `last_seen_at`, and counters/observations; it MUST NOT insert another logical error.
- If the rule meaning changes materially, `rule_code` or `rule_version` MUST change. Cosmetic message changes do not justify a new version.

`first_seen_at`, `last_seen_at`, `first_message`, and `latest_message` MUST be derived by total observation order `(observed_at, processing_attempt_sequence)`, not transaction last-writer order. Concurrent upserts update latest fields only when the incoming observation key is greater; unique observation rows determine counts.

#### Error lifecycle

Canonical lifecycle states are:

```txt
active
resolved
ignored
superseded
```

- New errors start `active`.
- A previously active error becomes `resolved` after a complete successful evaluation of the same raw content, row, processing stage, and rule version no longer emits that key.
- A later different raw payload resolves the content-bound error only when stable entity/field correlation is proven (canonical instrument ID or unique `(source_id, source_external_id)`), the later accepted row explicitly passes the same rule or an approved successor mapping, and resolution records `resolved_by_raw_payload_id`, `resolved_by_processing_attempt_id`, `resolution_occurrence_sequence`, and `resolution_reason=source_correction`. Under those exact conditions the repository MUST resolve it; otherwise it MUST remain unresolved historical evidence.
- A partial or failed processing attempt MUST NOT resolve an error merely because it stopped before emitting it.
- Reopening is content-bound because `raw_payload_id` is part of the unique key. After `resolution_reason=source_correction`, reprocessing the old pre-correction occurrence records historical reproduction and MUST leave the error resolved. A new collection occurrence for that **same raw content** reopens the same error only when its `occurrence_sequence` is greater than the stored `resolution_occurrence_sequence` and it emits the same key. A later **different raw content** that emits a correlated defect creates its own distinct active error key; it MUST NOT reopen or reuse the old content's row. It SHOULD link `recurrence_of_normalization_error_id` to the resolved historical error when stable entity/field/rule correlation is proven.
- After a complete clean reevaluation of the same content resolves the key, store `resolution_processing_attempt_sequence`. A later complete same-version attempt for that content reopens the same row only when its processing-attempt sequence is greater and it emits the key, recording a deterministic-processing regression. Display timestamps such as `resolved_at` MUST NOT decide either transition. In every same-key reopen, keep `first_seen_at`, clear resolution fields atomically, and increment recurrence/observation evidence.
- `ignored` is an explicit reviewed disposition. Reprocessing does not silently change it, but new observations still update `last_seen_at`. Under this contract an ignored blocking error never proves the row valid and never permits production group acceptance; a future waiver mechanism requires an explicit contract amendment.
- `superseded` means a newer rule version deliberately replaced evaluation under the old version. It is not the same as proving the old data valid.

`first_seen_at` and `last_seen_at` refer to processing observations. Collection time remains on occurrences. The error row SHOULD retain a sanitized first message, latest message, current status, first/last seen times, observation count, recurrence count, and resolution/supersession audit. Raw fragments MUST be bounded and redacted and MUST NOT participate in uniqueness.

An active historical error blocks only the processing group/revision to which its observation applies; it does not automatically block a later accepted group that contains a corrected, independently validated row. Cross-content resolution still requires the explicit correlation evidence above.

Diagnostics MUST scope error counts explicitly (`latest_attempt_group`, `latest_accepted_group`, or `historical_all`). A historical active content error MUST NOT be presented as an active error in the latest accepted group merely because both share an instrument/source.

Group-level issues such as missing pages or cross-page duplicates are pipeline quality diagnostics, not row normalization errors, unless an individual row also violates a named normalization rule.

### 10.2 Rationale

The current repository performs an application-side lookup using message/status/raw-fragment-related fields and has no database unique constraint. Concurrent workers can therefore insert duplicates, and cosmetic message changes can change identity. It also cannot distinguish one content defect observed in several collection occurrences from several logical defects.

A stable machine key plus observation links makes repeated normalization idempotent while retaining operational evidence and lifecycle history.

### 10.3 Example

Raw content `abc` has row 7 with a blank symbol. It is received in runs A and B and processed twice under `bvc_price_rules_v1`.

```txt
logical error rows = 1
key = (raw abc, normalizer, instrument, row:7,
       missing_symbol, symbol, bvc_price_rules_v1)
error observations = 2 or more, one per processing attempt
collection occurrences represented = A and B
```

If a later code release changes the message from "missing symbol" to "Symbol is required," the logical row remains one. If rule semantics change under `bvc_price_rules_v2`, the v1 row may be superseded and v2 emissions have distinct identity.

### 10.4 Edge cases

- Duplicate source record IDs within one payload force `row:<index>` locators and produce a separate duplicate-ID diagnostic.
- Reordering bytes creates different raw content and therefore different logical errors, even if the semantic JSON rows look equal. A future semantic lineage layer may relate them, but exact audit identity remains separate.
- One row violates the same rule on two fields: `field_name_key` distinguishes the errors.
- Two exceptions have the same class but represent different validation rules: their `rule_code` values must differ.
- A database outage after canonical rows commit but before error observations commit requires one transaction boundary or a recoverable processing attempt; silent divergence is prohibited.
- A successful reevaluation of only one row may resolve errors only for that row and declared rule scope.
- An ignored error recurring in a new live group remains visible in counts and acceptance diagnostics.
- Parser structure failure with no row boundary uses `payload`; it must not be mislabeled as a row normalization error.
- A raw diagnostic fragment containing cookies, tokens, or private headers is redacted/dropped before persistence.
- Two message variants arriving concurrently use observation order, not commit order, to choose `latest_message`.
- A corrected later payload with no stable entity correlation does not erase/resolve the earlier error, but the later group may still succeed on its own evidence.
- Replaying a pre-correction occurrence after source-correction resolution does not churn status. Recollecting the same exact content after the resolution sequence reopens its content-bound row; receiving changed content with the defect creates a new lineage-linked row.

### 10.5 Required tests

Later implementation MUST test:

1. repeated processing of the same raw row creates one logical error;
2. the same error across separate collection occurrences creates distinct observations but one error row;
3. changed message text with the same key updates metadata without duplication;
4. different `rule_code`, field, stage, row locator, raw content, or rule version creates a distinct error;
5. all identity fields are non-null/canonicalized;
6. duplicate source IDs fall back deterministically to source row indexes;
7. a complete clean reevaluation resolves an active error;
8. partial/failed reevaluation never resolves errors by omission;
9. same-content recurrence after the applicable sequence boundary reopens the same row while preserving first-seen history;
10. ignored errors remain ignored but receive new observations;
11. rule-version replacement records supersession without pretending resolution;
12. two concurrent PostgreSQL transactions emitting the same key converge to one logical row and two valid processing observations as applicable;
13. observation upsert is itself idempotent for a retried transaction;
14. redacted context and message changes do not affect uniqueness;
15. group diagnostics are not accidentally inserted as row normalization errors.
16. a different accepted raw payload resolves an earlier error only with stable entity/field correlation and explicit resolution provenance;
17. a cross-content correction without correlation leaves the historical error unresolved without blocking an independently clean later group;
18. concurrent out-of-order observations select first/latest message and timestamps by total observation order, not writer order.
19. API/diagnostic counts distinguish latest-attempt, latest-accepted, and historical active errors.
20. replay/reprocessing of old pre-correction content does not reopen a source-corrected error;
21. a new post-resolution occurrence of the same exact content reopens its key, while changed content with the correlated defect creates a distinct optionally lineage-linked error;
22. equal or out-of-order display clocks cannot change lifecycle ordering: source-correction recurrence uses occurrence sequence and clean-reevaluation regression uses processing-attempt sequence;
23. concurrent resolution and recurrence converge to the state dictated by those sequence boundaries, not transaction commit order.

SQLite-only tests are insufficient for the concurrency and unique-null semantics tests.

### 10.6 Database implications

- Replace or augment the current error fields with stable `processing_stage`, `row_locator`, `rule_code`, `field_name_key`, and `rule_version` columns.
- Add the exact composite unique constraint specified above.
- Add a `normalization_error_observations` table with unique `(normalization_error_id, processing_attempt_id)` and nullable occurrence/group provenance through the processing attempt.
- Add a first-class processing-attempt record so normalization retries, versions, completion scope, and transaction outcomes are auditable.
- Add stable optional entity-correlation fields, `recurrence_of_normalization_error_id`, resolution FKs (`resolved_by_raw_payload_id`, `resolved_by_processing_attempt_id`), and immutable resolution sequence boundaries without adding them to logical-error uniqueness.
- Use a checked internal lifecycle vocabulary and repository-controlled legal transitions.
- Perform canonical writes, error writes, resolution reconciliation, and processing-attempt finalization in a transaction whose retry behavior is explicit.
- Add indexes for active errors by raw payload, rule, entity/field, occurrence/group, and last-seen time.
- Migrating legacy rows requires deterministic key derivation. Ambiguous duplicates MUST be reported and preserved for reviewed reconciliation rather than silently deleted.

### 10.7 Open questions

- What repository-wide versioning convention will identify parser and normalization rule semantics?
- Should parser structural failures share this table with normalization errors, or use a sibling diagnostics table with the same identity pattern?
- Which authenticated operator role and audit fields are required to mark an error ignored, while retaining the no-acceptance rule above?
- How long should detailed error observations and sanitized raw fragments be retained?
- When a rule version changes, what evidence is required to mark old errors `superseded` automatically rather than leaving them historical and active-in-version?

## 11. PostgreSQL constraints versus application rules

### 11.1 Decision

Every invariant MUST be enforced at the lowest layer that has enough information to enforce it correctly, with higher layers providing clearer validation and diagnostics. Application checks do not replace database constraints, and database constraints do not replace source-aware semantic validation.

The layer responsibilities are:

1. **Normalizer validation** converts and validates one source row without relying on current database state.
2. **Repository conflict handling** atomically merges a validated candidate with current canonical state and records provenance/revisions.
3. **PostgreSQL constraints** prevent structurally impossible or duplicate committed state under concurrency.
4. **Pipeline diagnostics** determine page/group coverage, cross-row consistency, phase status, and acceptance.
5. **API serialization** presents already-validated canonical state exactly and safely; it never repairs or guesses data.

#### Invariant ownership matrix

| Invariant | PostgreSQL constraint | Repository logic | Normalizer validation | Pipeline diagnostics | API serialization |
|---|---|---|---|---|---|
| Instrument identity | Stored canonical-form checks; unique `(exchange_id, symbol)`; partial unique `(exchange_id, isin)` where ISIN is non-null; required FKs | Atomic lookup/upsert; detect split-key conflicts; field-aware merge and locking/retry | Apply section 1 NFKC/case/syntax rules and reject invalid identity | Report cross-row/page identity conflicts | Never merge identities; return canonical identifier only |
| Instrument weak/strong field precedence | Basic nullability/domain checks; first-seen <= last-seen | Enforce section 1 precedence and monotonic sightings; update material and confirmation provenance separately | Classify fallback/descriptive values and source authority inputs | Aggregate attribute conflicts | Expose approved canonical fields; never expose raw fallback fragments as truth |
| Latest-price identity | `UNIQUE(instrument_id)` in the accepted projection and required revision/provenance FKs | Stage then atomically publish; apply section 2 total ordering/correction predicates | Validate required price, trading-date/timestamp sources, semantics flags, price kind, and numeric domains | Report stale skips/equal-time conflicts and group provenance | Serialize exact Decimal/string and timestamp/quality metadata; no stale overwrite |
| Daily `1d` identity | Partial unique `(instrument_id, timeframe, trading_date)` for `timeframe='1d'`; daily date required | Atomic provisional/final/correction merge on the daily key | Derive Casablanca date/anchor and determine source eligibility/state | Detect several observations/conflicting authorities for one date | Ordinary history returns only accepted final bars; preview may describe one labeled provisional bar, never an intraday observation as an official close |
| Other timeframe identity | Existing/event-window uniqueness appropriate to that timeframe | Source-specific atomic conflict policy | Future source-specific contract | Future window coverage rules | Future contract; outside this slice |
| Raw content identity | Unique `(source_id, entity_body_sha256)`; immutable FK target; no contextual lifecycle state | Concurrent insert-or-reuse without mutating content | No parsing before storage | Detect duplicate page bodies as an occurrence/group issue | Raw bodies remain excluded |
| Collection occurrence identity | PK, unique sequence/composite attempt key with non-null parts; one canonical non-null `group_page_id`; content FK; outcome-to-response/raw/status and timestamp cross-checks | Create one auditable hop/response or no-response occurrence; derive all page/group/offset/limit ownership from its page parent; never overwrite another occurrence | Not applicable | Associate retries/hops to logical pages | Use safe aggregates only; no headers/body exposure |
| Logical page identity | Unique group/page number and offset; occurrence-to-page FK; selection PK/unique page, unique occurrence, and composite ownership FK | Select successful retry occurrence under section 6; reject differing multiple successes | Produce page role/result for candidate occurrence | Build contiguous selected pages and completion evidence | Use group/page aggregates only |
| Normalization-error identity | Exact composite unique key from section 10; error-observation unique key | Atomic upsert, lifecycle reconciliation, and observation linking | Emit stable rule/stage/row/field/version identifiers | Keep group-level issues distinct | Return only bounded aggregate diagnostics unless a privileged future contract says otherwise |
| Internal status vocabularies | `CHECK` constraints (or reviewed reference tables) on each internal field | Enforce legal transitions atomically | Emit only declared values | Aggregate without collapsing state dimensions | Validate response enums and preserve distinction |
| Source status vocabulary | Bounded text/domain only; no closed enum of undocumented codes | Preserve accepted raw/normalized token provenance | Syntactically normalize without business mapping | Count unknown/blank values and warnings | Expose only allowlisted status fields with unconfirmed-semantics metadata |
| Nonnegative financial/count values | Simple `CHECK` constraints on committed canonical values/counts | Refuse writes that violate constraints; translate constraint failures safely | Reject invalid negatives and non-finite/out-of-range values | Count rule failures | Never coerce a negative/invalid value to zero |
| Decimal exactness | `NUMERIC(precision, scale)`, explicit domain checks, no floating column | Bind `Decimal` directly | Parse exact tokens as section 4 requires and enforce scale | Report precision/scale errors | Serialize Decimal as exact JSON strings |
| Page/group completeness and acceptance | Checked processing/coverage/publication states; FKs; logical-page uniqueness; one publication pointer per exact scope | Atomic legal state transitions and publication pointer/revisions | Page-level results only | Sole owner of completion evidence, cross-page duplicates, group result, coverage, and eligibility | Current snapshot uses its accepted pointer; history/master use scope-specific provenance |
| Header/raw redaction | Bounded allowlisted occurrence metadata shape where practical; no secret columns | Filter before persistence and logging | Not applicable | Report only safe counts | Never return raw bodies or stored headers by default |
| Audit retention | Audit/revision FKs use `ON DELETE RESTRICT`/`NO ACTION`; no destructive cascades | Prefer deactivate/archive; reject deletion with dependents | Not applicable | Report retention/purge attempts | Read-only; never trigger purge |

#### Required PostgreSQL backstops

At minimum, future migrations for this contract MUST provide the following database guarantees after a reviewed data-reconciliation step:

- instrument unique keys plus canonical symbol/ISIN syntax/storage checks;
- one latest-price projection row per instrument;
- one `1d` bar per instrument/trading date;
- one immutable raw-content row per source/entity-body hash;
- one collection occurrence per run request sequence/attempt/redirect hop, with non-null identity and unique occurrence ordering;
- one logical page per group page number/offset, every attempt bound to its page, at most one selection per page, and no occurrence reusable/selectable outside its owner page;
- one logical normalization-error row per section 10 key;
- one error observation per error/processing attempt;
- foreign keys for source, exchange, instrument, run, group, raw content, occurrence, and processing provenance where applicable;
- `first_seen_at <= last_seen_at`, occurrence request/response/finish ordering where values are present, nonnegative page indexes/counts/sizes, and required daily trading dates;
- closed checks for system-owned statuses and classifications.

Stored canonical identity checks MUST make direct SQL variants nonconforming, not merely rely on Python. Conceptually, symbol must equal its NFKC/trim/uppercase canonical form, contain no whitespace/control characters, and have length 1–30; ISIN is null or equals its NFKC/trim/uppercase form and matches `[A-Z]{2}[A-Z0-9]{9}[0-9]`. Ordinary unique constraints then operate on canonical values; an equivalent reviewed normalized expression-index design is acceptable only if it also prevents storing blank/noncanonical variants.

For this BVC slice, the system-owned controlled values defined by this contract include:

```txt
phase/page results: success | partial_success | failed | skipped
run/group active/terminal results: running | success | partial_success | failed
raw content storage: stored (existence is authoritative; no contextual lifecycle)
processing attempts: running | success | partial_success | failed | skipped
occurrence outcome: success_response | redirect_response | http_error_response | transport_failure | fixture_loaded
instrument coverage: proven | violated | unknown | not_configured
publication state: not_evaluated | ineligible | eligible | published | publication_failed | superseded
error lifecycle: active | resolved | ignored | superseded
timestamp_source: direct_source | source_published | collection_received
trading_date_source: explicit_source_date | derived_direct_timestamp | source_published | collection_received
price_kind: last_trade | displayed_reference | unknown
bar_state: provisional | final
canonical data quality: valid | suspect
```

`skipped` is legal only where section 6 permits it. A status field MUST use only the vocabulary for its own state dimension. Contextual diagnosis/normalization outcomes MUST NOT be stored on immutable raw content. PostgreSQL `CHECK` constraints are preferred over PostgreSQL enum types for these small evolving internal vocabularies because they can be revised through explicit migrations without enum-type lifecycle hazards.

Legacy migration is explicit: every preserved legacy raw row becomes immutable stored content; legacy `collected`/`parsed`/`normalized`/`ignored`/`failed` values are copied, when evidence permits, into synthesized processing-attempt records labeled with an `unknown_legacy` rule version and are never treated as current freshness proof. Legacy normalization-error `open` maps to `active` and `fixed` maps to `resolved`; any unknown legacy value is quarantined/reported before enabling the new check constraint.

Opaque BVC codes such as `N.T` and `S` are explicitly excluded from closed system status checks.

#### Numeric domains

The following committed numeric values MUST be nonnegative when present:

- price, open, high, low, close, previous close;
- volume, number of trades, traded value, and market capitalization;
- raw body length, page/row counts, page offset, redirect hop, and durations are `>= 0`; logical page number, page limit, request sequence, attempt number, and occurrence sequence are `> 0`.

Change amount and change percentage MAY be negative. Zero is structurally nonnegative, but a source-specific rule MAY classify or reject zero where its financial meaning makes it invalid. Cross-field OHLC relationships such as `high >= low` and `high >= close` belong first to normalizer validation because nullable, provisional, and suspect source observations need an explicit quality outcome; database checks MUST NOT silently prevent preservation of auditable suspect source evidence in an appropriate noncanonical layer.

All non-finite numeric tokens, including NaN and infinities, MUST be rejected before canonical persistence. If the selected PostgreSQL numeric type accepts a NaN representation, the database domain/check MUST reject it as a final backstop.

#### Atomic repository decisions

PostgreSQL uniqueness resolves races only if repositories use it correctly. The repository MUST use transactional `INSERT ... ON CONFLICT`, row locking, conditional updates, or an equivalent atomic strategy for:

- two workers creating the same instrument;
- stale/new/equal latest-price candidates;
- same-date provisional/final bars;
- raw-content deduplication plus occurrence creation;
- logical-page retry selection;
- normalization-error upsert and resolution;
- group acceptance/publication.

Blind last-write-wins updates are nonconforming. A `CHECK` constraint cannot enforce temporal precedence, evidence authority, legal status transitions, completeness evidence, or accepted correction provenance; repository/pipeline logic owns those decisions.

Audit retention is restrictive. Foreign keys from occurrences, group pages, processing attempts, normalization-error observations, canonical revisions, and publication records to source/run/group/raw/occurrence parents MUST use `ON DELETE RESTRICT` or `NO ACTION`, never audit-erasing cascade. Sources/instruments/runs with audit dependents are deactivated/archived rather than deleted. Any future retention purge requires a separate explicit policy that preserves required content hashes, occurrence facts, and provenance; it is outside this contract.

### 11.2 Rationale

The current schema contains useful unique keys for instruments, latest prices, timestamp-keyed bars, and raw hashes, but many statuses and numeric domains are unconstrained. Important merge/error checks occur in application code and several use read-then-write behavior that is not a concurrency guarantee. The current daily timestamp key also enforces the wrong identity for a `1d` bar.

The selected division preserves source-aware decisions in understandable Python while making duplicate/impossible canonical state uncommittable under PostgreSQL concurrency.

### 11.3 Example

Two workers normalize the same instrument/date concurrently:

```txt
worker A: ATW, 1d, 2026-05-18, provisional observation at 12:00
worker B: ATW, 1d, 2026-05-18, provisional observation at 14:00
```

The normalizer validates both candidates. The daily unique constraint permits only one canonical key. Repository conflict handling compares observation/provenance evidence atomically and leaves the 14:00 coherent provisional bar. Pipeline diagnostics retain both occurrences. Ordinary public history excludes that provisional row; an explicitly authorized preview returns exactly one clearly labeled provisional bar and does not decide which candidate should win.

### 11.4 Edge cases

- Existing duplicate same-date bars must be audited before adding the constraint; a migration must not choose one silently.
- A unique conflict caused by symbol/ISIN keys resolving to different instruments is a data-quality conflict, not a cue to attach the row to whichever insert wins.
- A direct SQL writer bypassing normalizer validation still cannot insert duplicate identity or negative canonical values.
- A source code outside a known list remains storable as bounded opaque evidence; an unknown internal group status is rejected.
- A nullable optional identity component must use a partial unique index or explicit null semantics, never accidental PostgreSQL null-distinct behavior.
- A failed repository transaction must not leave an occurrence without its required raw-content link when a response existed, or mark a group accepted without all publication writes.
- Constraint errors exposed through APIs are translated to safe domain errors; SQL text, values, and stack traces remain private.
- Future non-daily timeframes must not be forced into the daily-date identity merely to reuse one constraint.
- Live, retry, redirect, and fixture occurrence identity fields cannot be null; fixture numbering follows section 7.
- Attempting to delete raw content or a run referenced by audit/provenance fails rather than cascading evidence away.
- `parsed` on a legacy raw row becomes historical processing evidence, not a new target raw-content status.

### 11.5 Required tests

Later implementation MUST test against PostgreSQL:

1. every unique/check/FK invariant listed in the ownership matrix;
2. normalized symbol/ISIN uniqueness, including null and case/whitespace behavior;
3. concurrent same-instrument creation converges without identity corruption;
4. concurrent stale/new/equal latest-price writes converge to section 2's result;
5. concurrent same-date bar writes produce one row with deterministic state/provenance;
6. concurrent raw-content inserts create one content row and all occurrence rows;
7. concurrent normalization errors obey section 10 uniqueness;
8. invalid internal statuses are rejected while bounded opaque source statuses are retained;
9. negative canonical price/count values and non-finite values are rejected, while signed change values are allowed;
10. Decimal bindings retain exact values without float conversion;
11. group promotion is atomic and cannot produce two accepted current publications for one scope;
12. migration upgrade on representative legacy data reports conflicts before constraints are enabled;
13. migration downgrade behavior is explicit and does not silently destroy audit data;
14. API serialization never compensates for invalid database data by coercion or raw leakage.
15. direct SQL cannot store case/whitespace/control variants of symbol/ISIN or blank optional ISIN;
16. occurrence identity rejects null components and duplicates across live, retry, redirect, and fixture modes;
17. group-page constraints allow many retry occurrences but exactly one logical page/selected occurrence, including concurrent 503-to-200 and conflicting-200 cases;
18. legal status transitions succeed and illegal transitions fail even when both endpoint values are individually allowed;
19. legacy raw/error status mapping produces explicit historical processing/error evidence and quarantines unknowns;
20. audit-parent delete attempts are restricted and never cascade occurrence/error/revision evidence;
21. publication uniqueness is enforced specifically for `(BVC, bvc_equity_prices, production)` and other declared scopes remain independent;
22. transaction failure between content insert, occurrence insert, page selection, staged canonical writes, and publication pointer cannot expose a partial accepted snapshot.
23. direct SQL cannot select one occurrence for two pages or select an occurrence owned by a different group/page/offset.
24. direct SQL rejects every occurrence outcome with missing/contradictory raw, response-time/URL, HTTP-status, fixture, or timestamp evidence.
25. direct SQL cannot create contradictory occurrence page provenance because `group_page_id` is canonical and all group/page/offset/limit values derive from its parent.

SQLite tests MAY remain useful for fast unit coverage but cannot prove PostgreSQL conflict, constraint, isolation, timezone, or numeric behavior.

### 11.6 Database implications

- Implement all database changes through reviewed Alembic migrations; this document creates no migration.
- Constraint and index names SHOULD be stable and explicit so operational errors identify the violated invariant.
- Migration order must be: inventory legacy violations, report/quarantine or explicitly reconcile them, backfill required provenance/state, add constraints as not-valid where appropriate, validate, then enable dependent publication behavior.
- Legacy status mapping and audit-FK retention behavior MUST be part of that reviewed migration plan; destructive cascade is prohibited.
- Financial column precision/scale must be documented per field and remain compatible with exact Decimal parsing.
- The target read-only API database role MUST lack write access and direct raw-body/header access; it reads canonical tables and the safe operational diagnostics view. Broader operator diagnostics require a separate role/contract.
- Repository transaction boundaries and isolation/retry behavior are part of the implementation contract and require integration tests.

### 11.7 Open questions

- What precision/scale and upper bounds are authoritative for each BVC price, percentage, volume, traded-value, and market-cap field?
- Should source external instrument IDs use a dedicated mapping table with unique `(source_id, source_external_id)` rather than instrument-level metadata?
- What reviewed rule will reconcile existing same-date bars and legacy normalization-error duplicates before constraints are added?
- Should detailed per-field instrument provenance use normalized history tables or a validated bounded JSON structure?
- What publication-version/table design will make accepted-group promotion atomic without duplicating unnecessary historical data?

## 12. Health and readiness semantics

### 12.1 Decision

Liveness, database connectivity, schema readiness, application readiness, and data freshness are separate checks. A successful result for one MUST NOT be reported as success for another.

#### Liveness

Liveness answers:

```txt
Can this application process respond to a local request?
```

It MUST NOT query PostgreSQL, inspect Alembic, call BVC, evaluate market freshness, or depend on optional services. A responding process returns HTTP 200. A process that cannot run/respond is detected by timeout/connection failure rather than by a dependency probe.

#### Database connectivity

Database connectivity answers:

```txt
Can the configured application role obtain a PostgreSQL connection and execute a bounded read-only query now?
```

It requires a short timeout and a read-only probe such as `SELECT 1`. It does not prove that migrations ran, application tables exist, permissions are sufficient for normal queries, or market data exists.

#### Schema readiness

Schema readiness answers:

```txt
Is the connected database schema exactly compatible with this application build?
```

It requires all of:

1. database connectivity succeeds;
2. the `alembic_version` table exists and its revision-head set exactly matches the migration head set packaged with the running application;
3. the minimum required application tables, columns, constraints, and readable permissions for this build are present;
4. no detected schema drift makes required queries unsafe.

The manifest is not operator prose or a hardcoded ad hoc table list in the endpoint. Every application build MUST package a machine-readable, role-specific `required_schema_manifest` tied to its expected Alembic head. It lists required logical tables/views, columns/types/nullability, complete constraint signatures, required grants, and forbidden privileges. If the manifest is missing or its build/head identity disagrees, schema readiness is `not_ready: schema_manifest_missing_or_mismatched`.

A constraint name alone is not compatibility evidence. The manifest and database introspection MUST compare the complete behaviorally relevant signature:

- unique constraints/indexes: ordered columns or canonical expressions, null semantics, predicate, index method, and any behaviorally relevant operator class or collation;
- checks: a canonicalized definition or deterministic semantic-definition hash, plus validation state;
- foreign keys: ordered local and referenced columns, referenced relation, match type, `ON UPDATE`, `ON DELETE`, deferrability, initial-deferred state, and validation state;
- exclusion or other required constraints: their full canonical PostgreSQL definition and validation state.

A required constraint with the expected name but altered columns, predicate, expression, action, or validation state is `not_ready: schema_drift`.

Security-critical views are signature-checked objects, not trusted by name or output columns. For each required view, the manifest MUST contain a canonical `pg_get_viewdef`-equivalent definition hash, ordered output-column signature, owner, schema, materialized/non-materialized kind, declared dependencies, and relevant relation options including `security_barrier` and `security_invoker` where supported. A changed definition, owner, option, or unallowlisted dependency is `not_ready: schema_drift`, even when the view name and output types are unchanged. A required view MUST NOT depend on a routine unless that routine's canonical definition, owner, language, argument/return signature, volatility/leakproof/security-definer properties, effective `EXECUTE` policy, and dependencies are also allowlisted in the manifest. Security-definer routines are prohibited from the read-only diagnostics path unless a future reviewed contract explicitly proves and manifests their need and safe search path.

Role compatibility is also fail-closed. A role-specific manifest states both what the role MUST be able to read and what it MUST NOT possess directly or through role membership. The read-only API role MUST NOT own application schemas/tables, be superuser, have `BYPASSRLS`, hold schema `CREATE`, hold table write privileges (`INSERT`, `UPDATE`, `DELETE`, `TRUNCATE`, `REFERENCES`, or `TRIGGER`), hold write-capable sequence privileges, select raw body/header columns directly, or inherit any role that grants those capabilities. A required read grant that is absent is `not_ready: permission_required`; any forbidden effective privilege is `not_ready: role_overprivileged`. Readiness evaluates effective privileges, ownership, role inheritance, and public grants, rather than checking only direct grants.

For the BVC read-only role after this contract is implemented, the manifest MUST cover at least:

- exchange, instrument, and data-source identity;
- accepted current-snapshot publication/pointer and latest-price projection/revisions;
- final/provisional daily-bar state and provenance;
- pagination groups, logical pages, sanitized collection occurrences, and processing attempts needed for freshness;
- normalization-error aggregate/observation state needed by safe diagnostics;
- a safe operational diagnostics view that excludes raw bodies and retained headers.

The API role MUST query that safe view for diagnostics/freshness and need not receive `SELECT` on raw body/header columns. Exact physical object names are selected by the schema-design mission and then become explicit entries in the build manifest; readiness behavior is testable against the packaged artifact, not deferred to runtime judgment.

Required outcomes include:

| Database state | Schema readiness |
|---|---|
| PostgreSQL unreachable | `unknown` because connectivity failed; application is not ready |
| PostgreSQL reachable, no `alembic_version` | `not_ready: schema_uninitialized` |
| Revision behind expected head | `not_ready: migration_required` |
| Revision ahead/unknown to this build | `not_ready: application_schema_incompatible` |
| Multiple/revision head set differs | `not_ready: migration_head_mismatch` |
| Expected manifest missing/mismatched | `not_ready: schema_manifest_missing_or_mismatched` |
| Revision matches but a required object is missing or a constraint signature differs | `not_ready: schema_drift` |
| Required effective read permission is missing | `not_ready: permission_required` |
| Runtime role has any forbidden effective privilege or ownership | `not_ready: role_overprivileged` |
| Revision, full required schema signatures, and role policy match | `ready` |

The readiness check MUST NOT call `create_all`, run Alembic upgrade, repair tables, or otherwise mutate the database.

The initial readiness implementation MUST re-evaluate connectivity, revision, manifest, and required read permission on each probe. A future bounded cache requires an explicit invalidation contract; cached success MUST NOT mask a subsequently detected connection/schema/permission failure.

#### Application readiness

Application readiness answers:

```txt
Can this instance safely serve its configured application role now?
```

For the current read-only API role it requires:

- liveness;
- valid required configuration;
- database connectivity;
- schema readiness;
- successful initialization of any dependency explicitly configured as required for that role.

It MUST NOT require a live BVC network request. Source availability and collector capability are operational/data-pipeline concerns, not API process readiness. Optional Redis, future worker, or collector checks must not become required until the deployed role declares them required.

The existing `/health` contract MUST become the aggregate application-readiness check for backward compatibility and return HTTP 200 only when application readiness is true; it returns HTTP 503 with a safe reason code otherwise. A dependency-free liveness check MUST be separately addressable. Exact additional route names are an implementation choice to be documented before endpoint work.

#### Data freshness

Data freshness answers:

```txt
Is accepted BVC data available, complete, and recent enough under section 9?
```

It is a domain/operational state, not a process-readiness dependency by default.

- A migrated but empty database may be application-ready while `data_available=false`, freshness is unknown, and `incomplete_data=true`.
- Stale accepted data may still be safely served with explicit freshness metadata; general application readiness remains true.
- A newer failed/partial group makes incomplete-data diagnostics visible but does not make the API process unready.
- When schema readiness is false, data freshness is `unavailable`; the application MUST NOT query missing tables and call the result fresh or empty.
- A future consumer-specific gate that requires fresh data must be a separate data-readiness policy, not a redefinition of liveness.

If a single operational summary is needed, data state uses explicit fields (`data_available`, `stale_data`, `incomplete_data`, latest accepted group) rather than collapsing all conditions into `healthy`.

#### Reachable PostgreSQL with missing tables

When PostgreSQL is reachable but Alembic migrations or required application tables are absent:

```txt
liveness = alive (HTTP 200 on the liveness check)
database connectivity = connected
schema readiness = not ready
application readiness = not ready (HTTP 503)
data freshness = unavailable
```

Normalized API endpoints MUST return a controlled HTTP 503 error with a stable code such as `schema_not_ready`. They MUST NOT return misleading empty data, expose a raw SQL/stack error, or create the schema. A correctly migrated but genuinely empty database is different: it is schema/application-ready, and collection/data availability is reported separately.

### 12.2 Rationale

The current `health_check()` in `src/tradehub_data/api/routes.py` runs only `SELECT 1`. A reachable empty PostgreSQL database is therefore reported as fully healthy even though normalized endpoints fail because application tables are missing. It also combines database connectivity and service health into one status.

The selected contract lets an orchestrator distinguish a dead process, a transient database outage, a deployment missing migrations, an incompatible build, and stale market data without restarting a live process merely because the market is closed or a collection failed.

### 12.3 Example

PostgreSQL accepts connections, but the database was newly created and has no Alembic table.

Required logical responses:

```txt
liveness check:       200 alive
database component:   connected
schema component:     schema_uninitialized
readiness check:      503 not_ready
data freshness:       unavailable
GET normalized data:  503 schema_not_ready
```

After the separately authorized deployment migration succeeds, the same empty database becomes application-ready. Normalized list queries may return an empty result with `data_available=false`; they must not claim fresh market data.

### 12.4 Edge cases

- Alembic revision matches but an operator manually dropped a required table: schema drift makes readiness fail.
- A constraint keeps its expected name but an operator changes its columns, predicate, foreign-key action, or check definition: signature drift makes readiness fail.
- A safe diagnostics view keeps the same name and columns but its definition, owner, security option, or dependency changes: signature drift makes readiness fail before the API queries it.
- Database is reachable with a role that can run `SELECT 1` but cannot read application tables: schema/application readiness fails with a safe permission reason.
- The API role can read everything it needs but also owns a table, inherits a writer role, or can select raw body/header columns: readiness fails as `role_overprivileged`.
- Database is temporarily slow: bounded connectivity/readiness checks fail or time out; they never hang indefinitely.
- A newer application is deployed before its migration: readiness remains 503 and traffic must not be sent to it.
- A database revision is newer than the application understands: fail closed as incompatible; do not attempt downgrade.
- Required configuration is missing/invalid: liveness remains alive, while application readiness is 503 with a safe configuration reason.
- The migrated database has no accepted group: readiness is true, data availability false.
- The last accepted group is stale after a collection outage: readiness remains true, data freshness is stale, and scheduler acceptance for the failed group remains false.
- BVC is unreachable while the read-only API/database are ready: API readiness stays true; collector operational diagnostics show the outage.
- Optional CA bundle configuration is invalid for a role that has live collection explicitly enabled: collector-role readiness may fail, while a separately deployed read-only API role need not depend on it.
- Health error output never includes database URLs, credentials, SQL exception text, table contents, raw headers, or source payloads.

### 12.5 Required tests

Later implementation MUST test:

1. liveness returns without creating a database session;
2. database connectivity succeeds on `SELECT 1` and fails safely/quickly on connection error or timeout;
3. reachable PostgreSQL with no Alembic table yields connected database, failed schema readiness, and HTTP 503 application readiness;
4. behind, ahead, mismatched-head, and matching Alembic revisions produce the specified outcomes;
5. matching revision with a missing required table/column/constraint is detected as schema drift;
6. insufficient application-table permissions fail readiness without leaking SQL details;
7. a correctly migrated empty database is application-ready while data availability/freshness is explicitly unavailable or unknown;
8. stale data and a newer failed group do not fail general application readiness;
9. normalized endpoints return controlled `schema_not_ready` rather than an empty list or unhandled exception when schema is absent;
10. no health/readiness path runs migrations, creates tables, or performs a BVC request;
11. readiness errors redact connection strings, credentials, SQL, stack traces, headers, and payloads;
12. freshness fields use the accepted-group/occurrence definitions from section 9;
13. role-specific optional dependencies affect only roles that declare them required;
14. all readiness/schema checks run against PostgreSQL in integration tests, not only SQLite or mocked `SELECT 1`.
15. invalid/missing required configuration fails aggregate `/health` readiness without affecting dependency-free liveness;
16. PostgreSQL-unreachable and schema-probe-timeout cases return readiness 503 while liveness remains 200;
17. the packaged manifest missing/mismatch and a missing named constraint are detected deterministically;
18. readiness is re-evaluated after schema drift/permission revocation and no cached success masks failure;
19. a read-only runtime role can inspect the manifest and safe diagnostics view without write or raw-body/header access;
20. a same-named unique, check, foreign-key, or index definition with a changed signature fails as schema drift;
21. effective forbidden privilege supplied by ownership, `PUBLIC`, direct grant, nested role membership, superuser, or `BYPASSRLS` fails as `role_overprivileged`;
22. permission checks distinguish a missing required read grant from a forbidden excess grant using the stable reason codes above;
23. a same-named diagnostics view with unchanged output types but altered definition, owner, security options, or unallowlisted routine dependency fails as schema drift.

### 12.6 Database implications

- No write is required for liveness, connectivity, schema readiness, application readiness, or freshness checks.
- The runtime role needs bounded read access to `alembic_version`, required application tables/views, and safe catalog metadata needed to verify the schema and effective-role manifests. It must not require application-data writes to perform these checks.
- The application build must package its expected Alembic head set and immutable role-specific required-schema manifest deterministically.
- The packaged manifest must contain canonical full constraint signatures and positive and negative privilege policy; checking names or direct grants alone is insufficient.
- Security-critical view and any allowlisted routine definitions/options/ownership/dependencies must be canonicalized and signed in the manifest; checking only view names and projected column types is insufficient.
- Group/occurrence/publication records defined earlier provide data-freshness evidence; a health table must not duplicate or override those facts.
- Readiness queries require indexes only where they read group/freshness aggregates; they MUST remain bounded.
- Deployment tooling, not a health endpoint, owns migration execution.

### 12.7 Open questions

- What exact URLs and authentication/network exposure should be used for liveness, readiness, and detailed operational diagnostics?
- Which exact physical table/view/constraint names will the schema-design migration assign to the logical BVC objects required above?
- Should an operator-only response reveal expected/current Alembic revision IDs, while the public response exposes only safe reason codes?
- Will API and collector/worker roles be deployed separately, and which configuration/dependencies are required for each?
- Does any future consumer require a separate strict data-readiness gate in addition to explicit API freshness metadata?
