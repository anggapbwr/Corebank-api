"""Test integrasi Fase 2 — jalankan dengan Postgres compose hidup.

Membuktikan acceptance criteria:
- buka rekening → approve manual → status ACTIVE (WORKFLOWS §5)
- RBAC: teller tidak bisa approve; manager bisa register user
- BOLA: nasabah hanya boleh lihat rekening miliknya
- BR-04: UPDATE/DELETE ke ledger_entries ditolak DATABASE (trigger)
- BR-05: Σ debit = Σ kredit; saldo & balance_after konsisten
"""

import uuid
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.db import SessionLocal
from app.core.security import hash_password
from app.main import app
from app.models.account import Account, AccountStatus, AccountType
from app.models.customer import Customer
from app.models.ledger import LedgerDirection
from app.models.transaction import Transaction, TransactionStatus, TransactionType
from app.models.user import User, UserRole
from app.services.ledger import LedgerUnbalancedError, post_journal

client = TestClient(app)

# Prefix unik per run — DB test append-only (BR-04), jadi data lama tidak bisa dihapus
RUN = uuid.uuid4().hex[:8]


def _login(username: str, password: str) -> dict:
    resp = client.post("/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _auth(headers: dict) -> dict:
    return headers


@pytest.fixture(scope="module")
def seeded() -> dict:
    """Buat user + nasabah + rekening referensi langsung via DB session."""
    usernames = {role: f"test_{role.value}_{RUN}" for role in UserRole if role != UserRole.NASABAH}
    nasabah_name = f"test_nasabah_{RUN}"

    async def _seed() -> None:
        async with SessionLocal() as db:
            for role, username in usernames.items():
                db.add(
                    User(
                        username=username, password_hash=hash_password("kata-sandi-123"), role=role
                    )
                )
            nasabah = User(
                username=nasabah_name,
                password_hash=hash_password("kata-sandi-123"),
                role=UserRole.NASABAH,
            )
            db.add(nasabah)
            await db.flush()
            customer = Customer(
                full_name="Nasabah Test", ktp_hash="hash-ktp-test", user_id=nasabah.id
            )
            db.add(customer)
            await db.commit()

    import asyncio

    asyncio.run(_seed())
    return {"usernames": usernames, "nasabah": nasabah_name, "password": "kata-sandi-123"}


def test_login_dan_me(seeded: dict) -> None:
    headers = _login(seeded["usernames"][UserRole.TELLER], seeded["password"])
    me = client.get("/auth/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["role"] == "teller"


def test_login_password_salah_401(seeded: dict) -> None:
    resp = client.post(
        "/auth/login",
        json={"username": seeded["usernames"][UserRole.TELLER], "password": "salah-sekali"},
    )
    assert resp.status_code == 401


def test_register_hanya_manager(seeded: dict) -> None:
    body = {"username": f"test_baru_{RUN}", "password": "kata-sandi-123", "role": "cs"}

    # teller → 403 (separation of duties)
    teller = _login(seeded["usernames"][UserRole.TELLER], seeded["password"])
    assert client.post("/auth/register", json=body, headers=teller).status_code == 403

    # manager → 201, lalu user baru bisa dipakai login
    admin = _login(seeded["usernames"][UserRole.MANAGER], seeded["password"])
    created = client.post("/auth/register", json=body, headers=admin)
    assert created.status_code == 201, created.text
    _login(body["username"], body["password"])


def test_alur_buka_rekening_approve(seeded: dict) -> None:
    """AC Fase 2: buka rekening → approve manual → ACTIVE."""
    teller = _login(seeded["usernames"][UserRole.TELLER], seeded["password"])
    supervisor = _login(seeded["usernames"][UserRole.SUPERVISOR], seeded["password"])

    # teller membuat customer + rekening → PENDING_APPROVAL
    cust = client.post(
        "/customers",
        json={"full_name": "Budi Santoso", "ktp_number": "3175xxxx0890"},
        headers=teller,
    )
    assert cust.status_code == 201, cust.text
    acc = client.post(
        "/accounts", json={"customer_id": cust.json()["id"], "type": "savings"}, headers=teller
    )
    assert acc.status_code == 201, acc.text
    assert acc.json()["status"] == "PENDING_APPROVAL"
    assert Decimal(acc.json()["balance"]) == Decimal("0")
    account_id = acc.json()["id"]

    # teller TIDAK boleh approve (RBAC)
    assert client.post(f"/accounts/{account_id}/approve", headers=teller).status_code == 403

    # supervisor approve → ACTIVE
    approved = client.post(f"/accounts/{account_id}/approve", headers=supervisor)
    assert approved.status_code == 200
    assert approved.json()["status"] == "ACTIVE"

    # approve dua kali → 409 (state machine guard)
    assert client.post(f"/accounts/{account_id}/approve", headers=supervisor).status_code == 409


def test_bola_nasabah_hanya_lihat_rekening_sendiri(seeded: dict) -> None:
    """NFR-03: BOLA protection — object-level authorization."""
    teller = _login(seeded["usernames"][UserRole.TELLER], seeded["password"])
    nasabah = _login(seeded["nasabah"], seeded["password"])

    cust = client.post(
        "/customers",
        json={"full_name": "Pemilik Test", "ktp_number": "3175xxxx1111"},
        headers=teller,
    )
    # customer yang user_id-nya kosong → milik nasabah lain
    acc_other = client.post(
        "/accounts", json={"customer_id": cust.json()["id"], "type": "savings"}, headers=teller
    ).json()

    # customer milik nasabah login
    cust_own = client.post(
        "/customers",
        json={"full_name": "Milik Nasabah", "ktp_number": "3175xxxx2222"},
        headers=teller,
    ).json()

    # rekening milik nasabah login — buat via teller, lalu tautkan user_id customer
    import asyncio

    acc_own = client.post(
        "/accounts", json={"customer_id": cust_own["id"], "type": "savings"}, headers=teller
    ).json()

    async def _link() -> None:
        async with SessionLocal() as db:
            nasabah_user = (
                await db.execute(
                    text("SELECT id FROM users WHERE username = :u"), {"u": seeded["nasabah"]}
                )
            ).scalar_one()
            await db.execute(
                text("UPDATE customers SET user_id = :uid WHERE id = :cid"),
                {"uid": nasabah_user, "cid": cust_own["id"]},
            )
            await db.commit()

    asyncio.run(_link())

    # boleh lihat rekening sendiri
    assert client.get(f"/accounts/{acc_own['id']}", headers=nasabah).status_code == 200
    # rekening orang lain → 404 (tidak membocorkan keberadaan objek)
    assert client.get(f"/accounts/{acc_other['id']}", headers=nasabah).status_code == 404


def test_freeze_unfreeze(seeded: dict) -> None:
    teller = _login(seeded["usernames"][UserRole.TELLER], seeded["password"])
    manager = _login(seeded["usernames"][UserRole.MANAGER], seeded["password"])

    cust = client.post(
        "/customers",
        json={"full_name": "Freeze Test", "ktp_number": "3175xxxx3333"},
        headers=teller,
    ).json()
    acc = client.post(
        "/accounts", json={"customer_id": cust["id"], "type": "savings"}, headers=teller
    ).json()
    client.post(f"/accounts/{acc['id']}/approve", headers=manager)

    frozen = client.post(f"/accounts/{acc['id']}/freeze", headers=manager)
    assert frozen.status_code == 200 and frozen.json()["status"] == "FROZEN"
    unfrozen = client.post(f"/accounts/{acc['id']}/unfreeze", headers=manager)
    assert unfrozen.status_code == 200 and unfrozen.json()["status"] == "ACTIVE"


def test_br04_ledger_append_only_ditegakkan_db() -> None:
    """BR-04: DATABASE menolak UPDATE/DELETE — bukan cuma aturan aplikasi."""

    async def _seed_minimal() -> None:
        async with SessionLocal() as db:
            result = await db.execute(
                text(
                    """
                    INSERT INTO users (id, username, password_hash, role, is_active, created_at)
                    VALUES (:id, :un, 'x', 'manager', true, now())
                    ON CONFLICT (username) DO NOTHING
                    """
                ),
                {"id": uuid.uuid4(), "un": f"test_ledger_user_{RUN}"},
            )
            del result
            user_id = (
                await db.execute(
                    text("SELECT id FROM users WHERE username = :u"),
                    {"u": f"test_ledger_user_{RUN}"},
                )
            ).scalar_one()
            cust_id = uuid.uuid4()
            await db.execute(
                text(
                    "INSERT INTO customers (id, user_id, full_name, ktp_hash, created_at) "
                    "VALUES (:id, NULL, 'Ledger Test', 'h', now())"
                ),
                {"id": cust_id},
            )
            acc_id = uuid.uuid4()
            await db.execute(
                text(
                    "INSERT INTO accounts "
                    "(id, customer_id, account_number, type, balance, status, created_at) "
                    "VALUES (:id, :cid, :num, 'savings', 0, 'ACTIVE', now())"
                ),
                {"id": acc_id, "cid": cust_id, "num": f"39{uuid.uuid4().hex[:10]}"},
            )
            tx_id = uuid.uuid4()
            await db.execute(
                text(
                    "INSERT INTO transactions (id, type, amount, status, created_by, created_at) "
                    "VALUES (:id, 'adjustment', 100, 'EXECUTED', :uid, now())"
                ),
                {"id": tx_id, "uid": user_id},
            )
            await db.execute(
                text(
                    "INSERT INTO ledger_entries "
                    "(id, transaction_id, account_id, direction, amount, "
                    "balance_after, created_at) "
                    "VALUES (:id, :tid, :aid, 'CREDIT', 100, 100, now())"
                ),
                {"id": uuid.uuid4(), "tid": tx_id, "aid": acc_id},
            )
            await db.commit()

            # BR-04: UPDATE harus melempar exception dari trigger
            with pytest.raises(Exception, match="APPEND-ONLY"):
                await db.execute(text("UPDATE ledger_entries SET amount = 999"))
            await db.rollback()

            with pytest.raises(Exception, match="APPEND-ONLY"):
                await db.execute(text("DELETE FROM ledger_entries"))
            await db.rollback()

    import asyncio

    asyncio.run(_seed_minimal())


def test_br05_ledger_balanced_dan_balance_after() -> None:
    """BR-05: Σ debit = Σ kredit; saldo ter-update; balance_after konsisten."""

    async def _run() -> None:
        async with SessionLocal() as db:
            # rekening A & B dengan saldo awal 1.000.000 (via transaksi adjustment nyata)
            import secrets

            user = User(
                username=f"test_journal_user_{RUN}_{secrets.token_hex(3)}",
                password_hash=hash_password("kata-sandi-123"),
                role=UserRole.MANAGER,
            )
            db.add(user)
            await db.flush()
            customer = Customer(full_name="Journal Test", ktp_hash="h")
            db.add(customer)
            await db.flush()
            accounts = []
            for _ in range(2):
                acc = Account(
                    customer_id=customer.id,
                    account_number="39" + secrets.token_hex(5),
                    type=AccountType.SAVINGS,
                    balance=Decimal("1000000"),
                    status=AccountStatus.ACTIVE,
                )
                db.add(acc)
                accounts.append(acc)
            tx = Transaction(
                type=TransactionType.TRANSFER,
                amount=Decimal("250000"),
                status=TransactionStatus.EXECUTED,
                created_by=user.id,
            )
            db.add(tx)
            await db.flush()

            from app.models.ledger import LedgerDirection as D

            # BR-05 gagal: 100 vs 90
            with pytest.raises(LedgerUnbalancedError):
                await post_journal(
                    db,
                    transaction_id=tx.id,
                    movements=[
                        (accounts[0], D.CREDIT, Decimal("100")),
                        (accounts[1], D.DEBIT, Decimal("90")),
                    ],
                )

            # BR-05 sukses: 250.000 DEBIT dari A → CREDIT ke B
            entries = await post_journal(
                db,
                transaction_id=tx.id,
                movements=[
                    (accounts[0], D.DEBIT, Decimal("250000")),
                    (accounts[1], D.CREDIT, Decimal("250000")),
                ],
            )
            await db.commit()
            assert len(entries) == 2
            assert accounts[0].balance == Decimal("750000")
            assert accounts[1].balance == Decimal("1250000")
            debits = [e for e in entries if e.direction == LedgerDirection.DEBIT]
            assert debits[0].balance_after == Decimal("750000")

    import asyncio

    asyncio.run(_run())
