# Roadmap — 8 Fase

Urutan pengerjaan dengan acceptance criteria (AC) per fase. **Tidak lanjut ke fase berikut sebelum AC hijau.**

---

## Fase 1 — Foundation & Docker
**Goal:** lingkungan dev reproduktibel.
- Inisialisasi repo Git + Python project (pyproject.toml / ruff / pytest)
- `docker-compose.yml`: app + postgres + redis + rabbitmq
- `Dockerfile` multi-stage (build deps di layer terpisah dari runtime)
- FastAPI skeleton: `/healthz`, `/readyz`, config dari env (pydantic-settings)
- `.env.example`, `.gitignore`, pre-commit hook dasar
- **AC:** `docker compose up` → `/healthz` return 200; container restart tidak menghilangkan data (volume)

## Fase 2 — Auth + Accounts + Ledger
**Goal:** inti domain.
- Registrasi user internal + login JWT (access+refresh)
- RBAC dependency dasar
- Model SQLAlchemy + Alembic migration pertama (users, customers, accounts, transactions, ledger_entries)
- Account CRUD (create → PENDING_APPROVAL, freeze/unfreeze)
- LedgerService: double-entry writer, balance check, append-only
- **AC:** test buka rekening → approve manual → saldo muncul; ledger balanced (BR-05); UPDATE/DELETE ke ledger ditolak DB (BR-04)

## Fase 3 — Transfer + Idempotency + Approval
**Goal:** alur uang lengkap.
- TransferService: atomic transfer (BR-02), cek saldo (BR-01), freeze check (BR-06)
- Idempotency-Key: Redis SETNX + UNIQUE column fallback (BR-03)
- ApprovalService: work queue, approve/reject/escalate (state machine §WORKFLOWS)
- Audit trail service — semua aksi penting tercatat
- **AC:** test suite membuktikan BR-01..BR-10; retry dengan key sama → transaksi tunggal; eskalasi 400jt mencapai manager

## Fase 4 — Testing & CI
**Goal:** kualitas otomatis.
- Test pyramid: unit (services) + integration (testcontainers: postgres+redis sungguhan)
- Coverage minimum 80% (gate di CI)
- GitHub Actions: lint (ruff) → test → build image → Trivy scan → push ke GHCR
- **AC:** PR hijau otomatis; merge ke main → image tersedia di GHCR ber-tag SHA

## Fase 5 — Observability
**Goal:** bisa melihat ke dalam.
- Structured JSON logging + correlation ID middleware
- Prometheus metrics (latency, transfer success/fail, queue depth)
- Grafana dashboard + Loki
- Alertmanager → bot Telegram (error rate, queue backlog)
- **AC:** simulasi beban → dashboard menunjukkan grafik; matikan postgres → alert masuk Telegram

## Fase 6 — Kubernetes (kind)
**Goal:** infra as code penuh.
- kind cluster lokal
- Manifests: Deployment, Service, Ingress, ConfigMap, Secret, HPA (opsional)
- Liveness & readiness probe (bedakan: "hidup" vs "siap terima trafik")
- **AC:** `kubectl apply` → API jalan di cluster; matikan 1 pod → traffic pindah tanpa error (self-healing)

## Fase 7 — CD & Zero-Downtime Deploy
**Goal:** deploy seperti tim produksi.
- Pipeline CD: image baru → rolling update → smoke test otomatis → rollback kalau gagal
- Blue-green atau canary (pilih salah satu, disimulasikan)
- **AC:** deploy versi baru saat load test jalan → tidak ada request gagal (zero downtime)

## Fase 8 — Hardening & DR
**Goal:** ketahanan (bagian " membosankan" yang membedakan amatir vs profesional).
- Backup harian (pg_dump ke volume terpisah) + **restore drill** (RPO/RTO NFR-07)
- Reconciliation job: Σ ledger vs account.balance → alert kalau mismatch
- Load test k6 → capai NFR-01 (100 RPS, p95 < 2 detik)
- Security pass: OWASP API Top 10 review, pip-audit, secret scan
- **AC:** backup restore terverifikasi; load test lulus threshold; checklist OWASP terisi

---

## Setelah Fase 8 (opsional / lanjutan)
- Dashboard monitoring per role (workbench) di atas data yang sudah ada
- HashiCorp Vault untuk secrets
- Terraform ke cloud free-tier (AWS/GCP) — deploy cluster beneran
- Microsplit: ledger service terpisah kalau ada alasan nyata
