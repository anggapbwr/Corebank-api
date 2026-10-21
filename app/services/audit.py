"""Audit trail service — semua aksi penting menyisakan jejak.

Ditulis DALAM transaksi yang sama dengan aksinya (konsistensi kuat):
kalau aksi gagal & rollback, jejak palsu juga ikut hilang.
Trade-off sadar: audit async via MQ (tanpa gagalkan transaksi utama) menyusul
di fase observability.
"""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditLog


async def record(
    db: AsyncSession,
    *,
    actor_id: UUID | None,
    action: str,
    object_type: str,
    object_id: UUID | None = None,
    payload: dict | None = None,
) -> AuditLog:
    entry = AuditLog(
        actor_user_id=actor_id,
        action=action,
        object_type=object_type,
        object_id=object_id,
        payload=payload,
    )
    db.add(entry)
    return entry
