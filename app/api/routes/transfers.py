"""Endpoint transfer — POST dengan header Idempotency-Key WAJIB (FR-TRF-1)."""

import json
from typing import Annotated
from uuid import UUID

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, Header, HTTPException, Response, status

from app.core.deps import DbSession, require_roles
from app.core.redis import get_redis
from app.models.transaction import Transaction
from app.models.user import User, UserRole
from app.schemas.transfer import TransferCreate, TransferOut
from app.services.transfer import TransferError, initiate_transfer

router = APIRouter(prefix="/transfers", tags=["transfers"])

# BR-09: auditor & ops_risk tidak punya akses tulis ke transfer
_TRANSFER_ROLES = (
    UserRole.NASABAH,
    UserRole.TELLER,
    UserRole.CS,
    UserRole.SUPERVISOR,
    UserRole.MANAGER,
)

# Role yang boleh lihat transfer siapa pun (audit/monitoring)
_READ_ROLES = (UserRole.SUPERVISOR, UserRole.MANAGER, UserRole.OPS_RISK, UserRole.AUDITOR)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_transfer(
    body: TransferCreate,
    db: DbSession,
    redis: Annotated[aioredis.Redis, Depends(get_redis)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=64)],
    user: User = Depends(require_roles(*_TRANSFER_ROLES)),
) -> Response:
    try:
        response, code = await initiate_transfer(
            db,
            redis,
            source_account_id=body.source_account_id,
            dest_account_id=body.dest_account_id,
            amount=body.amount,
            user=user,
            idempotency_key=idempotency_key,
        )
    except TransferError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc
    # 201 executed | 202 pending approval | 200 duplicate (retry dengan key sama)
    return Response(
        content=json.dumps(response, default=str), status_code=code, media_type="application/json"
    )


@router.get("/{transfer_id}", response_model=TransferOut)
async def get_transfer(
    transfer_id: UUID,
    db: DbSession,
    user: User = Depends(require_roles(*_TRANSFER_ROLES, *_READ_ROLES)),
) -> Transaction:
    tx = await db.get(Transaction, transfer_id)
    if tx is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Transfer tidak ditemukan")
    # BOLA: nasabah hanya boleh lihat transfer yang dia buat sendiri
    if user.role == UserRole.NASABAH and tx.created_by != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Transfer tidak ditemukan")
    if user.role not in _TRANSFER_ROLES and user.role not in _READ_ROLES:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Read not allowed")
    return tx
