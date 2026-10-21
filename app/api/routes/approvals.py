"""Endpoint approval: work queue per role + putus-kan (approve/reject/escalate)."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from app.core.deps import DbSession, require_roles
from app.models.approval import ApprovalRequest
from app.models.transaction import Transaction
from app.models.user import User, UserRole
from app.schemas.approval import ApprovalOut, DecisionIn, DecisionOut
from app.services.approval import ApprovalError, decide, list_queue

router = APIRouter(prefix="/approvals", tags=["approvals"])


async def _to_out(db, req: ApprovalRequest) -> ApprovalOut:  # noqa: ANN001
    tx = await db.get(Transaction, req.object_id)
    return ApprovalOut(
        id=req.id,
        object_type=req.object_type.value,
        object_id=req.object_id,
        amount=tx.amount if tx else None,
        status=req.status.value,
        current_role=req.current_role.value,
        escalation_level=req.escalation_level,
        requested_by=req.requested_by,
        matrix_snapshot=req.matrix_snapshot,
        created_at=req.created_at,
    )


@router.get("", response_model=list[ApprovalOut])
async def work_queue(
    db: DbSession,
    user: User = Depends(require_roles(UserRole.SUPERVISOR, UserRole.MANAGER)),
) -> list[ApprovalOut]:
    """Antrean kerja role yang sedang login (PENDING + ESCALATED)."""
    items = await list_queue(db, user.role)
    return [await _to_out(db, req) for req in items]


@router.get("/{request_id}", response_model=ApprovalOut)
async def get_request(
    request_id: UUID,
    db: DbSession,
    user: User = Depends(require_roles(UserRole.SUPERVISOR, UserRole.MANAGER, UserRole.AUDITOR)),
) -> ApprovalOut:
    req = await db.get(ApprovalRequest, request_id)
    if req is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Approval request tidak ditemukan")
    return await _to_out(db, req)


@router.post("/{request_id}/approve", response_model=DecisionOut)
async def approve(
    request_id: UUID,
    db: DbSession,
    body: DecisionIn | None = None,
    user: User = Depends(require_roles(UserRole.SUPERVISOR, UserRole.MANAGER)),
) -> tuple[dict, int]:
    try:
        result, _code = await decide(
            db,
            request_id=request_id,
            approver=user,
            approve=True,
            reason=body.reason if body else None,
        )
    except ApprovalError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc
    return result


@router.post("/{request_id}/reject", response_model=DecisionOut)
async def reject(
    request_id: UUID,
    body: DecisionIn,
    db: DbSession,
    user: User = Depends(require_roles(UserRole.SUPERVISOR, UserRole.MANAGER)),
) -> tuple[dict, int]:
    try:
        result, _code = await decide(
            db, request_id=request_id, approver=user, approve=False, reason=body.reason
        )
    except ApprovalError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc
    return result
