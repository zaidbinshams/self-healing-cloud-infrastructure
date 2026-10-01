#!/usr/bin/env bash
# [B] Install/upgrade Chaos Mesh on K3s with the CLAUDE.md §8.1 values. MUTATES THE CLUSTER.
# Usage:  source config/cluster.env && bash chaos/install-chaos-mesh.sh
#
# `helm upgrade --install` without --reuse-values: every value not set here returns to the chart
# default, so a pre-existing install with the dashboard / DNS server enabled is brought back to
# exactly this configuration.
set -euo pipefail

: "${KUBE_ADMIN:?source config/cluster.env}"
: "${CHAOS_MESH_VERSION:?source config/cluster.env}"
: "${HELM_VERSION:?source config/cluster.env}"
: "${NS:?source config/cluster.env}"
export KUBECONFIG="$KUBE_ADMIN"

if ! command -v helm >/dev/null || [[ "$(helm version --template '{{.Version}}')" != "$HELM_VERSION" ]]; then
  echo "installing helm ${HELM_VERSION} to ~/.local/bin"
  tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
  curl -fsSL "https://get.helm.sh/helm-${HELM_VERSION}-linux-amd64.tar.gz" | tar -xz -C "$tmp"
  mkdir -p ~/.local/bin && install -m 0755 "$tmp/linux-amd64/helm" ~/.local/bin/helm
  export PATH="$HOME/.local/bin:$PATH"
fi

helm repo add chaos-mesh https://charts.chaos-mesh.org >/dev/null 2>&1 || true
helm repo update chaos-mesh

helm upgrade --install chaos-mesh chaos-mesh/chaos-mesh \
  --namespace chaos-mesh --create-namespace \
  --version "$CHAOS_MESH_VERSION" \
  --set chaosDaemon.runtime=containerd \
  --set chaosDaemon.socketPath=/run/k3s/containerd/containerd.sock \
  --set controllerManager.enableFilterNamespace=true \
  --set dashboard.create=false \
  --wait --timeout 5m

# Only `boutique` may be targeted (enableFilterNamespace honours this annotation).
kubectl annotate namespace "$NS" chaos-mesh.org/inject=enabled --overwrite

kubectl -n chaos-mesh get pods -o wide
