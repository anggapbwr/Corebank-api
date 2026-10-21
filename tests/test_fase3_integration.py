"""Test integrasi Fase 3 — transfer, idempotency, approval workflow, audit.

Membuktikan acceptance criteria Fase 3:
- BR-01 saldo cukup, BR-02 atomik (gagal = tanpa perubahan)
- BR-03 idempotency: key sama → transaksi tunggal; payload beda → 409
- BR-06 rekening FROZEN ditolak
- BR-07 approval matrix: ≤50jt langsung; ≤250jt supervisor; >250jt eskalasi manager
- BR-08 reject wajib alasan
- BR-09 auditor read-only
- BR-10 matrix_snapshot tersimpan di approval request
"""

import asyncio
import uuid
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.core.db import SessionLocal
from app.core.security import hash_password
from app.main import app
from app.models.account import Account
from app.models.user import User, UserRole

client = TestClient(app)
RUN = uuid.uuid4().hex[:8]

PASSWORD = "kata-sandi-123"
ALL_ROLES = [r for r in UserRole]


def _login(username: str) -> dict:
    resp = client.post("/auth/login", json={"username": username, "password": PASSWORD})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.fixture(scope="module")
def seeded() -> dict:
    """User untuk semua role + cek Redis hidup."""

    async def _seed() -> None:
        async with SessionLocal() as db:
            for role in ALL_ROLES:
                name = f"t3_{role.value}_{RUN}"
                db.add(User(username=name, password_hash=hash_password(PASSWORD), role=role))
            await db.commit()

    asyncio.run(_seed())

    # Redis wajib hidup untuk test idempotency — kalau mati, skip (compose-nya dulu)
    async def _ping_redis() -> bool:
        from app.core.redis import get_redis

        try:
            return bool(await get_redis().ping())
        except Exception:  # noqa: BLE001
            return False

    if not asyncio.run(_ping_redis()):
        pytest.skip("Redis tidak terjangkau — jalankan compose dulu")
    return {role: f"t3_{role.value}_{RUN}" for role in ALL_ROLES}


_counter = {"n": 0}


_vault_number = f"99{RUN}"  # akun suspense khusus run ini (uang awal dari "luar sistem")


async def _fund(account_id: uuid.UUID, amount: Decimal, username: str) -> None:
    """Opening balance: dana awal masuk lewat akun SUSPENSE — entry TETAP seimbang
    (DEBIT suspense + CREDIT nasabah), bukan satu baris.

    Di produksi, saldo awal datang dari data migration — makanya di sini pakai
    SQL mentah: 'mencetak saldo awal' memang di luar alur transfer bisnis,
    tapi catatan ledger-nya tetap double-entry dan append-only.
    """
    async with SessionLocal() as db:
        manager_user = (
            await db.execute(select(User).where(User.username == username))
        ).scalar_one()

        vault = (
            await db.execute(
                text("SELECT id, balance FROM accounts WHERE account_number = :n"),
                {"n": _vault_number},
            )
        ).first()
        if vault is None:
            vault_id, vault_cust, vault_bal = uuid.uuid4(), uuid.uuid4(), Decimal("0")
            await db.execute(
                text(
                    "INSERT INTO customers (id, user_id, full_name, ktp_hash, created_at) "
                    "VALUES (:id, NULL, 'System Suspense', 'system', now())"
                ),
                {"id": vault_cust},
            )
            await db.execute(
                text(
                    "INSERT INTO accounts (id, customer_id, account_number, type, balance, "
                    "status, created_at) VALUES (:id, :cid, :num, 'savings', 0, 'ACTIVE', now())"
                ),
                {"id": vault_id, "cid": vault_cust, "num": _vault_number},
            )
        else:
            vault_id, vault_bal = vault[0], Decimal(vault[1])

        cust_bal = Decimal(
            (
                await db.execute(
                    text("SELECT balance FROM accounts WHERE id = :i"), {"i": account_id}
                )
            ).scalar_one()
        )

        tx_id = uuid.uuid4()
        await db.execute(
            text(
                "INSERT INTO transactions (id, type, source_account_id, dest_account_id, amount, "
                "status, created_by, executed_at, created_at) "
                "VALUES (:id, 'adjustment', :src, :dst, :amt, 'EXECUTED', :uid, now(), now())"
            ),
            {
                "id": tx_id,
                "src": vault_id,
                "dst": account_id,
                "amt": amount,
                "uid": manager_user.id,
            },
        )
        # DEBIT vault (saldo suspense turun, boleh negatif = uang dari luar)
        await db.execute(
            text(
                "INSERT INTO ledger_entries (id, transaction_id, account_id, direction, amount, "
                "balance_after, created_at) VALUES (:id, :tid, :aid, 'DEBIT', :amt, :bal, now())"
            ),
            {
                "id": uuid.uuid4(),
                "tid": tx_id,
                "aid": vault_id,
                "amt": amount,
                "bal": vault_bal - amount,
            },
        )
        # CREDIT nasabah (saldo naik)
        await db.execute(
            text(
                "INSERT INTO ledger_entries (id, transaction_id, account_id, direction, amount, "
                "balance_after, created_at) VALUES (:id, :tid, :aid, 'CREDIT', :amt, :bal, now())"
            ),
            {
                "id": uuid.uuid4(),
                "tid": tx_id,
                "aid": account_id,
                "amt": amount,
                "bal": cust_bal + amount,
            },
        )
        await db.execute(
            text("UPDATE accounts SET balance = balance + :amt WHERE id = :i"),
            {"amt": amount, "i": account_id},
        )
        await db.execute(
            text("UPDATE accounts SET balance = balance - :amt WHERE id = :i"),
            {"amt": amount, "i": vault_id},
        )
        await db.commit()


