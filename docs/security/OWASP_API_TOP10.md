# OWASP API Security Top 10 (2023) — Checklist Fase 9

Status review: **2026-10-22** · Alat: `pip-audit` (0 kerentanan), `gitleaks v8.30.1`
(14 commit, no leaks), review kode + test suite (pytest, coverage 85%).

Legenda: ✅ termitigasi & teruji · ⚠ sebagian (ada backlog) · ➖ tidak berlaku / risiko rendah

| # | Item | Status | Bukti utama |
|---|------|--------|-------------|
| API1 | Broken Object Level Authorization (BOLA) | ✅ | `test_bola_nasabah_hanya_lihat_rekening_sendiri`; query selalu scoped ke objek; RBAC per route (`require_roles`) |
| API2 | Broken Authentication | ✅ | Argon2id + timing-equalizer; JWT access/refresh beda `type` claim; `test_security` (forged/expired/refresh-as-access ditolak); **rate limit login 5 gagal/menit** (`test_rate_limit_login_nfr02` → 429) |
| API3 | Broken Object Property Level Authorization | ✅ | Skema Pydantic eksplisit per endpoint (tulisan rawan mass-assignment tak ada); `UserOut` hanya id/username/role/is_active; hash password & KTP (`ktp_hash`) tidak pernah direspons |
| API4 | Unrestricted Resource Consumption | ⚠ | Rate limit login (per-IP + per-username, jendela 60 dtk); approval matrix membatasi nominal (≤50jt/≤250jt/250jt); NFR-01 teruji 100 RPS stabil. **Backlog:** rate limit global per-endpath & batas ukuran body |
| API5 | Broken Function Level Authorization | ✅ | `test_register_hanya_manager`, `test_auditor_read_only`; `Depends(require_roles(...))` di tiap route protektif; menu UI disembunyikan **dan** dicek di API |
| API6 | Unrestricted Access to Sensitive Business Flows | ✅ | Flow transfer/diveto di layer service (bukan route): approval matrix dari config, state machine, idempotency BR-03, `test_400jt_antre_lalu_eskalasi_ke_manager`, `test_saldo_kurang_gagal_total`, `test_rekening_frozen_ditolak` |
| API7 | Server-Side Request Forgery (SSRF) | ➖ | Satu-satunya panggilan keluar = proxy Alertmanager (`/api/system/alerts`); URL dari config server-side, bukan input user; timeout 3 dtk |
| API8 | Security Misconfiguration | ✅ | Tanpa middleware CORS (same-origin only); container non-root; secret via env/.env (gitignored) + K8s Secret; Trivy CI gate CRITICAL/HIGH; JWT_SECRET default dev diberi peringatan wajib ganti. *Catatan produksi:* `/docs` & `/metrics` sebaiknya dimatikan/dijaringan internal |
| API9 | Improper Inventory Management | ✅ | `/openapi.json` = inventaris mesin; satu versi API; dependensi terpinning + `pip-audit` di CI (gate CD — lihat `ci.yml`); roadmap & CHANGELOG jelas |
| API10 | Unsafe Consumption of APIs | ➖ | Tidak ada integrasi API pihak ketiga yang menerima data asing (auth lokal); outbound hanya Prometheus/Alertmanager (server-to-server, URL config, tervalidasi status-nya) |

## Temuan & tindakan dari security pass ini

1. **Rate limit login (NFR-02) belum ada** — ketemu saat review, lalu
   diimplementasi di `app/api/routes/auth.py` (5 gagal → 429, reset saat login
   sukses). *Pelajaran: implementasi "ada di SRS" ≠ ada di kode — review harus baca kode.*
2. **Gagasan Redis singleton untuk rate limit ternyata rapuh di luar loop uvicorn**
   (connection pool menempel di event loop lama → fail-open diam-diam).
   Solusi: client sekali pakai per operasi + log membawa tipe/detail error.
3. Fixture test lama menulis jurnal **satu sisi** (melanggar BR-05) — ketahuan
   karena reconciliation menolak data fixture-nya sendiri. Diperbaiki
   (`test_fase2_integration.py`) + DB test lama dibuang.

## Celah yang disadari & sengaja ditunda (backlog)

- Rate limit global per-IP untuk semua endpoint (baru `/auth/login`) dan batas
  ukuran request body — belum ada.
- Rate limit **fail-open** saat Redis mati (pilihan sadar: availability menang;
  RedisDown sudah jadi alert Prometheus).
- `/docs`, `/redoc`, `/metrics` terbuka — posture development; wajib ditutup
  atau diisolasi jaringan sebelum produksi nyata.

## Cara menjalankan ulang

```bash
pip-audit                                                  # CVE dependensi
gitleaks detect --source . --redact                        # secret di git history
bash deploy/backup/backup.sh && bash deploy/backup/restore-drill.sh   # DR (NFR-07)
k6 run deploy/load/k6-nfr01.js                             # NFR-01
```
