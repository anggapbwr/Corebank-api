"""CoreBank API.

Windows ONLY: psycopg (async) tidak bisa berjalan di ProactorEventLoop bawaan
Windows — paksa SelectorEventLoop SEBELUM event loop apapun dibuat
(uvicorn, alembic, pytest asyncio.run). Dijalankan di sini supaya semua
entrypoint (app.main, migrations/env.py, tests) otomatis kena.
Linux (container produksi/K8s) tidak terpengaruh — policy default sudah selector.
"""

import asyncio
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
