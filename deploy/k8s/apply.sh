#!/usr/bin/env bash
# Bootstrap Fase 7 — cluster kind + seluruh manifest dalam urutan aman.
# Jalankan dari root repo (Git Bash / Linux / macOS):
#   bash deploy/k8s/apply.sh
# Idempoten: aman dijalankan berulang (job migrasi dijalankan ulang tiap run).
set -euo pipefail
cd "$(dirname "$0")/../.."

NS=corebank
CLUSTER=corebank
URL=http://localhost:8080
step() { printf '\n=== %s ===\n' "$1"; }

step "1/7 kind cluster (buat kalau belum ada)"
if kind get clusters 2>/dev/null | grep -qx "$CLUSTER"; then
  echo "cluster '$CLUSTER' sudah ada — lewati pembuatan"
else
  kind create cluster --config deploy/k8s/kind-config.yaml
fi
kubectl config use-context "kind-$CLUSTER" >/dev/null

step "2/7 ingress-nginx (controller untuk kind)"
kubectl apply -f deploy/k8s/ingress-nginx-kind.yaml
# rollout status, bukan `wait --selector` — pod belum tentu sudah ter-create
# saat baris ini dieksekusi (wait dengan label langsung error "no matching resources")
kubectl -n ingress-nginx rollout status deploy/ingress-nginx-controller --timeout=300s

step "3/7 namespace + configmap"
kubectl apply -f deploy/k8s/00-namespace.yaml -f deploy/k8s/01-configmap.yaml

step "4/7 secret (sekali saja — JWT_SECRET tidak dirotasi otomatis)"
if kubectl -n "$NS" get secret corebank-secret >/dev/null 2>&1; then
  echo "secret sudah ada — lewati (hapus manual untuk rotasi + restart rollout app)"
else
  JWT_SECRET="${JWT_SECRET:-$(python -c 'import secrets; print(secrets.token_hex(32))')}"
  kubectl -n "$NS" create secret generic corebank-secret \
    --from-literal=DATABASE_URL='postgresql+psycopg://corebank:corebank@postgres:5432/corebank' \
    --from-literal=JWT_SECRET="$JWT_SECRET" \
    --dry-run=client -o yaml | kubectl apply -f -
fi

step "5/7 datastore (postgres + redis) — tunggu siap"
kubectl apply -f deploy/k8s/10-postgres.yaml -f deploy/k8s/11-redis.yaml
kubectl -n "$NS" rollout status deploy/postgres --timeout=180s
kubectl -n "$NS" rollout status deploy/redis --timeout=180s

step "6/7 image aplikasi ke cluster"
if docker image inspect corebank-api:kind >/dev/null 2>&1; then
  kind load docker-image corebank-api:kind --name "$CLUSTER"
elif ! docker exec "$CLUSTER-control-plane" crictl images 2>/dev/null | grep -q corebank-api; then
  echo "image 'corebank-api:kind' belum ada — build dulu:"
  echo "  docker build -t corebank-api:kind -f deploy/docker/Dockerfile --target runtime ."
  exit 1
fi

step "7/7 migrasi (Job) → rollout app + ingress"
kubectl -n "$NS" delete job migrate --ignore-not-found >/dev/null
kubectl apply -f deploy/k8s/20-migrate-job.yaml
kubectl -n "$NS" wait --for=condition=complete job/migrate --timeout=180s
kubectl apply -f deploy/k8s/30-app.yaml -f deploy/k8s/40-ingress.yaml
kubectl -n "$NS" rollout status deploy/corebank --timeout=180s

printf '\nSelesai. API + dashboard → %s/  (healthz: %s/healthz)\n' "$URL" "$URL"
