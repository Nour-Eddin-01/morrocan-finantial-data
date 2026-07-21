from __future__ import annotations

import asyncio
import codecs
import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from urllib.parse import urlencode, urljoin, urlsplit, urlunsplit
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from tradehub_data.collectors.bvc_prices.client import BvcPriceClient
from tradehub_data.collectors.bvc_prices.config import BvcPriceCollectorConfig
from tradehub_data.collectors.bvc_prices.constants import (
    BVC_EQUITY_PRICE_DATASET_CODE,
    BVC_HTML_REQUEST_PROFILE,
    BVC_JSON_REQUEST_PROFILE,
    BVC_MAX_REDIRECT_HOPS,
    BVC_PRICE_COLLECTOR_NAME,
    BVC_PRICE_HTML_SOURCE_ENDPOINT,
    BVC_PRICE_JSON_SOURCE_ENDPOINT,
    BVC_PRICE_SOURCE_CODE,
    BVC_PRICE_SOURCE_NAME,
    TEMPORARY_STATUS_CODES,
)
from tradehub_data.collectors.bvc_prices.models import (
    BvcHttpResponseEvidence,
    BvcPriceCollectorResult,
    BvcTransportFailureEvidence,
)
from tradehub_data.collectors.http_metadata import (
    BVC_SAFE_RESPONSE_HEADERS_POLICY_VERSION,
    UnsafeBvcUrlError,
    filter_safe_response_headers,
    sanitize_bvc_http_url,
)
from tradehub_data.core.config import get_settings
from tradehub_data.core.logging import configure_logging
from tradehub_data.db.session import SessionLocal
from tradehub_data.models import CollectionGroup, CollectionGroupPage, RawPayload
from tradehub_data.parsers.bvc_prices.diagnostics import diagnose_bvc_price_payload
from tradehub_data.repositories.collection_audit import (
    create_collection_group,
    create_collection_group_page,
    finalize_collection_group_and_run,
    finalize_page_failure,
    finalize_page_with_selection,
    record_response_occurrence,
    record_transport_failure_occurrence,
)
from tradehub_data.repositories.exchanges import get_or_create_exchange
from tradehub_data.repositories.raw_contents import (
    ExactRawCompatibilityContext,
    fill_exact_raw_content_text_cache,
)
from tradehub_data.repositories.sources import create_ingestion_run, get_or_create_data_source

logger = logging.getLogger(__name__)

