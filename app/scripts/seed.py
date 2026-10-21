"""Seed user internal untuk DEV — jalankan: python -m app.scripts.seed

Membuat user awal supaya alur RBAC bisa langsung dicoba. Idempotent: user
yang sudah ada tidak ditimpa.

PRODUKSI: seed semacam ini tidak ada — pembuatan akun via /auth/register
oleh manager, dengan audit trail.
"""

import asyncio
import os

from sqlalchemy import select

from app.core.db import SessionLocal
from app.core.security import hash_password
from app.models.user import User, UserRole

_DEMO_USERS = [
    ("admin", UserRole.MANAGER),  # manager = approver level 2 + bisa register user
    ("supervisor", UserRole.SUPERVISOR),
    ("teller", UserRole.TELLER),
    ("auditor", UserRole.AUDITOR),
]
_PASSWORD = os.environ.get("SEED_PASSWORD", "rahasia-dev-123")


async def main() -> None:
    async with SessionLocal() as db:
        for username, role in _DEMO_USERS:
            exists = (
                await db.execute(select(User).where(User.username == username))
            ).scalar_one_or_none()
            if exists is not None:
                print(f"[skip]    {username} sudah ada")
                continue
            db.add(User(username=username, password_hash=hash_password(_PASSWORD), role=role))
            print(f"[create]  {username} ({role.value})")
        await db.commit()
    print(f"\nPassword semua user demo: {_PASSWORD} (ubah via env SEED_PASSWORD)")


if __name__ == "__main__":
    asyncio.run(main())
