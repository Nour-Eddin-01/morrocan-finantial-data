# Raw-First Occurrence Dual Write

## Status and scope

This mission activates the collection-audit schema introduced by
`0002_add_collection_audit_foundation` and
`0003_add_page_selection_and_processing_attempts` for new BVC price
collections. The BVC HTTP collector and local fixture loader now persist:

```txt
exact immutable content
+ collection group
+ logical page
+ one occurrence for every observed attempt or response
+ one immutable selection for every qualified page
```

This is a transitional dual-write implementation. The selected
`RawPayload` remains the compatibility input to the existing diagnostics,
parser, normalizer, and pipeline runner, while the audit tables are the
authoritative evidence for new acquisition attempts, page ownership, and
pagination completeness.

The mission does **not** create processing attempts or accepted publication
state. It does not change normalized instrument, price, bar, normalization
error, public API, scheduler, or worker behavior. No live BVC collection was
run during implementation or validation.

## Exact-content identity

For every received HTTP body, including a zero-byte body, and for every local
fixture, `insert_or_get_exact_raw_content()` computes lowercase SHA-256
directly over the exact client-visible bytes. The authoritative identity is:

```txt
(source_id, entity_body_sha256)
where content_evidence_kind = exact_entity_bytes
```

An exact-content row stores:

- the unmodified bytes in `entity_body`;
- their SHA-256 digest in `entity_body_sha256`;
- their byte length in `entity_body_length`;
- `entity_hash_algorithm = sha256_entity_body_v1`;
- `content_evidence_kind = exact_entity_bytes`; and
- `storage_status = stored`.

PostgreSQL uses `INSERT ... ON CONFLICT DO NOTHING` against the partial exact
content index, followed by an identity and byte-integrity check. This makes
concurrent deduplication atomic. SQLite supports the same functional contract
for fast unit tests, but only PostgreSQL establishes the production concurrency
guarantee.

### Legacy compatibility fields

The still-required legacy fields are populated deterministically without
claiming the former URL-plus-decoded-text hash algorithm:

- `payload_hash` is a namespaced SHA-256 filler over the canonical source UUID
  and exact entity digest;
- `legacy_hash_algorithm = target_exact_compat_filler_v1`;
- `payload_type = bvc_price_snapshot`;
- `status = collected`;
- `error_message = NULL`;
- decoded JSON `payload` is omitted and therefore stored as SQL `NULL`; and
- legacy raw `metadata` is omitted and therefore stored as SQL `NULL`.

The filler preserves the existing `(source_id, payload_hash)` uniqueness
contract but is not an alternative content identity. New code must use
`entity_body_sha256` and `content_evidence_kind` when reasoning about exact
content.

Legacy non-null fields such as `ingestion_run_id`, `source_url`,
`collected_at`, `http_status`, `content_type`, and `source_published_at` capture
only the first storing occurrence's compatibility context. They are frozen on
deduplication and are not authoritative for freshness, retry identity, page
ownership, or later collection occurrences.

### Derived decoded-text cache

`payload_text` is initially SQL `NULL`. After exact content and its occurrence
have committed, the collector performs strict charset decoding and may fill
`payload_text` once as a derived compatibility cache. The guarded update
requires:

- the exact raw-content ID and source;
- the first storing ingestion run;
- an occurrence connecting that raw content to that run; and
- an existing `NULL` text cache.

A duplicate body observed by a later run cannot rewrite or repair the first
row's text cache. Decode or structural failures leave the exact bytes and
occurrence intact. `entity_body` remains authoritative even when a derived
text cache exists.

## Safe response-header policy

`filter_safe_response_headers()` applies policy
`bvc-safe-response-headers-v1`. The closed, case-insensitive allowlist is:

```txt
content-type
content-length
content-encoding
etag
last-modified
date
cache-control
```

Stored names are lowercase and values are arrays of strings. The six fields
other than `cache-control` are singletons: if one appears more than once, the
entire name is dropped. `cache-control` retains source order and is limited to
16 values.

A name is dropped when it is denied, unknown, duplicated contrary to the
singleton rule, has too many cache-control values, or has any value containing
a control character or exceeding 2,048 UTF-8 bytes. Deny checks take
precedence over the allowlist and cover cookies, authorization, credentials,
CSRF/XSRF, WAF, session, security, private, secret, and token identifiers.

