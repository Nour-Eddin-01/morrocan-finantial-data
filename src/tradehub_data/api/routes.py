from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import text
from sqlalchemy.orm import Session

from tradehub_data.core.config import Settings, get_settings
from tradehub_data.db.session import get_db
from tradehub_data.api.bvc_market import router as bvc_market_router

router = APIRouter()
router.include_router(bvc_market_router)


@router.get("/health")
def health_check(
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, str]:
    try:
        db.execute(text("select 1"))
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "status": "unhealthy",
                "service": settings.app_name,
                "environment": settings.app_env,
                "database": "unavailable",
            },
        ) from exc

    return {
        "status": "healthy",
        "service": settings.app_name,
        "environment": settings.app_env,
        "database": "connected",
    }
