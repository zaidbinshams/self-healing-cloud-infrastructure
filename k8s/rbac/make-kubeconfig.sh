#!/usr/bin/env bash
# Build a namespace-scoped kubeconfig from a ServiceAccount's long-lived token Secret.
# Bootstrap script (uses the admin kubeconfig to read the Secret).
#
# Usage: source config/cluster.env
#        bash k8s/rbac/make-kubeconfig.sh <serviceaccount> <out-path>
set -euo pipefail

SA="${1:?usage: make-kubeconfig.sh <serviceaccount> <out-path>}"
OUT="${2:?usage: make-kubeconfig.sh <serviceaccount> <out-path>}"
: "${KUBE_ADMIN:?source config/cluster.env first}"
: "${A_IP:?source config/cluster.env first}"
: "${NS:?source config/cluster.env first}"

SECRET="${SA}-token"
SERVER="https://${A_IP}:6443"
WAIT_ATTEMPTS=30   # x 1 s: token controller populates the Secret asynchronously

k() { kubectl --kubeconfig "$KUBE_ADMIN" --request-timeout=10s -n "$NS" "$@"; }

k get serviceaccount "$SA" >/dev/null

TOKEN=""
for _ in $(seq "$WAIT_ATTEMPTS"); do
  TOKEN="$(k get secret "$SECRET" -o jsonpath='{.data.token}' 2>/dev/null || true)"
  [[ -n "$TOKEN" ]] && break
  sleep 1
done
[[ -n "$TOKEN" ]] || { echo "make-kubeconfig: secret $NS/$SECRET has no token" >&2; exit 1; }

TMPDIR_="$(mktemp -d)"
trap 'rm -rf "$TMPDIR_"' EXIT
k get secret "$SECRET" -o jsonpath='{.data.ca\.crt}' | base64 -d > "$TMPDIR_/ca.crt"
TOKEN="$(printf '%s' "$TOKEN" | base64 -d)"

mkdir -p "$(dirname "$OUT")"
rm -f "$OUT"
( umask 077
  kc() { kubectl --kubeconfig "$OUT" config "$@" >/dev/null; }
  kc set-cluster boutique-k3s --server="$SERVER" --certificate-authority="$TMPDIR_/ca.crt" --embed-certs=true
  kc set-credentials "$SA" --token="$TOKEN"
  kc set-context "$SA@boutique" --cluster=boutique-k3s --user="$SA" --namespace="$NS"
  kc use-context "$SA@boutique"
)
chmod 600 "$OUT"

kubectl --kubeconfig "$OUT" --request-timeout=10s auth whoami -o jsonpath='{.status.userInfo.username}{"\n"}'
echo "wrote $OUT"