def _make_funded_account(username: str, amount: Decimal) -> Account:
    """Rekening ACTIVE berisi `amount` — dibuat lewat API + dana via adjustment."""
    teller = _login(username)
    cust = client.post(
        "/customers",
        json={"full_name": f"Nasabah {RUN}", "ktp_number": f"3175{uuid.uuid4().hex[:10]}"},
        headers=teller,
    ).json()
    acc = client.post(
        "/accounts", json={"customer_id": cust["id"], "type": "savings"}, headers=teller
    ).json()
    supervisor = _login(f"t3_{UserRole.SUPERVISOR.value}_{RUN}")
    client.post(f"/accounts/{acc['id']}/approve", headers=supervisor)

    account_id = uuid.UUID(acc["id"])
    if amount > 0:
        asyncio.run(_fund(account_id, amount, f"t3_{UserRole.MANAGER.value}_{RUN}"))
    return asyncio.run(_get_account(account_id))


async def _get_account(account_id) -> Account:  # noqa: ANN001
    async with SessionLocal() as db:
        return await db.get(Account, account_id)


def _transfer(headers: dict, source, dest, amount: str, key: str | None = None):
    _counter["n"] += 1
    return client.post(
        "/transfers",
        json={"source_account_id": str(source), "dest_account_id": str(dest), "amount": amount},
        headers={
            **headers,
            "Idempotency-Key": key or f"key-{RUN}-{_counter['n']}-{uuid.uuid4().hex[:8]}",
        },
    )


async def _balances(source_id, dest_id) -> tuple[Decimal, Decimal]:  # noqa: ANN001
    src = await _get_account(source_id)
    dst = await _get_account(dest_id)
    return Decimal(src.balance), Decimal(dst.balance)


# ---------------------------------------------------------------- BR-01/02


def test_transfer_langsung_dieksekusi_balanced(seeded: dict) -> None:
    """≤50jt → 201 EXECUTED; saldo pindah; ledger 2 entry seimbang (BR-01/02/05)."""
    teller = _login(seeded[UserRole.TELLER])
    src = _make_funded_account(seeded[UserRole.TELLER], Decimal("1000000"))
    dst = _make_funded_account(seeded[UserRole.TELLER], Decimal("1000000"))
    src_id, dst_id = src.id, dst.id

    resp = _transfer(teller, src_id, dst_id, "500000")
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "EXECUTED" and body["duplicate"] is False

    src_bal, dst_bal = asyncio.run(_balances(src_id, dst_id))
    assert src_bal == Decimal("500000")
    assert dst_bal == Decimal("1500000")

    # ledger entries sumber & tujuan — masing-masing 1 baris, arah benar
    auditor = _login(seeded[UserRole.AUDITOR])
    src_entries = client.get(f"/accounts/{src_id}/entries", headers=auditor).json()
    dst_entries = client.get(f"/accounts/{dst_id}/entries", headers=auditor).json()
    debits = [
        e
        for e in src_entries
        if e["direction"] == "DEBIT" and e["transaction_id"] == body["transaction_id"]
    ]
    credits = [
        e
        for e in dst_entries
        if e["direction"] == "CREDIT" and e["transaction_id"] == body["transaction_id"]
    ]
    assert len(debits) == 1 and len(credits) == 1
    assert Decimal(debits[0]["amount"]) == Decimal(credits[0]["amount"]) == Decimal("500000")


