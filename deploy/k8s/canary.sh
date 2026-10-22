#!/usr/bin/env bash
# Simulasi CANARY — Fase 8 (docs/ROADMAP.md): versi baru menerima SEBAGIAN trafik
# lewat ingress-nginx (canary-weight), bukan langsung 100%.
#
#   canary.sh up [image]      pasang Deployment/Service corebank-green + ingress canary
#   canary.sh sample [N]      kirim N request, cetak split trafik blue vs green (dari log)
#   canary.sh promote [image] green jadi versi utama (rolling) lalu canary dibersihkan
#   canary.sh down            BATALKAN canary (rollback = hapus ingress green; blue tak tersentuh)
#
# Bobot: env CANARY_WEIGHT (default 20 = 20% ke green).
set -euo pipefail
cd "$(dirname "$0")/../.."

NS=corebank
URL=${URL:-http://localhost:8080}
WEIGHT=${CANARY_WEIGHT:-20}
ACTION=${1:-status}
IMAGE=${2:-corebank-api:kind}

req_count() { # jumlah request TRAFIK INGRESS per pod (route "/") — sengaja
  # mengecualikan probe kubelet (/readyz tiap 10 dtk, /healthz tiap 20 dtk) yang
  # juga lewat middleware dan ikut tercatat di log tanpa melewati ingress.
  kubectl -n "$NS" logs -l "app=$1" --tail=20000 --prefix --max-log-requests=10 2>/dev/null \
    | grep -c '"route": "/"' || true
}

gen_green() { # turunan 30-app.yaml: nama & label diganti corebank-green
  sed -e 's/^  name: corebank$/  name: corebank-green/' \
      -e 's/app: corebank$/app: corebank-green/g' \
      deploy/k8s/30-app.yaml
}

case "$ACTION" in
  up)
    echo "=== pasang green ($IMAGE) + ingress canary bobot ${WEIGHT}% ==="
    gen_green | kubectl apply -f -
    kubectl -n "$NS" set image deploy/corebank-green "app=$IMAGE"
    kubectl -n "$NS" rollout status deploy/corebank-green --timeout=180s
    kubectl apply -f deploy/k8s/41-canary-ingress.yaml
    kubectl -n "$NS" annotate ingress corebank-canary \
      "nginx.ingress.kubernetes.io/canary-weight=$WEIGHT" --overwrite
    echo "canary aktif — trafik ${WEIGHT}% ke corebank-green."
    echo "bukti split: bash deploy/k8s/canary.sh sample 60"
    ;;
  sample)
    N=${2:-60}
    echo "=== sample $N request → split blue vs green ==="
    b0=$(req_count corebank)
    g0=$(req_count corebank-green)
    i=0
    while [ "$i" -lt "$N" ]; do
      curl -s -o /dev/null --max-time 3 "$URL/" || true
      i=$((i + 1))
      sleep 0.05
    done
    b1=$(req_count corebank)
    g1=$(req_count corebank-green)
    db=$((b1 - b0)); dg=$((g1 - g0)); tot=$((db + dg))
    echo "blue (corebank)        : $db request"
    echo "green (corebank-green) : $dg request"
    if [ "$tot" -gt 0 ]; then
      echo "split: blue $((db * 100 / tot))% / green $((dg * 100 / tot))% dari $tot (target green ≈ ${WEIGHT}%)"
    else
      echo "tidak ada request tercatat — cek log: kubectl -n $NS logs -l app=corebank-green"
    fi
    ;;
  promote)
    echo "=== promote: green → versi utama (rolling) ==="
    kubectl -n "$NS" set image deploy/corebank "app=$IMAGE"
    kubectl -n "$NS" rollout status deploy/corebank --timeout=180s
    "$0" down
    echo "promote selesai — $IMAGE kini versi utama."
    ;;
  down)
    echo "=== batalkan canary (rollback: blue tidak tersentuh) ==="
    kubectl -n "$NS" delete ingress corebank-canary --ignore-not-found
    kubectl -n "$NS" delete deploy corebank-green --ignore-not-found
    kubectl -n "$NS" delete svc corebank-green --ignore-not-found
    echo "bersih. trafik 100% ke blue (deploy/corebank)."
    ;;
  status)
    echo "blue  (deploy/corebank)      : $(kubectl -n "$NS" get deploy corebank -o jsonpath='{.spec.replicas}') replica, image=$(kubectl -n "$NS" get deploy corebank -o jsonpath='{.spec.template.spec.containers[0].image}')"
    if kubectl -n "$NS" get deploy corebank-green >/dev/null 2>&1; then
      echo "green (deploy/corebank-green): $(kubectl -n "$NS" get deploy corebank-green -o jsonpath='{.spec.replicas}') replica, image=$(kubectl -n "$NS" get deploy corebank-green -o jsonpath='{.spec.template.spec.containers[0].image}')"
      echo "ingress canary: $(kubectl -n "$NS" get ingress corebank-canary -o jsonpath='{.metadata.annotations.nginx\.ingress\.kubernetes\.io/canary-weight}' 2>/dev/null || echo '-')"
    else
      echo "green: tidak ada (canary tidak aktif)"
    fi
    ;;
  *)
    echo "pemakaian: canary.sh {up|sample [N]|promote|down|status} [image]"; exit 2
    ;;
esac
