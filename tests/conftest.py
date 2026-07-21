from collections.abc import Generator
from typing import Any

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from tradehub_data.models import Base, CollectionGroup, CollectionOccurrence


_SQLITE_IDENTITY_FIELDS = (
    (CollectionGroup, "group_sequence"),
    (CollectionOccurrence, "occurrence_sequence"),
)


def _emulate_sqlite_audit_identities(
    session: Session,
    _flush_context: Any,
    _instances: Any,
) -> None:
    """Provide SQLite-only values for PostgreSQL non-PK identity columns.

    SQLite ignores ``Identity`` on these non-primary-key columns. The real
    PostgreSQL integration suite continues to exercise database-generated
    identity values; this hook exists only for the in-memory unit-test fixture.
    """

    bind = session.get_bind()
    if bind.dialect.name != "sqlite":
        return

    counters = session.info.setdefault("sqlite_audit_identity_counters", {})
    for model, attribute_name in _SQLITE_IDENTITY_FIELDS:
        pending_rows = [row for row in session.new if isinstance(row, model)]
        explicit_values = [
            value
            for row in pending_rows
            if (value := getattr(row, attribute_name)) is not None
        ]
        next_value = max([counters.get(attribute_name, 0), *explicit_values])
        for row in pending_rows:
            if getattr(row, attribute_name) is None:
                next_value += 1
                setattr(row, attribute_name, next_value)
        counters[attribute_name] = next_value


@pytest.fixture()
def db_session() -> Generator[Session, None, None]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    with SessionLocal() as session:
        event.listen(session, "before_flush", _emulate_sqlite_audit_identities)
        yield session
