# Workflows — Role, Work Queue & Escalation

Desain alur kerja antar role (konsep Operations Workbench ala kantor bank).

---

## 1. Role & Responsibility

| Role | Tanggung jawab | Tulis? |
|---|---|---|
| `teller` / `cs` | Input data nasabah & transaksi level 1 | create |
| `supervisor` | Review & approve ≤ limit | approve |
| `manager` | Eksekusi eskalasi, override, freeze | approve + admin |
| `ops_risk` | Monitoring insiden, recon, freeze akun | admin |
| `auditor` | Telusuri ledger & audit trail | read-only |

## 2. Approval Matrix (config, bukan hardcode — BR-07)

```yaml
approval_matrix:
  direct_execute_max: 50_000_000      # ≤ 50jt: eksekusi langsung
  supervisor_max: 250_000_000         # ≤ 250jt: supervisor cukup
  # > 250jt: eskalasi ke manager
```

Disimpan di config file / env, di-snapshot ke `approval_requests.matrix_snapshot` setiap keputusan (BR-10).

## 3. State Machine — Transfer

```
            (≤ limit)                    (≥ limit)
  PENDING ──────────────▶ EXECUTED     PENDING ──────────────▶ PENDING_APPROVAL
                                                │
                              ┌─────────────────┼─────────────────┐
                              ▼                 ▼                 ▼
                        APPROVED→EXECUTED   REJECTED          ESCALATED
                        (supervisor)        (alasan wajib)   (naik level)
                                                                     │
                                                     ┌───────────────┴───────┐
                                                     ▼                       ▼
                                              APPROVED→EXECUTED          REJECTED
                                              (manager)
```

Semua transisi ditulis ke `audit_logs` dengan actor + timestamp + alasan.

## 4. Work Queue per Role

Antrean = query `approval_requests WHERE status='PENDING_APPROVAL' AND current_role=<role>`.

```
┌────────────────────────────────────────────────────────┐
│ 👮 SUPERVISOR QUEUE            (contoh isi)             │
├────────────────────────────────────────────────────────┤
│ TRF-0021  Rp  75.000.000  Budi → Sari    [✓][✗][↗]    │
│ TRF-0024  Rp 120.000.000  Andi → Maya    [✓][✗][↗]    │
│ ACC-0007  Rekening baru CV Maju Jaya     [✓][✗][↗]    │
└────────────────────────────────────────────────────────┘
   ✓ Approve   ✗ Reject (wajib alasan)   ↗ Escalate ke manager
```

## 5. Eskalasi — contoh alur nyata

```
1. Teller transfer Rp 400 juta dari rekening nasabah
2. API cek matrix: 400jt > 250jt (supervisor_max)
   → transaksi masuk PENDING_APPROVAL, current_role=supervisor
3. Supervisor buka queue, review:
   a. Approve? → tetap di atas limit → ESCALATE, current_role=manager
      (supervisor tidak bisa approve di atas limitnya — least privilege)
   b. Reject?  → alasan wajib → status REJECTED, feedback ke teller
4. Manager buka queue, approve → EXECUTED:
   → ledger DEBIT + KREDIT di-commit atomik
   → notifikasi async ke nasabah (RabbitMQ)
5. Auditor kapanpun bisa query: siapa input, siapa review,
   siapa approve, alasan, matrix berlaku saat itu
```

## 6. Trigger otomatis (non-manual)

| Kondisi | Aksi sistem |
|---|---|
| Login gagal 5× dalam 10 menit | Rate limit → akun masuk antrean `ops_risk` untuk review |
| Rekening baru disetujui | Status ACTIVE → publish event → notifikasi ke nasabah |
| Recon job harian (02:00) | Cek Σ ledger vs account.balance → mismatch → alert ke `ops_risk` |
| Transfer FAILED | Alert metric + retry log, masuk monitoring dashboard |

## 7. Konsep yang dipelajari dari workflow ini

- **RBAC** — route + object level
- **State machine** — status tidak pernah "nyangkut" tanpa jelas kemana
- **Work queue pattern** — antrean kerja role-based (mirip ticketing sistem nyata)
- **Separation of duties** — input ≠ approve ≠ audit (fraud prevention di bank beneran)
- **Config-driven business rule** — limit berubah tanpa deploy ulang kode
