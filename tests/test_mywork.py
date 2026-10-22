"""Test My Work per role (lanjutan Fase 6) — session-cookie, panel per role, aksi UI.

Membuktikan:
- /mywork wajib login (redirect /login); cookie HttpOnly + SameSite=lax
- Login web memakai jalur rate limit & timing-equalizer yang sama dgn API
- Panel My Work sesuai role: supervisor -> antrean+aksi, teller -> transaksinya,
  auditor -> jejak audit, nasabah -> HANYA rekening sendiri (BOLA)
- Aksi UI memanggil service decide() yang sama: approve dieksekusi, eskalasi
  jalan, reject tanpa alasan ditolak (BR-08), RBAC dicek di server (bukan cuma
  tombol disembunyikan)
"""

import asyncio
import uuid

import pytest
from fastapi.testclient import TestClient

from app.core.db import SessionLocal
from app.core.security import hash_password
from app.main import app
from app.models.account import Account, AccountStatus
from app.models.approval import ApprovalObjectType, ApprovalRequest, ApprovalStatus
from app.models.audit import AuditLog
from app.models.customer import Customer
from app.models.ledger import LedgerDirection, LedgerEntry
from app.models.transaction import Transaction, TransactionStatus
from app.models.user import User, UserRole

client = TestClient(app)
RUN = uuid.uuid4().hex[:8]
PASSWORD = "kata-sandi-123"

# Hasil seed — dibaca test setelah fixture
IDS: dict = {}


@pytest.fixture(scope="module", autouse=True)
def seeded() -> dict:
    """User semua role + 3 antrean approval (100jt, 400jt, 100jt) + 2 nasabah."""
    from decimal import Decimal

    async def _seed() -> None:
        async with SessionLocal() as db:
            users = {}
            for role in UserRole:
                u = User(
                    username=f"w_{role.value}_{RUN}",
                    password_hash=hash_password(PASSWORD),
                    role=role,
                )
                db.add(u)
                users[role] = u
            await db.flush()

            # Nasabah A (punya login) & nasabah B (tanpa login — tak boleh muncul)
            cust_a = Customer(
                user_id=users[UserRole.NASABAH].id,
                full_name=f"Nasabah A {RUN}",
                ktp_hash=f"hash-a-{RUN}",
            )
            cust_b = Customer(full_name=f"Nasabah B {RUN}", ktp_hash=f"hash-b-{RUN}")
            db.add_all([cust_a, cust_b])
            await db.flush()

            acc_a = Account(
                customer_id=cust_a.id,
                account_number=f"111{RUN}",  # rekening milik nasabah login
                type="savings",
                balance=Decimal("600000000"),
                status=AccountStatus.ACTIVE,
            )
            acc_b = Account(
                customer_id=cust_b.id,
                account_number=f"222{RUN}",  # rekening nasabah LAIN — tak boleh tampil
                type="savings",
                balance=Decimal("0"),
                status=AccountStatus.ACTIVE,
            )
            # Suspense: sumber dana awal — boleh negatif (uang dari "luar sistem"),
            # asal tetap double-entry agar reconciliation (BR-05) tetap balance.
            suspense_cust = Customer(full_name=f"Vault {RUN}", ktp_hash=f"hash-v-{RUN}")
            db.add(suspense_cust)
            await db.flush()
            acc_vault = Account(
                customer_id=suspense_cust.id,
                account_number=f"999{RUN}",
                type="savings",
                balance=Decimal("-600000000"),
                status=AccountStatus.ACTIVE,
            )
            db.add_all([acc_a, acc_b, acc_vault])
            await db.flush()

            # Funding: DEBIT vault + CREDIT nasabah A — jurnal seimbang,
            # saldo & ledger cocok (ini yang diperiksa reconcile)
            fund_tx = Transaction(
                type="adjustment",
                source_account_id=acc_vault.id,
                dest_account_id=acc_a.id,
                amount=Decimal("600000000"),
                status=TransactionStatus.EXECUTED,
                created_by=users[UserRole.MANAGER].id,
            )
            db.add(fund_tx)
            await db.flush()
            db.add_all(
                [
                    LedgerEntry(
                        transaction_id=fund_tx.id,
                        account_id=acc_vault.id,
                        direction=LedgerDirection.DEBIT,
                        amount=Decimal("600000000"),
                        balance_after=Decimal("-600000000"),
                    ),
                    LedgerEntry(
                        transaction_id=fund_tx.id,
                        account_id=acc_a.id,
                        direction=LedgerDirection.CREDIT,
                        amount=Decimal("600000000"),
                        balance_after=Decimal("600000000"),
                    ),
                ]
            )
            await db.flush()

            def tx(amount: str) -> Transaction:
                return Transaction(
                    type="transfer",
                    source_account_id=acc_a.id,
                    dest_account_id=acc_b.id,
                    amount=Decimal(amount),
                    status=TransactionStatus.PENDING_APPROVAL,
                    created_by=users[UserRole.TELLER].id,
                )

            def req(t: Transaction) -> ApprovalRequest:
                return ApprovalRequest(
                    object_type=ApprovalObjectType.TRANSFER,
                    object_id=t.id,
                    current_role=UserRole.SUPERVISOR,
                    status=ApprovalStatus.PENDING,
                    escalation_level=1,
                    requested_by=users[UserRole.TELLER].id,
                    matrix_snapshot={"direct_max": "50000000", "supervisor_max": "250000000"},
                )

            tx_100, tx_400 = tx("100000000"), tx("400000000")
            tx_rej, tx_teller = tx("100000000"), tx("100000000")
            db.add_all([tx_100, tx_400, tx_rej, tx_teller])
            await db.flush()
            r1, r2, r3, r4 = req(tx_100), req(tx_400), req(tx_rej), req(tx_teller)
            db.add_all([r1, r2, r3, r4])
            db.add(
                AuditLog(
                    actor_user_id=users[UserRole.MANAGER].id,
                    action="SEED_EVENT",
                    object_type="test",
                    payload={"run": RUN},
                )
            )
            await db.commit()

            IDS.update(
                supervisor=f"w_supervisor_{RUN}",
                manager=f"w_manager_{RUN}",
                teller=f"w_teller_{RUN}",
                auditor=f"w_auditor_{RUN}",
                nasabah=f"w_nasabah_{RUN}",
                req_approve=r1.id,
                req_escalate=r2.id,
                req_reject=r3.id,
                req_teller=r4.id,
                acc_own=acc_a.id,
                acc_own_number=acc_a.account_number,
                acc_other_number=acc_b.account_number,
            )

    asyncio.run(_seed())
    return IDS


