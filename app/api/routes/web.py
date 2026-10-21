"""Halaman web dashboard (Fase 6) — server-rendered Jinja + HTMX, tanpa login.

Keputusan scope (dari brainstorm):
- Tanpa auth session-cookie: langsung dashboard (UI internal belajar — bukan produk)
- Fitur minimal: System Health (kartu gauge) + panel Alert di KANAN layar
- Update tanpa refresh: HTMX polling ke /fragments/* (yang memanggil API yang sama)
"""

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.api.routes.system import system_alerts, system_health

router = APIRouter(tags=["web"])
templates = Jinja2Templates(directory=Path(__file__).resolve().parents[2] / "templates")


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request) -> HTMLResponse:
    """Dashboard utama — render awal server-side, polling HTMX lanjutannya."""
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {"health": await system_health(), "alerts": await system_alerts()},
    )


@router.get("/fragments/health", response_class=HTMLResponse)
async def health_fragment(request: Request) -> HTMLResponse:
    """Partial kartu System Health — target polling HTMX (tiap 15 dtk)."""
    return templates.TemplateResponse(
        request, "partials/_health.html", {"health": await system_health()}
    )


@router.get("/fragments/alerts", response_class=HTMLResponse)
async def alerts_fragment(request: Request) -> HTMLResponse:
    """Partial panel alert (kanan) — target polling HTMX (tiap 20 dtk)."""
    return templates.TemplateResponse(
        request, "partials/_alerts.html", {"alerts": await system_alerts()}
    )
