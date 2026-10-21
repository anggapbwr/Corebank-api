# CoreBank API

Mini core banking API — project belajar **DevOps + API** dengan domain perbankan yang real:
double-entry ledger, idempotency, RBAC, approval workflow, CI/CD, Kubernetes, observability.

> Bukan tutorial CRUD. Setiap fitur lahir dari aturan bisnis perbankan yang nyata.

---

## Tech Stack

| Layer | Teknologi |
|---|---|
| API Framework | FastAPI (Python 3.14) |
| Database | PostgreSQL 16 |
| Cache | Redis 7 |
| Message Queue | RabbitMQ (async notification & settlement) |
| ORM / Migration | SQLAlchemy 2 + Alembic |
| Auth | OAuth2 + JWT (access + refresh) |
| Container | Docker multi-stage + docker-compose |
| CI/CD | GitHub Actions → GHCR |
| Orchestration | Kubernetes (kind, lokal) |
| Monitoring | Prometheus + Grafana + Loki + Alertmanager |
| Testing | pytest, testcontainers, k6 (load) |

## Fitur Utama

- **Auth** — JWT, login/refresh, role-based (RBAC)
- **Accounts** — buka rekening, saldo, mutasi, freeze/unfreeze
- **Transfers** — antar rekening, atomic, idempotent, dengan approval workflow
- **Ledger** — double-entry, append-only (tidak pernah UPDATE/DELETE)
- **Approval & Escalation** — work queue per role: Teller → Supervisor → Manager
- **Audit Trail** — setiap aksi tercatat: siapa, kapan, apa, alasan
- **Notifications** — async via RabbitMQ
- **Reconciliation Job** — cek konsistensi ledger vs saldo periodik

## Struktur Direktori

```
corebank-api/
├── app/
│   ├── main.py                 # FastAPI entrypoint
│   ├── api/routes/             # auth, accounts, transfers, ledger, approvals
│   ├── core/                   # config, security (JWT), deps
│   ├── models/                 # SQLAlchemy: Account, Transaction, LedgerEntry, ...
│   ├── schemas/                # Pydantic request/response
│   ├── services/               # domain logic: transfer, ledger, approval
│   └── tasks/                  # async workers (RabbitMQ consumer)
├── migrations/                 # Alembic
├── tests/
│   ├── unit/
│   ├── integration/            # testcontainers (postgres+redis)
│   └── load/                   # k6 scripts
├── docs/                       # ⭐ dokumentasi lengkap (lihat di bawah)
├── deploy/
│   ├── docker/
│   │   ├── Dockerfile          # multi-stage
│   │   └── docker-compose.yml  # lokal: app + postgres + redis + rabbitmq
│   ├── k8s/                    # manifests: deployment, service, ingress, configmap
│   └── monitoring/             # prometheus, grafana, loki, alertmanager config
├── .github/workflows/          # ci.yml (test → build → scan → push)
├── .env.example
└── README.md
```

## Dokumentasi

Semua keputusan desain ada di `docs/`, dibuat **sebelum kode** (prinsip: dokumentasi = kontrak):

- `docs/SRS.md` — Software Requirements Specification: kebutuhan + aturan bisnis
- `docs/ARCHITECTURE.md` — arsitektur, keputusan teknologi (ADR), alur data
- `docs/ERD.md` — struktur database
- `docs/WORKFLOWS.md` — role, work queue, escalation matrix, state machine
- `docs/ROADMAP.md` — 8 fase pengerjaan + acceptance criteria tiap fase
- `docs/CONVENTIONS.md` — aturan Git, kode, testing, config

## Quickstart

> Belum jalan sampai Fase 1 selesai. Placeholder:

```bash
cp .env.example .env
docker compose -f deploy/docker/docker-compose.yml up -d
# API:    http://localhost:8000/docs
# Health: http://localhost:8000/healthz
```

## Prinsip Inti (non-negotiable)

1. **Ledger append-only** — transaksi finansial tidak pernah diubah/dihapus
2. **Atomicity** — transfer all-or-nothing, tidak pernah uang hilang di tengah
3. **Idempotency** — retry client tidak menghasilkan transaksi ganda
4. **Auditability** — setiap aksi menyisakan jejak: siapa, kapan, apa, alasan
5. **Least privilege** — RBAC di setiap endpoint, auditor read-only
