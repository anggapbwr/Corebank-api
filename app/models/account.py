"""Model rekening."""

import enum
import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import Enum, ForeignKey, Numeric, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.customer import Customer


class AccountType(enum.StrEnum):
    SAVINGS = "savings"
    CHECKING = "checking"


class AccountStatus(enum.StrEnum):
    PENDING_APPROVAL = "PENDING_APPROVAL"  # baru dibuat teller, menunggu supervisor
    ACTIVE = "ACTIVE"
    FROZEN = "FROZEN"  # BR-06: tidak bisa debit/kredit
    CLOSED = "CLOSED"


def _now() -> datetime:
    return datetime.now(UTC)


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    customer_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("customers.id"), index=True)
    account_number: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    type: Mapped[AccountType] = mapped_column(
        Enum(AccountType, name="account_type", values_callable=lambda e: [m.value for m in e])
    )
    # NUMERIC, bukan float — uang tidak pernah pakai binary floating point (ERD §5)
    balance: Mapped[Decimal] = mapped_column(Numeric(19, 4), default=Decimal("0"))
    status: Mapped[AccountStatus] = mapped_column(
        Enum(AccountStatus, name="account_status", values_callable=lambda e: [m.value for m in e]),
        default=AccountStatus.PENDING_APPROVAL,
    )
    created_at: Mapped[datetime] = mapped_column(default=_now)

    customer: Mapped[Customer] = relationship()
