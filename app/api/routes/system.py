"""Endpoint sistem untuk dashboard (Fase 6).

/api/system/health  — gauge monitor (observability) → JSON, sumber "System Health" di UI
/api/system/alerts  — proxy Alertmanager (/api/v2/alerts): UI tidak memanggil
                      Alertmanager langsung (auth/CORS), dan Alertmanager yang mati
                      tidak boleh membuat dashboard error (fallback graceful).
"""

import logging

import httpx
from fastapi import APIRouter
from prometheus_client import Gauge

from app.core.config import get_settings
from app.core.observability import APPROVAL_QUEUE_SIZE, DB_UP, REDIS_UP

router = APIRouter(prefix="/api/system", tags=["system"])
logger = logging.getLogger("corebank.system")

_SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}


def _gauge(gauge: Gauge) -> int:
    """Nilai gauge saat ini — prometheus_client tidak punya getter publik."""
    return int(gauge._value.get())  # noqa: SLF001


@router.get("/health")
async def system_health() -> dict:
    """Kesehatan dependency dari gauge yang di-update monitor_loop (tiap 15 dtk)."""
    return {
        "db_up": _gauge(DB_UP),
        "redis_up": _gauge(REDIS_UP),
        "approval_queue": _gauge(APPROVAL_QUEUE_SIZE),
    }


async def _query_alertmanager(settings) -> list[dict]:  # noqa: ANN001
    """Ambil alert aktif mentah dari Alertmanager v2 API."""
    url = settings.alertmanager_url.rstrip("/") + "/api/v2/alerts"
    async with httpx.AsyncClient(timeout=3.0) as client:
        resp = await client.get(
            url, params={"active": "true", "silenced": "false", "inhibited": "false"}
        )
        resp.raise_for_status()
        data = resp.json()
    return data if isinstance(data, list) else []


def _normalize(raw: list[dict]) -> list[dict]:
    """Alertmanager API → bentuk ringkas untuk UI, diurutkan severity dulu."""
    alerts = []
    for item in raw:
        labels = item.get("labels") or {}
        annotations = item.get("annotations") or {}
        severity = labels.get("severity", "info").lower()
        alerts.append(
            {
                "name": labels.get("alertname", "Unknown"),
                "severity": severity if severity in _SEVERITY_ORDER else "info",
                "summary": annotations.get("summary")
                or annotations.get("description")
                or labels.get("alertname", ""),
                "starts_at": item.get("startsAt"),
                "state": (item.get("status") or {}).get("state", "active"),
            }
        )
    alerts.sort(key=lambda a: _SEVERITY_ORDER[a["severity"]])
    return alerts


@router.get("/alerts")
async def system_alerts() -> dict:
    """Proxy Alertmanager. Down/tidak terjangkau → daftar kosong + penanda 'down'."""
    settings = get_settings()
    try:
        raw = await _query_alertmanager(settings)
    except Exception as exc:  # noqa: BLE001 — dashboard tidak boleh gagal karena alert
        logger.warning("alertmanager tidak terjangkau", extra={"error": type(exc).__name__})
        return {"alerts": [], "alertmanager": "down", "error": type(exc).__name__}
    return {"alerts": _normalize(raw), "alertmanager": "up"}
