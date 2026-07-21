# Collection Audit Foundation Migration

## Status and scope

Alembic revision `0002_add_collection_audit_foundation` is an additive schema foundation for recording future collection attempts without conflating raw-content identity with collection-occurrence identity. Its parent is `0001_initial_foundation`.

This migration deliberately does **not** activate the new audit model. The existing BVC collector, fixture loader, parser, diagnostics, normalizer, runner, repositories, and API keep their current behavior. In particular, runtime code does not yet create collection groups, logical pages, or occurrences, and it does not yet store exact response bytes. Collector dual-write belongs to a later mission.

The migration preserves every legacy ID, body representation, hash, timestamp, status, normalized price/bar row, and normalization-error row. It does not rename or remove any `0001` column.

## Objects added

### Transitional `ingestion_runs` fields

`ingestion_runs` gains:

- `run_role VARCHAR(40) NOT NULL`, with legacy/default value `legacy_unclassified`;
- `parent_run_id UUID NULL`, using a restrictive self-reference;
- `safe_error_code VARCHAR(80) NULL`; and
- `UNIQUE (id, source_id)`, which supports source-coherent composite foreign keys.

Controlled run roles, allowed legacy statuses, nonnegative counters, terminal/running timestamp state, and timestamp ordering are database checks. Checks over pre-existing status, counter, and lifecycle columns are created as PostgreSQL `NOT VALID`: they reject invalid future inserts or updates without asserting that unaudited historical rows were already valid. Validating those constraints against all legacy data requires a separate audit and explicit migration.

### Transitional `raw_payloads` fields

`raw_payloads` gains:

- `entity_body BYTEA NULL`;
- `entity_body_sha256 VARCHAR(64) NULL`;
- `entity_body_length BIGINT NULL`;
- `content_evidence_kind VARCHAR(40) NOT NULL`;
- `entity_hash_algorithm VARCHAR(50) NULL`;
- `storage_status VARCHAR(20) NOT NULL`; and
- `legacy_hash_algorithm VARCHAR(80) NULL`.

It also gains `UNIQUE (id, source_id)` and a partial unique index on `(source_id, entity_body_sha256)` for rows classified as `exact_entity_bytes`. Checks require exact rows to have bytes, a lowercase 64-character hexadecimal hash, the algorithm marker `sha256_entity_body_v1`, and a byte length equal to `octet_length(entity_body)`. These checks validate shape and internal consistency; this migration does not install `pgcrypto` or a trigger to recompute the digest. A future repository must compute SHA-256 from the captured response bytes before insertion.

The existing `UNIQUE (source_id, payload_hash)` constraint and all legacy raw fields remain unchanged.

### Acquisition audit tables

`collection_groups` represents one bounded acquisition group for a source, exchange, dataset, purpose, run, and page size. It records group ordering, collection and coverage states, pagination-completion evidence, expected/observed counts, safe diagnostic codes, and lifecycle timestamps. Composite foreign keys prevent a group from referring to a run owned by a different source.

`collection_group_pages` represents one logical page position in a group. Unique constraints prevent duplicate logical page numbers or offsets within a group. The database enforces positive page numbers and limits, nonnegative offsets, the formula `offset = (page_number - 1) * page_limit`, outcome/finalization coherence, and source/run/page-limit coherence with the parent group.

`collection_occurrences` represents one HTTP, transport, or fixture occurrence. It records request/attempt/redirect ordering, bounded safe diagnostics, response timing and status evidence, optional raw-content provenance, and a generated occurrence sequence. Composite foreign keys enforce source/run/page/raw coherence. Outcome checks distinguish successful responses, redirects, HTTP errors, transport failures, and fixture loads. The table requires safe response headers to be a JSON object; the seven-name header allowlist remains repository work for the future dual-write mission.

Generated identity sequences, explicitly named unique/check/foreign-key constraints, and bounded lookup indexes are included for groups and occurrences. No page-selection, processing-attempt, publication, revision, instrument-provenance, or normalization-error-observation structures are part of `0002`.

## Legacy evidence treatment

The upgrade classifies each existing raw row from evidence already stored:

| Existing body evidence | `content_evidence_kind` |
|---|---|
| `payload_text IS NOT NULL` | `legacy_decoded_text` |
| no text, but `payload IS NOT NULL` | `legacy_jsonb_only` |
| neither representation | `legacy_body_missing` |

