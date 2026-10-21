"""Model nasabah (customer) — identitas bisnis, terpisah dari akun login."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import ForeignKey, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


def _now() -> datetime:
    return datetime.now(UTC)


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    # boleh NULL: nasabah bisa punya rekening tanpa punya login
    user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"), nullable=True)
    full_name: Mapped[str] = mapped_column(String(120))
    # KTP tidak pernah disimpan mentah — hanya hash (NFR-08 kehati-hatian data)
    ktp_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(default=_now)