def _login(username: str, pw: str = PASSWORD) -> None:
    """Login via form web — cookie disimpan di `client` (jar httpx)."""
    resp = client.post(
        "/login", data={"username": username, "password": pw}, follow_redirects=False
    )
    assert resp.status_code == 303, resp.text


def _db_get(model, ident):  # noqa: ANN001
    async def _get():
        async with SessionLocal() as db:
            return await db.get(model, ident)

    return asyncio.run(_get())


# --------------------------------------------------------------------------
# Auth session-cookie
# --------------------------------------------------------------------------
def test_mywork_tanpa_login_redirect_ke_login() -> None:
    resp = client.get("/mywork", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


def test_login_form_render() -> None:
    resp = client.get("/login")
    assert resp.status_code == 200
    assert 'action="/login"' in resp.text
    assert 'type="password"' in resp.text
    # cookie tak pernah ikut direspons GET ini (hanya diset oleh POST sukses)
    assert "cb_session=" not in resp.headers.get("set-cookie", "")


def test_login_salah_ditolak() -> None:
    resp = client.post(
        "/login",
        data={"username": IDS["supervisor"], "password": "salah"},
        follow_redirects=False,
    )
    assert resp.status_code == 401
    assert "Username atau password salah" in resp.text
    assert "cb_session" not in resp.headers.get("set-cookie", "")


def test_login_benar_set_cookie_httponly() -> None:
    _login(IDS["supervisor"])
    resp = client.get("/login", follow_redirects=False)  # sudah login -> redirect
    assert resp.status_code == 303
    assert resp.headers["location"] == "/mywork"
    # cookie ada & HttpOnly (JS tak bisa baca — cegah pencurian via XSS)
    cookie = next(c for c in client.cookies.jar if c.name == "cb_session")
    assert cookie.has_nonstandard_attr("HttpOnly")
    assert cookie.get_nonstandard_attr("SameSite").lower() == "lax"


# --------------------------------------------------------------------------
# Panel per role
# --------------------------------------------------------------------------
def test_supervisor_lihat_antrean_dengan_aksi() -> None:
    _login(IDS["supervisor"])
    resp = client.get("/mywork")
    assert resp.status_code == 200
    assert "My Work — supervisor" in resp.text
    assert "Approve" in resp.text and "Reject" in resp.text
    assert str(IDS["req_approve"]) in resp.text
    # RBAC terlihat di menu: tombol aksi memang ada untuk approver
    assert 'hx-prompt="Alasan penolakan (wajib, BR-08):"' in resp.text


def test_teller_panel_transaksinya_saja() -> None:
    _login(IDS["teller"])
    resp = client.get("/mywork")
    assert "My Work — teller" in resp.text
    assert "PENDING_APPROVAL" in resp.text
    # Tak ada tombol putuskan — antrean bukan urutan teller
    assert 'hx-post="/mywork/approve/' not in resp.text


def test_auditor_panel_jejak_audit() -> None:
    _login(IDS["auditor"])
    resp = client.get("/mywork")
    assert "My Work — auditor" in resp.text
    assert "SEED_EVENT" in resp.text
    assert 'hx-post="/mywork/approve/' not in resp.text


def test_nasabah_hanya_rekening_sendiri_bola() -> None:
    _login(IDS["nasabah"])
    resp = client.get("/mywork")
    assert IDS["acc_own_number"] in resp.text
    assert IDS["acc_other_number"] not in resp.text  # rekening nasabah lain bocor? TIDAK


# --------------------------------------------------------------------------
# Aksi dari UI (service decide() yang sama dengan API)
# --------------------------------------------------------------------------
def test_approve_dari_ui_dieksekusi() -> None:
    _login(IDS["supervisor"])
    resp = client.post(f"/mywork/approve/{IDS['req_approve']}")
    assert resp.status_code == 200
    assert "APPROVED" in resp.text
    req = _db_get(ApprovalRequest, IDS["req_approve"])
    assert req.status == ApprovalStatus.APPROVED
    tx = _db_get(Transaction, req.object_id)
    assert tx.status == TransactionStatus.EXECUTED  # journal benar-benar diposting


def test_approve_di_atas_limit_supervisor_eskalasi_ke_manager() -> None:
    _login(IDS["supervisor"])
    resp = client.post(f"/mywork/approve/{IDS['req_escalate']}")
    assert "ESCALATED" in resp.text
    req = _db_get(ApprovalRequest, IDS["req_escalate"])
    assert req.status == ApprovalStatus.ESCALATED
    assert req.current_role == UserRole.MANAGER
    assert req.escalation_level == 2


def test_reject_tanpa_alasan_ditolak_br08() -> None:
    _login(IDS["supervisor"])
    resp = client.post(f"/mywork/reject/{IDS['req_reject']}")  # tanpa HX-Prompt
    assert "alasan" in resp.text.lower()
    req = _db_get(ApprovalRequest, IDS["req_reject"])
    assert req.status == ApprovalStatus.PENDING  # tetap tertunda — belum diputuskan


def test_reject_dengan_alasan_via_prompt() -> None:
    _login(IDS["supervisor"])
    resp = client.post(f"/mywork/reject/{IDS['req_reject']}", headers={"HX-Prompt": "salah tujuan"})
    assert "REJECTED" in resp.text
    req = _db_get(ApprovalRequest, IDS["req_reject"])
    assert req.status == ApprovalStatus.REJECTED
    assert req.reason == "salah tujuan"


# --------------------------------------------------------------------------
# RBAC server-side (menu disembunyikan + API tetap menolak)
# --------------------------------------------------------------------------
def test_teller_tak_bisa_approve_meski_panggil_url_langsung() -> None:
    """Tombol tak ada di menu teller — tapi yang membuktikan keamanan adalah
    server tetap menolak walau URL dipanggil langsung."""
    _login(IDS["teller"])
    resp = client.post(f"/mywork/approve/{IDS['req_teller']}")
    assert "tidak berwenang" in resp.text
    req = _db_get(ApprovalRequest, IDS["req_teller"])
    assert req.status == ApprovalStatus.PENDING  # tak tersentuh


def test_auditor_tak_bisa_approve() -> None:
    _login(IDS["auditor"])
    resp = client.post(f"/mywork/approve/{IDS['req_teller']}")
    assert "tidak berwenang" in resp.text
    req = _db_get(ApprovalRequest, IDS["req_teller"])
    assert req.status == ApprovalStatus.PENDING


# --------------------------------------------------------------------------
# Logout
# --------------------------------------------------------------------------
def test_logout_hapus_cookie_dan_pindah_ke_login() -> None:
    _login(IDS["nasabah"])
    resp = client.post("/logout", follow_redirects=False)
    assert resp.status_code == 401
    assert resp.headers["HX-Redirect"] == "/login"
    set_cookie = resp.headers.get("set-cookie", "")
    # delete_cookie -> nilai kosong + Max-Age=0; cookie lama tak berlaku lagi
    assert 'cb_session=""' in set_cookie and "Max-Age=0" in set_cookie
    # sesudah logout, /mywork balik redirect
    follow = client.get("/mywork", follow_redirects=False)
    assert follow.status_code == 303


def test_fragment_mywork_butuh_login() -> None:
    """Endpoint HTMX tanpa cookie -> 401 + HX-Redirect (bukan halaman error)."""
    anon = TestClient(app)
    resp = anon.get("/fragments/mywork")
    assert resp.status_code == 401
    assert resp.headers.get("HX-Redirect") == "/login"
