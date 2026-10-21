"""Model approval request — work queue & eskalasi (WORKFLOWS.md)."""

import enum
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, Text, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.user import UserRole


class ApprovalObjectType(enum.StrEnum):
    TRANSFER = "transfer"
    ACCOUNT = "account"


class ApprovalStatus(enum.StrEnum):
    PENDING = "PENDING"  # menunggu keputusan di current_role
    ESCALATED = "ESCALATED"  # naik level (di atas limit approver)
    APPROVED = "APPROVED"  # dieksekusi
    REJECTED = "REJECTED"  # ditolak, alasan wajib (BR-08)


def _now() -> datetime:
    return datetime.now(UTC)


class ApprovalRequest(Base):
    __tablename__ = "approval_requests"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    object_type: Mapped[ApprovalObjectType] = mapped_column(
        Enum(
            ApprovalObjectType,
            name="approval_object_type",
            values_callable=lambda e: [m.value for m in e],
        )
    )
    object_id: Mapped[uuid.UUID] = mapped_column(Uuid)  # id transaksi/rekening
    current_role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, name="user_role", values_callable=lambda e: [m.value for m in e])
    )
    status: Mapped[ApprovalStatus] = mapped_column(
        Enum(
            ApprovalStatus, name="approval_status", values_callable=lambda e: [m.value for m in e]
        ),
        default=ApprovalStatus.PENDING,
    )
    escalation_level: Mapped[int] = mapped_column(Integer, default=1)
    requested_by: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"))
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id"), nullable=True
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # BR-10: limit yang berlaku saat keputusan — riwayat bisa dijelaskan ke regulator
    matrix_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=_now)
