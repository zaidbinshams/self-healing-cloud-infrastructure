#!/usr/bin/env bash
# [A] Run once on Machine A with sudo. Shortens the kubelet's cAdvisor housekeeping interval.
# Human-approved 2026-10-02 (CLAUDE.md §2 Environment Overrides, §8.3).
#
# Why: the kubelet's embedded cAdvisor refreshes container stats every 10 s by default and
# backs off to 15 s for quiet containers. Measured on 2026-10-01, the newest sample was a
# median 12 s / p95 21 s old at query time, so the contract's rate(...[30s]) at the live edge
# was empty for 8-46 % of evaluations. A 5 s interval (= Prometheus scrape interval) gives
# rate() several samples per 30 s window, so the agent's CPU/throttle features stay fresh.
#
# K3s merges drop-ins from /etc/rancher/k3s/config.yaml.d/; "kubelet-arg+" appends instead of
# replacing any kubelet-arg already set elsewhere. Restarting k3s does not restart pods.
set -euo pipefail

INTERVAL="${INTERVAL:-5s}"
DROPIN_DIR=/etc/rancher/k3s/config.yaml.d
DROPIN="$DROPIN_DIR/capstone-kubelet.yaml"

sudo mkdir -p "$DROPIN_DIR"
sudo tee "$DROPIN" >/dev/null <<EOF
# capstone: faster cAdvisor stats refresh for the 30 s PromQL rate window
kubelet-arg+:
  - "housekeeping-interval=${INTERVAL}"
EOF
echo "wrote $DROPIN:"; sudo cat "$DROPIN"

sudo systemctl restart k3s
for _ in $(seq 1 60); do
  if sudo k3s kubectl get --raw /readyz >/dev/null 2>&1; then break; fi
  sleep 2
done
sudo k3s kubectl get nodes -o wide

# The flag must reach the kubelet; K3s logs the full kubelet argv at startup. Check the most
# recent "Running kubelet" line rather than a --since window: chrony steps of A's clock can
# push the restart outside a relative time window.
if sudo journalctl -u k3s -b --no-pager | grep "Running kubelet" | tail -n 1 \
    | grep -q -- "--housekeeping-interval=${INTERVAL}"; then
  echo "OK: kubelet running with --housekeeping-interval=${INTERVAL}"
else
  echo "WARN: --housekeeping-interval=${INTERVAL} not found in the k3s journal; check 'journalctl -u k3s'" >&2
  exit 1
fi
