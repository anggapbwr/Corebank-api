"""Loop factory uvicorn (lihat app/core/loops.py — psycopg vs ProactorEventLoop)."""

from app.core.loops import selector_loop_factory


def test_selector_loop_factory_menghasilkan_selector_event_loop() -> None:
    loop = selector_loop_factory()
    try:
        # Windows: _WindowsSelectorEventLoop; Linux: SelectorEventLoop — intinya bukan Proactor
        nama = type(loop).__name__
        assert "Selector" in nama and "Proactor" not in nama
    finally:
        loop.close()
