"""Shared FastAPI dependencies: koneksi DB & otorisasi (RBAC)."""

from collections.abc import Awaitable, Callable
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.security import TokenError, decode_token
from app.models.user import User, UserRole

DbSession = Annotated[AsyncSession, Depends(get_db)]


async def get_current_user(
    db: DbSession,
    authorization: Annotated[str | None, Header()] = None,
) -> User:
    """Validasi Bearer token → user aktif dari DB.

    Token hanya membawa ID; role & is_active dibaca dari DB setiap request
    (revoke/ubah role langsung berlaku tanpa menunggu token kedaluwarsa).
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing Bearer token")
    try:
        payload = decode_token(authorization.removeprefix("Bearer "), expected_type="access")
        user_id = UUID(payload["sub"])
    except (TokenError, ValueError) as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token") from exc

    user = await db.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User inactive or not found")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_roles(*roles: UserRole) -> Callable[[User], Awaitable[User]]:
    """Dependency factory RBAC: 403 kalau role tidak diizinkan."""

    async def checker(user: CurrentUser) -> User:
        if user.role not in roles:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"Role '{user.role.value}' not allowed; need one of {[r.value for r in roles]}",
            )
        return user

    return checker
