"""Skema rekening & ledger."""

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel

from app.models.account import AccountType


class AccountCreate(BaseModel):
    customer_id: uuid.UUID
    type: AccountType = AccountType.SAVINGS


class AccountOut(BaseModel):
    id: uuid.UUID
    account_number: str
    customer_id: uuid.UUID
    type: AccountType
    balance: Decimal
    status: str

    model_config = {"from_attributes": True}


class LedgerEntryOut(BaseModel):
    id: uuid.UUID
    transaction_id: uuid.UUID
    direction: str
    amount: Decimal
    balance_after: Decimal
    created_at: datetime

    model_config = {"from_attributes": True}
