"""Model ledger double-entry — APPEND-ONLY (BR-04).

Tabel ini tidak pernah di-UPDATE/di-DELETE — ditegakkan oleh DATABASE TRIGGER
(bukan cuma aturan aplikasi). Lihat migration 0002.
"""

import enum
import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import Enum, ForeignKey, Numeric, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class LedgerDirection(enum.StrEnum):
    DEBIT = "DEBIT"
    CREDIT = "CREDIT"


def _now() -> datetime:
    return datetime.now(UTC)


class LedgerEntry(Base):
    __tablename__ = "ledger_entries"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    transaction_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("transactions.id"), index=True
    )
    account_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("accounts.id"), index=True)
    direction: Mapped[LedgerDirection] = mapped_column(
        Enum(
            LedgerDirection, name="ledger_direction", values_callable=lambda e: [m.value for m in e]
        )
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(19, 4))  # selalu > 0; arah di 'direction'
    # snapshot saldo setelah entry ini — jawab auditor: "berapa saldo tanggal X?" (ERD §1)
    balance_after: Mapped[Decimal] = mapped_column(Numeric(19, 4))
    created_at: Mapped[datetime] = mapped_column(default=_now)
