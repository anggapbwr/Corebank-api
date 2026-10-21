# Conventions — Aturan Kolaborasi & Kode

---

## Git

- **Branch:** `main` (stabil) + `feat/*`, `fix/*`, `docs/*`
- **Commits:** Conventional Commits — `feat(transfer): add idempotency check`
  - scope: `auth | accounts | transfer | ledger | approval | ci | k8s | docs`
- **Merge:** PR → CI hijau → squash merge. Tidak ada push langsung ke `main`
- **Tag:** `v0.1.0` per rilis fase

## Python & Kode

- Formatter/linter: **ruff** (format + lint dalam satu tool)
- Type hints wajib di function signature; Pydantic untuk semua boundary data
- Layer rule: route → service → repository/model. Route tidak boleh akses DB langsung
- Error handling: exception domain (`InsufficientBalanceError`) di-translate ke HTTP di route
- Tidak ada `print()` — pakai structured logger

## Database

- Semua perubahan schema lewat **Alembic migration** — tidak ada `create_all` di produksi
- Amount: `NUMERIC(19,4)`, tidak pernah float
- Tabel finansial (ledger_entries, audit_logs): append-only, ditegakkan DB trigger
- Migration harus reversible (ada `down_revision` yang jalan)

## Config & Secrets

- Settings via pydantic-settings, dibaca dari env
- `.env` = lokal saja, di-gitignore; `.env.example` di-commit (tanpa nilai sensitif)
- Kunci API/token tidak pernah di kode, log, atau Git (cek: `hermes` secret scan / gitleaks di CI)

## Testing

- Pyramid: banyak unit, sedikit integration, tipis E2E
- Integration test pakai **testcontainers** (postgres+redis asli, bukan mock)
- Setiap business rule (BR-xx) wajib punya test yang menyebut ID-nya:
  ```python
  def test_transfer_gagal_saldo_cukup_rollback():
      """BR-02: transfer atomik — gagal = tanpa perubahan"""
  ```
- Coverage gate 80% di CI. **Pitfall:** core default coverage (pytrace) memutus
  tracing di greenlet SQLAlchemy async → angka coverage palsu turun ke ~73%.
  Wajib `COVERAGE_CORE=sysmon` (workflow CI sudah set; lokal py3.14 otomatis).

## API Design

- RESTful: noun, plural — `/transfers`, `/accounts/{id}/entries`
- Status code jujur: 201 create, 202 (diterima, diproses async), 409 (idempotency conflict), 422 (validation)
- Semua response error punya `detail` + `correlation_id`
- Breaking change → `/v2` prefix (v1 tetap jalan)

## Logging

- JSON structured: `ts, level, correlation_id, actor, action, object`
- correlation_id di-generate di middleware, dibawa sampai consumer MQ
- Finansial event (transfer executed/rejected) selalu `INFO` — jangan `DEBUG`

## Dokumentasi

- `docs/` adalah kontrak — ubah desain = update dokumen di PR yang sama
- README hanya overview; detail di docs/
