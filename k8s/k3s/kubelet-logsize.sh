#!/usr/bin/env bash
# [A] Run once on Machine A with sudo. Raises the kubelet's container log rotation size.
# Human-approved 2026-10-09 (CLAUDE.md §2 "Cluster tuning", "Measurement point").
#
# Why: SLA latency is read server-side from the frontend's request log every tick. At the
# kubelet default (containerLogMaxSize 10Mi) the frontend log (~0.75 MB per 20 s tick at U_base)
# rotates about every 13 ticks, and a read that straddles a rotation is truncated -> missing data
# -> stale tick, far above gate G2's < 1 % budget. At 200Mi it rotates about every 93 minutes of
# traffic (~280 ticks, ~0.4 % of ticks). max-files 2 caps disk use at ~400 MB per container.
#
# K3s merges drop-ins from /etc/rancher/k3s/config.yaml.d/; "kubelet-arg+" appends to any
# kubelet-arg set elsewhere (e.g. capstone-kubelet.yaml, housekeeping). Restarting k3s does not
# restart pods; the new size applies to the running containers' next rotation.
set -euo pipefail

MAX_SIZE="${MAX_SIZE:-200Mi}"
MAX_FILES="${MAX_FILES:-2}"
DROPIN_DIR=/etc/rancher/k3s/config.yaml.d
DROPIN="$DROPIN_DIR/capstone-kubelet-logs.yaml"

sudo mkdir -p "$DROPIN_DIR"
sudo tee "$DROPIN" >/dev/null <<EOF2
# capstone: larger container logs so per-tick server-latency reads are not truncated by rotation
kubelet-arg+:
  - "container-log-max-size=${MAX_SIZE}"
  - "container-log-max-files=${MAX_FILES}"
EOF2
echo "wrote $DROPIN:"; sudo cat "$DROPIN"

sudo systemctl restart k3s
for _ in $(seq 1 60); do
  if sudo k3s kubectl get --raw /readyz >/dev/null 2>&1; then break; fi
  sleep 2
done
sudo k3s kubectl get nodes -o wide

# Check the most recent kubelet launch line (not a --since window: chrony steps can move it).
LAST=$(sudo journalctl -u k3s -b --no-pager | grep "Running kubelet" | tail -n 1)
if grep -q -- "--container-log-max-size=${MAX_SIZE}" <<<"$LAST" && grep -q -- "--container-log-max-files=${MAX_FILES}" <<<"$LAST"; then
  echo "OK: kubelet running with --container-log-max-size=${MAX_SIZE} --container-log-max-files=${MAX_FILES}"
else
  echo "WARN: flags not found on the latest 'Running kubelet' line; check 'journalctl -u k3s'" >&2
  exit 1
fi
