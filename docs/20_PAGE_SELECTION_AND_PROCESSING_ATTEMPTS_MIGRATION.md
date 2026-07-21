# Page Selection and Processing Attempts Migration

## Status and scope

Alembic revision `0003_add_page_selection_and_processing_attempts` is an additive schema migration whose parent is `0002_add_collection_audit_foundation`. It adds structures for conceptually immutable page-selection and processing-attempt evidence without activating them in the runtime pipeline. Database checks enforce valid row shapes; future repository/write policy owns update prevention.

The migration creates only:

- `processing_attempts`;
- `collection_page_selections`; and
- their named checks, foreign keys, unique constraints, and bounded indexes.

It does not alter any `0002` table or row. Existing `0002` composite unique keys already support every source/page/content ownership foreign key required by `0003`.

## Processing attempts

`processing_attempts` stores one execution envelope for diagnostics, parsing, normalization, repository validation, group evaluation, or publication staging. Each row has a caller-generated UUID and a positive PostgreSQL-generated `processing_attempt_sequence` for total database order. It records component/rule versions, declared input and optional output fingerprints, lifecycle status/times, bounded counts, optional group-evaluation fields, and safe diagnostic codes. This migration validates fingerprint shape and pairing; it does not recompute a digest or constrain the algorithm identifier.

Controlled stages are:

```txt
diagnostics
parser
normalizer
repository_validation
group_evaluation
publication_staging
```

Controlled statuses are:

```txt
running
success
partial_success
failed
skipped
```

The database requires a running row to have no completion time and a terminal row to have a completion time at or after its start. Counts are nonnegative. Input digests and present output digests are lowercase 64-character hexadecimal values, output algorithm/digest fields are all-or-none, `safe_diagnostic_codes` is a JSON array, and a present `eligibility_reason_codes` text array cannot contain null entries.

### Context shapes

| Stage kind | Group | Page | Occurrence | Raw content |
|---|---:|---:|---:|---:|
| Normal occurrence-backed page execution | required | required | required | required |
| Explicit raw-only reprocessing | null | null | null | required |
| Group evaluation or publication staging | required | null | null | null |

The page-stage set is `diagnostics`, `parser`, `normalizer`, and `repository_validation`. Group-only evaluation fields must remain null for those stages. The migration intentionally does not encode the complete publication-eligibility decision; it checks only context shape, vocabulary, and nonnegative values.

Composite foreign keys prove that:

- the processing run belongs to the declared source;
- a group belongs to the declared source;
- a page belongs to the declared source and group;
- raw content belongs to the declared source; and
- an occurrence belongs to the same source, raw content, and page used by the attempt.

The processing run may differ from the acquisition run that owns the group/page. This permits a later validation or reprocessing run while retaining immutable acquisition provenance.

### UUID and retry policy

Future repositories must generate the attempt UUID before execution, insert the running envelope, and retain that UUID across an ambiguous transaction or connection retry. A retry with the same UUID must load/reuse the already committed row. Intentional reprocessing uses a new UUID and therefore creates a new attempt; there is deliberately no uniqueness key on input/rule version that would collapse it.

Only the shape of `running` and terminal rows is database-enforced in this migration. Future repositories must finalize once and must never overwrite a terminal attempt. No update-prevention trigger or write-role privilege change is introduced by `0003`.

## Page selections

`collection_page_selections` records one insert-only choice of an occurrence for a logical page:

- primary key `group_page_id` guarantees at most one selection per page;
- unique `occurrence_id` prevents one occurrence from being selected for several pages;
- composite foreign key `(occurrence_id, group_page_id)` proves that the occurrence owns the selected page;
- optional `selected_by_processing_attempt_id` retains a selector-attempt reference; contextual coherence with the selected page/occurrence remains repository validation; and
- every delete dependency uses `RESTRICT`.

Selection reasons are `first_qualifying_success`, `fixture_selected`, and `legacy_validation_selection`. The last value is reserved by future repository policy for explicitly constructed non-production validation evidence; the database check controls only the vocabulary.

The database does not decide whether an occurrence is the first qualifying success, whether its structure is safe, whether several successful bodies conflict, or whether `selected_at` is after the occurrence time. Those are future repository rules. A selection has no mutable status and must never be replaced or deleted by normal runtime code; `0003` deliberately adds no update-prevention trigger or privilege change.

## Legacy evidence treatment

Upgrade from `0002` creates no processing attempts and no page selections. Legacy raw status, mutable metadata, runner result JSON, and processing timestamps cannot prove a separate execution start/end, component/rule version, fingerprint, page/occurrence context, or selection decision. Synthesizing rows from those fields would invent audit evidence.

The representative `0002` PostgreSQL fixture proves that existing source, exchange, run, exact raw bytes/hash, collection group, logical page, and successful occurrence remain byte/value-for-value unchanged after upgrade.

## Migration-test coverage and results

The isolated PostgreSQL 16 suite covers:

- fresh upgrade to the `0003` head and exact catalog signatures;
- SQLAlchemy/Alembic metadata comparison with no unintended drift;
- upgrade from a synthetic `0002` baseline with no history synthesized;
- representative legal instances of page, raw-only reprocessing, and group context shapes;
- invalid context, vocabulary, lifecycle, count, fingerprint, JSON, and array shapes;
- source/raw/page/occurrence composite-FK substitutions;
- page-selection reasons, one-per-page and one-use cardinality, cross-page rejection, and restrictive deletion; and
- disposable `0002 -> 0003 -> 0002 -> 0003` preservation.

Commands run on 2026-07-21:

```bash
python3 -m compileall -q src tests
docker compose config
docker compose -f docker-compose.test.yml config
git diff --check
docker compose \
  -p tradehub-data-migration-test \
  -f docker-compose.test.yml \
  run --rm --build migration-tests
```

Validation results:

| Validation | Result |
|---|---|
| Compileall | Passed |
| Main Compose config | Passed |
| Isolated-test Compose config | Passed |
| `git diff --check` | Passed |
| Existing non-PostgreSQL suite | 92 passed, 64 deselected, 1 warning in 9.88s |
| Isolated PostgreSQL migration suite | 64 passed, 92 deselected, 1 warning in 35.54s |

Both pytest runs reported the existing upstream `StarletteDeprecationWarning` from `fastapi.testclient`; no migration or metadata-drift warning remained.

## Downgrade limitation

The downgrade drops `collection_page_selections`, then the processing-attempt indexes and table. It removes no `0002` support key and changes no `0002` row.

Downgrade is safe only before these tables contain irreplaceable selection or processing evidence. Once runtime persistence is activated, evidence export, impact review, and explicit approval are required; erasing audit history is not an acceptable rollback strategy.

## Runtime boundary and deferred decisions

Runtime behavior is unchanged. The collector does not create groups, pages, occurrences, selections, or processing attempts. The parser, diagnostics, normalizer, runner, repositories, and API do not read or write the new tables.

A later runtime mission must define stage-specific mappings for current result counters and statuses. In particular, current diagnostics/runner pagination and partial-success behavior is not automatically target-compliant, legacy `RawPayload.status` remains mutable, and current string `pagination_group_id` metadata is not the new UUID group identity. The physical evaluated-coverage vocabulary is `unknown | proven | failed`; broader business vocabulary reconciliation remains deferred and `failed` must not be silently reinterpreted as another contract term.
