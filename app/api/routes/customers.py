"""Endpoint nasabah (customer) — identitas bisnis terpisah dari akun login."""

import hashlib
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from app.core.deps import CurrentUser, DbSession, require_roles
from app.models.customer import Customer
from app.models.user import User, UserRole
from app.schemas.customer import CustomerCreate, CustomerOut

router = APIRouter(prefix="/customers", tags=["customers"])

_STAFF_ROLES = (UserRole.TELLER, UserRole.CS, UserRole.MANAGER, UserRole.OPS_RISK)


@router.post("", response_model=CustomerOut, status_code=status.HTTP_201_CREATED)
async def create_customer(
    body: CustomerCreate,
    db: DbSession,
    _staff: User = Depends(require_roles(UserRole.TELLER, UserRole.CS, UserRole.MANAGER)),
) -> Customer:
    customer = Customer(
        full_name=body.full_name,
        # KTP tidak pernah disimpan mentah — hanya SHA-256 (NFR-08)
        ktp_hash=hashlib.sha256(body.ktp_number.encode()).hexdigest(),
    )
    db.add(customer)
    await db.commit()
    await db.refresh(customer)
    return customer


@router.get("/{customer_id}", response_model=CustomerOut)
async def get_customer(customer_id: UUID, db: DbSession, user: CurrentUser) -> Customer:
    customer = await db.get(Customer, customer_id)
    if customer is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Customer not found")
    # BOLA protection (NFR-03): nasabah hanya boleh lihat datanya sendiri
    if user.role == UserRole.NASABAH and customer.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Customer not found")
    if user.role not in _STAFF_ROLES and user.role != UserRole.NASABAH:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Read not allowed for this role")
    return customer
