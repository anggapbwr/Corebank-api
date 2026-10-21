"""Skema request/response auth."""

from uuid import UUID

from pydantic import BaseModel, Field

from app.models.user import UserRole


class LoginRequest(BaseModel):
    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=6, max_length=128)


class RegisterRequest(BaseModel):
    """Buat user internal — hanya manager (least privilege, FR-AUTH-4)."""

    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=8, max_length=128)
    role: UserRole


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int  # detik — client tahu kapan harus refresh


class UserOut(BaseModel):
    id: UUID
    username: str
    role: UserRole
    is_active: bool

    model_config = {"from_attributes": True}
