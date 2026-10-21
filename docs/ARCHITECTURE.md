# Arsitektur — CoreBank API

Dokumen desain: arsitektur menyeluruh, keputusan teknologi (ADR), dan alur data.

---

## 1. Arsitektur Logis (Monolith Modular)

v1 adalah *modular monolith* — satu service, tapi batas module tegas. Microsplit di later fase hanya kalau ada bukti butuh (prinsip: jangan split sebelum sakit).

```
┌──────────────────────── Channel (konsumen API) ───────────────────────┐
│  Nasabah app · Teller/CS web · Auditor query · Job internal           │
└──────────────────────────────────┬────────────────────────────────────┘
                                   ▼
┌────────────────────────── API Layer (FastAPI) ────────────────────────┐
│  /auth  /accounts  /transfers  /ledger  /approvals  /admin            │
│  middleware: correlation-ID · rate limit · RBAC dependency            │
└──────────────────────────────────┬────────────────────────────────────┘
                                   ▼
┌────────────────────────── Domain Services ────────────────────────────┐
│  AuthService        JWT issue/refresh, password hash                  │
│  AccountService     buka rekening, freeze, saldo view                 │
│  TransferService ⭐ orchestration: idempotency → validasi → ledger     │
│  LedgerService      double-entry, append-only, balance check          │
│  ApprovalService    work queue, approve/reject/escalate (state machine)│
│  AuditService       tulis audit_logs (fire-and-forget, tidak gagalkan │
│                     transaksi utama)                                  │
└───────┬──────────────────┬──────────────────┬─────────────────────────┘
        ▼                  ▼                  ▼
   PostgreSQL           Redis            RabbitMQ
   (source of truth,  (idempotency     (async: notifikasi,
    append-only        keys, session,   settlement, recon
    ledger)            rate limit)      trigger)
```

**Catatan async:** transfer dieksekusi sinkron di DB (harus cepat & atomic), tapi efek samping (notifikasi) publish ke RabbitMQ — consumer gagal ≠ transaksi gagal.

## 2. Alur Transfer (sequence)

```
Client          API           TransferSvc       PostgreSQL         RabbitMQ
  │  POST /transfers           │                   │                  │
  │  +Idempotency-Key ────────▶│                   │                  │
  │                            │ SETNX key di Redis│                  │
  │                            │── key sudah ada? ─▶ return hasil lama│
  │                            │ BEGIN TX          │                  │
  │                            │── cek saldo cukup, account ACTIVE    │
  │                            │── insert transactions (status)       │
  │                            │── insert ledger DEBIT + KREDIT       │
  │                            │── COMMIT ─────────▶                  │
  │                            │── publish event ────────────────────▶│
  │◀── 201 {transfer_id,...} ──│                   │   consumer kirim │
  │                            │                   │   notifikasi     │
```

- Gagal cek saldo / account frozen → ROLLBACK, status `FAILED`, tidak ada jejak ledger
- Key sudah dipakai → return respons yang sama (bukan error, bukan transaksi baru)

## 3. Keputusan Teknologi (ADR ringkas)

| # | Keputusan | Alternatif | Alasan |
|---|---|---|---|
| ADR-1 | FastAPI | Flask/Django | Async-native, OpenAPI otomatis, Pydantic validation |
| ADR-2 | PostgreSQL | MySQL | Constraint kuat, JSONB untuk audit payload, ekosistem financial apps |
| ADR-3 | Modular monolith | Microservices | Scope belajar; split = biaya operasional, butuh bukti butuh |
| ADR-4 | Alembic migration | raw SQL | Schema versioning = bagian DevOps penting |
| ADR-5 | JWT access+refresh | session cookie | Multi-channel (stateless) — pola industri |
| ADR-6 | Idempotency via Redis SETNX | tabel DB | Cepat + TTL otomatis; ledger tetap sumber kebenaran final |
| ADR-7 | RabbitMQ | Kafka | Scope kecil, learning curve lebih landai |
| ADR-8 | kind (K8s lokal) | minikube/k3s | Cukup untuk manifest-as-code, ringan di Docker Desktop |
| ADR-9 | GitHub Actions | GitLab CI | Terintegrasi repo + GHCR push |

## 4. RBAC — desain implementasi

- JWT claim: `sub`, `role`, `exp`
- FastAPI dependency `require_role("supervisor", "manager")` di route group
- **Object-level check (anti-BOLA):** nasabah hanya boleh akses rekening miliknya — dicek di service layer, bukan cukup di route
- Approval matrix dibaca dari config (env/config file), bukan hardcode → BR-07

## 5. Observability (dirancang sejak awal, diaktifkan Fase 5)

- **Logs** — structured JSON, field: `timestamp, level, correlation_id, actor, action`
- **Metrics** — Prometheus: request latency histogram, transfer success/fail counter, queue depth
- **Traces** — correlation ID dari middleware → log → consumer
- **Alerts** — Alertmanager: transfer fail rate > 5%, queue backlog, pod restart → Telegram

## 6. Deployment (evolusi per fase)

| Fase | Bentuk |
|---|---|
| 1–3 | docker-compose lokal (app + postgres + redis + rabbitmq) |
| 4 | CI: test → build → scan → push GHCR |
| 6 | kind cluster: Deployment + Service + Ingress + ConfigMap/Secret |
| 7 | CD: rolling update dari image GHCR; liveness/readiness gate |
| 8 | Hardening: backup/restore drill, load test, recon job |

## 7. Keamanan (threat ringkas)

- Password: argon2/bcrypt, tidak pernah log
- Rate limit login (Redis) — anti brute-force
- BOLA: ownership check di setiap object access
- Secrets: `.env` lokal (di-gitignore), K8s Secret di cluster — tidak pernah di Git
- Supply chain: Trivy (image) + bandit (SAST) + `pip-audit` di CI
- Ledger/audit: append-only juga ditegakkan di DB (trigger block UPDATE/DELETE)
