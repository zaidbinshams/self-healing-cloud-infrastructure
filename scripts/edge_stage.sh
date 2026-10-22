#!/usr/bin/env bash
# [B] Edge subset, unattended (docs/PREREGISTRATION.md §3, H5). Mutates the cluster and B's qdisc (netem).
#   1. apply the pre-registered edge condition (root-owned /usr/local/sbin/capstone-netem, values baked in by
#      scripts/install_netem_sudo.sh: +150 ms, 1 % loss on B->A :6443/:30090 only; Locust :30080 untouched)
#   2. validity dry run, once: >= 15 ticks with no fault and no actions (scripts/record_ticks: the step()
#      telemetry stack). > 10 % stale ticks -> the pre-registered fallback is 0.5 % loss, which needs the
#      installer re-run by a human (sudo): write data/logs/ALERT and stop (exit 0 so the watchdog does not retry)
#   3. eval.run_eval --subset edge (runbook + sac_per, interleaved; resumable)
# netem is cleared on every exit (EXIT trap) and verified cleared, so a failure, a LAN outage (the watchdog then
# runs session_start.sh) or a pause never leaves the condition applied. Signals are forwarded to the child.
# Run under the watchdog as chain stage eval_edge. Overridable for sandbox tests: NETEM, DRYRUN_CMD, EVAL_CMD.
set -uo pipefail
cd "$(dirname "$0")/.."
NETEM="${NETEM:-sudo -n /usr/local/sbin/capstone-netem}"
OUT="${EDGE_OUT:-data/eval/eval_v1_edge}"
DRY_TICKS=15
DRY_MAX_STALE=0.10
mkdir -p "$OUT"
child=0

clear_netem() {
  $NETEM clear
  if $NETEM status | grep -q netem; then
    echo "edge: $(date -Is) netem STILL PRESENT after clear" >&2
    echo "$(date -Is) edge_stage: netem still applied after clear; clear it: sudo -n /usr/local/sbin/capstone-netem clear" >> data/logs/ALERT
  else
    echo "edge: $(date -Is) netem cleared and verified"
  fi
}
trap clear_netem EXIT
trap 'echo "edge: $(date -Is) signal received, forwarding to child $child"; [ "$child" -gt 0 ] && kill -TERM "$child" && wait "$child"; exit 0' TERM INT
run() { "$@" & child=$!; wait "$child"; local rc=$?; child=0; return "$rc"; }

$NETEM clear >/dev/null
$NETEM apply || { echo "edge: netem apply failed" >&2; exit 1; }
echo "edge: $(date -Is) netem applied"

if [[ ! -f "$OUT/dryrun_passed" ]]; then
  dry="$OUT/dryrun_$(date +%Y%m%dT%H%M%S).jsonl"
  run ${DRYRUN_CMD:-python -m scripts.record_ticks --n "$DRY_TICKS" --run edge_dryrun} --out "$dry" || exit 1
  read -r n stale < <(python -c '
import json, sys
t = [r for r in map(json.loads, open(sys.argv[1])) if r.get("kind") == "tick"]
print(len(t), sum(bool(r["stale"]) for r in t))' "$dry") || exit 1
  echo "edge: dry run $stale/$n stale ticks (limit $DRY_MAX_STALE)"
  if python -c "import sys; n, s, lim = int(sys.argv[1]), int(sys.argv[2]), float(sys.argv[3]); sys.exit(0 if n >= $DRY_TICKS and s / n <= lim else 1)" "$n" "$stale" "$DRY_MAX_STALE"; then
    echo "$(date -Is) $stale/$n stale ($dry)" > "$OUT/dryrun_passed"
  else
    echo "$(date -Is) edge_stage: dry run $stale/$n stale ticks > $DRY_MAX_STALE (or < $DRY_TICKS ticks): pre-registered fallback is 0.5 % loss; re-run the netem installer with NETEM_LOSS=0.5% (sudo), then archive this ALERT and relaunch the chain" >> data/logs/ALERT
    exit 0
  fi
fi

run ${EVAL_CMD:-python -m eval.run_eval --schedule eval/schedules/eval_v1.json --subset edge --out "$OUT" --policy runbook --policy sac_per=data/m4/sac_per_s0/ckpt/final.pt}
