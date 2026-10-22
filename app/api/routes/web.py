"""Halaman web dashboard (Fase 6) + My Work per role (lanjutan Fase 6).

Keputusan scope (dari brainstorm + instruksi "kerjakan"):
- "/" (monitoring: health + alert) tetap TANPA login — UI internal belajar
- "/mywork" butuh session-cookie: form login -> access token JWT disimpan di
  cookie HttpOnly (HTMX ikut mengirim cookie otomatis; tanpa localStorage/JS)
- Aksi approve/reject dari UI memanggil service decide() YANG SAMA dengan
  API: RBAC dicek di service (separation of duties), bukan cuma tombol
  disembunyikan di menu — "UI cantik != keamanan"
- Cookie membawa access token 15 menit; lewat itu redirect /login lagi.
  Refresh flow untuk UI = backlog sadar (demo workbench, bukan produk)
"""

from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.auth import (
    _DUMMY_HASH,
    _clear_failures,
    _issue_tokens,
    _rate_limited,
    _register_failure,
)
from app.api.routes.system import system_alerts, system_health
from app.core.config import get_settings
from app.core.db import get_db
from app.core.security import TokenError, decode_token, verify_password
from app.models.account import Account
from app.models.audit import AuditLog
from app.models.customer import Customer
from app.models.transaction import Transaction
from app.models.user import User, UserRole
from app.services.approval import ApprovalError, decide, list_queue

router = APIRouter(tags=["web"])
templates = Jinja2Templates(directory=Path(__file__).resolve().parents[2] / "templates")

SESSION_COOKIE = "cb_session"
DbSession = Annotated[AsyncSession, Depends(get_db)]


# --------------------------------------------------------------------------
# Session-cookie auth (khusus halaman web)
# --------------------------------------------------------------------------
async def _web_user(request: Request, db: AsyncSession) -> User | None:
    """User dari cookie session — None kalau belum login / token kedaluwarsa."""
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    try:
        payload = decode_token(token, expected_type="access")
        user = await db.get(User, UUID(payload["sub"]))
    except (TokenError, ValueError):
        return None
    return user if user is not None and user.is_active else None


def _set_session_cookie(response: Response, token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=settings.access_token_expire_minutes * 60,
        httponly=True,  # JS halaman tak bisa baca — cegah pencurian via XSS
        samesite="lax",  # cookie tak ikut request lintas situs (CSRF ringan)
        secure=settings.app_env == "prod",
    )


async def _require_web_user(request: Request, db: AsyncSession) -> User:
    """Untuk endpoint HTMX: 401 + HX-Redirect (htmx otomatis pindah ke /login)."""
    user = await _web_user(request, db)
    if user is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Silakan login",
            headers={"HX-Redirect": "/login"},
        )
    return user


# --------------------------------------------------------------------------
# Login / logout
# --------------------------------------------------------------------------
@router.get("/login", response_class=HTMLResponse)
async def login_form(request: Request, db: DbSession) -> Response:
    """Form login — redirect ke /mywork kalau sudah login."""
    if await _web_user(request, db) is not None:
        return RedirectResponse("/mywork", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"error": None})


@router.post("/login", response_class=HTMLResponse)
async def login_submit(
    request: Request,
    db: DbSession,
    username: Annotated[str, Form()],
    password: Annotated[str, Form()],
) -> Response:
    """Verifikasi kredensial — pakai jalur rate limit & timing-equalizer yang
    SAMA dengan API /auth/login (NFR-02): gagal 5x/menit kena 429."""
    ip = request.client.host if request.client else "unknown"
    if await _rate_limited(ip, username):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Terlalu banyak percobaan gagal — coba lagi dalam semenit",
        )
    user = (await db.execute(select(User).where(User.username == username))).scalar_one_or_none()
    if user is None:
        verify_password(_DUMMY_HASH, password)  # timing seragam — cegah user enumeration
        await _register_failure(ip, username)
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": "Username atau password salah"},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    if not verify_password(user.password_hash, password):
        await _register_failure(ip, username)
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": "Username atau password salah"},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    if not user.is_active:
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": "Akun dinonaktifkan"},
            status_code=status.HTTP_403_FORBIDDEN,
        )
    await _clear_failures(ip, username)
    resp = RedirectResponse("/mywork", status_code=status.HTTP_303_SEE_OTHER)
    _set_session_cookie(resp, _issue_tokens(user).access_token)
    return resp


@router.post("/logout")
async def logout() -> Response:
    """Hapus cookie session + minta htmx pindah ke /login (HX-Redirect)."""
    resp = HTMLResponse(status_code=status.HTTP_401_UNAUTHORIZED)
    resp.delete_cookie(SESSION_COOKIE)
    resp.headers["HX-Redirect"] = "/login"
    return resp


