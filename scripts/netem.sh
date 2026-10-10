#!/usr/bin/env bash
# [B, sudo] Edge-degradation condition for the M5 edge experiment (docs/PLAN_OF_ACTION.md, P4).
# Adds delay/jitter/loss ONLY to B->A control-plane traffic: Kubernetes API (:6443, actions + kube
# telemetry + server-latency log reads) and Prometheus (:30090). Locust's load traffic (:30080) is
# untouched, so the workload is unchanged and only the controller's observe/act path is degraded.
#
# Usage:  source config/cluster.env
#         sudo -E bash scripts/netem.sh apply   # uses NETEM_DELAY / NETEM_JITTER / NETEM_LOSS
#         sudo -E bash scripts/netem.sh status
#         sudo -E bash scripts/netem.sh clear
# Values come from config/runs/edge_degraded.env (sourced if present) so the condition is versioned.
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f config/runs/edge_degraded.env ] && source config/runs/edge_degraded.env
: "${A_IP:?source config/cluster.env (sudo -E keeps it)}"
DEV="${NETEM_DEV:-eth0}"
DELAY="${NETEM_DELAY:?set in config/runs/edge_degraded.env}"
JITTER="${NETEM_JITTER:?set in config/runs/edge_degraded.env}"
LOSS="${NETEM_LOSS:?set in config/runs/edge_degraded.env}"
PORTS=(6443 30090)

case "${1:-}" in
  apply)
    tc qdisc del dev "$DEV" root 2>/dev/null || true
    tc qdisc add dev "$DEV" root handle 1: prio                       # default traffic -> bands 1:1/1:2
    tc qdisc add dev "$DEV" parent 1:3 handle 30: netem delay "$DELAY" "$JITTER" loss "$LOSS"
    for p in "${PORTS[@]}"; do
      tc filter add dev "$DEV" protocol ip parent 1:0 prio 3 u32 \
        match ip dst "${A_IP}/32" match ip dport "$p" 0xffff flowid 1:3
    done
    echo "netem applied on $DEV: delay $DELAY ± $JITTER, loss $LOSS -> ${A_IP} ports ${PORTS[*]}"
    ;;
  clear)
    tc qdisc del dev "$DEV" root 2>/dev/null && echo "netem cleared on $DEV" || echo "nothing to clear on $DEV"
    ;;
  status)
    tc qdisc show dev "$DEV"; tc filter show dev "$DEV"
    ;;
  *)
    echo "usage: $0 apply|status|clear" >&2; exit 2
    ;;
esac
