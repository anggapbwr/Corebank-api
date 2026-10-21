"""Password hashing (argon2) & JWT access/refresh.

Keputusan (lihat docs/ARCHITECTURE.md):
- argon2, bukan bcrypt — pemenang Password Hashing Competition, default modern
- token tipis: hanya sub (user id) + role + type; data lain di-lookup ke DB
  supaya perubahan role/is_active langsung berlaku (token tidak jadi sumber kebenaran)
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, VerifyMismatchError

from app.core.config import get_settings

_hasher = PasswordHasher()


class TokenError(Exception):
    """Token tidak valid, kedaluwarsa, atau tipe salah."""


def hash_password(plain: str) -> str:
    return _hasher.hash(plain)


def verify_password(password_hash: str, plain: str) -> bool:
    try:
        return _hasher.verify(password_hash, plain)
    except (VerifyMismatchError, VerificationError):
        return False


def _create_token(sub: UUID, token_type: str, expires_delta: timedelta) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    payload = {
        "sub": str(sub),
        "type": token_type,  # "access" | "refresh" — cegah token refresh dipakai sebagai access
        "iat": now,
        "exp": now + expires_delta,
        "jti": str(uuid4()),  # jejak audit per token
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_access_token(user_id: UUID, role: str) -> str:  # noqa: ARG001 — role di-lookup ulang di DB
    settings = get_settings()
    return _create_token(user_id, "access", timedelta(minutes=settings.access_token_expire_minutes))


def create_refresh_token(user_id: UUID) -> str:
    settings = get_settings()
    return _create_token(user_id, "refresh", timedelta(days=settings.refresh_token_expire_days))


def decode_token(token: str, expected_type: str) -> dict:
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError as exc:
        raise TokenError(str(exc)) from exc
    if payload.get("type") != expected_type:
        raise TokenError(f"expected {expected_type} token")
    return payload
