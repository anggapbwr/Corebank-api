# SRS — CoreBank API

Software Requirements Specification. Dokumen kontrak antara *domain perbankan* dan *implementasi teknis*.

- Versi: 0.1 (draft)
- Status: menunggu validasi (dalam project belajar, "stakeholder" = diri sendiri + referensi industri)

---

## 1. Ikhtisar (Overview)

CoreBank API adalah sistem inti perbankan skala kecil yang memodelkan operasi bank nyata:
rekening, transfer, buku besar (*ledger*) double-entry, persetujuan berjenjang, dan audit trail.

**Tujuan pembelajaran:**
- Domain perbankan sebagai katalis konsep DevOps & API design yang realistis
- SDLC lengkap: analisis → desain → implementasi → testing → deployment → operasi

## 2. Aktor & Role

| Role | Keterangan | Contoh aksi |
|---|---|---|
| `nasabah` | Customer via channel (app/web) | login, lihat saldo, transfer |
| `teller` | Frontliner level 1 | input pembukaan rekening, proses antrean |
| `cs` | Customer service | update data nasabah, layanan dasar |
| `supervisor` | Approver level 1 | approve/reject transaksi di bawah limit |
| `manager` | Approver level 2 | eksekusi eskalasi, override, freeze |
| `ops_risk` | Operations & risk | monitoring insiden, freeze akun, recon |
| `auditor` | Read-only penuh | query ledger & audit trail |
| `sistem` | Automated actor | settlement async, recon job, notifikasi |

## 3. Kebutuhan Fungsional

### 3.1 Auth (`/auth`)
- FR-AUTH-1: Login dengan username/password → access token (JWT, 15 mnt) + refresh token (7 hari)
- FR-AUTH-2: Refresh token dapat ditukar access token baru
- FR-AUTH-3: Setiap endpoint (kecuali `/healthz`, `/auth/login`) wajib Bearer token
- FR-AUTH-4: Role tertentu hanya boleh akses endpoint yang diizinkan (lihat §6 RBAC matrix)

### 3.2 Accounts (`/accounts`)
- FR-ACC-1: Buka rekening baru → status `PENDING_APPROVAL` sampai supervisor approve
- FR-ACC-2: Lihat saldo & info rekening (pemilik atau role berwenang)
- FR-ACC-3: Mutasi rekening (histori transaksi, paginated)
- FR-ACC-4: Freeze/unfreeze rekening oleh `manager` / `ops_risk`
- FR-ACC-5: Rekening `FROZEN` tidak bisa menerima/mengirim dana

### 3.3 Transfers (`/transfers`)
- FR-TRF-1: Transfer antar rekening internal dengan header `Idempotency-Key` (wajib)
- FR-TRF-2: Transfer nominal ≥ limit per role → otomatis `PENDING_APPROVAL` (hold)
- FR-TRF-3: Transfer di bawah limit → langsung dieksekusi
- FR-TRF-4: Eksekusi transfer atomik: cek saldo, debit, kredit, insert ledger — satu transaksi DB
- FR-TRF-5: Status transfer bisa di-query: `PENDING_APPROVAL | EXECUTED | REJECTED | FAILED`
- FR-TRF-6: Cancel oleh pengirim hanya bila masih `PENDING_APPROVAL`

### 3.4 Ledger (`/ledger`)
- FR-LDG-1: Setiap transaksi finansial menghasilkan ≥ 2 ledger entry (debit + kredit)
- FR-LDG-2: Total debit − total kredit = 0 untuk setiap transaksi (*balanced*)
- FR-LDG-3: Ledger append-only: tidak ada endpoint UPDATE/DELETE
- FR-LDG-4: Query ledger per rekening / per transaksi (audit), filter tanggal

### 3.5 Approvals (`/approvals`) — work queue & eskalasi
- FR-APR-1: Transaksi/rekening yang butuh persetujuan masuk antrean role approver yang sesuai
- FR-APR-2: Approve → eksekusi otomatis; Reject → alasan wajib, objek kembali `REJECTED`
- FR-APR-3: Escalation: objek di atas limit supervisor → otomatis naik ke antrean manager
- FR-APR-4: Setiap keputusan mencatat: actor, timestamp, alasan (audit trail)

