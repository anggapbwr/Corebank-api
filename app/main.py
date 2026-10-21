"""CoreBank API — FastAPI entrypoint."""

from fastapi import FastAPI

from app.api.routes import health
from app.core.config import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    application = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description="Mini core banking API — double-entry ledger, RBAC, approval workflow.",
    )
    application.include_router(health.router)
    return application


app = create_app()
