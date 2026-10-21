"""Idempotency service — BR-03, dua lapis pengaman.

Lapis 1: Redis SET NX (cepat, TTL otomatis) — kalau Redis mati, lapis ini
dilewati TANPA menggagalkan request (lihat _try_redis).
Lapis 2: tabel idempotency_keys (unique) — sumber kebenaran final.

Semantik: key yang sama + payload sama → jawaban IDENTIK, tanpa transaksi baru.
         key yang sama + payload BEDA → 409 (client salah pakai key).
"""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

import redis.asyncio as aioredis
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.idempotency import IdempotencyKey


class IdempotencyConflict(Exception):
    """Key dipakai untuk payload berbeda."""


class DuplicateInFlight(Exception):
    """Key sama sedang diproses request lain (atau sudah selesai tanpa snapshot)."""


def request_hash(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


async def _try_redis_reserve(redis: aioredis.Redis, key: str) -> bool | None:
    """True = reservasi sukses; False = key sudah ada; None = Redis tidak bisa dihubungi."""
    try:
        settings = get_settings()
        result = await redis.set(key, "1", nx=True, ex=settings.idempotency_ttl_seconds)
        return bool(result)
    except Exception:  # noqa: BLE001 — Redis down ≠ transfer gagal; lanjut ke lapis DB
        return None


async def find_existing(db: AsyncSession, key: str) -> IdempotencyKey | None:
    return (
        await db.execute(select(IdempotencyKey).where(IdempotencyKey.key == key))
    ).scalar_one_or_none()


async def begin(
    db: AsyncSession,
    redis: aioredis.Redis,
    *,
    key: str,
    user_id: UUID,
    payload_hash: str,
) -> tuple[IdempotencyKey | None, IdempotencyKey | None]:
    """Mulai proses idempoten.

    Returns (existing, fresh_row):
    - existing tidak None  → request SUDAH pernah diproses (atau sedang); caller
      kembalikan respons lama / 409. TIDAK boleh membuat transaksi baru.
    - fresh_row tidak None → silakan proses; set response_snapshot lalu commit.
    """
    existing = await find_existing(db, key)
    if existing is not None:
        if existing.request_hash != payload_hash:
            raise IdempotencyConflict("Idempotency-Key dipakai untuk payload berbeda")
        return existing, None

    reserved = await _try_redis_reserve(redis, key)
    if reserved is False:
        # Redis bilang key ada tapi tabel belum (race sangat sempit / Redis kotor)
        existing = await find_existing(db, key)
        if existing is not None and existing.request_hash != payload_hash:
            raise IdempotencyConflict("Idempotency-Key dipakai untuk payload berbeda")
        if existing is not None:
            return existing, None
        raise DuplicateInFlight("Request dengan key ini sedang diproses")

    settings = get_settings()
    row = IdempotencyKey(
        key=key,
        user_id=user_id,
        request_hash=payload_hash,
        expires_at=datetime.now(UTC) + timedelta(seconds=settings.idempotency_ttl_seconds),
    )
    db.add(row)
    try:
        await db.flush()
    except IntegrityError as exc:
        # Lapis 2 menangkap race: dua request bersamaan lolos check pertama
        await db.rollback()
        existing = await find_existing(db, key)
        if existing is not None:
            if existing.request_hash != payload_hash:
                raise IdempotencyConflict("Idempotency-Key dipakai untuk payload berbeda") from exc
            return existing, None
        raise DuplicateInFlight("Request dengan key ini sedang diproses") from exc
    return None, row


async def complete(db: AsyncSession, row: IdempotencyKey, response: dict) -> None:
    """Simpan respons final — inilah yang dikembalikan pada retry berikutnya."""
    row.response_snapshot = response
