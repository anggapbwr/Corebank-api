"""Reconciliation job (Fase 9, ROADMAP) — Σ ledger vs saldo rekening.

Invarian yang dicek berkala:
1. BR-05 agregat : Σ amount DEBIT == Σ amount CREDIT (seluruh ledger)
2. Per rekening  : accounts.balance == (Σ CREDIT − Σ DEBIT) rekening itu
   (konvensi arah dari ledger service: CREDIT masuk, DEBIT keluar)

Hasilnya menjadi:
- gauge Prometheus `corebank_ledger_recon_mismatch` → alert rule
  LedgerReconMismatch (deploy/monitoring/alert_rules.yml) → Telegram
- laporan dict untuk CLI `python -m app.scripts.reconcile` (exit 1 saat selisih)
- log ERROR JSON (field detail) supaya terjaring Loki

Sifat: READ-ONLY — hanya SELECT + set gauge; tidak pernah menyentuh data.
"""

import asyncio
import logging
from decimal import Decimal

from prometheus_client import Gauge
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import SessionLocal

logger = logging.getLogger("corebank.reconcile")

RECON_MISMATCH = Gauge(
    "corebank_ledger_recon_mismatch",
    "1 = reconciliation menemukan selisih ledger vs saldo rekening, 0 = balance",
)
RECON_MISMATCHED_ACCOUNTS = Gauge(
    "corebank_ledger_recon_mismatched_accounts",
    "Jumlah rekening dengan selisih saldo pada reconciliation terakhir",
)
RECON_CHECKED_ACCOUNTS = Gauge(
    "corebank_recon_checked_accounts",
    "Jumlah rekening yang diperiksa pada reconciliation terakhir",
)

_GLOBAL_SQL = text(
    """
    SELECT COALESCE(SUM(amount) FILTER (WHERE direction = 'DEBIT'), 0)  AS debit,
           COALESCE(SUM(amount) FILTER (WHERE direction = 'CREDIT'), 0) AS credit,
           COUNT(*) AS entries
      FROM ledger_entries
    """
)

# HAVING memakai ekspresi agregat yang sama → hanya baris yang selisih yang keluar
_PER_ACCOUNT_SQL = text(
    """
    SELECT a.account_number, a.balance,
           COALESCE(SUM(l.amount) FILTER (WHERE l.direction = 'CREDIT'), 0)
         - COALESCE(SUM(l.amount) FILTER (WHERE l.direction = 'DEBIT'), 0) AS expected
      FROM accounts a
      LEFT JOIN ledger_entries l ON l.account_id = a.id
     GROUP BY a.id, a.account_number, a.balance
    HAVING a.balance IS DISTINCT FROM (
           COALESCE(SUM(l.amount) FILTER (WHERE l.direction = 'CREDIT'), 0)
         - COALESCE(SUM(l.amount) FILTER (WHERE l.direction = 'DEBIT'), 0))
    """
)


async def run_reconcile(db: AsyncSession) -> dict:
    """Satu putaran reconciliation → laporan + gauge. Tidak menulis data apa pun."""
    row = (await db.execute(_GLOBAL_SQL)).one()
    debit, credit = Decimal(row.debit), Decimal(row.credit)
    imbalance = debit != credit

    mismatched = [
        {
            "account_number": r.account_number,
            "balance": str(r.balance),
            "expected": str(r.expected),
            "diff": str(Decimal(r.balance) - Decimal(r.expected)),
        }
        for r in (await db.execute(_PER_ACCOUNT_SQL)).all()
    ]
    checked = int((await db.execute(text("SELECT COUNT(*) FROM accounts"))).scalar_one())

    ok = not imbalance and not mismatched
    RECON_MISMATCH.set(0 if ok else 1)
    RECON_MISMATCHED_ACCOUNTS.set(len(mismatched))
    RECON_CHECKED_ACCOUNTS.set(checked)

    report = {
        "ok": ok,
        "imbalance": imbalance,
        "debit_total": str(debit),
        "credit_total": str(credit),
        "entries": int(row.entries),
        "checked_accounts": checked,
        "mismatched_accounts": mismatched,
    }
    if ok:
        logger.info(
            "reconciliation balance",
            extra={"entries": report["entries"], "accounts": checked},
        )
    else:
        logger.error(
            "reconciliation SELISIH — ledger vs saldo rekening tidak cocok",
            extra={
                "debit_total": report["debit_total"],
                "credit_total": report["credit_total"],
                "imbalance": imbalance,
                "mismatched_accounts": mismatched,
            },
        )
    return report


async def reconcile_loop(interval: int = 900) -> None:
    """Background task seperti monitor_loop: tak boleh mati walau sekali gagal."""
    logger.info("reconcile loop dimulai", extra={"interval_s": interval})
    while True:
        try:
            async with SessionLocal() as db:
                await run_reconcile(db)
        except asyncio.CancelledError:
            logger.info("reconcile loop dihentikan")
            raise
        except Exception:  # noqa: BLE001 — loop observability tidak boleh crash
            logger.exception("reconcile loop error")
        await asyncio.sleep(interval)