PostgreSQL distinguishes SQL `NULL` from the JSONB literal `null`. The latter is
still a stored decoded JSON representation, so it follows the
`legacy_jsonb_only` branch; `legacy_body_missing` is reserved for SQL `NULL` in
both body columns. The SQLAlchemy transitional default follows the same rule
when Python `None` is explicitly sent through the existing JSON/JSONB type.

Every existing row receives `storage_status = stored` and `legacy_hash_algorithm = unknown_legacy`. It receives no `entity_body`, `entity_body_sha256`, `entity_body_length`, or `entity_hash_algorithm` value. This is intentional: decoded text or JSONB is not proof of the exact entity bytes received over HTTP.

`payload_hash` and `entity_body_sha256` have different meanings:

- legacy `payload_hash` is retained exactly as-is. Current BVC collection computes it from `source_url + "\n" + normalized decoded body text`; its identity therefore includes the URL and normalizes line endings;
- `entity_body_sha256` is reserved for SHA-256 over the exact response entity bytes only, independent of URL and text decoding; and
- `legacy_hash_algorithm = unknown_legacy` avoids retroactively claiming a universal legacy algorithm for rows whose provenance may differ.

No `collection_groups`, `collection_group_pages`, or `collection_occurrences` rows are synthesized during upgrade. A legacy raw row may have been reused after content deduplication and does not prove how many collection attempts occurred, which request or redirect produced it, its logical page membership, or its request/response timing. Creating occurrence history from that row would invent audit evidence and corrupt freshness semantics.

## Migration-test coverage

The isolated PostgreSQL 16 harness in `tests/postgres/` retains an explicit `0001_initial_foundation` fixture while treating `0002_add_collection_audit_foundation` as the current head. Its migration tests cover:

- a fresh empty database upgraded through `0001` and `0002`;
- PostgreSQL catalog inspection for all old and new tables, columns, identity properties, named constraints, and indexes;
- upgrade of representative `0001` rows, including all three legacy evidence classifications;
- preservation of legacy IDs and important raw, price, bar, and normalization-error values;
- proof that exact bytes/hashes and occurrence history are not invented;
- direct PostgreSQL rejection of invalid vocabularies, counters, lifecycle states, exact-content evidence, composite-FK mismatches, page offsets, and contradictory occurrence outcomes;
- successful valid occurrence evidence for each controlled outcome; and
- disposable `0002 -> 0001 -> 0002` downgrade/upgrade behavior.

Run safe static and Compose validation with:

```bash
python3 -m compileall -q src tests
docker compose config
docker compose -f docker-compose.test.yml config
```

Run the existing non-PostgreSQL suite with:

```bash
python3 -m pytest -m 'not postgres'
```

Run the migration suite only against the isolated, tmpfs-backed PostgreSQL 16 service:

```bash
docker compose \
  -p tradehub-data-migration-test \
  -f docker-compose.test.yml \
  run --rm --build migration-tests
```

Final validation results recorded on 2026-07-21:

| Validation | Result |
|---|---|
| `python3 -m compileall -q src tests` | Passed |
| `docker compose config` | Passed |
| `docker compose -f docker-compose.test.yml config` | Passed |
| Existing non-PostgreSQL suite | 92 passed, 26 deselected, 1 warning in 9.59s |
| Isolated PostgreSQL migration suite | 26 passed, 92 deselected, 1 warning in 8.33s |

Both pytest runs emitted the same upstream `StarletteDeprecationWarning` from
`fastapi.testclient`; no migration or SQLAlchemy reflection warning remained in
the final suites.

## Downgrade limitation

The downgrade drops only objects introduced by `0002`, in reverse dependency order, and preserves all `0001` rows and columns. It is safe only while the audit tables and new columns contain no irreplaceable collection evidence. After collector dual-write begins, downgrade requires an evidence export, impact review, and explicit approval; otherwise it would destroy occurrence history or exact response bytes.

`alembic_version.version_num` is widened from `VARCHAR(32)` to `VARCHAR(64)` because `0002_add_collection_audit_foundation` is 36 characters. The downgrade intentionally leaves that bookkeeping column at `VARCHAR(64)`: Alembic still stores the 36-character `0002` identifier while the downgrade function runs, so narrowing it within this revision could break Alembic's own version-row update. The wider type is backward-compatible with the `0001` identifier and contains no domain data.

## Runtime boundary

This revision provides storage and integrity primitives only. Runtime collection behavior is unchanged: no audit row is emitted, no new network request is made, no collector header policy is activated, and no parser, normalizer, price, bar, diagnostics, or API semantics are changed.
