from sqlalchemy import select
from sqlalchemy.orm import Session

from tradehub_data.models import SyncState


def update_sync_state(db: Session, *, component_name: str, values: dict) -> SyncState:
    state = db.scalar(select(SyncState).where(SyncState.component_name == component_name))
    if state is None:
        state = SyncState(component_name=component_name, **values)
        db.add(state)
    else:
        for key, value in values.items():
            setattr(state, key, value)
    db.flush()
    return state