def test_saldo_kurang_gagal_total(seeded: dict) -> None:
    """BR-01/BR-02: saldo tidak cukup → 422, TIDAK ada perubahan apa pun."""
    teller = _login(seeded[UserRole.TELLER])
    src = _make_funded_account(seeded[UserRole.TELLER], Decimal("100000"))
    dst = _make_funded_account(seeded[UserRole.TELLER], Decimal("0"))

    resp = _transfer(teller, src.id, dst.id, "500000")
    assert resp.status_code == 422, resp.text

    src_bal, dst_bal = asyncio.run(_balances(src.id, dst.id))
    assert src_bal == Decimal("100000")  # tidak berubah
    assert dst_bal == Decimal("0")

    auditor = _login(seeded[UserRole.AUDITOR])
    entries = client.get(f"/accounts/{src.id}/entries", headers=auditor).json()
    # hanya baris funding awal (adjustment), TIDAK ada jejak transfer gagal
    assert all(e["direction"] == "CREDIT" for e in entries)


# ---------------------------------------------------------------- BR-03


def test_idempotency_key_sama_transaksi_tunggal(seeded: dict) -> None:
    """BR-03: retry dengan key sama → respons identik, transaksi tetap satu."""
    teller = _login(seeded[UserRole.TELLER])
    src = _make_funded_account(seeded[UserRole.TELLER], Decimal("1000000"))
    dst = _make_funded_account(seeded[UserRole.TELLER], Decimal("1000000"))
    key = f"idem-{RUN}-{uuid.uuid4().hex[:10]}"

    first = _transfer(teller, src.id, dst.id, "250000", key=key)
    assert first.status_code == 201, first.text

    second = _transfer(teller, src.id, dst.id, "250000", key=key)
    assert second.status_code == 200, second.text
    assert second.json()["duplicate"] is True
    assert second.json()["transaction_id"] == first.json()["transaction_id"]

    # bukti di DB: hanya SATU transaksi dengan key ini
    async def _count() -> int:
        async with SessionLocal() as db:
            result = await db.execute(
                text("SELECT count(*) FROM transactions WHERE idempotency_key = :k"), {"k": key}
            )
            return int(result.scalar_one())

    assert asyncio.run(_count()) == 1
    # saldo hanya berkurang sekali
    src_bal, _ = asyncio.run(_balances(src.id, dst.id))
    assert src_bal == Decimal("750000")


def test_idempotency_payload_berbeda_409(seeded: dict) -> None:
    """BR-03: key sama untuk payload BERBEDA → 409 (client salah pakai key)."""
    teller = _login(seeded[UserRole.TELLER])
    src = _make_funded_account(seeded[UserRole.TELLER], Decimal("1000000"))
    dst = _make_funded_account(seeded[UserRole.TELLER], Decimal("1000000"))
    key = f"idem-{RUN}-{uuid.uuid4().hex[:10]}"

    assert _transfer(teller, src.id, dst.id, "100000", key=key).status_code == 201
    conflict = _transfer(teller, src.id, dst.id, "200000", key=key)  # amount beda
    assert conflict.status_code == 409, conflict.text


# ---------------------------------------------------------------- BR-06


def test_rekening_frozen_ditolak(seeded: dict) -> None:
    """BR-06: rekening FROZEN tidak bisa kirim dana → 409."""
    teller = _login(seeded[UserRole.TELLER])
    manager = _login(seeded[UserRole.MANAGER])
    src = _make_funded_account(seeded[UserRole.TELLER], Decimal("1000000"))
    dst = _make_funded_account(seeded[UserRole.TELLER], Decimal("1000000"))

    frozen = client.post(f"/accounts/{src.id}/freeze", headers=manager)
    assert frozen.status_code == 200

    resp = _transfer(teller, src.id, dst.id, "100000")
    assert resp.status_code == 409, resp.text


# ---------------------------------------------------------------- BR-07/10


