"""Model transaksi finansial (header) — logika transfer di Fase 3."""

import enum
import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import Enum, ForeignKey, Numeric, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class TransactionType(enum.StrEnum):
    TRANSFER = "transfer"
    ADJUSTMENT = "adjustment"  # koreksi/deposit manual oleh ops (dengan audit)


class TransactionStatus(enum.StrEnum):
    PENDING_APPROVAL = "PENDING_APPROVAL"
    EXECUTED = "EXECUTED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"


def _now() -> datetime:
    return datetime.now(UTC)


class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    type: Mapped[TransactionType] = mapped_column(
        Enum(
            TransactionType, name="transaction_type", values_callable=lambda e: [m.value for m in e]
        )
    )
    source_account_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("accounts.id"), nullable=True, index=True
    )
    dest_account_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("accounts.id"), nullable=True
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(19, 4))
    status: Mapped[TransactionStatus] = mapped_column(
        Enum(
            TransactionStatus,
            name="transaction_status",
            values_callable=lambda e: [m.value for m in e],
        )
    )
    # UNIQUE partial di migration — idempotency layer 2 (setelah Redis)
    idempotency_key: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"))
    approved_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id"), nullable=True
    )
    rejected_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    executed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=_now)
