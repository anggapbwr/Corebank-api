"""Endpoint auth: login, refresh, register (admin-only), me."""

import logging
from contextlib import asynccontextmanager, suppress
from uuid import UUID

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select

from app.core.config import get_settings
from app.core.deps import CurrentUser, DbSession, require_roles
from app.core.security import (
    TokenError,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.models.user import User, UserRole
from app.schemas.auth import LoginRequest, RefreshRequest, RegisterRequest, TokenResponse, UserOut

router = APIRouter(prefix="/auth", tags=["auth"])

# Hash palsu untuk user yang tidak ada — samakan waktu respons (cegah user enumeration)
_DUMMY_HASH = hash_password("timing-equalizer-not-a-real-password")

# NFR-02: rate limit login 5 percobaan/menit — hanya percobaan GAGAL yang dihitung,
# dihitung per-IP DAN per-username (lindungi 1 akun dari banyak IP & 1 IP dari ramai)
LOGIN_MAX_ATTEMPTS = 5
LOGIN_WINDOW_SECONDS = 60
_auth_logger = logging.getLogger("corebank.auth")


@asynccontextmanager
async def _rate_store():
    """Client Redis SEKALI PAKAI (bukan singleton get_redis()).

    Alasan: connection pool singleton membawa koneksi yang menempel di event loop
    pemakai sebelumnya — di TestClient (loop per request) itu melempar
    RuntimeError "Event loop is closed" pada operasi baca. Login tidak hot-path;
    biaya ~1ms per percobaan lebih murah daripada rate limit yang diam-diam
    melepas (fail-open) saat justru dibutuhkan.
    """
    client = aioredis.from_url(
        get_settings().redis_url,
        decode_responses=True,
        socket_connect_timeout=3,
        socket_timeout=3,
    )
    try:
        yield client
    finally:
        with suppress(Exception):
            await client.aclose()


def _rate_keys(ip: str, username: str) -> tuple[str, str]:
    return (f"login:fail:ip:{ip}", f"login:fail:user:{username.lower()}")


async def _rate_limited(ip: str, username: str) -> bool:
    """True bila sudah >=5 gagal dalam jendela 60 dtk (per IP atau per username).

    Redis tak terjangkau -> lewati pembatasan (availability menang) + log warning
    - Alertmanager sudah punya alert RedisDown untuk kasus ini.
    """
    try:
        async with _rate_store() as redis:
            counts = await redis.mget(*_rate_keys(ip, username))
        return any(int(c or 0) >= LOGIN_MAX_ATTEMPTS for c in counts)
    except Exception as exc:  # noqa: BLE001
        _auth_logger.warning(
            "rate limit login tak terjangkau — pembatasan dilewati",
            extra={"error": type(exc).__name__, "detail": str(exc)},
        )
        return False


async def _register_failure(ip: str, username: str) -> None:
    try:
        async with _rate_store() as redis:
            for key in _rate_keys(ip, username):
                await redis.incr(key)
                # jendela tetap sejak gagal pertama (EXPIRE NX); TTL tak diperpanjang
                await redis.expire(key, LOGIN_WINDOW_SECONDS, nx=True)
    except Exception as exc:  # noqa: BLE001
        _auth_logger.warning(
            "gagal mencatat percobaan login (rate limit)",
            extra={"error": type(exc).__name__, "detail": str(exc)},
        )


async def _clear_failures(ip: str, username: str) -> None:
    try:
        async with _rate_store() as redis:
            await redis.delete(*_rate_keys(ip, username))
    except Exception as exc:  # noqa: BLE001
        _auth_logger.warning(
            "gagal mereset rate limit login",
            extra={"error": type(exc).__name__, "detail": str(exc)},
        )


def _issue_tokens(user: User) -> TokenResponse:
    settings = get_settings()
    return TokenResponse(
        access_token=create_access_token(user.id, user.role.value),
        refresh_token=create_refresh_token(user.id),
        expires_in=settings.access_token_expire_minutes * 60,
    )


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, request: Request, db: DbSession) -> TokenResponse:
    ip = request.client.host if request.client else "unknown"
    if await _rate_limited(ip, body.username):  # NFR-02 — cek sebelum verifikasi
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many failed login attempts — try again in a minute",
        )
    user = (
        await db.execute(select(User).where(User.username == body.username))
    ).scalar_one_or_none()
    if user is None:
        verify_password(_DUMMY_HASH, body.password)  # jalankan hash palsu → timing seragam
        await _register_failure(ip, body.username)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")
    if not verify_password(user.password_hash, body.password):
        await _register_failure(ip, body.username)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")
    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Account disabled")
    await _clear_failures(ip, body.username)  # login sukses → hitungannya reset
    return _issue_tokens(user)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(body: RefreshRequest, db: DbSession) -> TokenResponse:
    try:
        payload = decode_token(body.refresh_token, expected_type="refresh")
        user_id = UUID(payload["sub"])
    except (TokenError, ValueError) as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid refresh token") from exc
    user = await db.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User inactive or not found")
    return _issue_tokens(user)


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def register(
    body: RegisterRequest,
    db: DbSession,
    _admin: User = Depends(require_roles(UserRole.MANAGER)),
) -> User:
    """Buat user internal — hanya manager (separation of duties)."""
    exists = (
        await db.execute(select(User).where(User.username == body.username))
    ).scalar_one_or_none()
    if exists is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Username already exists")
    user = User(username=body.username, password_hash=hash_password(body.password), role=body.role)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser) -> User:
    return user
