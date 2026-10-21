"""Model user internal (teller, supervisor, nasabah dengan login, dst)."""

import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, Enum, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class UserRole(enum.StrEnum):
    NASABAH = "nasabah"  # customer via channel
    TELLER = "teller"  # frontliner input
    CS = "cs"  # customer service
    SUPERVISOR = "supervisor"  # approver level 1
    MANAGER = "manager"  # approver level 2 + admin
    OPS_RISK = "ops_risk"  # monitoring, freeze, recon
    AUDITOR = "auditor"  # read-only penuh


def _now() -> datetime:
    return datetime.now(UTC)


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    username: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, name="user_role", values_callable=lambda e: [m.value for m in e])
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(default=_now)
