"""Health endpoints.

/healthz — liveness : proses hidup? (tidak cek dependency — kalau mati, restart pod)
/readyz  — readiness : siap terima trafik? (cek PostgreSQL + Redis — kalau belum,
           K8s tahan routing request dulu; membedakan ini adalah salah satu konsep
           probe k8s yang sering salah paham)
"""

import asyncio

from fastapi import APIRouter, Response

from app.core.config import get_settings

router = APIRouter(tags=["health"])


async def check_database(dsn: str) -> str:
    try:
        import psycopg

        conn = await asyncio.wait_for(
            psycopg.AsyncConnection.connect(dsn, connect_timeout=3), timeout=4
        )
    except Exception as exc:  # noqa: BLE001 — readiness melaporkan apa adanya
        return f"error: {type(exc).__name__}"
    try:
        await conn.execute("SELECT 1")
    finally:
        await conn.close()
    return "ok"


async def check_redis(url: str) -> str:
    try:
        import redis.asyncio as aioredis

        client = aioredis.from_url(url, socket_connect_timeout=3, socket_timeout=3)
        await asyncio.wait_for(client.ping(), timeout=4)
        await client.aclose()
    except Exception as exc:  # noqa: BLE001
        return f"error: {type(exc).__name__}"
    return "ok"


@router.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(response: Response) -> dict:
    settings = get_settings()
    db_status, redis_status = await asyncio.gather(
        check_database(settings.psycopg_dsn),
        check_redis(settings.redis_url),
    )
    ready = db_status == "ok" and redis_status == "ok"
    if not ready:
        response.status_code = 503
    return {
        "status": "ready" if ready else "not_ready",
        "checks": {"database": db_status, "redis": redis_status},
    }
