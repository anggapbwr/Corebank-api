"""Transfer service — orkestrasi lengkap satu transfer.

Alur (ARCHITECTURE.md §2):
  idempotency → validasi rekening → keputusan: eksekusi atomik ATAU antre approval

Aturan yang dijaga:
- BR-01 saldo cukup (di post_journal)
- BR-02 atomik: gagal = rollback total, tidak ada jejak ledger
- BR-03 idempotency dua lapis (Redis + UNIQUE column)
- BR-06 rekening FROZEN tidak bisa terima/kirim dana
- BR-07 approval matrix dari config (≤50jt langsung; ≤250jt supervisor; >250jt manager)
"""

import logging
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.observability import TRANSFER_EXECUTED, TRANSFER_FAILED
from app.models.account import Account, AccountStatus
from app.models.approval import ApprovalObjectType, ApprovalRequest, ApprovalStatus
from app.models.ledger import LedgerDirection
from app.models.transaction import Transaction, TransactionStatus, TransactionType
from app.models.user import User, UserRole
from app.services import audit, idempotency
from app.services.ledger import InsufficientBalanceError, post_journal


class TransferError(Exception):
    """Error domain transfer — diterjemahkan route ke HTTP."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def matrix_snapshot() -> dict:
    """BR-10 — salin konfigurasi limit yang berlaku SEKARANG ke request approval."""
    settings = get_settings()
    return {
        "direct_execute_max": str(settings.approval_direct_execute_max),
        "supervisor_max": str(settings.approval_supervisor_max),
    }


async def _release(db: AsyncSession, redis: aioredis.Redis, key: str) -> None:
    """Batalkan proses: rollback DB + lepas reservasi Redis
    supaya client bisa retry dengan key yang sama."""
    await db.rollback()
    try:
        await redis.delete(key)
    except Exception:  # noqa: BLE001 — Redis boleh sedang mati; DB sudah rollback
        pass


async def initiate_transfer(
    db: AsyncSession,
    redis: aioredis.Redis,
    *,
    source_account_id: UUID,
    dest_account_id: UUID,
    amount: Decimal,
    user: User,
    idempotency_key: str,
) -> tuple[dict, int]:
    """Returns (respons_JSON_safe, http_status).

    201 = dieksekusi, 202 = masuk antrean approval, 200 = duplicate (retry).
    """
    payload_hash = idempotency.request_hash(
        {
            "source": str(source_account_id),
            "dest": str(dest_account_id),
            "amount": str(amount),
            "user": str(user.id),
        }
    )

    # --- BR-03: lapis idempotency ---
    try:
        existing, fresh = await idempotency.begin(
            db, redis, key=idempotency_key, user_id=user.id, payload_hash=payload_hash
        )
    except (idempotency.IdempotencyConflict, idempotency.DuplicateInFlight) as exc:
        await _release(db, redis, idempotency_key)
        raise TransferError(409, str(exc)) from exc

    if existing is not None:
        if existing.response_snapshot is None:
            raise TransferError(409, "Request dengan key ini masih diproses — coba lagi")
        return {**existing.response_snapshot, "duplicate": True}, 200

    try:
        # --- validasi rekening (BOLA & BR-06) ---
        source = await db.get(Account, source_account_id)
        dest = await db.get(Account, dest_account_id)
        if source is None or dest is None:
            raise TransferError(404, "Rekening sumber/tujuan tidak ditemukan")
        if source.id == dest.id:
            raise TransferError(422, "Rekening sumber dan tujuan tidak boleh sama")
        if source.status != AccountStatus.ACTIVE or dest.status != AccountStatus.ACTIVE:
            raise TransferError(409, "Rekening tidak aktif (FROZEN/PENDING) — BR-06")

        settings = get_settings()
        now = datetime.now(UTC)

        if amount <= settings.approval_direct_execute_max:
            # ============ EKSEKUSI ATOMIK (BR-02) ============
            tx = Transaction(
                type=TransactionType.TRANSFER,
                source_account_id=source.id,
                dest_account_id=dest.id,
                amount=amount,
                status=TransactionStatus.EXECUTED,
                created_by=user.id,
                # lapis 2 BR-03: UNIQUE di kolom ini menangkap race kalau Redis mati
                idempotency_key=idempotency_key,
                executed_at=now,
            )
            db.add(tx)
            await db.flush()
            # 2 baris ledger seimbang dalam SATU transaksi DB — post_journal
            # melempar InsufficientBalanceError → seluruh tx di-rollback (BR-01/02)
            await post_journal(
                db,
                transaction_id=tx.id,
                movements=[
                    (source, LedgerDirection.DEBIT, amount),
                    (dest, LedgerDirection.CREDIT, amount),
                ],
            )
            response = {
                "transaction_id": str(tx.id),
                "status": TransactionStatus.EXECUTED.value,
                "amount": str(amount),
                "duplicate": False,
            }
            TRANSFER_EXECUTED.labels("direct").inc()
            logging.getLogger("corebank.transfer").info(
                "transfer dieksekusi",
                extra={"transaction_id": str(tx.id), "amount": str(amount)},
            )
            await audit.record(
                db,
                actor_id=user.id,
                action="TRANSFER_EXECUTED",
                object_type="transfer",
                object_id=tx.id,
                payload={"source": str(source.id), "dest": str(dest.id), "amount": str(amount)},
            )
            if fresh is not None:
                await idempotency.complete(db, fresh, response)
            await db.commit()
            return response, 201

        # ============ MASUK ANTREAN APPROVAL (BR-07) ============
        tx = Transaction(
            type=TransactionType.TRANSFER,
            source_account_id=source.id,
            dest_account_id=dest.id,
            amount=amount,
            status=TransactionStatus.PENDING_APPROVAL,
            created_by=user.id,
            idempotency_key=idempotency_key,
        )
        db.add(tx)
        await db.flush()
        approval = ApprovalRequest(
            object_type=ApprovalObjectType.TRANSFER,
            object_id=tx.id,
            current_role=UserRole.SUPERVISOR,
            status=ApprovalStatus.PENDING,
            escalation_level=1,
            requested_by=user.id,
            matrix_snapshot=matrix_snapshot(),
        )
        db.add(approval)
        await db.flush()
        response = {
            "transaction_id": str(tx.id),
            "approval_id": str(approval.id),
            "status": TransactionStatus.PENDING_APPROVAL.value,
            "amount": str(amount),
            "duplicate": False,
        }
        await audit.record(
            db,
            actor_id=user.id,
            action="TRANSFER_PENDING_APPROVAL",
            object_type="transfer",
            object_id=tx.id,
            payload={"approval_id": str(approval.id), "amount": str(amount)},
        )
        if fresh is not None:
            await idempotency.complete(db, fresh, response)
        await db.commit()
        return response, 202

    except InsufficientBalanceError as exc:
        # BR-01/02: tidak ada yang berubah — lepas key supaya retry tetap mungkin
        await _release(db, redis, idempotency_key)
        TRANSFER_FAILED.labels("insufficient_balance").inc()
        raise TransferError(422, str(exc)) from exc
    except TransferError as exc:
        await _release(db, redis, idempotency_key)
        TRANSFER_FAILED.labels(str(exc.status_code)).inc()
        raise
