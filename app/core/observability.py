"""Observability — logging JSON, correlation ID, metrics Prometheus, monitor dependency.

Tiga pilar Fase 5 (ROADMAP):
1. Structured logging: setiap baris log JSON + correlation_id (ContextVar — NFR-06),
   satu request bisa ditelusuri lintas middleware → service → log
2. Metrics: histogram latency, counter request & event bisnis (transfer, approval)
3. Monitor berjalan periodik: gauge kesehatan DB/Redis + kedalaman antrean approval
   → jadi bahan alert (Alertmanager → Telegram)
"""

import asyncio
import json
import logging
import time
import uuid
from contextvars import ContextVar
from typing import Any

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

correlation_id_var: ContextVar[str | None] = ContextVar("correlation_id", default=None)

# ---------------------------------------------------------------- metrics

REQUEST_COUNT = Counter(
    "http_requests_total",
    "Request HTTP per method/route/status",
    ["method", "route", "status"],
)
REQUEST_LATENCY = Histogram(
    "http_request_duration_seconds",
    "Latensi request per route",
    ["method", "route"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
)

DB_UP = Gauge("corebank_db_up", "1 = PostgreSQL terjangkau, 0 = tidak")
REDIS_UP = Gauge("corebank_redis_up", "1 = Redis terjangkau, 0 = tidak")
APPROVAL_QUEUE_SIZE = Gauge(
    "corebank_approval_queue_size",
    "Jumlah approval menunggu keputusan (PENDING + ESCALATED)",
)

TRANSFER_EXECUTED = Counter(
    "corebank_transfer_executed_total", "Transfer dieksekusi", ["channel"]
)  # channel: direct | approval
TRANSFER_FAILED = Counter("corebank_transfer_failed_total", "Transfer gagal", ["reason"])
APPROVAL_DECISIONS = Counter(
    "corebank_approval_decisions_total", "Keputusan approval", ["decision", "role"]
)  # decision: approved | rejected | escalated


def metrics_response() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


# ---------------------------------------------------------------- logging JSON


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        corr = getattr(record, "correlation_id", None) or correlation_id_var.get()
        if corr:
            payload["correlation_id"] = corr
        # field tambahan yang dilewatkan lewat extra={...}
        for key, value in record.__dict__.items():
            if (
                key not in _STANDARD_ATTRS
                and not key.startswith("_")
                and not isinstance(value, (logging.LogRecord,))
            ):
                payload.setdefault(key, value)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


_STANDARD_ATTRS = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {
    "message",
    "asctime",
    "correlation_id",
}


def setup_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
    # uvicorn menulis log sendiri — biarkan ikut root (format JSON seragam)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logging.getLogger(name).handlers.clear()
        logging.getLogger(name).propagate = True
    logging.getLogger("uvicorn.access").disabled = True  # akses sudah dicatat middleware


# ---------------------------------------------------------------- middleware


class ObservabilityMiddleware(BaseHTTPMiddleware):
    """Correlation ID (X-Correlation-ID) + metrik & log per request.

    Label 'route' memakai template path (/accounts/{account_id}) — bukan URL mentah —
    supaya jumlah time-series di Prometheus tetap kecil (kartinalitas terkendali).
    """

    async def dispatch(self, request: Request, call_next) -> Response:  # noqa: ANN001
        corr = request.headers.get("X-Correlation-ID") or str(uuid.uuid4())
        token = correlation_id_var.set(corr)
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            duration = time.perf_counter() - start
            route = _route_template(request)
            REQUEST_COUNT.labels(request.method, route, "500").inc()
            REQUEST_LATENCY.labels(request.method, route).observe(duration)
            logging.getLogger("corebank.request").exception(
                "request failed", extra={"route": route, "method": request.method}
            )
            raise
        finally:
            correlation_id_var.reset(token)

        duration = time.perf_counter() - start
        route = _route_template(request)
        status = str(response.status_code)
        REQUEST_COUNT.labels(request.method, route, status).inc()
        REQUEST_LATENCY.labels(request.method, route).observe(duration)
        logging.getLogger("corebank.request").info(
            "request",
            extra={
                "route": route,
                "method": request.method,
                "status": status,
                "duration_ms": round(duration * 1000, 2),
            },
        )
        response.headers["X-Correlation-ID"] = corr
        return response


def _route_template(request: Request) -> str:
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    return path or request.url.path


# ---------------------------------------------------------------- monitor periodik


async def check_dependencies() -> None:
    """Sekali cek: update gauge DB & Redis + kedalaman antrean approval."""
    import psycopg
    import redis.asyncio as aioredis

    from app.core.config import get_settings

    settings = get_settings()
    try:
        conn = await psycopg.AsyncConnection.connect(settings.psycopg_dsn, connect_timeout=3)
        await conn.close()
        DB_UP.set(1)
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("corebank.monitor").error(
            "PostgreSQL TIDAK terjangkau",
            extra={"error": type(exc).__name__, "detail": str(exc)},
        )
        DB_UP.set(0)

    try:
        client = aioredis.from_url(settings.redis_url, socket_connect_timeout=3, socket_timeout=3)
        await client.ping()
        await client.aclose()
        REDIS_UP.set(1)
    except Exception:  # noqa: BLE001
        logging.getLogger("corebank.monitor").error("Redis TIDAK terjangkau")
        REDIS_UP.set(0)

    try:
        from sqlalchemy import text

        from app.core.db import SessionLocal

        async with SessionLocal() as db:
            row = await db.execute(
                text(
                    "SELECT count(*) FROM approval_requests "
                    "WHERE status IN ('PENDING', 'ESCALATED')"
                )
            )
            APPROVAL_QUEUE_SIZE.set(int(row.scalar_one()))
    except Exception:  # noqa: BLE001
        logging.getLogger("corebank.monitor").warning("Gagal membaca antrean approval")


async def monitor_loop(interval: int = 15) -> None:
    """Background task: gauge kesehatan dependency — bahan alert & dashboard."""
    logger = logging.getLogger("corebank.monitor")
    logger.info("monitor dependency dimulai", extra={"interval_s": interval})
    while True:
        try:
            await check_dependencies()
        except asyncio.CancelledError:
            logger.info("monitor dihentikan")
            raise
        except Exception:  # noqa: BLE001 — monitor tidak boleh mati
            logger.exception("monitor loop error")
        await asyncio.sleep(interval)
