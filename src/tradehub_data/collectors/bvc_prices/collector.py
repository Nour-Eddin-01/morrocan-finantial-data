import asyncio
import json
import logging
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode
from uuid import uuid4

from sqlalchemy.orm import Session

from tradehub_data.collectors.bvc_prices.client import BvcPriceClient
from tradehub_data.collectors.bvc_prices.config import BvcPriceCollectorConfig
from tradehub_data.collectors.bvc_prices.constants import (
    BVC_PRICE_COLLECTOR_NAME,
    BVC_PRICE_JSON_SOURCE_ENDPOINT,
    BVC_PRICE_PAYLOAD_TYPE,
    BVC_PRICE_SOURCE_CODE,
    BVC_PRICE_SOURCE_NAME,
)
from tradehub_data.collectors.bvc_prices.errors import BvcFetchError
from tradehub_data.collectors.bvc_prices.models import BvcPriceCollectorResult
from tradehub_data.core.config import get_settings
from tradehub_data.core.hashing import sha256_source_payload
from tradehub_data.core.logging import configure_logging
from tradehub_data.db.session import SessionLocal
from tradehub_data.repositories.raw_payloads import insert_raw_payload_if_new, update_raw_payload_metadata
from tradehub_data.repositories.sources import create_ingestion_run, finish_ingestion_run, get_or_create_data_source

