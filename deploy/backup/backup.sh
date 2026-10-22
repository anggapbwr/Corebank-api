#!/usr/bin/env bash
# Backup harian PostgreSQL (NFR-07: RPO 24 jam) — Fase 9.
# pg_dump custom format → folder backups/ (TERPISAH dari volume pgdata;
# di luar Docker → tetap ada walau `compose down -v`).
#
# Jadwal harian:
#   Linux/macOS : 0 2 * * * cd <repo> && bash deploy/backup/backup.sh
#   Windows     : schtasks /create /tn corebank-backup /sc daily /st 02:00 \
#                   /tr "bash D:\GitHub\corebank-api\deploy\backup\backup.sh"
#
# Retensi: 7 backup terbaru (cukup untuk RPO 24h + margin koreksi).
set -euo pipefail
cd "$(dirname "$0")/../.."

PG=${PG_CONTAINER:-corebank-api-postgres-1}
DB=${PG_DB:-corebank}
mkdir -p backups
OUT="backups/corebank-$(date +%Y%m%d-%H%M%S).dump"

docker exec "$PG" pg_dump -U "$DB" -Fc "$DB" > "$OUT"

SIZE=$(wc -c < "$OUT" | tr -d ' ')
if [ "$SIZE" -lt 100 ]; then
  echo "GAGAL: dump kosong/terlalu kecil ($SIZE byte) — $OUT" >&2
  rm -f "$OUT"
  exit 1
fi

echo "backup OK: $OUT ($SIZE byte)"

# retensi 7 terbaru
ls -1t backups/corebank-*.dump 2>/dev/null | tail -n +8 | xargs -r rm --

# umur backup paling baru (bahan bukti RPO)
LATEST=$(ls -1t backups/corebank-*.dump | head -1)
AGE_H=$(( ($(date +%s) - $(stat -c %Y "$LATEST")) / 3600 ))
echo "backup terbaru: $LATEST (umur ${AGE_H} jam; RPO target ≤ 24 jam)"
