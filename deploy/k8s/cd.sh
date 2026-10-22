#!/usr/bin/env bash
# Pipeline CD — Fase 8 (docs/ROADMAP.md): image baru → rolling update SELAMA load
# test → smoke test otomatis → rollback kalau gagal → laporan angka.
#
# Pemakaian (cluster sudah dibuat apply.sh):
#   bash deploy/k8s/cd.sh                  # deploy; gagal → rollback → exit 1
#   bash deploy/k8s/cd.sh --demo-rollback  # buktikan jalur rollback (image sengaja rusak)
set -euo pipefail
cd "$(dirname "$0")/../.."

NS=corebank
URL=${URL:-http://localhost:8080}
DEMO_ROLLBACK=0
[ "${1:-}" = "--demo-rollback" ] && DEMO_ROLLBACK=1

SHA=$(git rev-parse --short HEAD 2>/dev/null || echo local)
DIRTY=""
[ -n "$(git status --porcelain 2>/dev/null || true)" ] && DIRTY="-dirty"
# Tag unik tiap run → rolling update selalu terpicu (perubahan spec pod template),
# walau isi image identik (di CI isinya memang commit baru)
RUN_ID=$(date +%Y%m%d%H%M%S)
IMAGE="corebank-api:cd-${SHA}${DIRTY}-${RUN_ID}"

TMP_DIR=$(mktemp -d)
LOAD_FILE="$TMP_DIR/load.txt"
LOAD_STOP="$TMP_DIR/load.stop"
SMOKE_FAIL=0

# Docker CLI Windows tak memahami path MSYS (/tmp/...) → konversi bila tersedia
native_path() {
  if command -v cygpath >/dev/null 2>&1; then cygpath -w "$1"; else printf '%s' "$1"; fi
}

step() { printf '\n=== %s ===\n' "$1"; }

cleanup() {
  touch "$LOAD_STOP" 2>/dev/null || true
  [ -n "${LOAD_PID:-}" ] && kill "$LOAD_PID" 2>/dev/null || true
}
trap cleanup EXIT

# ------------------------------------------------------------- smoke test
# Semua kelas endpoint lewat ingress: liveness, readiness, halaman render,
# API JSON. Catatan: port-forward Docker Desktop Windows bisa melambat ~2-3 dtk
# di tengah churn rollout (respons server tetap ~ms) → beri max-time 5 + 1 retry;
# kegagalan persisten tetap tercetak dengan kode & potongan isi respons.
# Mengisi SMOKE_FAIL (0 = hijau).
check_endpoint() { # $1 = path, $2 = pola wajib di bodi (opsional)
  local path="$1" pat="${2:-}" attempt out c body
  for attempt in 1 2; do
    # bodi + kode lewat stdout SATU curl — tanpa file (-o ke path MSYS gagal
    # untuk curl native Windows: konversi path nonaktif)
    out=$(curl -s --max-time 5 -w '\n%{http_code}' "$URL$path" || true)
    c=${out##*$'\n'}
    body=${out%$'\n'*}
    c=${c:-000}
    if [ "$c" = 200 ] && { [ -z "$pat" ] || printf '%s' "$body" | grep -q "$pat"; }; then
      return 0
    fi
    [ "$attempt" = 1 ] && sleep 1
  done
  echo "  $path -> GAGAL (HTTP $c): $(printf '%.120s' "$body" | tr '\n' ' ')"
  return 1
}

smoke_check() {
  local fail=0 i
  for i in $(seq 1 10); do
    check_endpoint /healthz || fail=$((fail + 1))
  done
  check_endpoint /readyz || fail=$((fail + 1))
  check_endpoint / alerts-panel || fail=$((fail + 1))
  check_endpoint /api/system/health db_up || fail=$((fail + 1))
  SMOKE_FAIL=$fail
}

load_report() {
  local total bad
  total=$(wc -l < "$LOAD_FILE" 2>/dev/null || echo 0)
  bad=$(grep -cv '^200$' "$LOAD_FILE" 2>/dev/null || true)
  bad=${bad:-0}
  echo "total=$total  non-200=$bad"
  LOAD_TOTAL=$total
  LOAD_BAD=$bad
}

# ------------------------------------------------------------- build
step "1/6 build image baru ($IMAGE)"
if [ "$DEMO_ROLLBACK" = 1 ]; then
  # Image sengaja rusak: container langsung exit → readiness tak pernah hijau
  printf 'FROM corebank-api:kind\nCMD ["sh", "-c", "exit 1"]\n' > "$TMP_DIR/Dockerfile"
  docker build -q -t corebank-api:broken "$(native_path "$TMP_DIR")" >/dev/null
  IMAGE=corebank-api:broken
else
  docker build -q -t "$IMAGE" -f deploy/docker/Dockerfile --target runtime . >/dev/null
fi

step "2/6 load ke cluster kind"
kind load docker-image "$IMAGE" --name corebank

# ------------------------------------------------------------- load generator
step "3/6 load generator menyala (background, berhenti sendiri setelah deploy+smoke)"
: > "$LOAD_FILE"
(
  i=0
  while [ ! -f "$LOAD_STOP" ] && [ "$i" -lt 4000 ]; do
    c=$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 "$URL/healthz" || true)
    echo "${c:-000}" >> "$LOAD_FILE"
    i=$((i + 1))
    sleep 0.05
  done
) &
LOAD_PID=$!

# ------------------------------------------------------------- rolling update
ROLL_TIMEOUT=150s
[ "$DEMO_ROLLBACK" = 1 ] && ROLL_TIMEOUT=60s # demo: jangan tunggu lama pada pod yang crash
step "4/6 rolling update deploy/corebank → $IMAGE (timeout $ROLL_TIMEOUT)"
kubectl -n "$NS" set image deploy/corebank "app=$IMAGE"
ROLL_OK=1
if ! kubectl -n "$NS" rollout status deploy/corebank --timeout="$ROLL_TIMEOUT"; then
  ROLL_OK=0
  echo "!! rollout GAGAL (timeout / pod tak pernah Ready)"
fi

# ------------------------------------------------------------- smoke
step "5/6 smoke test"
smoke_check
echo "kegagalan smoke: $SMOKE_FAIL"

ROLLBACKED=0
if [ "$ROLL_OK" = 0 ] || [ "$SMOKE_FAIL" -gt 0 ]; then
  step "6/6 ROLLBACK otomatis (kubectl rollout undo)"
  kubectl -n "$NS" rollout undo deploy/corebank
  kubectl -n "$NS" rollout status deploy/corebank --timeout=150s
  smoke_check
  echo "smoke setelah rollback: $SMOKE_FAIL kegagalan"
  ROLLBACKED=1
else
  step "6/6 rollback tidak diperlukan — deploy hijau"
fi

# ------------------------------------------------------------- laporan load
touch "$LOAD_STOP"
wait "$LOAD_PID" 2>/dev/null || true
echo
echo "=== Beban selama deploy ==="
load_report
echo "smoke akhir: $SMOKE_FAIL kegagalan | rollback: $ROLLBACKED"

if [ "$ROLLBACKED" = 1 ]; then
  if [ "$DEMO_ROLLBACK" = 1 ] && [ "$SMOKE_FAIL" = 0 ]; then
    echo "HASIL: rollback TERBUKTI — deploy gagal, service pulih tanpa gangguan."
    exit 0
  fi
  echo "HASIL: GAGAL (rollback sudah dijalankan)"
  exit 1
fi
if [ "${LOAD_BAD:-0}" -gt 0 ]; then
  echo "HASIL: GAGAL — ada ${LOAD_BAD} request non-200 saat rolling update (bukan zero-downtime)"
  exit 1
fi
echo "HASIL: SUKSES — ${LOAD_TOTAL:-0} request saat rolling update, 0 gagal."