### 3.6 Audit & Notifications
- FR-AUD-1: Semua akses & mutasi tercatat di `audit_logs` (immutable)
- FR-NTF-1: Notifikasi (rekening aktif, transfer selesai, approval ditolak) dikirim async via RabbitMQ

## 4. Aturan Bisnis (Business Rules)

- **BR-01** — Saldo rekening tidak boleh negatif
- **BR-02** — Transfer atomik: gagal di tahap mana pun = tidak ada perubahan sama sekali
- **BR-03** — `Idempotency-Key` unik per request; request sama dengan key sama → hasil identik, tanpa transaksi baru
- **BR-04** — Ledger hanya boleh INSERT; tidak ada UPDATE/DELETE (immutability untuk audit)
- **BR-05** — Setiap transaksi wajib balanced: Σ debit = Σ kredit
- **BR-06** — Rekening `FROZEN` tidak bisa debit maupun kredit
- **BR-07** — Approval matrix (limit) adalah *config*, bukan hardcode:
  - `teller/cs` — input ≤ Rp 50 juta → eksekusi langsung
  - `supervisor` — approve ≤ Rp 250 juta
  - `manager` — approve > Rp 250 juta (eskalasi)
- **BR-08** — Reject wajib beralasan; alasan masuk audit trail
- **BR-09** — `auditor` tidak pernah punya endpoint tulis
- **BR-10** — Setiap keputusan approval menyimpan versi approval matrix yang berlaku saat itu (traceability regulator)

## 5. Kebutuhan Non-Fungsional

- **NFR-01 Performansi** — 95% request API < 2 detik pada beban 100 RPS (load test k6)
- **NFR-02 Keamanan** — password di-hash (bcrypt/argon2), JWT signed, rate limit login (5 percobaan/menit)
- **NFR-03 Keamanan** — mitigasi OWASP API Top 10: terutama BOLA (authorization object-level) & broken auth
- **NFR-04 Ketersediaan** — API uptime 99% (belajar: health & readiness probe di K8s)
- **NFR-05 Keamanan data** — secrets tidak pernah di Git; via `.env` lokal / K8s Secret / Vault
- **NFR-06 Ketertelusuran** — correlation ID di setiap request, terus sampai log & notifikasi
- **NFR-07 Backup/RPO** — RPO 24 jam (backup harian), RTO 1 jam; backup WAJIB dites restore-nya
- **NFR-08 Immutability** — audit_logs & ledger_entries tidak bisa diubah bahkan oleh admin aplikasi

## 6. RBAC Matrix (ringkas)

| Endpoint | nasabah | teller/cs | supervisor | manager | ops_risk | auditor |
|---|---|---|---|---|---|---|
| Lihat saldo sendiri | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Transfer ≤ limit | ✅ | ✅ | ✅ | ✅ | — | — |
| Approve ≤ 250jt | — | — | ✅ | ✅ | — | — |
| Approve > 250jt (eskalasi) | — | — | — | ✅ | — | — |
| Freeze rekening | — | — | — | ✅ | ✅ | — |
| Query ledger | ❌ | ❌ | ❌ | ✅ | ✅ | ✅ |
| Audit trail | ❌ | ❌ | ❌ | ❌ | ✅ | ✅ |

## 7. Di Luar Scope (v1)

- Nasabah antar bank (kliring/BI-FAST) — hanya transfer internal
- Multi-currency, bunga, biaya admin
- Mobile app sungguhan (channel lain = API consumer lain)
- Integrasi payment gateway
- Dashboard monitoring — fase lanjutan setelah observability jalan

## 8. Acceptance Criteria (global)

- Semua FR tercakup test (unit + integration)
- Semua BR punya minimal satu test yang membuktikan (terutama BR-02..BR-05)
- Pipeline CI hijau sebelum merge (test + lint + SAST + image scan)