The canonical retained JSON is limited to 8,192 UTF-8 bytes. Overflow stores
an empty object and sets `response_headers_overflow = true`. Audit storage
retains only the number of distinct dropped normalized names; rejected names
and values are neither persisted nor logged. Redirect `Location` is not a
retained response header.

## Persisted URL policy

Every persisted logical-request, requested, and response URL passes through
`sanitize_bvc_http_url()` independently. It:

- accepts only `http` or `https`;
- requires an exact configured BVC host after IDNA/lowercase normalization;
- removes user information and fragments;
- removes default ports and normalizes percent escapes;
- retains only `page[offset]`, `page[limit]`, `offset`, and `limit` query keys;
- retains only nonnegative ASCII-decimal pagination values and canonicalizes
  leading zeros; and
- deterministically sorts the retained query pairs.

All other query names and values are discarded without logging. Malformed or
disallowed-host URLs raise a constant-message `UnsafeBvcUrlError` that cannot
echo private URL material. Transport may use the original configured URL, but
only the sanitized representation can enter audit storage.

Unknown or valueless non-pagination query fields are discarded rather than
making an otherwise safe URL fail after a body has been received. Configured
base URLs are sanitized separately before they can enter `data_sources` or
`exchanges`, and no base-URL query or fragment is retained there.

Fixtures use the stable logical identifier:

```txt
manual-fixture://bvc-equity-prices
```

No local file path or file name is stored as collection evidence. An optional
public fixture source URL is accepted only after the same BVC URL policy.

## HTTP response and failure boundary

`BvcPriceClient.fetch_attempt()` performs exactly one network hop with
`follow_redirects=False`. It does not call `raise_for_status()`, decode text,
parse JSON, or inspect the response body. A response result exposes only the
evidence needed for raw-first persistence:

- request, response-received, and finish times;
- requested and response URLs;
- HTTP status;
- exact entity bytes;
- response-header items and content type; and
- one redirect location only when the response has exactly one such field.

A transport failure returns its request and finish times plus a stable error
code and constant safe message. Current codes are `timeout`, `connect_error`,
`tls_verification_error`, `protocol_error`, and `network_error`. Raw exception
text is not returned to persistence or logs.

HTTP-client and SSL-context construction are inside the same safe boundary.
An unreadable custom CA bundle becomes a redacted
`tls_verification_error`, and an unexpected custom-client exception is reduced
to `network_error` while only its exception class is logged. The original
message and private context are not retained. Custom CA bundles are loaded as
an `SSLContext`; verification remains enabled.

Retries and redirects are collector responsibilities so evidence is committed
before the next wait or request. Redirect response bodies are stored as exact
content and each observed hop receives a `redirect_response` occurrence. The
collector follows only a single usable `Location` value to an approved host,
and the chain is bounded by the configured redirect-hop cap. Missing,
duplicate, malformed, cross-host, or over-limit redirect targets stop the
page safely. No redirect hop is fabricated, and unrestricted `Location`
content is never stored.

This is deliberately a bounded one-hop client model, not a general browser
redirect/session implementation. More complex redirect semantics would
require a separately reviewed collector change.

## Raw-first ordering and transaction boundaries

The HTTP order is:

```txt
receive exact bytes
-> filter headers and sanitize persisted URLs
-> atomically insert/reuse exact content and insert occurrence
-> commit collection evidence
-> decode text
-> fill the guarded compatibility text cache
-> inspect JSON/HTML structure and row count
-> finalize page and selection
-> make pagination decision
```

The transaction boundaries are intentionally short:

1. The source, ingestion run, and running collection group are created and
   committed before any network wait.
2. Each logical page is created and committed before its request attempts.
3. Exact-content insert/reuse and response-occurrence insertion share one
   transaction. A storage failure rolls both back and prevents parsing.
4. A transport-failure occurrence is committed independently without raw
   content.
5. Decoding and the optional text-cache fill happen only after evidence is
   durable; parser or diagnostics failure cannot roll it back.
6. Page finalization and selection creation share one transaction.
7. Group finalization and its ingestion-run finalization share one transaction.

No database transaction remains open during an HTTP request, retry delay, or
inter-page sleep.

The fixture path follows the same principle: it commits the exact fixture
bytes and `fixture_loaded` occurrence before decoding or diagnostics, then
qualifies or fails the page in a later transaction.

If the fixture cannot be read, no body or occurrence is invented. The already
created page, group, and validation run are finalized as failed with the safe
code `fixture_read_failed`, so no pending lifecycle is abandoned.

## Group, page, occurrence, and selection lifecycle