def test_400jt_antre_lalu_eskalasi_ke_manager(seeded: dict) -> None:
    """BR-07: >50jt masuk antrean; >250jt supervisor approve → ESKALATE → manager eksekusi.
    BR-10: matrix_snapshot terbaca di approval request."""
    teller = _login(seeded[UserRole.TELLER])
    supervisor = _login(seeded[UserRole.SUPERVISOR])
    manager = _login(seeded[UserRole.MANAGER])
    src = _make_funded_account(seeded[UserRole.TELLER], Decimal("500000000"))
    dst = _make_funded_account(seeded[UserRole.TELLER], Decimal("0"))

    # 1) transfer 400jt → 202, masuk antrean supervisor
    resp = _transfer(teller, src.id, dst.id, "400000000")
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["status"] == "PENDING_APPROVAL"
    approval_id = body["approval_id"]

    # 2) antrean supervisor berisi item ini
    queue = client.get("/approvals", headers=supervisor).json()
    assert any(a["id"] == approval_id for a in queue)
    # BR-10: snapshot limit tersimpan
    item = next(a for a in queue if a["id"] == approval_id)
    assert item["matrix_snapshot"]["supervisor_max"] == "250000000"

    # 3) supervisor approve → 400jt > 250jt → ESKALATED, bukan dieksekusi
    esc = client.post(f"/approvals/{approval_id}/approve", headers=supervisor)
    assert esc.status_code == 200, esc.text
    assert esc.json()["status"] == "ESCALATED"
    assert esc.json()["transaction_status"] == "PENDING_APPROVAL"  # belum pindah dana
    # supervisor tidak bisa putuskan lagi setelah eskalasi → 403
    assert client.post(f"/approvals/{approval_id}/approve", headers=supervisor).status_code == 403

    # 4) masuk antrean manager → approve → EKSEKUSI
    mgr_queue = client.get("/approvals", headers=manager).json()
    assert any(a["id"] == approval_id and a["current_role"] == "manager" for a in mgr_queue)

    done = client.post(f"/approvals/{approval_id}/approve", headers=manager)
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "APPROVED"
    assert done.json()["transaction_status"] == "EXECUTED"

    # 5) dana benar-benar pindah
    src_bal, dst_bal = asyncio.run(_balances(src.id, dst.id))
    assert src_bal == Decimal("100000000")
    assert dst_bal == Decimal("400000000")


def test_transfer_100jt_supervisor_cukup(seeded: dict) -> None:
    """100jt (antara 50jt–250jt) → supervisor approve langsung eksekusi, tanpa eskalasi."""
    teller = _login(seeded[UserRole.TELLER])
    supervisor = _login(seeded[UserRole.SUPERVISOR])
    src = _make_funded_account(seeded[UserRole.TELLER], Decimal("300000000"))
    dst = _make_funded_account(seeded[UserRole.TELLER], Decimal("0"))

    resp = _transfer(teller, src.id, dst.id, "100000000")
    assert resp.status_code == 202, resp.text
    approval_id = resp.json()["approval_id"]

    done = client.post(f"/approvals/{approval_id}/approve", headers=supervisor)
    assert done.status_code == 200, done.text
    assert done.json()["transaction_status"] == "EXECUTED"


# ---------------------------------------------------------------- BR-08


def test_reject_wajib_alasan(seeded: dict) -> None:
    """BR-08: reject tanpa alasan → 422; dengan alasan → REJECTED + transaksi REJECTED."""
    teller = _login(seeded[UserRole.TELLER])
    supervisor = _login(seeded[UserRole.SUPERVISOR])
    src = _make_funded_account(seeded[UserRole.TELLER], Decimal("500000000"))
    dst = _make_funded_account(seeded[UserRole.TELLER], Decimal("0"))

    resp = _transfer(teller, src.id, dst.id, "60000000")
    assert resp.status_code == 202, resp.text
    approval_id = resp.json()["approval_id"]

    # tanpa alasan → 422
    no_reason = client.post(f"/approvals/{approval_id}/reject", json={}, headers=supervisor)
    assert no_reason.status_code == 422, no_reason.text

    # dengan alasan → REJECTED
    rejected = client.post(
        f"/approvals/{approval_id}/reject",
        json={"reason": "Dokumen tidak lengkap"},
        headers=supervisor,
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["status"] == "REJECTED"
    assert rejected.json()["transaction_status"] == "REJECTED"

    # putuskan ulang → 409 (sudah final)
    assert client.post(f"/approvals/{approval_id}/approve", headers=supervisor).status_code == 409


# ---------------------------------------------------------------- BR-09


def test_auditor_read_only(seeded: dict) -> None:
    """BR-09: auditor tidak menulis transfer, tapi bisa baca audit trail."""
    auditor = _login(seeded[UserRole.AUDITOR])
    teller = _login(seeded[UserRole.TELLER])
    src = _make_funded_account(seeded[UserRole.TELLER], Decimal("1000000"))

    # auditor menulis → 403
    resp = client.post(
        "/transfers",
        json={"source_account_id": str(src.id), "dest_account_id": str(src.id), "amount": "1000"},
        headers={**auditor, "Idempotency-Key": f"aud-{RUN}-{uuid.uuid4().hex[:10]}"},
    )
    assert resp.status_code == 403, resp.text

    # auditor baca /audit/logs → 200 dan berisi aksi transfer nyata
    logs = client.get("/audit/logs", headers=auditor)
    assert logs.status_code == 200
    actions = {entry["action"] for entry in logs.json()}
    assert "TRANSFER_EXECUTED" in actions

    # teller (bukan role pembaca) → 403
    assert client.get("/audit/logs", headers=teller).status_code == 403
