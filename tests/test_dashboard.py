"""Test Fase 6 — dashboard Jinja+HTMX, /api/system/health, proxy /api/system/alerts."""

import asyncio

import pytest
from fastapi.testclient import TestClient

import app.api.routes.system as system_mod
from app.main import app

client = TestClient(app)


def test_dashboard_render_tanpa_login() -> None:
    """GET / langsung dashboard (200, tanpa redirect login) + ada panel alert kanan."""
    resp = client.get("/")
    assert resp.status_code == 200
    assert "id=\"alerts-panel\"" in resp.text
    assert "hx-get=\"/fragments/alerts\"" in resp.text
    assert "hx-get=\"/fragments/health\"" in resp.text
    assert "PostgreSQL" in resp.text  # kartu System Health ikut ter-render


def test_static_htmx_tersedia() -> None:
    """htmx di-vendor di repo (tanpa CDN) → harus bisa diambil dari /static."""
    resp = client.get("/static/htmx.min.js")
    assert resp.status_code == 200
    assert "htmx" in resp.text.lower()


def test_system_health_json_dari_gauge() -> None:
    resp = client.get("/api/system/health")
    assert resp.status_code == 200
    data = resp.json()
    assert set(data) == {"db_up", "redis_up", "approval_queue"}
    assert data["db_up"] in (0, 1)
    assert data["redis_up"] in (0, 1)
    assert data["approval_queue"] >= 0


def test_fragments_html() -> None:
    """Partial polling HTMX → HTML siap tukar (bukan JSON, bukan error)."""
    for path in ("/fragments/health", "/fragments/alerts"):
        resp = client.get(path)
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/html")


def test_alerts_normalisasi_dan_urutan_severity(monkeypatch: pytest.MonkeyPatch) -> None:
    """Proxy menormalkan payload Alertmanager & urutkan critical dulu."""
    raw = [
        {
            "labels": {"alertname": "QueueBacklog", "severity": "warning"},
            "annotations": {"summary": "Antrean menumpuk"},
            "startsAt": "2026-10-21T10:00:00Z",
            "status": {"state": "active"},
        },
        {
            "labels": {"alertname": "DatabaseDown", "severity": "critical"},
            "annotations": {"summary": "PostgreSQL tidak terjangkau"},
            "startsAt": "2026-10-21T09:59:00Z",
            "status": {"state": "active"},
        },
    ]

    async def fake_query(_settings):  # noqa: ANN001, ANN202
        return raw

    monkeypatch.setattr(system_mod, "_query_alertmanager", fake_query)
    data = client.get("/api/system/alerts").json()
    assert data["alertmanager"] == "up"
    assert [a["name"] for a in data["alerts"]] == ["DatabaseDown", "QueueBacklog"]
    assert data["alerts"][0]["severity"] == "critical"
    assert data["alerts"][0]["summary"] == "PostgreSQL tidak terjangkau"


def test_alerts_alertmanager_down_graceful(monkeypatch: pytest.MonkeyPatch) -> None:
    """Alertmanager mati → dashboard tetap 200, panel kasih tahu kondisinya."""
    async def fake_query(_settings):  # noqa: ANN001, ANN202
        raise ConnectionError("koneksi ditolak")

    monkeypatch.setattr(system_mod, "_query_alertmanager", fake_query)
    resp = client.get("/api/system/alerts")
    assert resp.status_code == 200
    assert resp.json() == {"alerts": [], "alertmanager": "down", "error": "ConnectionError"}
    # fragment panel-nya juga menampilkan status down, bukan daftar kosong
    fragment = client.get("/fragments/alerts").text
    assert "tidak terjangkau" in fragment


def test_alerts_panel_isi_alert_firing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Panel kanan menampilkan alert yang sedang firing (bukan hanya angka)."""
    async def fake_query(_settings):  # noqa: ANN001, ANN202
        return [
            {
                "labels": {"alertname": "DatabaseDown", "severity": "critical"},
                "annotations": {"summary": "PostgreSQL tidak terjangkau"},
                "startsAt": "2026-10-21T09:59:00Z",
                "status": {"state": "active"},
            }
        ]

    monkeypatch.setattr(system_mod, "_query_alertmanager", fake_query)
    fragment = client.get("/fragments/alerts").text
    assert "DatabaseDown" in fragment
    assert "PostgreSQL tidak terjangkau" in fragment
    assert "class=\"count\"" in fragment


def test_health_fragment_kartu_up() -> None:
    """Fragment health memakai gauge yang sama dengan /metrics."""
    asyncio.run(system_mod.system_health())  # memastikan pemanggilan aman (tanpa DB)
    fragment = client.get("/fragments/health").text
    assert "Antrean Approval" in fragment
    assert "PostgreSQL" in fragment
