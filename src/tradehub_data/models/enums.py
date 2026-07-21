SOURCE_TYPES = ("exchange", "regulator", "news", "macro", "manual", "other")
RUN_TYPES = ("scheduled", "manual", "backfill", "retry")
INGESTION_RUN_STATUSES = ("running", "success", "partial_success", "failed")
INGESTION_RUN_ROLES = (
    "acquisition",
    "authoritative_pipeline",
    "validation",
    "backfill",
    "publication_retry",
    "legacy_unclassified",
)
RAW_PAYLOAD_TYPES = ("json", "html", "csv", "xml", "text", "pdf_metadata", "other")
RAW_PAYLOAD_STATUSES = ("collected", "parsed", "normalized", "failed", "ignored")
RAW_CONTENT_EVIDENCE_KINDS = (
    "exact_entity_bytes",
    "legacy_decoded_text",
    "legacy_jsonb_only",
    "legacy_body_missing",
)
RAW_STORAGE_STATUSES = ("stored",)
COLLECTION_MODES = ("live_json", "live_html", "manual_fixture", "replay", "backfill")
COLLECTION_GROUP_PURPOSES = ("production", "validation", "backfill")
COLLECTION_STATUSES = ("running", "success", "partial_success", "failed")
COLLECTION_COVERAGE_STATUSES = ("unknown", "proven", "failed")
COLLECTION_COMPLETION_EVIDENCE_KINDS = (
    "authoritative_total",
    "short_page",
    "terminal_sentinel",
    "max_pages_exact_authoritative_total",
    "declared_fixture_scope",
    "none",
    "unknown_legacy",
)
COLLECTION_PAGE_ROLES = ("data", "terminal_sentinel", "unknown")
COLLECTION_PAGE_OUTCOMES = ("pending", "success", "failed")
COLLECTION_OCCURRENCE_OUTCOMES = (
    "success_response",
    "redirect_response",
    "http_error_response",
    "transport_failure",
    "fixture_loaded",
)
PROCESSING_STAGES = (
    "diagnostics",
    "parser",
    "normalizer",
    "repository_validation",
    "group_evaluation",
    "publication_staging",
)
PROCESSING_PAGE_STAGES = (
    "diagnostics",
    "parser",
    "normalizer",
    "repository_validation",
)
PROCESSING_GROUP_STAGES = ("group_evaluation", "publication_staging")
PROCESSING_ATTEMPT_STATUSES = (
    "running",
    "success",
    "partial_success",
    "failed",
    "skipped",
)
PROCESSING_COVERAGE_STATUSES = ("unknown", "proven", "failed")
PROCESSING_ACCEPTANCE_ELIGIBILITIES = (
    "not_evaluated",
    "eligible",
    "ineligible",
)
COLLECTION_PAGE_SELECTION_REASONS = (
    "first_qualifying_success",
    "fixture_selected",
    "legacy_validation_selection",
)
INSTRUMENT_TYPES = ("equity", "bond", "fund", "index", "other")
DATA_QUALITY_STATUSES = ("valid", "suspect", "stale", "missing")
SYNC_STATUSES = ("healthy", "degraded", "failed", "unknown")
NORMALIZATION_ERROR_STATUSES = ("open", "ignored", "fixed")