logger = logging.getLogger(__name__)


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
        if not self.config.enabled:
            logger.info("bvc_price_collector_skipped", extra={"collector": BVC_PRICE_COLLECTOR_NAME})
            return BvcPriceCollectorResult(
                status="skipped",
                ingestion_run_id=None,
                source_urls_count=len(self.config.source_urls),
                payloads_stored=0,
                payloads_skipped=0,
                errors_count=0,
                message="collector disabled",
            )

        source = self._get_source()
        run = create_ingestion_run(
            self.db,
            source_id=source.id,
            collector_name=BVC_PRICE_COLLECTOR_NAME,
            run_type="manual",
            started_at=datetime.now(UTC),
            metadata={"source_urls": self.config.source_urls},
        )
        self.db.commit()

        payloads_stored = 0
        payloads_skipped = 0
        errors: list[dict[str, str]] = []
        payload_ids: list[str] = []

        logger.info(
            "bvc_price_collector_started",
            extra={"collector": BVC_PRICE_COLLECTOR_NAME, "ingestion_run_id": str(run.id)},
        )

        for index, source_url in enumerate(self.config.source_urls):
            if index > 0 and self.config.sleep_between_requests_ms > 0:
                await asyncio.sleep(self.config.sleep_between_requests_ms / 1000)

            try:
                fetch_result = await self.client.fetch(source_url)
                payload_hash = sha256_source_payload(
                    source_url=fetch_result.source_url,
                    body_text=fetch_result.body_text,
                )
                raw_payload, inserted = insert_raw_payload_if_new(
                    self.db,
                    source_id=source.id,
                    ingestion_run_id=run.id,
                    source_url=fetch_result.source_url,
                    source_endpoint="bvc_price_snapshot",
                    payload_type=BVC_PRICE_PAYLOAD_TYPE,
                    payload_text=fetch_result.body_text,
                    payload_hash=payload_hash,
                    http_status=fetch_result.http_status,
                    content_type=fetch_result.content_type,
                    collected_at=fetch_result.fetched_at,
                    status="collected",
                    metadata={
                        "hash_strategy": "sha256(source_url + normalized_body)",
                        "headers": fetch_result.headers,
                    },
                )
                payload_ids.append(str(raw_payload.id))
                if inserted:
                    payloads_stored += 1
                    log_event = "bvc_raw_payload_stored"
                else:
                    payloads_skipped += 1
                    log_event = "bvc_raw_payload_duplicate"
                logger.info(
                    log_event,
                    extra={
                        "collector": BVC_PRICE_COLLECTOR_NAME,
                        "ingestion_run_id": str(run.id),
                        "source_url": fetch_result.source_url,
                        "payload_hash": payload_hash,
                    },
                )
            except BvcFetchError as exc:
                errors.append({"url": exc.source_url, "error": str(exc), "error_type": exc.error_type})
                logger.warning(
                    "bvc_price_fetch_failed",
                    extra={"collector": BVC_PRICE_COLLECTOR_NAME, "source_url": exc.source_url, "error_type": exc.error_type},
                )

        if errors and not payload_ids:
            status = "failed"
            message = f"all configured source URLs failed: {errors[0]['error_type']} {errors[0]['error']}"
        elif errors:
            status = "partial_success"
            message = "some configured source URLs failed"
        else:
            status = "success"
            message = None

        finish_ingestion_run(
            self.db,
            run,
            status=status,
            finished_at=datetime.now(UTC),
            records_collected=payloads_stored + payloads_skipped,
            records_inserted=payloads_stored,
            records_updated=0,
            records_failed=len(errors),
            error_message=message,
            metadata={
                "source_urls": self.config.source_urls,
                "raw_payload_ids": payload_ids,
                "failed_urls": errors,
                "payloads_skipped": payloads_skipped,
            },
        )
        self.db.commit()

        logger.info(
            "bvc_price_collector_finished",
            extra={"collector": BVC_PRICE_COLLECTOR_NAME, "ingestion_run_id": str(run.id), "status": status},
        )

        return BvcPriceCollectorResult(
            status=status,
            ingestion_run_id=run.id,
            source_urls_count=len(self.config.source_urls),
            payloads_stored=payloads_stored,
            payloads_skipped=payloads_skipped,
            errors_count=len(errors),
            message=message,
        )

    async def run_json_pages(self) -> BvcPriceCollectorResult:
        if not self.config.enabled:
            logger.info("bvc_price_json_collector_skipped", extra={"collector": BVC_PRICE_COLLECTOR_NAME})
            return BvcPriceCollectorResult(
                status="skipped",
                ingestion_run_id=None,
                source_urls_count=0,
                payloads_stored=0,
                payloads_skipped=0,
                errors_count=0,
                message="collector disabled",
            )
        if not self.config.json_enabled:
            return BvcPriceCollectorResult(
                status="skipped",
                ingestion_run_id=None,
                source_urls_count=0,
                payloads_stored=0,
                payloads_skipped=0,
                errors_count=0,
                message="JSON collector disabled",
            )

        source = self._get_source()
        pagination_group_id = f"bvc_price_snapshot:live_json:{uuid4()}"
        run = create_ingestion_run(
            self.db,
            source_id=source.id,
            collector_name=BVC_PRICE_COLLECTOR_NAME,
            run_type="manual",
            started_at=datetime.now(UTC),
            metadata={
                "collection_mode": "live_json",
                "json_endpoint": self.config.json_endpoint_base_url,
                "page_limit": self.config.json_page_limit,
                "max_pages": self.config.json_max_pages,
                "pagination_group_id": pagination_group_id,
            },
        )
        self.db.commit()

        payloads_stored = 0
        payloads_skipped = 0
        errors: list[dict[str, str]] = []
        payload_ids: list[str] = []
        pages_attempted = 0
        stop_reason: str | None = None
        last_payload = None

        for page_index in range(self.config.json_max_pages):
            if page_index > 0 and self.config.sleep_between_requests_ms > 0:
                await asyncio.sleep(self.config.sleep_between_requests_ms / 1000)

            page_number = page_index + 1
            page_offset = page_index * self.config.json_page_limit
            source_url = self._json_page_url(limit=self.config.json_page_limit, offset=page_offset)
            pages_attempted += 1
            try:
                fetch_result = await self.client.fetch(
                    source_url,
                    headers={
                        "Accept": self.config.json_accept_header,
                        "Referer": self.config.json_referer,
                        "Accept-Language": self.config.accept_language,
                    },
                )
                json_payload = json.loads(fetch_result.body_text)
                page_size = _json_rows_count(json_payload)
                if page_size == 0:
                    stop_reason = "empty_page"
                    break

                payload_hash = sha256_source_payload(
                    source_url=fetch_result.source_url,
                    body_text=fetch_result.body_text,
                )
                raw_payload, inserted = insert_raw_payload_if_new(
                    self.db,
                    source_id=source.id,
                    ingestion_run_id=run.id,
                    source_url=fetch_result.source_url,
                    source_endpoint=BVC_PRICE_JSON_SOURCE_ENDPOINT,
                    payload_type=BVC_PRICE_PAYLOAD_TYPE,
                    payload=json_payload if isinstance(json_payload, dict) else None,
                    payload_text=fetch_result.body_text,
                    payload_hash=payload_hash,
                    http_status=fetch_result.http_status,
                    content_type=fetch_result.content_type or "application/json",
                    collected_at=fetch_result.fetched_at,
                    status="collected",
                    metadata={
                        "hash_strategy": "sha256(source_url + normalized_body)",
                        "headers": fetch_result.headers,
                        "collection_mode": "live_json",
                        "page_number": page_number,
                        "page_offset": page_offset,
                        "page_limit": self.config.json_page_limit,
                        "page_size": page_size,
                        "pagination_group_id": pagination_group_id,
                        "pagination_stop_reason": None,
                    },
                )
                payload_ids.append(str(raw_payload.id))
                last_payload = raw_payload
                if inserted:
                    payloads_stored += 1
                else:
                    payloads_skipped += 1

                if page_size < self.config.json_page_limit:
                    stop_reason = "short_page"
                    break
            except (json.JSONDecodeError, ValueError) as exc:
                stop_reason = "malformed_json"
                errors.append({"url": source_url, "error": str(exc), "error_type": "malformed_json"})
                break
            except BvcFetchError as exc:
                stop_reason = "fetch_error"
                errors.append({"url": exc.source_url, "error": str(exc), "error_type": exc.error_type})
                logger.warning(
                    "bvc_price_json_fetch_failed",
                    extra={"collector": BVC_PRICE_COLLECTOR_NAME, "source_url": exc.source_url, "error_type": exc.error_type},
                )
                break

        if stop_reason is None:
            stop_reason = "max_pages"
        if last_payload is not None:
            update_raw_payload_metadata(self.db, last_payload, {"pagination_stop_reason": stop_reason})

        if errors and not payload_ids:
            status = "failed"
            message = f"live JSON collection failed: {errors[0]['error_type']} {errors[0]['error']}"
        elif errors:
            status = "partial_success"
            message = "live JSON collection stopped after a page failure"
        else:
            status = "success"
            message = None

        finish_ingestion_run(
            self.db,
            run,
            status=status,
            finished_at=datetime.now(UTC),
            records_collected=payloads_stored + payloads_skipped,
            records_inserted=payloads_stored,
            records_updated=0,
            records_failed=len(errors),
            error_message=message,
            metadata={
                "collection_mode": "live_json",
                "json_endpoint": self.config.json_endpoint_base_url,
                "page_limit": self.config.json_page_limit,
                "max_pages": self.config.json_max_pages,
                "pages_attempted": pages_attempted,
                "pagination_group_id": pagination_group_id,
                "pagination_stop_reason": stop_reason,
                "raw_payload_ids": payload_ids,
                "failed_urls": errors,
                "payloads_skipped": payloads_skipped,
            },
        )
        self.db.commit()

        return BvcPriceCollectorResult(
            status=status,
            ingestion_run_id=run.id,
            source_urls_count=pages_attempted,
            payloads_stored=payloads_stored,
            payloads_skipped=payloads_skipped,
            errors_count=len(errors),
            message=message,
        )

    def _get_source(self):
        return get_or_create_data_source(
            self.db,
            code=BVC_PRICE_SOURCE_CODE,
            name=BVC_PRICE_SOURCE_NAME,
            source_type="exchange",
            base_url=self.config.base_url,
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
