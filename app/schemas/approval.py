"""Skema approval queue & keputusan."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel


class ApprovalOut(BaseModel):
    id: UUID
    object_type: str
    object_id: UUID
    amount: Decimal | None = None  # dari transaksi terkait
    status: str
    current_role: str
    escalation_level: int
    requested_by: UUID
    matrix_snapshot: dict | None = None
    created_at: datetime


class DecisionIn(BaseModel):
    # alasan opsional di body utk approve; reject wajib (dicek di service, BR-08)
    reason: str | None = None


class DecisionOut(BaseModel):
    approval_id: UUID
    status: str
    transaction_id: UUID
    transaction_status: str
    detail: str | None = None