# --------------------------------------------------------------------------
# My Work per role
# --------------------------------------------------------------------------
async def _mywork_context(db: AsyncSession, user: User, flash: dict | None = None) -> dict:
    """Data panel My Work sesuai role — satu sumber kebenaran untuk render
    awal, fragment, dan respons aksi (approve/reject)."""
    ctx: dict = {"user": user, "flash": flash}

    if user.role in (UserRole.SUPERVISOR, UserRole.MANAGER):
        queue = await list_queue(db, user.role)
        rows = []
        for req in queue:
            tx = await db.get(Transaction, req.object_id)
            rows.append({"req": req, "tx": tx})
        ctx["queue"] = rows
    elif user.role in (UserRole.TELLER, UserRole.CS):
        stmt = (
            select(Transaction)
            .where(Transaction.created_by == user.id)
            .order_by(Transaction.created_at.desc())
            .limit(20)
        )
        ctx["my_transfers"] = list((await db.execute(stmt)).scalars().all())
    elif user.role in (UserRole.AUDITOR, UserRole.OPS_RISK):
        stmt = select(AuditLog).order_by(AuditLog.created_at.desc()).limit(20)
        ctx["audit_logs"] = list((await db.execute(stmt)).scalars().all())
    else:  # NASabah — rekening miliknya sendiri (scoped by user_id; BOLA aman)
        cust_ids = [
            c.id
            for c in (
                await db.execute(select(Customer).where(Customer.user_id == user.id))
            ).scalars()
        ]
        ctx["accounts"] = (
            list(
                (
                    await db.execute(select(Account).where(Account.customer_id.in_(cust_ids)))
                ).scalars()
            )
            if cust_ids
            else []
        )
    return ctx


@router.get("/mywork", response_class=HTMLResponse)
async def mywork(request: Request, db: DbSession) -> Response:
    """Halaman My Work — login wajib; tampilan kerja sesuai role."""
    user = await _web_user(request, db)
    if user is None:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(request, "mywork.html", await _mywork_context(db, user))


@router.get("/fragments/mywork", response_class=HTMLResponse)
async def mywork_fragment(request: Request, db: DbSession) -> Response:
    """Partial panel My Work — target swap setelah aksi & tombol Segarkan."""
    user = await _require_web_user(request, db)
    return templates.TemplateResponse(
        request, "partials/_mywork.html", await _mywork_context(db, user)
    )


async def _decide_via_ui(
    request: Request, db: AsyncSession, request_id: UUID, *, approve: bool
) -> Response:
    """Aksi approve/reject dari UI — service decide() yang sama dengan API.

    Reject mengambil alasan dari header HX-Prompt (htmx prompt() native) —
    BR-08: reject tanpa alasan ditolak service.
    """
    user = await _require_web_user(request, db)
    if user.role not in (UserRole.SUPERVISOR, UserRole.MANAGER):
        ctx = await _mywork_context(
            db, user, {"kind": "err", "text": "Role kamu tidak berwenang memutuskan approval."}
        )
        return templates.TemplateResponse(request, "partials/_mywork.html", ctx)

    reason = None if approve else request.headers.get("hx-prompt")
    try:
        result, _ = await decide(
            db, request_id=request_id, approver=user, approve=approve, reason=reason
        )
    except ApprovalError as exc:
        ctx = await _mywork_context(db, user, {"kind": "err", "text": exc.detail})
        return templates.TemplateResponse(request, "partials/_mywork.html", ctx)

    detail = result.get("detail") or "transfer dieksekui"
    kind = "err" if result["status"] == "REJECTED" else "ok"
    text = f"{result['status']} — {detail}"
    ctx = await _mywork_context(db, user, {"kind": kind, "text": text})
    return templates.TemplateResponse(request, "partials/_mywork.html", ctx)


@router.post("/mywork/approve/{request_id}", response_class=HTMLResponse)
async def mywork_approve(request_id: UUID, request: Request, db: DbSession) -> Response:
    return await _decide_via_ui(request, db, request_id, approve=True)


@router.post("/mywork/reject/{request_id}", response_class=HTMLResponse)
async def mywork_reject(request_id: UUID, request: Request, db: DbSession) -> Response:
    return await _decide_via_ui(request, db, request_id, approve=False)


# --------------------------------------------------------------------------
# Dashboard monitoring (tanpa login) + fragments HTMX
# --------------------------------------------------------------------------
@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request) -> Response:
    """Dashboard utama — render awal server-side, polling HTMX lanjutannya."""
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {"health": await system_health(), "alerts": await system_alerts(), "user": None},
    )


@router.get("/fragments/health", response_class=HTMLResponse)
async def health_fragment(request: Request) -> Response:
    """Partial kartu System Health — target polling HTMX (tiap 15 dtk)."""
    return templates.TemplateResponse(
        request, "partials/_health.html", {"health": await system_health()}
    )


@router.get("/fragments/alerts", response_class=HTMLResponse)
async def alerts_fragment(request: Request) -> Response:
    """Partial panel alert (kanan) — target polling HTMX (tiap 20 dtk)."""
    return templates.TemplateResponse(
        request, "partials/_alerts.html", {"alerts": await system_alerts()}
    )
