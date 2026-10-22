"""CoreBank API — FastAPI entrypoint."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.routes import (
    accounts,
    approvals,
    audit,
    auth,
    customers,
    health,
    system,
    transfers,
    web,
)
from app.core.config import get_settings
from app.core.observability import ObservabilityMiddleware, monitor_loop, setup_logging
from app.services.reconcile import reconcile_loop


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    """Startup: logging JSON + monitor gauge + reconciliation periodik."""
    settings = get_settings()
    setup_logging(settings.log_level)
    monitor_task = asyncio.create_task(monitor_loop())
    application.state.monitor_task = monitor_task
    recon_task = asyncio.create_task(reconcile_loop(settings.reconcile_interval_seconds))
    application.state.recon_task = recon_task
    yield
    for task in (monitor_task, recon_task):
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


def create_app() -> FastAPI:
    settings = get_settings()
    application = FastAPI(
        title=settings.app_name,
        version="0.3.0",
        description="Mini core banking API — double-entry ledger, RBAC, approval workflow.",
        lifespan=lifespan,
    )
    application.add_middleware(ObservabilityMiddleware)
    # Aset statis dashboard (Fase 6): htmx.min.js di-vendor → tanpa CDN/build step
    static_dir = Path(__file__).resolve().parent / "static"
    if static_dir.is_dir():
        application.mount("/static", StaticFiles(directory=static_dir), name="static")
    application.include_router(health.router)
    application.include_router(system.router)  # JSON untuk dashboard — sebelum web
    application.include_router(web.router)
    application.include_router(auth.router)
    application.include_router(customers.router)
    application.include_router(accounts.router)
    application.include_router(transfers.router)
    application.include_router(approvals.router)
    application.include_router(audit.router)
    return application


app = create_app()
