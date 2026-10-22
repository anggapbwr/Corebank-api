"""Unit test security + rate limit login NFR-02 (butuh DB & Redis — disiapkan conftest)."""

from datetime import timedelta
from uuid import uuid4

import jwt as pyjwt
import pytest

from app.core.config import get_settings
from app.core.security import (
    TokenError,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)


def test_password_hash_verify_sanity() -> None:
    h = hash_password("rahasia-kuat-123")
    assert h != "rahasia-kuat-123"  # tidak pernah tersimpan plain
    assert verify_password(h, "rahasia-kuat-123")
    assert not verify_password(h, "salah")


def test_access_token_roundtrip() -> None:
    uid = uuid4()
    token = create_access_token(uid, "teller")
    payload = decode_token(token, expected_type="access")
    assert payload["sub"] == str(uid)
    assert payload["type"] == "access"


def test_refresh_token_ditolak_sebagai_access() -> None:
    """Token refresh tidak boleh dipakai akses API (type claim dicek)."""
    token = create_refresh_token(uuid4())
    with pytest.raises(TokenError, match="expected access"):
        decode_token(token, expected_type="access")


def test_token_tamu_ditolak() -> None:
    with pytest.raises(TokenError):
        decode_token("bukan-token-sama-sekali", expected_type="access")


def test_token_signature_tidak_valid_ditolak() -> None:
    """Token yang dipalsui dengan secret lain harus ditolak (integritas signature)."""
    settings = get_settings()
    forged = pyjwt.encode(
        {"sub": str(uuid4()), "type": "access", "exp": 9999999999},
        "secret-lain",
        algorithm=settings.jwt_algorithm,
    )
    with pytest.raises(TokenError):
        decode_token(forged, expected_type="access")


def test_token_kedaluwarsa_ditolak() -> None:
    settings = get_settings()
    from datetime import UTC, datetime

    expired = pyjwt.encode(
        {
            "sub": str(uuid4()),
            "type": "access",
            "iat": datetime.now(UTC) - timedelta(hours=2),
            "exp": datetime.now(UTC) - timedelta(hours=1),
        },
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )
    with pytest.raises(TokenError):
        decode_token(expired, expected_type="access")


def test_rate_limit_login_nfr02() -> None:
    """NFR-02: 5 gagal/menit → percobaan ke-6 kena 429; setelah jendela bersih → normal lagi."""
    import asyncio

    import redis.asyncio as aioredis
    from fastapi.testclient import TestClient

    from app.api.routes.auth import _rate_keys
    from app.core.config import get_settings
    from app.main import app

    client = TestClient(app)
    username = "rate-limit-uji-tidak-ada"
    payload = {"username": username, "password": "salah-banget"}  # ≥6 char: bukan 422
    keys = _rate_keys("testclient", username)  # IP TestClient = "testclient"

    async def _clean() -> None:
        # client redis SEKALI PAKAI — aman terhadap ganti event loop antar panggilan
        c = aioredis.from_url(
            get_settings().redis_url,
            decode_responses=True,
            socket_connect_timeout=3,
            socket_timeout=3,
        )
        await c.delete(*keys)
        await c.aclose()

    asyncio.run(_clean())
    try:
        for i in range(5):
            r = client.post("/auth/login", json=payload)
            assert r.status_code == 401, f"percobaan {i + 1}: {r.status_code}"
        r = client.post("/auth/login", json=payload)
        assert r.status_code == 429, f"percobaan ke-6 harus diblokir, dapat {r.status_code}"

        # jendela di-reset (simulasi lewatnya 60 dtk) → login berfungsi normal lagi
        asyncio.run(_clean())
        r = client.post("/auth/login", json=payload)
        assert r.status_code == 401, "setelah reset buka lagi jadi 401 (bukan 429)"
    finally:
        asyncio.run(_clean())
