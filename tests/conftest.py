"""Konfigurasi test: arahkan app ke database terpisah (corebank_test).

Conftest di-import pytest SEBELUM modul test — aman meng-set env yang
membentuk settings (get_settings masih cache kosong saat itu).
"""

import asyncio
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

# Windows: psycopg async butuh SelectorEventLoop (sama dengan app/__init__.py)
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

REPO_ROOT = Path(__file__).resolve().parent.parent

_base_url = os.environ.get(
    "DATABASE_URL", "postgresql+psycopg://corebank:corebank@localhost:5432/corebank"
)
# Ganti nama database → corebank_test (isolasi dari data dev)
TEST_DATABASE_URL = re.sub(r"/([A-Za-z0-9_]+)$", "/corebank_test", _base_url)
os.environ["DATABASE_URL"] = TEST_DATABASE_URL


def _admin_dsn() -> str:
    """DSN ke database 'postgres' — untuk cek koneksi & CREATE DATABASE."""
    return re.sub(r"/([A-Za-z0-9_]+)$", "/postgres", TEST_DATABASE_URL).replace(
        "postgresql+psycopg://", "postgresql://"
    )


def _db_reachable() -> bool:
    try:
        asyncio.run(_ping(_admin_dsn()))
        return True
    except Exception:  # noqa: BLE001
        return False


async def _ping(dsn: str) -> None:
    import psycopg

    conn = await psycopg.AsyncConnection.connect(dsn, connect_timeout=3)
    await conn.close()


def _create_test_db() -> None:
    """Buat database corebank_test kalau belum ada (koneksi ke db 'postgres')."""
    import psycopg

    admin_dsn = _admin_dsn()

    async def _ensure() -> None:
        conn = await psycopg.AsyncConnection.connect(admin_dsn, connect_timeout=3)
        try:
            # CREATE DATABASE tidak boleh di dalam transaksi — psycopg default transaksional
            await conn.set_autocommit(True)
            exists = await conn.execute("SELECT 1 FROM pg_database WHERE datname = 'corebank_test'")
            if await exists.fetchone() is None:
                await conn.execute("CREATE DATABASE corebank_test")
                await conn.commit()
        finally:
            await conn.close()

    asyncio.run(_ensure())


@pytest.fixture(scope="session", autouse=True)
def apply_migrations() -> None:
    """Jalankan alembic upgrade head ke database test — pakai MIGRASI ASLI
    (termasuk trigger append-only), bukan create_all."""
    if not _db_reachable():
        pytest.skip("Postgres tidak terjangkau — jalankan compose dulu atau lewati test integrasi")
    _create_test_db()
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=REPO_ROOT,
        check=True,
        env={**os.environ, "DATABASE_URL": TEST_DATABASE_URL},
    )
