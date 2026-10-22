"""Test Fase 9 — reconciliation Σ ledger vs saldo rekening (BR-04/BR-05)."""

import asyncio
import uuid
from decimal import Decimal

from app.core.db import SessionLocal
from app.models.account import Account, AccountStatus, AccountType
from app.models.customer import Customer
from app.models.ledger import LedgerDirection, LedgerEntry
from app.models.transaction import Transaction, TransactionStatus, TransactionType
from app.models.user import User, UserRole
from app.scripts import reconcile as reconcile_cli
from app.services.reconcile import RECON_MISMATCH, run_reconcile


def test_database_test_balance() -> None:
    """DB test (berisi data transfer dari test Fase 2/3) harus reconciliation balance."""

    async def _run() -> dict:
        async with SessionLocal() as db:
            return await run_reconcile(db)

    report = asyncio.run(_run())
    assert report["ok"] is True, report
    assert report["imbalance"] is False
    assert report["mismatched_accounts"] == []
    assert RECON_MISMATCH._value.get() == 0


def test_deteksi_selisih_lalu_reset() -> None:
    """Selisih global + per-rekening terdeteksi (gauge=1), hilang setelah data pulih.

    Data rusak dibuat di SAVEPOINT dan di-rollback — DB test tidak pernah
    tercemar (ledger append-only juga mustahil dibersihkan dengan DELETE).
    """

    async def _run() -> tuple[dict, dict, str, float, float]:
        async with SessionLocal() as db:
            sp = await db.begin_nested()
            user = User(
                username=f"recon-{uuid.uuid4().hex[:8]}",
                password_hash="x",
                role=UserRole.AUDITOR,
            )
            customer = Customer(full_name="Recon Test", ktp_hash="0" * 64)
            db.add_all([user, customer])
            await db.flush()
            account = Account(
                customer_id=customer.id,
                account_number=f"RC{uuid.uuid4().hex[:10]}",
                type=AccountType.SAVINGS,
                balance=Decimal("999"),  # sengaja salah: ledger hanya bilang 70
                status=AccountStatus.ACTIVE,
            )
            db.add(account)
            await db.flush()
            tx = Transaction(
                type=TransactionType.ADJUSTMENT,
                source_account_id=account.id,
                amount=Decimal("100"),
                status=TransactionStatus.EXECUTED,
                created_by=user.id,
            )
            db.add(tx)
            await db.flush()
            # Dua baris ledger: CREDIT 100 & DEBIT 30 → Σ debit(30) ≠ Σ kredit(100)
            db.add_all(
                [
                    LedgerEntry(
                        transaction_id=tx.id,
                        account_id=account.id,
                        direction=LedgerDirection.CREDIT,
                        amount=Decimal("100"),
                        balance_after=Decimal("100"),
                    ),
                    LedgerEntry(
                        transaction_id=tx.id,
                        account_id=account.id,
                        direction=LedgerDirection.DEBIT,
                        amount=Decimal("30"),
                        balance_after=Decimal("70"),
                    ),
                ]
            )
            await db.flush()

            bad = await run_reconcile(db)
            number = account.account_number
            # gauge dipakai ulang run berikutnya → tangkap nilainya di sini
            gauge_bad = RECON_MISMATCH._value.get()
            await sp.rollback()  # semua data rusak dibuang di sini

            good = await run_reconcile(db)
            gauge_good = RECON_MISMATCH._value.get()
            return bad, good, number, gauge_bad, gauge_good

    bad, good, number, gauge_bad, gauge_good = asyncio.run(_run())

    # 1) selisih terdeteksi: imbalance global + rekening ketahuan + gauge menyala
    assert bad["ok"] is False
    assert bad["imbalance"] is True
    # Σ global memuat seluruh data test lain (yang konsisten) — yang diassert
    # jurnal rusak: kredit 100 vs debit 30 → selisih +70
    assert Decimal(bad["credit_total"]) - Decimal(bad["debit_total"]) == Decimal("70")
    assert [m["account_number"] for m in bad["mismatched_accounts"]] == [number]
    assert Decimal(bad["mismatched_accounts"][0]["diff"]) == Decimal("929")  # 999 − 70
    assert gauge_bad == 1

    # 2) setelah data pulih (rollback) → balance lagi, gauge kembali 0
    assert good["ok"] is True
    assert gauge_good == 0


def test_cli_exit_code() -> None:
    """CLI: 0 = balance, 1 = selisih (untuk cron/job)."""
    assert asyncio.run(reconcile_cli._main()) == 0