Every enabled collector execution creates a running group before page
requests. Current groups use:

```txt
dataset_code = bvc_equity_prices
group_purpose = validation
coverage_status = unknown
pagination_complete = NULL while running
completion_evidence_kind = none while running
```

Modes are `live_json`, `live_html`, or `manual_fixture`. HTTP collector runs
use `run_role = acquisition`; fixture runs use `run_role = validation`. None
of these groups is an accepted production publication.

Each bounded page position is created once with a positive logical page
number, deterministic offset, inherited page limit, `page_role = unknown`, and
`collection_page_outcome = pending`.

Every observed attempt receives a distinct occurrence with its owning page,
positive attempt number, request sequence, redirect hop, safe request profile,
and timing evidence:

| Outcome | Raw content | HTTP response evidence |
|---|---:|---|
| `success_response` | required, including zero bytes | 2xx status, response URL and time |
| `redirect_response` | required, including zero bytes | allowed redirect status, response URL and time |
| `http_error_response` | required, including zero bytes | non-2xx/non-supported-redirect status, response URL and time |
| `transport_failure` | none | no response URL, response time, or HTTP status |
| `fixture_loaded` | required | no HTTP response URL, response time, or status |

Temporary HTTP responses and transport failures are committed before bounded
retry. Consequently, an exhausted three-attempt page has three occurrence
rows even if response bodies deduplicate to fewer exact-content rows.

### Qualification and immutable selection

A structurally valid, non-empty JSON page or successful non-empty HTML
diagnostic becomes a `data` page. Page success and selection are committed
atomically. HTTP pages use `first_qualifying_success`; fixtures use
`fixture_selected`.

The selection repository additionally enforces that:

- the occurrence owns the logical page;
- the occurrence outcome agrees with the selection reason;
- selection time does not precede occurrence completion;
- the same page cannot select another occurrence later;
- multiple different successful response bodies for one page are a conflict;
  and
- among equivalent successful bodies, the earliest occurrence is selected.

Malformed JSON, unexpected JSON shape, undecodable bytes, structurally
unqualified HTML/fixtures, and an empty HTTP body still retain exact content
and an occurrence. Their page becomes `failed`, remains role `unknown`, and
has no selection.

## Pagination and group finalization

JSON pagination uses only positive evidence:

| Scenario | Group result | Pagination evidence |
|---|---|---|
| Non-empty short page after contiguous successful pages | `success` | `pagination_complete = true`, `short_page` |
| Valid zero-row later page after contiguous full pages | `success` | selected terminal page, `terminal_sentinel` |
| Valid zero-row first page | `failed` | incomplete; no authoritative zero-universe rule exists |
| `max_pages` reached with usable pages | `partial_success` | incomplete, `none` |
| Later transport/HTTP/decode/shape failure after usable pages | `partial_success` | incomplete, `none` |
| Failure before any usable data page | `failed` | incomplete, `none` |

A later zero-row sentinel is selected and finalized as
`page_role = terminal_sentinel` but is not passed to normalization as a data
page. It proves completion only when all preceding selected pages are full and
contiguous. A short non-empty page proves completion directly. Reaching a
configured bound is never silently upgraded to success.

Group finalization verifies that successful pages all have selections, failed
pages have none, successful groups are contiguous and complete, and partial
groups have at least one selected data page. `selected_data_pages` and
`terminal_page_present` are derived from page/selection evidence.
`collection_completed_at` is derived from the latest selected required-page
response time (or occurrence finish time for non-HTTP evidence), including a
selected terminal sentinel. Coverage remains `unknown` in this mission.

The current HTML path has no independently verified pagination-completion
signal. A qualified HTML page is retained and selected, but the group remains
`partial_success` with `pagination_complete = false`; HTML collection cannot
claim complete acquisition yet.

## Fixture behavior

The fixture loader creates a validation run, manual-fixture group, and one
logical page before reading and storing evidence. It then:

1. reads the exact file bytes;
2. inserts/reuses exact content;
3. records a `fixture_loaded` occurrence with no HTTP response fields;
4. commits that evidence;
5. strictly decodes and runs existing diagnostics; and
6. either selects the page or finalizes it as failed.

A structurally qualified non-empty fixture receives
`completion_evidence_kind = declared_fixture_scope` and selection reason
`fixture_selected`. Invalid or empty fixture data remains audited but
unselected. Fixture load time is compatibility collection time only; it does
not become a market or source observation timestamp, and
`source_published_at` remains unknown unless separately proven.

