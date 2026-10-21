"""Unit test security — tanpa database."""

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
