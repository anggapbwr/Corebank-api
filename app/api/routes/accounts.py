"""Endpoint rekening: buka → approve → freeze/unfreeze; query + ledger entries.

Alur sesuai WORKFLOWS.md: teller input (PENDING_APPROVAL) → supervisor/manager
approve → ACTIVE. Status change selalu dijaga guard (tidak bisa sembarang lompat).
"""

import secrets
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.deps import CurrentUser, DbSession, require_roles
from app.models.account import Account, AccountStatus
from app.models.customer import Customer
from app.models.ledger import LedgerEntry
from app.models.user import User, UserRole
from app.schemas.account import AccountCreate, AccountOut, LedgerEntryOut

router = APIRouter(prefix="/accounts", tags=["accounts"])


def _gen_account_number() -> str:
    """Nomor rekening 12 digit (prefix 3 = tabungan). Production: nomor resmi dari BUKU."""
    return "3" + "".join(secrets.choice("0123456789") for _ in range(11))


async def _load_account(db: DbSession, account_id: UUID) -> Account:
    account = (
        await db.execute(
            select(Account).options(selectinload(Account.customer)).where(Account.id == account_id)
        )
    ).scalar_one_or_none()
    if account is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Account not found")
    return account


@router.post("", response_model=AccountOut, status_code=status.HTTP_201_CREATED)
async def create_account(
    body: AccountCreate,
    db: DbSession,
    _staff: User = Depends(require_roles(UserRole.TELLER, UserRole.CS)),
) -> Account:
    customer = await db.get(Customer, body.customer_id)
    if customer is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Customer not found")
    account = Account(
        customer_id=customer.id,
        account_number=_gen_account_number(),
        type=body.type,
        # status default PENDING_APPROVAL — menunggu supervisor (WORKFLOWS §5)
    )
    db.add(account)
    await db.commit()
    await db.refresh(account)
    return account


@router.post("/{account_id}/approve", response_model=AccountOut)
async def approve_account(
    account_id: UUID,
    db: DbSession,
    _approver: User = Depends(require_roles(UserRole.SUPERVISOR, UserRole.MANAGER)),
) -> Account:
    account = await _load_account(db, account_id)
    if account.status != AccountStatus.PENDING_APPROVAL:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Only PENDING_APPROVAL accounts can be approved (current: {account.status.value})",
        )
    account.status = AccountStatus.ACTIVE
    await db.commit()
    await db.refresh(account)
    return account


@router.post("/{account_id}/freeze", response_model=AccountOut)
async def freeze_account(
    account_id: UUID,
    db: DbSession,
    _staff: User = Depends(require_roles(UserRole.MANAGER, UserRole.OPS_RISK)),
) -> Account:
    account = await _load_account(db, account_id)
    if account.status != AccountStatus.ACTIVE:
        raise HTTPException(status.HTTP_409_CONFLICT, "Only ACTIVE accounts can be frozen")
    account.status = AccountStatus.FROZEN
    await db.commit()
    await db.refresh(account)
    return account


@router.post("/{account_id}/unfreeze", response_model=AccountOut)
async def unfreeze_account(
    account_id: UUID,
    db: DbSession,
    _staff: User = Depends(require_roles(UserRole.MANAGER, UserRole.OPS_RISK)),
) -> Account:
    account = await _load_account(db, account_id)
    if account.status != AccountStatus.FROZEN:
        raise HTTPException(status.HTTP_409_CONFLICT, "Only FROZEN accounts can be unfrozen")
    account.status = AccountStatus.ACTIVE
    await db.commit()
    await db.refresh(account)
    return account


@router.get("/{account_id}", response_model=AccountOut)
async def get_account(account_id: UUID, db: DbSession, user: CurrentUser) -> Account:
    account = await _load_account(db, account_id)
    # BOLA protection (NFR-03): nasabah hanya boleh lihat rekening miliknya sendiri
    if user.role == UserRole.NASABAH and account.customer.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Account not found")
    return account


@router.get("/{account_id}/entries", response_model=list[LedgerEntryOut])
async def list_ledger_entries(
    account_id: UUID,
    db: DbSession,
    _auditorish: User = Depends(
        require_roles(UserRole.AUDITOR, UserRole.MANAGER, UserRole.OPS_RISK, UserRole.SUPERVISOR)
    ),
) -> list[LedgerEntry]:
    await _load_account(db, account_id)  # 404 kalau rekening tidak ada
    entries = (
        (
            await db.execute(
                select(LedgerEntry)
                .where(LedgerEntry.account_id == account_id)
                .order_by(LedgerEntry.created_at)
            )
        )
        .scalars()
        .all()
    )
    return list(entries)
