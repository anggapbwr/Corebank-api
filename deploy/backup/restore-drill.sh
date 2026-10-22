#!/usr/bin/env bash
# RESTORE DRILL (NFR-07: RTO 1 jam) — Fase 9.
# Backup yang tak pernah dites restore = TIDAK ADA. Drill:
#   dump terbaru → container postgres BARU → pg_restore → verifikasi
#   (jumlah baris per tabel, alembic_version, ledger balance) → bersihkan.
# Keluar 0 = lulus, 1 = gagal. Waktu restore+verifikasi dilaporkan (RTO).
set -euo pipefail
cd "$(dirname "$0")/../.."

PG=${PG_CONTAINER:-corebank-api-postgres-1}
DB=${PG_DB:-corebank}
SRC=${1:-$(ls -1t backups/corebank-*.dump 2>/dev/null | head -1 || true)}
if [ -z "${SRC:-}" ] || [ ! -f "$SRC" ]; then
  echo "belum ada backup — jalankan deploy/backup/backup.sh dulu" >&2
  exit 1
fi

NAME="corebank-restore-drill-$$"
FAIL=0
START=$(date +%s)

cleanup() { docker rm -f "$NAME" >/dev/null 2>&1 || true; }
trap cleanup EXIT

echo "=== RESTORE DRILL ==="
echo "sumber : $SRC"
SIZE=$(wc -c < "$SRC" | tr -d ' ')
echo "ukuran : $SIZE byte"
AGE_H=$(( ($(date +%s) - $(stat -c %Y "$SRC")) / 3600 ))
echo "umur   : ${AGE_H} jam (RPO target ≤ 24 jam)"
[ "$AGE_H" -le 24 ] || { echo "  ✗ RPO terlewat — backup lebih tua dari 24 jam"; FAIL=1; }

echo "--- 1/4 container postgres bersih"
docker run -d --name "$NAME" \
  -e POSTGRES_USER="$DB" -e POSTGRES_PASSWORD=drill -e POSTGRES_DB="$DB" \
  postgres:16-alpine >/dev/null
for _ in $(seq 1 30); do
  docker exec "$NAME" pg_isready -U "$DB" -d "$DB" >/dev/null 2>&1 && break
  sleep 1
done

echo "--- 2/4 pg_restore"
RESTERR=$(mktemp)
trap 'rm -f "$RESTERR"; cleanup' EXIT
docker exec -i "$NAME" pg_restore -U "$DB" -d "$DB" < "$SRC" 2> "$RESTERR" || true
# pg_restore menulis warning (owner/privilege) ke stderr — gagal NYATA = tabel kosong
if grep -qiE "error:" "$RESTERR"; then
  echo "  ✗ pg_restore error:"; head -5 "$RESTERR"; FAIL=1
fi

echo "--- 3/4 verifikasi"
# 3a. jumlah baris per tabel: sumber == hasil restore
TABLES=$(docker exec "$PG" psql -U "$DB" -d "$DB" -Atc \
  "SELECT table_name FROM information_schema.tables
    WHERE table_schema='public' AND table_type='BASE TABLE'
      AND table_name <> 'alembic_version' ORDER BY 1")
OK_T=0; TOTAL_T=0
for t in $TABLES; do
  TOTAL_T=$((TOTAL_T + 1))
  s=$(docker exec "$PG" psql -U "$DB" -d "$DB" -Atc "SELECT count(*) FROM \"$t\"")
  r=$(docker exec "$NAME" psql -U "$DB" -d "$DB" -Atc "SELECT count(*) FROM \"$t\"")
  if [ "$s" = "$r" ]; then OK_T=$((OK_T + 1)); else
    echo "  ✗ $t: sumber=$s restore=$r"; FAIL=1
  fi
done
echo "  tabel cocok: $OK_T/$TOTAL_T"

# 3b. alembic_version identik
VS=$(docker exec "$PG" psql -U "$DB" -d "$DB" -Atc "SELECT version_num FROM alembic_version")
VR=$(docker exec "$NAME" psql -U "$DB" -d "$DB" -Atc "SELECT version_num FROM alembic_version")
if [ "$VS" = "$VR" ]; then echo "  alembic_version: $VS == $VR ✓"; else
  echo "  ✗ alembic_version beda: $VS vs $VR"; FAIL=1
fi

# 3c. ledger balance di data hasil restore (BR-05 + saldo rekening)
BAL=$(docker exec "$NAME" psql -U "$DB" -d "$DB" -Atc \
  "SELECT COALESCE(SUM(amount) FILTER (WHERE direction='DEBIT'),0)
        = COALESCE(SUM(amount) FILTER (WHERE direction='CREDIT'),0)
     FROM ledger_entries")
MIS=$(docker exec "$NAME" psql -U "$DB" -d "$DB" -Atc \
  "SELECT count(*) FROM (
      SELECT a.id FROM accounts a LEFT JOIN ledger_entries l ON l.account_id=a.id
       GROUP BY a.id, a.balance
      HAVING a.balance IS DISTINCT FROM (
             COALESCE(SUM(l.amount) FILTER (WHERE direction='CREDIT'),0)
           - COALESCE(SUM(l.amount) FILTER (WHERE direction='DEBIT'),0))) x")
if [ "$BAL" = "t" ]; then echo "  Σdebit = Σkredit: ✓"; else echo "  ✗ ledger imbalance"; FAIL=1; fi
if [ "$MIS" = "0" ]; then echo "  rekening selisih saldo: 0 ✓"; else
  echo "  ✗ $MIS rekening selisih saldo"; FAIL=1
fi

echo "--- 4/4 bersihkan container drill (data sumber tak tersentuh)"
ELAPSED=$(( $(date +%s) - START ))
echo
echo "=== HASIL ==="
echo "restore + verifikasi : ${ELAPSED} dtk (RTO target ≤ 3600 dtk)"
[ "$ELAPSED" -le 3600 ] || { echo "  ✗ RTO terlewat"; FAIL=1; }

if [ "$FAIL" = 0 ]; then
  echo "DRILL LULUS — backup terverifikasi restore-nya."
else
  echo "DRILL GAGAL — lihat ✗ di atas." >&2
fi
exit "$FAIL"
