"""Test Fase 5 — metrics endpoint, correlation ID, monitor dependency, log JSON."""

import asyncio
import json
import logging
import re

from app.core.observability import JsonFormatter, check_dependencies, correlation_id_var


def test_metrics_exposed() -> None:
    """GET /metrics menyediakan metrik HTTP + gauge dependency."""
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    client.get("/healthz")  # satu request dulu → ada isi di http_requests_total
    resp = client.get("/metrics")
    assert resp.status_code == 200
    body = resp.text
    assert "http_requests_total" in body
    assert "corebank_db_up" in body
    assert "corebank_approval_queue_size" in body
    # kartinalitas terkendali: label route pakai template, bukan URL mentah
    assert re.search(r'route="/healthz"', body)


def test_correlation_id_direspons_dan_dibuat_otomatis() -> None:
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)

    # klien mengirim sendiri → diteruskan balik (telusuri lintas service)
    r1 = client.get("/healthz", headers={"X-Correlation-ID": "corr-test-123"})
    assert r1.headers["X-Correlation-ID"] == "corr-test-123"

    # tanpa header → server membuat UUID sendiri
    r2 = client.get("/healthz")
    generated = r2.headers["X-Correlation-ID"]
    assert generated and len(generated) == 36


def test_check_dependencies_update_gauge() -> None:
    """Sekali jalan check_dependencies → gauge DB & Redis ter-update."""

    async def _run() -> None:
        await check_dependencies()

    asyncio.run(_run())

    from fastapi.testclient import TestClient

    from app.main import app

    body = TestClient(app).get("/metrics").text
    # compose dev punya DB+Redis hidup → kedua gauge harus 1
    assert re.search(r"^corebank_db_up 1\.0", body, re.M)
    assert re.search(r"^corebank_redis_up 1\.0", body, re.M)


def test_log_json_membawa_correlation_id() -> None:
    formatter = JsonFormatter()
    record = logging.LogRecord("corebank.test", logging.INFO, __file__, 1, "contoh pesan", (), None)
    token = correlation_id_var.set("corr-json-1")
    try:
        output = formatter.format(record)
    finally:
        correlation_id_var.reset(token)

    payload = json.loads(output)
    assert payload["msg"] == "contoh pesan"
    assert payload["level"] == "INFO"
    assert payload["correlation_id"] == "corr-json-1"
    assert "ts" in payload
