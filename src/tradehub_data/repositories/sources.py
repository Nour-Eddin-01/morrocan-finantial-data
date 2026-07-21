from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from tradehub_data.models import DataSource, IngestionRun


def get_data_source_by_code(db: Session, code: str) -> DataSource | None:
    return db.scalar(select(DataSource).where(DataSource.code == code))


def get_or_create_data_source(
    db: Session,
    *,
    code: str,
    name: str,
    source_type: str,
    base_url: str | None = None,
    country_code: str | None = None,
    priority: int = 100,
    metadata: dict[str, Any] | None = None,
) -> DataSource:
    source = get_data_source_by_code(db, code)
    if source is not None:
        return source

    source = DataSource(
        code=code,
        name=name,
        source_type=source_type,
        base_url=base_url,
        country_code=country_code,
        priority=priority,
        metadata_=metadata,
    )
    db.add(source)
    db.flush()
    return source


def create_ingestion_run(
    db: Session,
    *,
    source_id,
    collector_name: str,
    run_type: str,
    started_at: datetime,
    run_role: str = "legacy_unclassified",
    parent_run_id=None,
    metadata: dict[str, Any] | None = None,
) -> IngestionRun:
    run = IngestionRun(
        source_id=source_id,
        collector_name=collector_name,
        run_type=run_type,
        run_role=run_role,
        parent_run_id=parent_run_id,
        status="running",
        started_at=started_at,
        metadata_=metadata,
    )
    db.add(run)
    db.flush()
    return run


def finish_ingestion_run(
    db: Session,
    run: IngestionRun,
    *,
    status: str,
    finished_at: datetime,
    records_collected: int | None = None,
    records_inserted: int | None = None,
    records_updated: int | None = None,
    records_failed: int | None = None,
    safe_error_code: str | None = None,
    error_message: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> IngestionRun:
    run.status = status
    run.finished_at = finished_at
    if records_collected is not None:
        run.records_collected = records_collected
    if records_inserted is not None:
        run.records_inserted = records_inserted
    if records_updated is not None:
        run.records_updated = records_updated
    if records_failed is not None:
        run.records_failed = records_failed
    run.safe_error_code = safe_error_code
    run.error_message = error_message
    if metadata is not None:
        run.metadata_ = metadata
    db.flush()
    return run
