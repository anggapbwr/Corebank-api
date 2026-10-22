"""Reconciliation on-demand — jalankan: python -m app.scripts.reconcile

Exit code: 0 = balance, 1 = ada selisih (langsung bisa dipakai cron / job CI).
"""

import asyncio
import json
import sys

from app.core.db import SessionLocal
from app.services.reconcile import run_reconcile


async def _main() -> int:
    async with SessionLocal() as db:
        report = await run_reconcile(db)
    print(json.dumps(report, indent=2, default=str))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