_BVC_EXCHANGE_CODE = "BVC"
_BVC_EXCHANGE_NAME = "Bourse de Casablanca"
_BVC_EXCHANGE_TIMEZONE = "Africa/Casablanca"
_BVC_EXCHANGE_CURRENCY = "MAD"
_ALLOWED_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_CHARSET_PARAMETER = re.compile(r"(?:^|;)\s*charset\s*=\s*(?:\"([^\"]+)\"|'([^']+)'|([^;\s]+))", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class _CollectionContext:
    source_id: UUID
    ingestion_run_id: UUID
    group_id: UUID
    started_at: datetime


@dataclass(frozen=True, slots=True)
class _AuditedResponse:
    evidence: BvcHttpResponseEvidence
    occurrence_id: UUID
    raw_payload_id: UUID
    raw_content_inserted: bool
    persisted_content_type: str | None


@dataclass(frozen=True, slots=True)
class _AuditedPageFetch:
    response: _AuditedResponse | None
    raw_contents_inserted: int
    raw_contents_reused: int
    occurrences_recorded: int
    failure_code: str | None = None


class _AuditStorageFailure(RuntimeError):
    """A constant-message failure that cannot disclose request evidence."""


class BvcPriceCollector:
    def __init__(
        self,
        db: Session,
        config: BvcPriceCollectorConfig,
        client: BvcPriceClient | None = None,
    ) -> None:
        self.db = db
        self.config = config
        self.client = client or BvcPriceClient(config)

    async def run(self) -> BvcPriceCollectorResult:
        """Collect configured HTML pages with exact-response audit evidence.

        The HTML listing currently has no independently verified completion
        signal.  Qualifying pages therefore remain useful to the compatibility
        pipeline, but the acquisition group is conservatively incomplete.
        """

        if not self.config.enabled:
            logger.info("bvc_price_collector_skipped", extra={"collector": BVC_PRICE_COLLECTOR_NAME})
            return _skipped_result(
                source_urls_count=len(self.config.source_urls),
                message="collector disabled",
            )

        context = self._start_collection(
            collection_mode="live_html",
            page_limit=1,
            run_role="acquisition",
        )
        stored = 0
        reused = 0
        occurrences = 0
        selected_raw_ids: list[str] = []
        observed_rows = 0
        failures: list[str] = []
        pages_attempted = 0

        logger.info(
            "bvc_price_collector_started",
            extra={
                "collector": BVC_PRICE_COLLECTOR_NAME,
                "ingestion_run_id": str(context.ingestion_run_id),
                "collection_group_id": str(context.group_id),
            },
        )

        for page_number, source_url in enumerate(self.config.source_urls, start=1):
            if page_number > 1 and self.config.sleep_between_requests_ms > 0:
                await asyncio.sleep(self.config.sleep_between_requests_ms / 1000)

            pages_attempted += 1
            page = self._create_page(context.group_id, page_number)
            try:
                logical_url = sanitize_bvc_http_url(
                    source_url,
                    allowed_hosts=self.config.allowed_domains,
                )
                fetched = await self._fetch_page_with_audit(
                    context=context,
                    page=page,
                    source_url=source_url,
                    logical_url=logical_url,
                    source_endpoint=BVC_PRICE_HTML_SOURCE_ENDPOINT,
                    request_profile=BVC_HTML_REQUEST_PROFILE,
                    headers=None,
                )
            except (UnsafeBvcUrlError, _AuditStorageFailure):
                fetched = _AuditedPageFetch(
                    response=None,
                    raw_contents_inserted=0,
                    raw_contents_reused=0,
                    occurrences_recorded=0,
                    failure_code="audit_storage_error",
                )

            stored += fetched.raw_contents_inserted
            reused += fetched.raw_contents_reused
            occurrences += fetched.occurrences_recorded
            if fetched.response is None:
                reason = fetched.failure_code or "page_request_failed"
                failures.append(reason)
                self._fail_page(context.group_id, page.id, reason)
                continue

            response = fetched.response
            text, decode_error = self._decode_and_cache_response(
                response=response,
                context=context,
            )
            if decode_error is not None:
                failures.append(decode_error)
                self._fail_page(context.group_id, page.id, decode_error)
                continue

            try:
                diagnostic = diagnose_bvc_price_payload(
                    raw_payload_id=response.raw_payload_id,
                    payload_text=text,
                    content_type=response.persisted_content_type,
                    source_endpoint=BVC_PRICE_HTML_SOURCE_ENDPOINT,
                )
            except Exception:
                # The response evidence was committed before diagnostics.  The
                # exception text is deliberately neither logged nor persisted.
                failures.append("structural_inspection_error")
                self._fail_page(context.group_id, page.id, "structural_inspection_error")
                continue

            if diagnostic.status != "success" or diagnostic.rows_detected <= 0:
                failures.append("html_structure_not_qualified")
                self._fail_page(context.group_id, page.id, "html_structure_not_qualified")
                continue

            self._select_page(
                context.group_id,
                page.id,
                response,
                page_role="data",
                reason="first_qualifying_success",
                structural_reason_code="html_diagnostics_qualified",
            )
            selected_raw_ids.append(str(response.raw_payload_id))
            observed_rows += diagnostic.rows_detected

        selected_count = len(selected_raw_ids)
        if selected_count:
            status = "partial_success"
            stop_reason = (
                "page_failure" if failures else "configured_html_scope_no_completion_evidence"
            )
            message = (
                "HTML collection is usable but pagination completeness is not proven"
            )
        else:
            status = "failed"
            stop_reason = failures[0] if failures else "no_qualified_pages"
            message = "HTML collection produced no qualifying page"

        self._finalize_group(
            context=context,
            status=status,
            pagination_complete=False,
            completion_evidence_kind="none",
            stop_reason=stop_reason,
            observed_instrument_count=observed_rows,
            records_collected=occurrences,
            records_inserted=stored,
            records_failed=len(failures),
            safe_error_code=failures[0] if failures else "pagination_incomplete",
            message=message,
            run_metadata={
                "collection_mode": "live_html",
                "collection_group_id": str(context.group_id),
                "pages_attempted": pages_attempted,
                "pagination_complete": False,
                "collection_stop_reason": stop_reason,
                "raw_payload_ids": selected_raw_ids,
                "payloads_skipped": reused,
            },
            safe_diagnostic_codes=failures,
        )

        logger.info(
            "bvc_price_collector_finished",
            extra={
                "collector": BVC_PRICE_COLLECTOR_NAME,
                "ingestion_run_id": str(context.ingestion_run_id),
                "status": status,
            },
        )
        return BvcPriceCollectorResult(
            status=status,
            ingestion_run_id=context.ingestion_run_id,
            source_urls_count=pages_attempted,
            payloads_stored=stored,
            payloads_skipped=reused,
            errors_count=len(failures),
            message=message,
        )

    async def run_json_pages(self) -> BvcPriceCollectorResult:
        if not self.config.enabled:
            logger.info("bvc_price_json_collector_skipped", extra={"collector": BVC_PRICE_COLLECTOR_NAME})
            return _skipped_result(source_urls_count=0, message="collector disabled")
        if not self.config.json_enabled:
            return _skipped_result(source_urls_count=0, message="JSON collector disabled")

        context = self._start_collection(
            collection_mode="live_json",
            page_limit=self.config.json_page_limit,
            run_role="acquisition",
        )
        stored = 0
        reused = 0
        occurrences = 0
        selected_raw_ids: list[str] = []
        selected_page_sizes: list[int] = []
        failures: list[str] = []
        pages_attempted = 0
        pagination_complete = False
        completion_evidence = "none"
        stop_reason = "max_pages"

        for page_index in range(self.config.json_max_pages):
            if page_index > 0 and self.config.sleep_between_requests_ms > 0:
                await asyncio.sleep(self.config.sleep_between_requests_ms / 1000)

            page_number = page_index + 1
            page_offset = page_index * self.config.json_page_limit
            source_url = self._json_page_url(
                limit=self.config.json_page_limit,
                offset=page_offset,
            )
            pages_attempted += 1
            page = self._create_page(context.group_id, page_number)

            try:
                logical_url = sanitize_bvc_http_url(
                    source_url,
                    allowed_hosts=self.config.allowed_domains,
                )
                fetched = await self._fetch_page_with_audit(
                    context=context,
                    page=page,
                    source_url=source_url,
                    logical_url=logical_url,
                    source_endpoint=BVC_PRICE_JSON_SOURCE_ENDPOINT,
                    request_profile=BVC_JSON_REQUEST_PROFILE,
                    headers={
                        "Accept": self.config.json_accept_header,
                        "Referer": self.config.json_referer,
                        "Accept-Language": self.config.accept_language,
                    },
                )
            except (UnsafeBvcUrlError, _AuditStorageFailure):
                fetched = _AuditedPageFetch(
                    response=None,
                    raw_contents_inserted=0,
                    raw_contents_reused=0,
                    occurrences_recorded=0,
                    failure_code="audit_storage_error",
                )

            stored += fetched.raw_contents_inserted
            reused += fetched.raw_contents_reused
            occurrences += fetched.occurrences_recorded

            if fetched.response is None:
                reason = fetched.failure_code or "page_request_failed"
                failures.append(reason)
                stop_reason = reason
                self._fail_page(context.group_id, page.id, reason)
                break

            response = fetched.response
            text, decode_error = self._decode_and_cache_response(
                response=response,
                context=context,
            )
            if decode_error is not None:
                failures.append(decode_error)
                stop_reason = decode_error
                self._fail_page(context.group_id, page.id, decode_error)
                break

            try:
                decoded_json = json.loads(text, parse_float=Decimal)
                page_size = _json_rows_count(decoded_json)
            except json.JSONDecodeError:
                failures.append("malformed_json")
                stop_reason = "malformed_json"
                self._fail_page(context.group_id, page.id, "malformed_json")
                break
            except (TypeError, ValueError):
                failures.append("unexpected_json_shape")
                stop_reason = "unexpected_json_shape"
                self._fail_page(context.group_id, page.id, "unexpected_json_shape")
                break

            if page_size == 0:
                if page_number == 1:
                    failures.append("empty_first_page")
                    stop_reason = "empty_first_page"
                    self._fail_page(context.group_id, page.id, "empty_first_page")
                    break
                if not selected_page_sizes or any(
                    size != self.config.json_page_limit for size in selected_page_sizes
                ):
                    failures.append("unproven_empty_later_page")
                    stop_reason = "unproven_empty_later_page"
                    self._fail_page(context.group_id, page.id, "unproven_empty_later_page")
                    break

                self._select_page(
                    context.group_id,
                    page.id,
                    response,
                    page_role="terminal_sentinel",
                    reason="first_qualifying_success",
                    structural_reason_code="valid_zero_row_terminal_sentinel",
                )
                pagination_complete = True
                completion_evidence = "terminal_sentinel"
                stop_reason = "terminal_sentinel"
                break

            self._select_page(
                context.group_id,
                page.id,
                response,
                page_role="data",
                reason="first_qualifying_success",
                structural_reason_code="valid_json_data_page",
            )
            selected_raw_ids.append(str(response.raw_payload_id))
            selected_page_sizes.append(page_size)

            if page_size < self.config.json_page_limit:
                pagination_complete = True
                completion_evidence = "short_page"
                stop_reason = "short_page"
                break

        if pagination_complete:
            status = "success"
            message = None
        elif selected_raw_ids:
            status = "partial_success"
            message = "JSON collection has usable pages but pagination is incomplete"
        else:
            status = "failed"
            message = "JSON collection produced no qualifying data page"

        self._finalize_group(
            context=context,
            status=status,
            pagination_complete=pagination_complete,
            completion_evidence_kind=completion_evidence,
            stop_reason=stop_reason,
            observed_instrument_count=sum(selected_page_sizes),
            records_collected=occurrences,
            records_inserted=stored,
            records_failed=len(failures),
            safe_error_code=failures[0] if failures else (
                None if pagination_complete else "pagination_incomplete"
            ),
            message=message,
            run_metadata={
                "collection_mode": "live_json",
                "collection_group_id": str(context.group_id),
                "page_limit": self.config.json_page_limit,
                "max_pages": self.config.json_max_pages,
                "pages_attempted": pages_attempted,
                "pagination_complete": pagination_complete,
                "pagination_stop_reason": stop_reason,
                "raw_payload_ids": selected_raw_ids,
                "payloads_skipped": reused,
            },
            safe_diagnostic_codes=failures,
        )

        return BvcPriceCollectorResult(
            status=status,
            ingestion_run_id=context.ingestion_run_id,
            source_urls_count=pages_attempted,
            payloads_stored=stored,
            payloads_skipped=reused,
            errors_count=len(failures),
            message=message,
        )

    def _start_collection(
        self,
        *,
        collection_mode: str,
        page_limit: int,
        run_role: str,
    ) -> _CollectionContext:
        started_at = datetime.now(UTC)
        try:
            persisted_base_url = self._persisted_base_url()
            source = self._get_source(persisted_base_url=persisted_base_url)
            exchange, _ = get_or_create_exchange(
                self.db,
                code=_BVC_EXCHANGE_CODE,
                name=_BVC_EXCHANGE_NAME,
                country_code="MA",
                currency_code=_BVC_EXCHANGE_CURRENCY,
                timezone=_BVC_EXCHANGE_TIMEZONE,
                website_url=persisted_base_url,
                metadata={"official": True},
            )
            run = create_ingestion_run(
                self.db,
                source_id=source.id,
                collector_name=BVC_PRICE_COLLECTOR_NAME,
                run_type="manual",
                run_role=run_role,
                started_at=started_at,
                metadata={
                    "collection_mode": collection_mode,
                    "page_limit": page_limit,
                },
            )
            group = create_collection_group(
                self.db,
                source_id=source.id,
                exchange_id=exchange.id,
                ingestion_run_id=run.id,
                dataset_code=BVC_EQUITY_PRICE_DATASET_CODE,
                collection_mode=collection_mode,
                group_purpose="validation",
                page_limit=page_limit,
                started_at=started_at,
            )
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        return _CollectionContext(
            source_id=source.id,
            ingestion_run_id=run.id,
            group_id=group.id,
            started_at=started_at,
        )

    def _create_page(self, group_id: UUID, page_number: int) -> CollectionGroupPage:
        try:
            page = create_collection_group_page(
                self.db,
                group_id=group_id,
                logical_page_number=page_number,
            )
            self.db.commit()
            return page
        except Exception:
            self.db.rollback()
            raise

    async def _fetch_page_with_audit(
        self,
        *,
        context: _CollectionContext,
        page: CollectionGroupPage,
        source_url: str,
        logical_url: str,
        source_endpoint: str,
        request_profile: str,
        headers: dict[str, str] | None,
    ) -> _AuditedPageFetch:
        raw_contents_inserted = 0
        raw_contents_reused = 0
        occurrences_recorded = 0

        for attempt_number in range(1, self.config.max_retries + 2):
            request_url = source_url
            for redirect_hop in range(BVC_MAX_REDIRECT_HOPS + 1):
                boundary_started_at = datetime.now(UTC)
                try:
                    result = await self.client.fetch_attempt(request_url, headers=headers)
                except Exception as exc:
                    # Keep the committed group/page lifecycle deterministic
                    # even if a custom client violates the typed boundary.  Do
                    # not persist or log its message, URL, headers, or body.
                    logger.warning(
                        "bvc_price_client_boundary_failed",
                        extra={"error_type": type(exc).__name__},
                    )
                    result = BvcTransportFailureEvidence(
                        requested_at=boundary_started_at,
                        finished_at=datetime.now(UTC),
                        safe_error_code="network_error",
                        safe_error_message="network request failed",
                    )
                occurrence_id = uuid4()
                sanitized_requested_url = sanitize_bvc_http_url(
                    request_url,
                    allowed_hosts=self.config.allowed_domains,
                )

                if isinstance(result, BvcTransportFailureEvidence):
                    try:
                        record_transport_failure_occurrence(
                            self.db,
                            group_id=context.group_id,
                            group_page_id=page.id,
                            source_id=context.source_id,
                            ingestion_run_id=context.ingestion_run_id,
                            request_sequence=page.logical_page_number,
                            attempt_number=attempt_number,
                            redirect_hop=redirect_hop,
                            logical_request_url=logical_url,
                            requested_url=sanitized_requested_url,
                            source_endpoint=source_endpoint,
                            request_profile=request_profile,
                            requested_at=result.requested_at,
                            finished_at=result.finished_at,
                            safe_error_code=result.safe_error_code,
                            safe_error_message=result.safe_error_message,
                            response_headers_policy_version=BVC_SAFE_RESPONSE_HEADERS_POLICY_VERSION,
                            occurrence_id=occurrence_id,
                        )
                        self.db.commit()
                    except Exception as exc:
                        self.db.rollback()
                        raise _AuditStorageFailure("collection audit storage failed") from exc
                    occurrences_recorded += 1
                    if attempt_number <= self.config.max_retries:
                        await self._retry_delay(attempt_number)
                        break
                    return _AuditedPageFetch(
                        response=None,
                        raw_contents_inserted=raw_contents_inserted,
                        raw_contents_reused=raw_contents_reused,
                        occurrences_recorded=occurrences_recorded,
                        failure_code=result.safe_error_code,
                    )

                audited = self._store_http_response(
                    context=context,
                    page=page,
                    result=result,
                    occurrence_id=occurrence_id,
                    request_sequence=page.logical_page_number,
                    attempt_number=attempt_number,
                    redirect_hop=redirect_hop,
                    logical_url=logical_url,
                    source_endpoint=source_endpoint,
                    request_profile=request_profile,
                )
                occurrences_recorded += 1
                if audited.raw_content_inserted:
                    raw_contents_inserted += 1
                else:
                    raw_contents_reused += 1

                if 200 <= result.status_code <= 299:
                    return _AuditedPageFetch(
                        response=audited,
                        raw_contents_inserted=raw_contents_inserted,
                        raw_contents_reused=raw_contents_reused,
                        occurrences_recorded=occurrences_recorded,
                    )

                if result.status_code in _ALLOWED_REDIRECT_STATUSES:
                    if result.redirect_location is None or redirect_hop >= BVC_MAX_REDIRECT_HOPS:
                        return _AuditedPageFetch(
                            response=None,
                            raw_contents_inserted=raw_contents_inserted,
                            raw_contents_reused=raw_contents_reused,
                            occurrences_recorded=occurrences_recorded,
                            failure_code=(
                                "redirect_limit_reached"
                                if redirect_hop >= BVC_MAX_REDIRECT_HOPS
                                else "redirect_location_unusable"
                            ),
                        )
                    redirected_url = urljoin(result.response_url, result.redirect_location)
                    # Validate ownership before following, but transport may use
                    # the original redirect URL while persistence uses only its
                    # sanitized representation on the next hop.  The rejected
                    # location itself is never logged or persisted.
                    try:
                        sanitize_bvc_http_url(
                            redirected_url,
                            allowed_hosts=self.config.allowed_domains,
                        )
                    except UnsafeBvcUrlError:
                        return _AuditedPageFetch(
                            response=None,
                            raw_contents_inserted=raw_contents_inserted,
                            raw_contents_reused=raw_contents_reused,
                            occurrences_recorded=occurrences_recorded,
                            failure_code="redirect_host_not_allowed",
                        )
                    request_url = redirected_url
                    continue

                if (
                    result.status_code in TEMPORARY_STATUS_CODES
                    and attempt_number <= self.config.max_retries
                ):
                    await self._retry_delay(attempt_number)
                    break
                return _AuditedPageFetch(
                    response=None,
                    raw_contents_inserted=raw_contents_inserted,
                    raw_contents_reused=raw_contents_reused,
                    occurrences_recorded=occurrences_recorded,
                    failure_code="http_error_response",
                )
            else:  # pragma: no cover - bounded loop always returns or continues
                return _AuditedPageFetch(
                    response=None,
                    raw_contents_inserted=raw_contents_inserted,
                    raw_contents_reused=raw_contents_reused,
                    occurrences_recorded=occurrences_recorded,
                    failure_code="redirect_limit_reached",
                )

        return _AuditedPageFetch(  # pragma: no cover - attempts always resolve
            response=None,
            raw_contents_inserted=raw_contents_inserted,
            raw_contents_reused=raw_contents_reused,
            occurrences_recorded=occurrences_recorded,
            failure_code="network_error",
        )

    def _store_http_response(
        self,
        *,
        context: _CollectionContext,
        page: CollectionGroupPage,
        result: BvcHttpResponseEvidence,
        occurrence_id: UUID,
        request_sequence: int,
        attempt_number: int,
        redirect_hop: int,
        logical_url: str,
        source_endpoint: str,
        request_profile: str,
    ) -> _AuditedResponse:
        headers = filter_safe_response_headers(result.response_header_items)
        requested_url = sanitize_bvc_http_url(
            result.requested_url,
            allowed_hosts=self.config.allowed_domains,
        )
        response_url = sanitize_bvc_http_url(
            result.response_url,
            allowed_hosts=self.config.allowed_domains,
        )
        persisted_content_type = _bounded_content_type(headers.safe_response_headers)
        if 200 <= result.status_code <= 299:
            outcome = "success_response"
        elif result.status_code in _ALLOWED_REDIRECT_STATUSES:
            outcome = "redirect_response"
        else:
            outcome = "http_error_response"

        try:
            write = record_response_occurrence(
                self.db,
                group_id=context.group_id,
                group_page_id=page.id,
                source_id=context.source_id,
                ingestion_run_id=context.ingestion_run_id,
                entity_body=result.entity_body,
                compatibility_context=ExactRawCompatibilityContext(
                    ingestion_run_id=context.ingestion_run_id,
                    source_url=response_url,
                    source_endpoint=source_endpoint,
                    http_status=result.status_code,
                    content_type=persisted_content_type,
                    collected_at=result.response_received_at,
                    source_published_at=result.source_published_at,
                ),
                request_sequence=request_sequence,
                attempt_number=attempt_number,
                redirect_hop=redirect_hop,
                logical_request_url=logical_url,
                requested_url=requested_url,
                response_url=response_url,
                source_endpoint=source_endpoint,
                request_profile=request_profile,
                requested_at=result.requested_at,
                response_received_at=result.response_received_at,
                finished_at=result.finished_at,
                source_published_at=result.source_published_at,
                http_status=result.status_code,
                outcome=outcome,
                content_type=persisted_content_type,
                safe_response_headers=headers.safe_response_headers,
                dropped_response_header_name_count=(
                    headers.dropped_response_header_name_count
                ),
                response_headers_overflow=headers.response_headers_overflow,
                response_headers_policy_version=headers.policy_version,
                occurrence_id=occurrence_id,
            )
            raw_payload_id = write.raw_payload.id if write.raw_payload is not None else None
            if raw_payload_id is None:  # response outcomes always require content
                raise _AuditStorageFailure("collection audit storage failed")
            self.db.commit()
        except Exception as exc:
            self.db.rollback()
            if isinstance(exc, _AuditStorageFailure):
                raise
            raise _AuditStorageFailure("collection audit storage failed") from exc

        return _AuditedResponse(
            evidence=result,
            occurrence_id=write.occurrence.id,
            raw_payload_id=raw_payload_id,
            raw_content_inserted=write.raw_content_inserted,
            persisted_content_type=persisted_content_type,
        )

    def _decode_and_cache_response(
        self,
        *,
        response: _AuditedResponse,
        context: _CollectionContext,
    ) -> tuple[str, str | None]:
        try:
            payload_text = _decode_entity_body(
                response.evidence.entity_body,
                response.persisted_content_type,
            )
        except (LookupError, UnicodeDecodeError):
            return "", "entity_body_decode_failed"

        try:
            fill_exact_raw_content_text_cache(
                self.db,
                raw_payload_id=response.raw_payload_id,
                source_id=context.source_id,
                first_ingestion_run_id=context.ingestion_run_id,
                payload_text=payload_text,
            )
            self.db.commit()
        except Exception:
            self.db.rollback()
            return "", "compatibility_text_cache_failed"
        return payload_text, None

    def _select_page(
        self,
        group_id: UUID,
        page_id: UUID,
        response: _AuditedResponse,
        *,
        page_role: str,
        reason: str,
        structural_reason_code: str,
    ) -> None:
        try:
            finalize_page_with_selection(
                self.db,
                group_id=group_id,
                group_page_id=page_id,
                occurrence_id=response.occurrence_id,
                page_role=page_role,
                selected_at=max(datetime.now(UTC), response.evidence.finished_at),
                selection_reason=reason,
                structural_reason_code=structural_reason_code,
            )
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

    def _fail_page(self, group_id: UUID, page_id: UUID, reason: str) -> None:
        try:
            finalize_page_failure(
                self.db,
                group_id=group_id,
                group_page_id=page_id,
                finalized_at=datetime.now(UTC),
                structural_reason_code=reason,
            )
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

    def _finalize_group(
        self,
        *,
        context: _CollectionContext,
        status: str,
        pagination_complete: bool,
        completion_evidence_kind: str,
        stop_reason: str,
        observed_instrument_count: int,
        records_collected: int,
        records_inserted: int,
        records_failed: int,
        safe_error_code: str | None,
        message: str | None,
        run_metadata: dict[str, Any],
        safe_diagnostic_codes: list[str],
    ) -> CollectionGroup:
        try:
            group = finalize_collection_group_and_run(
                self.db,
                group_id=context.group_id,
                collection_status=status,
                pagination_complete=pagination_complete,
                completion_evidence_kind=completion_evidence_kind,
                finalized_at=datetime.now(UTC),
                collection_stop_reason=stop_reason,
                observed_instrument_count=observed_instrument_count,
                records_collected=records_collected,
                records_inserted=records_inserted,
                records_updated=0,
                records_failed=records_failed,
                safe_error_code=safe_error_code,
                error_message=message,
                run_metadata=run_metadata,
                safe_diagnostic_codes=safe_diagnostic_codes,
            )
            self.db.commit()
            return group
        except Exception:
            self.db.rollback()
            raise

    async def _retry_delay(self, attempt_number: int) -> None:
        if self.config.retry_backoff_seconds > 0:
            await asyncio.sleep(self.config.retry_backoff_seconds * (2 ** (attempt_number - 1)))

    def _persisted_base_url(self) -> str:
        sanitized = sanitize_bvc_http_url(
            self.config.base_url,
            allowed_hosts=self.config.allowed_domains,
        )
        parsed = urlsplit(sanitized)
        # A source/exchange base URL is identity/configuration context, never
        # request pagination context.  Persist no query at all here.
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))

    def _get_source(self, *, persisted_base_url: str):
        return get_or_create_data_source(
            self.db,
            code=BVC_PRICE_SOURCE_CODE,
            name=BVC_PRICE_SOURCE_NAME,
            source_type="exchange",
            base_url=persisted_base_url,
            country_code="MA",
            priority=100,
            metadata={
                "official": True,
                "market": "Casablanca Stock Exchange",
                "collector": BVC_PRICE_COLLECTOR_NAME,
            },
        )

    def _json_page_url(self, *, limit: int, offset: int) -> str:
        query = urlencode({"page[limit]": limit, "page[offset]": offset})
        separator = "&" if "?" in self.config.json_endpoint_base_url else "?"
        return f"{self.config.json_endpoint_base_url}{separator}{query}"


