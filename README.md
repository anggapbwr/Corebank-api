# CoreBank API

[![CI](https://github.com/anggapbwr/Corebank-api/actions/workflows/ci.yml/badge.svg)](https://github.com/anggapbwr/Corebank-api/actions/workflows/ci.yml)

Mini core banking API — project belajar **DevOps + API** dengan domain perbankan yang real:
double-entry ledger, idempotency, RBAC, approval workflow, CI/CD, Kubernetes, observability.

> Bukan tutorial CRUD. Setiap fitur lahir dari aturan bisnis perbankan yang nyata.

---

## ✨ Fitur

| | Status | Keterangan |
|---|---|---|
| FastAPI + config via env | ✅ | pydantic-settings; `/healthz` (liveness) + `/readyz` (readiness) |
| Docker Compose stack | ✅ | app + PostgreSQL 16 + Redis 7 + RabbitMQ (volumes, healthchecks) |
| Auth JWT + RBAC | ✅ | access+refresh token, argon2, 7 role; register khusus manager |
| Accounts + double-entry ledger | ✅ | Alembic migration; trigger DB append-only (BR-04); balance check (BR-01/05) |
| Transfer atomic + idempotency | ✅ | Redis SETNX + UNIQUE column (BR-03); approval matrix dari config; work queue + eskalasi; audit trail |
| CI/CD (GitHub Actions → GHCR) | ✅ | ruff → pytest (coverage gate 80%) → docker build → Trivy scan → push GHCR |
| Observability (Prometheus/Grafana/Loki) | ✅ | metrics HTTP+bisnis, log JSON+correlation ID, alert DB mati → Alertmanager/Telegram |
| Dashboard workbench (Jinja + HTMX) | ✅ | tanpa login; kartu System Health + panel alert kanan (proxy Alertmanager) |
| Kubernetes (kind) | ✅ | cluster lokal + ingress-nginx; probes liveness/readiness; 2 replica; migrasi = Job terpisah |
| Zero-downtime CD (rolling + rollback) | 🚧 Fase 8 | readiness/liveness probe, rolling update + auto rollback |
| Hardening (backup/restore, load test) | 🚧 Fase 9 | RPO/RTO restore drill, k6, OWASP review |

## 🏗️ Arsitektur (target)

```
[Channel]   nasabah app · teller web · auditor query
     │
     ▼
[FastAPI]   /auth  /accounts  /transfers  /ledger  /approvals
     │       middleware: correlation-ID · rate limit · RBAC
     ▼
[Services]  Transfer ⭐ idempotency → validasi → ledger atomik
            Ledger (double-entry, append-only) · Approval (state machine)
     │
     ├─▶ PostgreSQL   source of truth; ledger append-only
     ├─▶ Redis        idempotency key, session, rate limit
     └─▶ RabbitMQ     async: notifikasi, settlement, recon
```

Detail lengkap + 9 ADR: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)

## 🧰 Tech Stack

| Layer | Teknologi |
|---|---|
| API Framework | FastAPI (Python 3.12+, jalan di 3.14) |
| Database | PostgreSQL 16 |
| Cache | Redis 7 |
| Message Queue | RabbitMQ 3.13 |
| ORM / Migration | SQLAlchemy 2 + Alembic |
| Auth | OAuth2 + JWT (access + refresh) |
| Container | Docker multi-stage + docker-compose |
| CI/CD | GitHub Actions → GHCR |
| Orchestration | Kubernetes (kind, lokal) |
| Monitoring | Prometheus + Grafana + Loki + Alertmanager |
| Testing | pytest, testcontainers, k6 |

## 🚀 Quickstart

Prasyarat: [Docker Desktop](https://www.docker.com/products/docker-desktop/) running.

```bash
git clone https://github.com/anggapbwr/Corebank-api.git
cd Corebank-api

# Start seluruh stack
docker compose -f deploy/docker/docker-compose.yml up -d --build

# Cek kesehatan
curl http://localhost:8000/healthz   # {"status":"ok"}
curl http://localhost:8000/readyz    # {"status":"ready","checks":{"database":"ok","redis":"ok"}}

# Stop — data tetap aman di named volume
docker compose -f deploy/docker/docker-compose.yml down
```

**Kubernetes (kind) — Fase 7:** (prasyarat: `kind`; `kubectl` ikut Docker Desktop)

```bash
docker build -t corebank-api:kind -f deploy/docker/Dockerfile --target runtime .
bash deploy/k8s/apply.sh     # cluster + ingress-nginx + datastore + migrasi (Job) + app
# → http://localhost:8080/   (dashboard & API lewat ingress)

kind delete cluster --name corebank   # bersihkan cluster (PVC ikut terhapus)
```

`apply.sh` idempoten — jalankan ulang untuk menerapkan perubahan manifest
(migrasi diulang tiap run, secret JWT tidak dirotasi otomatis).

**Endpoint lokal:**

| URL | Isi |
|---|---|
| `http://localhost:8000/` | **Dashboard workbench** — System Health + panel alert (kanan) |
| `http://localhost:8000/api/system/health` | Gauge monitor → JSON (db/redis/antrean) |
| `http://localhost:8000/api/system/alerts` | Proxy Alertmanager → JSON alert aktif |
| `http://localhost:8000/docs` | Swagger UI interaktif |
| `http://localhost:8000/healthz` | Liveness — proses hidup? (tanpa cek dependency) |
| `http://localhost:8000/readyz` | Readiness — Postgres+Redis siap terima trafik? |
| `http://localhost:8080` | API + dashboard via **ingress di cluster kind** (Fase 7) |
| `http://localhost:15672` | RabbitMQ management UI (guest/guest) |
| `http://localhost:9090` | Prometheus — query metrics & alert |
| `http://localhost:3000` | Grafana — dashboard (admin/admin), folder *CoreBank* |
| `http://localhost:9093` | Alertmanager — alert aktif (Telegram opsional, isi `.env`) |

## 🧪 Development

```bash
python -m venv .venv
.venv/Scripts/pip install -e ".[dev]"     # Windows; Linux/macOS: .venv/bin/pip

pytest                      # unit test lokal
ruff check app tests        # lint
ruff format app tests       # format

# Test di image container (sama dengan yang dipakai CI nanti)
docker compose -f deploy/docker/docker-compose.yml --profile test run --rm app-test

# Pre-commit hook (sekali per clone)
pip install pre-commit && pre-commit install
```

> **Windows + uvicorn lokal:** uvicorn ≥0.36 memakai `ProactorEventLoop` yang ditolak
> psycopg async (monitor DB & semua rute SQLAlchemy gagal — `InterfaceError`). Jalankan dengan:
> `uvicorn app.main:app --loop app.core.loops:selector_loop_factory`
> (container Linux tidak terpengaruh — Dockerfile tanpa flag ini.)

## 📁 Struktur

```
corebank-api/
├── app/
│   ├── main.py              # FastAPI entrypoint
│   ├── core/config.py       # settings (env/.env) — pydantic-settings
│   ├── api/routes/          # health, system, web/dashboard (+ auth, accounts, transfers)
│   ├── templates/           # Jinja — dashboard workbench (Fase 6)
│   ├── static/              # htmx.min.js di-vendor (tanpa CDN/build)
│   ├── models/              # SQLAlchemy (Fase 2)
│   ├── schemas/             # Pydantic request/response (Fase 2)
│   └── services/            # domain logic: transfer, ledger, approval (Fase 2–3)
├── migrations/              # Alembic (Fase 2)
├── tests/                   # pytest (unit, integration, load nanti)
├── docs/                    # ⭐ kontrak desain — baca sebelum sentuh kode
├── deploy/
│   ├── docker/              # Dockerfile multi-stage + docker-compose.yml ✅
│   ├── k8s/                 # Fase 7 ✅: kind-config, manifest (00..40), apply.sh, ingress-nginx
│   └── monitoring/          # prometheus/grafana/loki/alertmanager (Fase 5)
├── .github/workflows/       # CI/CD (Fase 4)
└── pyproject.toml           # deps + ruff + pytest config
```

## 🗺️ Roadmap

| Fase | Isi | Status |
|---|---|---|
| 1 | Foundation & Docker Compose | ✅ **Selesai** — healthz/readyz 200, pytest container 3 passed, data persist setelah restart |
| 2 | Auth JWT + RBAC + Accounts + Ledger | ✅ **Selesai** — 17 test passed (lokal & container), smoke test 9/9, BR-01/04/05 terbukti |
| 3 | Transfer + Idempotency + Approval workflow | ✅ **Selesai** — 26 test passed (lokal & container), smoke 9/9; BR-01..10 terbukti termasuk eskalasi 400jt → manager |
| 4 | Testing & CI (GitHub Actions → GHCR) | ✅ **Selesai** — lint + pytest coverage gate 80% (lokal 87%) + Trivy scan + push `sha-<7digit>` ke GHCR |
| 5 | Observability (Prometheus/Grafana/Loki/Alert) | ✅ **Selesai** — verifikasi 8/8: rate/latency terukur, dashboard terprovision, log → Loki, **mati Postgres → alert firing → resolve** |
| 6 | Dashboard Workbench (Jinja+HTMX, tanpa login) | ✅ **Selesai** — verifikasi 3/3 skenario live: sehat, AM mati (graceful), alert firing → panel kanan; 39 test, coverage 86% |
| 7 | Kubernetes (kind): probe, ConfigMap/Secret | ✅ **Selesai** — `apply.sh` bootstrap terurut; 2/2 pod + ingress hijau; **90/90 request 200 saat 1 pod dihapus paksa** (self-healing) |
| 8 | CD: rolling update + auto rollback | ⬜ |
| 9 | Hardening: backup/restore drill, k6, OWASP | ⬜ |

Acceptance criteria tiap fase: [`docs/ROADMAP.md`](docs/ROADMAP.md)

## 📖 Dokumentasi

Semua keputusan desain ada di `docs/`, dibuat **sebelum kode** (prinsip: dokumentasi = kontrak):

- [`docs/SRS.md`](docs/SRS.md) — kebutuhan fungsional/non-fungsional, 10 aturan bisnis (BR-01..10), RBAC matrix
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — arsitektur logis, sequence transfer, 9 ADR
- [`docs/ERD.md`](docs/ERD.md) — 7 tabel + alasan tiap keputusan desain
- [`docs/WORKFLOWS.md`](docs/WORKFLOWS.md) — role, work queue, state machine, contoh eskalasi Rp 400 juta
- [`docs/ROADMAP.md`](docs/ROADMAP.md) — 9 fase + acceptance criteria
- [`docs/CONVENTIONS.md`](docs/CONVENTIONS.md) — aturan Git, kode, DB, testing, logging

## 📜 Prinsip Inti (non-negotiable)

1. **Ledger append-only** — transaksi finansial tidak pernah diubah/dihapus
2. **Atomicity** — transfer all-or-nothing, tidak pernah uang hilang di tengah
3. **Idempotency** — retry client tidak menghasilkan transaksi ganda
4. **Auditability** — setiap aksi menyisakan jejak: siapa, kapan, apa, alasan
5. **Least privilege** — RBAC di setiap endpoint, auditor read-only

## 📜 License

MIT
