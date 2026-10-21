"""Loop factory kustom uvicorn — psycopg async menolak ProactorEventLoop (Windows).

Konteks (Fase 6, verifikasi lokal Windows):
- `app/__init__.py` memaksa SelectorEventLoop lewat set_event_loop_policy — tapi
  uvicorn >= 0.36 tidak lagi memakai policy asyncio; ia memilih loop lewat
  loop_factory, dan `auto` di Windows = ProactorEventLoop.
- psycopg (mode async) langsung menolak Proactor → InterfaceError →
  monitor DB/Redis-antrean mati dan SEMUA rute berbasis SQLAlchemy gagal
  saat uvicorn dijalankan lokal di Windows (di container Linux tidak kena).

Pemakaian (lokal Windows saja — Linux default sudah selector, tanpa flag):

    uvicorn app.main:app --loop app.core.loops:selector_loop_factory
"""

import asyncio


def selector_loop_factory(**_kwargs: object) -> asyncio.AbstractEventLoop:
    """Dipanggil uvicorn tanpa argumen → instance SelectorEventLoop siap pakai."""
    return asyncio.SelectorEventLoop()
