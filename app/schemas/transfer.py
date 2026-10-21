"""Skema transfer."""

from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field


class TransferCreate(BaseModel):
    source_account_id: UUID
    dest_account_id: UUID
    amount: Decimal = Field(gt=0, max_digits=19, decimal_places=2)


class TransferOut(BaseModel):
    transaction_id: UUID
    status: str
    amount: Decimal
    duplicate: bool = False
    approval_id: UUID | None = None
