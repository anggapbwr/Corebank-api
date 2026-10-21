# ERD — CoreBank API

Struktur database PostgreSQL. Prinsip: **append-only untuk data finansial**.

---

## Diagram

```
┌──────────────┐       ┌──────────────────┐       ┌──────────────────┐
│    users     │       │    customers     │       │    accounts      │
├──────────────┤       ├──────────────────┤       ├──────────────────┤
│ id (PK)      │       │ id (PK)          │       │ id (PK)          │
│ username(U)  │──┐    │ user_id (FK,ops) │◀──┐   │ customer_id (FK) │
│ password_hash│  │    │ full_name        │   │   │ account_number(U)│
│ role (enum)  │  └───▶│ ktp_hash         │   └──▶│ type (savings/   │
│ is_active    │       │ created_at       │       │   checking)      │
│ created_at   │       └──────────────────┘       │ balance (numeric)│
└──────────────┘                                   │ status (enum)    │
                                                   │ created_at       │
       ┌────────────────────────┐                  └───────┬──────────┘
       │     audit_logs         │                          │
       ├────────────────────────┤                          │
       │ id (PK)                │       ┌──────────────────┴──────────┐
       │ actor_user_id (FK)     │       │       transactions          │
       │ action (enum)          │       ├─────────────────────────────┤
       │ object_type, object_id │       │ id (PK)                     │
       │ payload (JSONB)        │       │ type (transfer/adjustment)  │
       │ correlation_id         │       │ source_account_id (FK,ops)  │
       │ created_at             │       │ dest_account_id (FK,ops)    │
       └────────────────────────┘       │ amount (numeric, >0)        │
                                        │ status (enum)               │
┌──────────────────────────┐           │ idempotency_key (U, nullable)│
│     approval_requests    │           │ created_by (FK users)        │
├──────────────────────────┤           │ approved_by (FK, ops)        │
│ id (PK)                  │           │ rejected_reason (text, ops)  │
│ object_type (enum:       │           │ executed_at (ts, ops)        │
│   transfer/account)      │           │ created_at                   │
│ object_id                │           └──────────┬──────────────────┘
│ current_role (enum)      │                      │
│ status (enum)            │                      │
│ escalation_level (int)   │        ┌─────────────┴──────────────────┐
│ requested_by (FK)        │        │        ledger_entries          │
│ decided_by (FK, ops)     │        ├───────────────────────────────-┤
│ decided_at, reason       │        │ id (PK)                        │
│ matrix_snapshot (JSONB)  │        │ transaction_id (FK)            │
│ created_at               │        │ account_id (FK)                │
└──────────────────────────┘        │ direction (enum: DEBIT/CREDIT) │
                                    │ amount (numeric, >0)           │
┌──────────────────────────┐        │ balance_after (numeric, ops)   │
│   idempotency_keys       │        │ created_at                     │
├──────────────────────────┤        └────────────────────────────────┘
│ id (PK)                  │
│ key (U)                  │   ⭐ append-only: trigger DB menolak
│ user_id (FK)             │      UPDATE & DELETE di ledger_entries
│ request_hash             │      dan audit_logs
│ response_snapshot (JSONB)│
│ created_at               │   Σ DEBIT = Σ KREDIT per transaction_id
│ expires_at               │   (dicek service + constraint check)
└──────────────────────────┘
```

## Catatan desain penting

1. **`ledger_entries.balance_after`** — snapshot saldo setelah entry. Menjawab pertanyaan auditor: "berapa saldo rekening X pada tanggal Y?" tanpa replay seluruh history.

2. **`approval_requests.matrix_snapshot`** — salin limit approval matrix yang berlaku saat keputusan diambil (BR-10). Kalau bank mengubah limit besok, riwayat keputusan kemarin tetap bisa dijelaskan ke regulator.

3. **`transactions.status`** vs eksekusi — row dibuat dulu dengan status, di-commit bersama ledger entries dalam SATU transaksi DB (atomic). `PENDING_APPROVAL` untuk yang di-hold, `EXECUTED` setelah eksekusi.

4. **Idempotency dua jaring pengaman:**
   - Redis (cepat, TTL) — cek pertama
   - `transactions.idempotency_key` UNIQUE — jaring pengaman final kalau Redis down

5. **Numeric, bukan float** — semua amount pakai `NUMERIC(19,4)`. Float untuk uang = bug produksi klasik.

6. **Enum via check constraint atau lookup table** — v1 pakai SQLAlchemy Enum + CHECK constraint; lookup table kalau nanti role/status sering bertambah.

## Index (minimal)

- `transactions(source_account_id, created_at)` — mutasi
- `transactions(idempotency_key) WHERE NOT NULL` — UNIQUE
- `ledger_entries(account_id, created_at)` — query audit
- `ledger_entries(transaction_id)` — balance check
- `audit_logs(object_type, object_id)` — telusuri jejak objek
- `approval_requests(status, current_role)` — work queue query
