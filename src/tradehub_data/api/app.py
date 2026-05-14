import logging

import uvicorn
from fastapi import FastAPI

from tradehub_data.api.routes import router
from tradehub_data.core.config import get_settings
from tradehub_data.core.logging import configure_logging


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(title=settings.app_name, version="0.1.0")
    app.include_router(router)

    logging.getLogger(__name__).info("api_app_created", extra={"app_env": settings.app_env})
    return app


def run() -> None:
    settings = get_settings()
    uvicorn.run(
        "tradehub_data.api.app:create_app",
        factory=True,
        host=settings.api_host,
        port=settings.api_port,
    )

