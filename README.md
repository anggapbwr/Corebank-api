# CoreBank API

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
| Transfer atomic + idempotency | 🚧 Fase 3 | Redis SETNX + UNIQUE column; approval workflow + eskalasi |
| CI/CD (GitHub Actions → GHCR) | 🚧 Fase 4 | test → build → Trivy scan → push |
| Observability (Prometheus/Grafana/Loki) | 🚧 Fase 5 | alert ke Telegram |
| Kubernetes (kind) + zero-downtime | 🚧 Fase 6–7 | readiness/liveness probe, rolling update + auto rollback |
| Hardening (backup/restore, load test) | 🚧 Fase 8 | RPO/RTO restore drill, k6, OWASP review |

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

**Endpoint lokal:**

| URL | Isi |
|---|---|
| `http://localhost:8000/docs` | Swagger UI interaktif |
| `http://localhost:8000/healthz` | Liveness — proses hidup? (tanpa cek dependency) |
| `http://localhost:8000/readyz` | Readiness — Postgres+Redis siap terima trafik? |
| `http://localhost:15672` | RabbitMQ management UI (guest/guest) |

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

## 📁 Struktur

```
corebank-api/
├── app/
│   ├── main.py              # FastAPI entrypoint
│   ├── core/config.py       # settings (env/.env) — pydantic-settings
│   ├── api/routes/          # health (+ auth, accounts, transfers nanti)
│   ├── models/              # SQLAlchemy (Fase 2)
│   ├── schemas/             # Pydantic request/response (Fase 2)
│   └── services/            # domain logic: transfer, ledger, approval (Fase 2–3)
├── migrations/              # Alembic (Fase 2)
├── tests/                   # pytest (unit, integration, load nanti)
├── docs/                    # ⭐ kontrak desain — baca sebelum sentuh kode
├── deploy/
│   ├── docker/              # Dockerfile multi-stage + docker-compose.yml ✅
│   ├── k8s/                 # manifests K8s (Fase 6)
│   └── monitoring/          # prometheus/grafana/loki/alertmanager (Fase 5)
├── .github/workflows/       # CI/CD (Fase 4)
└── pyproject.toml           # deps + ruff + pytest config
```

## 🗺️ Roadmap

| Fase | Isi | Status |
|---|---|---|
| 1 | Foundation & Docker Compose | ✅ **Selesai** — healthz/readyz 200, pytest container 3 passed, data persist setelah restart |
| 2 | Auth JWT + RBAC + Accounts + Ledger | ✅ **Selesai** — 17 test passed (lokal & container), smoke test 9/9, BR-01/04/05 terbukti |
| 3 | Transfer + Idempotency + Approval workflow | ⬜ |
| 4 | Testing & CI (GitHub Actions → GHCR) | ⬜ |
| 5 | Observability (Prometheus/Grafana/Loki/Alert) | ⬜ |
| 6 | Kubernetes (kind): probe, ConfigMap/Secret | ⬜ |
| 7 | CD: rolling update + auto rollback | ⬜ |
| 8 | Hardening: backup/restore drill, k6, OWASP | ⬜ |

Acceptance criteria tiap fase: [`docs/ROADMAP.md`](docs/ROADMAP.md)

## 📖 Dokumentasi

Semua keputusan desain ada di `docs/`, dibuat **sebelum kode** (prinsip: dokumentasi = kontrak):

- [`docs/SRS.md`](docs/SRS.md) — kebutuhan fungsional/non-fungsional, 10 aturan bisnis (BR-01..10), RBAC matrix
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — arsitektur logis, sequence transfer, 9 ADR
- [`docs/ERD.md`](docs/ERD.md) — 7 tabel + alasan tiap keputusan desain
- [`docs/WORKFLOWS.md`](docs/WORKFLOWS.md) — role, work queue, state machine, contoh eskalasi Rp 400 juta
- [`docs/ROADMAP.md`](docs/ROADMAP.md) — 8 fase + acceptance criteria
- [`docs/CONVENTIONS.md`](docs/CONVENTIONS.md) — aturan Git, kode, DB, testing, logging

## 📜 Prinsip Inti (non-negotiable)

1. **Ledger append-only** — transaksi finansial tidak pernah diubah/dihapus
2. **Atomicity** — transfer all-or-nothing, tidak pernah uang hilang di tengah
3. **Idempotency** — retry client tidak menghasilkan transaksi ganda
4. **Auditability** — setiap aksi menyisakan jejak: siapa, kapan, apa, alasan
5. **Least privilege** — RBAC di setiap endpoint, auditor read-only

## 📜 License

MIT