## Duplicate content and compatibility behavior

Identical bytes from the same source reuse one exact raw-content row across
runs, pages, retries, and fixture loads. Every observation still creates its
own occurrence and belongs to its own group/page context. Each qualified page
has its own selection even when the selected raw-content ID is shared.

A duplicate occurrence never changes the raw row's first-run compatibility
URL, time, run ID, HTTP fields, source timestamp, derived text, status, error,
or metadata. Existing `update_raw_payload_status()` and
`update_raw_payload_metadata()` deliberately become no-ops for
`exact_entity_bytes`; processing lifecycle and computed grouping do not belong
on immutable content identity.

Selected exact `RawPayload` IDs remain consumable by the existing diagnostics,
normalizer, and runner. Existing normalized output and API response contracts
are unchanged.

## Transitional limitations

- Legacy `RawPayload.ingestion_run_id`, URL, collection time, HTTP fields, and
  source timestamp on an exact row describe only its first storing occurrence.
  New freshness logic must use occurrences and complete/selected groups.
- The guarded `payload_text` is a derived compatibility cache. Exact bytes are
  authoritative, and decoded JSON is intentionally not stored in `payload`.
- If identical bytes first arrive as a redirect, HTTP error, or otherwise
  unusable occurrence and the first-run text cache remains empty or has a
  different first content type, a later qualifying occurrence cannot mutate
  that frozen compatibility context. A legacy runner may therefore be unable
  to process that duplicate even though its occurrence is valid. Correcting
  this requires occurrence-bound processing inputs, not mutation of the exact
  raw row.
- Exact rows keep legacy `status = collected` because status mutation is a
  no-op. Until processing-attempt integration changes eligibility tracking,
  `normalize_eligible()` may select and reprocess an exact row on later runs.
- The legacy pipeline runner may still compute a string pagination-group ID
  for its current result and normalized metadata behavior. Its attempt to
  write that computed grouping into an exact raw row is a no-op; it is not the
  UUID `collection_groups.id` and must not be treated as audit provenance.
- HTML acquisition is intentionally incomplete until it has independent,
  validated completion evidence.
- The one-hop client and bounded redirect loop support observed HTTP redirect
  responses but are not a browser/session engine. Only one safe `Location`
  value per hop is followed, and no rejected location is persisted.
- `source_published_at` is currently retained only when safely supplied by the
  response boundary; the BVC HTTP client does not infer it from body content.
- Audit persistence stops before parsing on unsafe URL or database storage
  failure; it does not fall back to an unaudited legacy write.
- No runtime `processing_attempts` rows are created, so parser and normalizer
  execution history is not yet represented by the new processing schema.
- No publication attempt, eligibility decision, accepted-group pointer, price
  revision, or daily-bar revision is created. All current groups remain
  validation evidence, with coverage `unknown`.

## Validation

Safe validation commands for this mission are:

```bash
python3 -m compileall -q src tests
docker compose config
docker compose -f docker-compose.test.yml config
git diff --check
```

The focused non-PostgreSQL audit suite covers metadata helpers, the one-hop
client, mocked JSON/HTML/fixture collection, exact-content repositories,
collection lifecycle repositories, and legacy normalizer/runner compatibility.
The isolated PostgreSQL suite adds real constraint, deduplication,
transaction-ownership, parser-failure-survival, zero-byte, duplicate-content,
and header-redaction coverage.

```bash
docker compose run --rm --build api \
  sh -c "pip install -e '.[dev]' && pytest"

docker compose \
  -p tradehub-data-migration-test \
  -f docker-compose.test.yml \
  run --rm --build migration-tests
```

Results recorded during the focused implementation validation on 2026-07-21:

| Validation | Result |
|---|---|
| Focused non-PostgreSQL audit and compatibility suite | 138 passed |
| Focused isolated PostgreSQL raw-first suite | 9 passed |
| Full repository command without a configured test PostgreSQL URL | 161 passed, 64 PostgreSQL tests skipped, 1 warning |
| Complete isolated PostgreSQL suite | 73 passed, 152 non-PostgreSQL tests deselected, 1 warning |

The sole remaining warning is the existing Starlette `TestClient` deprecation
warning recommending `httpx2`; no collection-audit warning remains.

## Explicit runtime boundary

This mission adds collection evidence only. It does not write
`processing_attempts`, redesign normalization errors, change instrument merge
or price/bar rules, create publication state, alter public API behavior, add a
scheduler or worker, add a source or data type, or perform a live BVC request.
