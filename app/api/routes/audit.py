"""Endpoint audit log — read-only untuk auditor/manager/ops_risk (BR-09)."""

from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select

from app.core.deps import DbSession, require_roles
from app.models.audit import AuditLog
from app.models.user import User, UserRole
from app.schemas.audit import AuditOut

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("/logs", response_model=list[AuditOut])
async def list_audit_logs(
    db: DbSession,
    object_type: str | None = Query(default=None),
    object_id: UUID | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    _reader: User = Depends(require_roles(UserRole.AUDITOR, UserRole.MANAGER, UserRole.OPS_RISK)),
) -> list[AuditLog]:
    """Jejak aksi — terbaru dulu, filter per objek (navigasi audit trail)."""
    stmt = select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit)
    if object_type:
        stmt = stmt.where(AuditLog.object_type == object_type)
    if object_id:
        stmt = stmt.where(AuditLog.object_id == object_id)
    return list((await db.execute(stmt)).scalars().all())
