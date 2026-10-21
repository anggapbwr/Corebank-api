"""Approval service — work queue, putus-kan, eskalasi (state machine WORKFLOWS §3).

Keputusan yang diambil selalu menyisakan jejak: approval_requests (siapa,
kapan, alasan, matrix snapshot) + audit_logs (BR-08, BR-10).
"""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.account import Account, AccountStatus
from app.models.approval import (
    ApprovalObjectType,
    ApprovalRequest,
    ApprovalStatus,
)
from app.models.ledger import LedgerDirection
from app.models.transaction import Transaction, TransactionStatus
from app.models.user import User, UserRole
from app.services import audit
from app.services.ledger import InsufficientBalanceError, post_journal


class ApprovalError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


async def list_queue(db: AsyncSession, role: UserRole) -> list[ApprovalRequest]:
    """Antrean kerja satu role — PENDING maupun ESCALATED (sudah naik level)."""
    stmt = (
        select(ApprovalRequest)
        .where(
            ApprovalRequest.current_role == role,
            ApprovalRequest.status.in_([ApprovalStatus.PENDING, ApprovalStatus.ESCALATED]),
            ApprovalRequest.object_type == ApprovalObjectType.TRANSFER,
        )
        .order_by(ApprovalRequest.created_at)
    )
    return list((await db.execute(stmt)).scalars().all())


async def _load_open_request(db: AsyncSession, request_id: UUID, approver: User) -> ApprovalRequest:
    req = await db.get(ApprovalRequest, request_id)
    if req is None:
        raise ApprovalError(404, "Approval request tidak ditemukan")
    if req.status not in (ApprovalStatus.PENDING, ApprovalStatus.ESCALATED):
        raise ApprovalError(409, f"Sudah diputuskan ({req.status.value})")
    # Separation of duties: putuskan hanya antrean levelmu —
    # kecuali manager boleh override antrean supervisor
    if req.current_role != approver.role and not (
        approver.role == UserRole.MANAGER and req.current_role == UserRole.SUPERVISOR
    ):
        raise ApprovalError(403, f"Antrean ini milik role {req.current_role.value}")
    return req


async def decide(
    db: AsyncSession,
    *,
    request_id: UUID,
    approver: User,
    approve: bool,
    reason: str | None,
) -> tuple[dict, int]:
    req = await _load_open_request(db, request_id, approver)
    tx = await db.get(Transaction, req.object_id)
    if tx is None:
        raise ApprovalError(409, "Objek transaksi tidak ditemukan")

    if not approve:
        # --- REJECT (BR-08: alasan wajib) ---
        if not reason or not reason.strip():
            raise ApprovalError(422, "Reject wajib menyertakan alasan (BR-08)")
        now = datetime.now(UTC)
        req.status = ApprovalStatus.REJECTED
        req.decided_by = approver.id
        req.decided_at = now
        req.reason = reason
        tx.status = TransactionStatus.REJECTED
        tx.rejected_reason = reason
        await audit.record(
            db,
            actor_id=approver.id,
            action="APPROVAL_REJECTED",
            object_type="approval",
            object_id=req.id,
            payload={"transaction_id": str(tx.id), "amount": str(tx.amount), "reason": reason},
        )
        await db.commit()
        return {
            "approval_id": str(req.id),
            "status": req.status.value,
            "transaction_id": str(tx.id),
            "transaction_status": tx.status.value,
        }, 200

    # --- APPROVE: cek limit role (BR-07) ---
    settings = get_settings()
    if approver.role == UserRole.SUPERVISOR and tx.amount > settings.approval_supervisor_max:
        # Di atas limit supervisor → ESKALASI naik ke manager (WORKFLOWS §5)
        req.status = ApprovalStatus.ESCALATED
        req.current_role = UserRole.MANAGER
        req.escalation_level += 1
        await audit.record(
            db,
            actor_id=approver.id,
            action="APPROVAL_ESCALATED",
            object_type="approval",
            object_id=req.id,
            payload={
                "transaction_id": str(tx.id),
                "amount": str(tx.amount),
                "supervisor_max": str(settings.approval_supervisor_max),
                "escalation_level": req.escalation_level,
            },
        )
        await db.commit()
        return {
            "approval_id": str(req.id),
            "status": req.status.value,
            "transaction_id": str(tx.id),
            "transaction_status": tx.status.value,
            "detail": f"Di atas limit supervisor, naik ke manager (level {req.escalation_level})",
        }, 200

    # --- APPROVE: eksekusi atomik transfer ---
    source = await db.get(Account, tx.source_account_id)
    dest = await db.get(Account, tx.dest_account_id)
    if source is None or dest is None:
        raise ApprovalError(409, "Rekening terkait tidak ditemukan")
    if source.status != AccountStatus.ACTIVE or dest.status != AccountStatus.ACTIVE:
        raise ApprovalError(409, "Rekening tidak aktif saat eksekusi (BR-06)")

    try:
        await post_journal(
            db,
            transaction_id=tx.id,
            movements=[
                (source, LedgerDirection.DEBIT, tx.amount),
                (dest, LedgerDirection.CREDIT, tx.amount),
            ],
        )
    except InsufficientBalanceError as exc:
        # Dana berkurang di antara request & approval → biarkan tetap tertunda,
        # approver bisa putuskan ulang setelah dana cukup
        await db.rollback()
        raise ApprovalError(409, f"Eksekusi gagal — {exc}. Request tetap tertunda.") from exc

    now = datetime.now(UTC)
    tx.status = TransactionStatus.EXECUTED
    tx.executed_at = now
    tx.approved_by = approver.id
    req.status = ApprovalStatus.APPROVED
    req.decided_by = approver.id
    req.decided_at = now
    await audit.record(
        db,
        actor_id=approver.id,
        action="APPROVAL_EXECUTED",
        object_type="approval",
        object_id=req.id,
        payload={
            "transaction_id": str(tx.id),
            "amount": str(tx.amount),
            "approved_by_role": approver.role.value,
        },
    )
    await db.commit()
    return {
        "approval_id": str(req.id),
        "status": req.status.value,
        "transaction_id": str(tx.id),
        "transaction_status": tx.status.value,
    }, 200
