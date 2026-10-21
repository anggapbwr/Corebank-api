"""Skema audit log."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class AuditOut(BaseModel):
    id: UUID
    actor_user_id: UUID | None
    action: str
    object_type: str
    object_id: UUID | None
    payload: dict | None
    created_at: datetime

    model_config = {"from_attributes": True}