def _json_rows_count(payload: Any) -> int:
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, dict) and isinstance(data.get("data"), list):
            return len(data["data"])
        if isinstance(data, list):
            return len(data)
    raise ValueError("BVC JSON market data array not found")


def _bounded_content_type(headers: dict[str, list[str]]) -> str | None:
    values = headers.get("content-type")
    if not values or len(values) != 1:
        return None
    value = values[0]
    return value if len(value) <= 120 else None


def _decode_entity_body(entity_body: bytes, content_type: str | None) -> str:
    charset = "utf-8"
    if content_type:
        match = _CHARSET_PARAMETER.search(content_type)
        if match:
            charset = next(value for value in match.groups() if value).strip()
    codecs.lookup(charset)
    return entity_body.decode(charset, errors="strict")


def _skipped_result(*, source_urls_count: int, message: str) -> BvcPriceCollectorResult:
    return BvcPriceCollectorResult(
        status="skipped",
        ingestion_run_id=None,
        source_urls_count=source_urls_count,
        payloads_stored=0,
        payloads_skipped=0,
        errors_count=0,
        message=message,
    )


async def run_once() -> BvcPriceCollectorResult:
    config = BvcPriceCollectorConfig.from_env()
    with SessionLocal() as db:
        collector = BvcPriceCollector(db=db, config=config)
        return await collector.run()


def main() -> None:
    configure_logging(get_settings().log_level)
    config = BvcPriceCollectorConfig.from_env()
    with SessionLocal() as db:
        collector = BvcPriceCollector(db=db, config=config)
        result = asyncio.run(collector.run())
    print(result.model_dump_json())
    if config.fail_on_error and result.status == "failed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
