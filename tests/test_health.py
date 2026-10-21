from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_healthz_returns_200() -> None:
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_openapi_available() -> None:
    assert client.get("/openapi.json").status_code == 200


def test_root_docs_redirect() -> None:
    assert client.get("/docs").status_code == 200
