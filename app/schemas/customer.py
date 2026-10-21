"""Skema nasabah."""

import uuid

from pydantic import BaseModel, Field


class CustomerCreate(BaseModel):
    full_name: str = Field(min_length=2, max_length=120)
    ktp_number: str = Field(min_length=8, max_length=32)  # tidak pernah disimpan mentah


class CustomerOut(BaseModel):
    id: uuid.UUID
    full_name: str
    user_id: uuid.UUID | None

    model_config = {"from_attributes": True}
