#!/usr/bin/env bash
# [B] Seed-1 replication, unattended (docs/PREREGISTRATION.md Amendment 8). Mutates the cluster.
#   0. wait: if scripts/chain_m4.sh is still running, wait until it exits; start only if its log ends the
#      run with "chain done" (a failed or paused chain_m4 leaves this script waiting for a human)
#   1. sac_per_s1: PER-SAC, warm start from the shared runbook collection, seed 1, 200 episodes, no probes
#   2. eval_seed1: eval_v1 main schedule (50 entries), sac_per_s1 + runbook (same-block anchor), interleaved
# Stages run under scripts/watchdog.sh and are resumable: rerunning this script after a pause continues
# where it stopped. Stop it like chain_m4.sh: kill this script FIRST, then TERM the watchdog.
#
# Usage:  nohup bash scripts/chain_seed1.sh >> data/logs/chain_seed1.log 2>&1 &
set -uo pipefail
cd "$(dirname "$0")/.."
source .venv/bin/activate
set -a; source config/cluster.env; set +a

EPISODES=200
SEED=1
RUN=sac_per_s1
WARM_RUN=warmstart_runbook

chain_m4_running() { ps -eo args | awk '$1=="bash" && $2=="scripts/chain_m4.sh"{f=1} END{exit !f}'; }
while chain_m4_running; do sleep 60; done
if ! grep -q "chain done" <(tail -3 data/logs/chain_m4.log); then
  echo "=== $(date -Is) chain_m4 is not running and did not log 'chain done'; not starting (human check needed)"
  exit 1
fi
echo "=== $(date -Is) chain_m4 done; starting seed-1 chain"

# Locust keeper and network logger, as in scripts/chain_m4.sh
U_BASE=$(python -c 'import json; print(json.load(open("config/calibration.json"))["u_base"])')
keep_locust() {
  while true; do
    if ! { [[ -f data/locust.pid ]] && kill -0 "$(cat data/locust.pid)" 2>/dev/null; }; then
      echo "=== $(date -Is) locust keeper: Locust not running; restarting at $U_BASE users"
      bash locust/run_locust.sh --users "$U_BASE" --background
    fi
    sleep 60
  done
}
keep_locust &
KEEPER=$!
net_watch() {
  local lan_prev="" inet_prev="" lan inet
  while true; do
    ping -c1 -W2 "$A_IP" >/dev/null 2>&1 && lan=up || lan=DOWN
    ping -c1 -W2 1.1.1.1 >/dev/null 2>&1 && inet=up || inet=DOWN
    if [[ "$lan" != "$lan_prev" || "$inet" != "$inet_prev" ]]; then
      echo "$(date -Is) lan=$lan internet=$inet" >> data/logs/network.log
      lan_prev=$lan; inet_prev=$inet
    fi
    sleep 30
  done
}
net_watch &
NETWATCH=$!
trap 'kill $KEEPER $NETWATCH 2>/dev/null' EXIT

stage() {   # stage <name> <killed-marker or -> <command...>
  local name="$1" killed="$2"; shift 2
  echo "=== $(date -Is) stage $name: $*"
  if [[ -f data/logs/ALERT ]]; then echo "=== ALERT present; stopping before $name"; exit 1; fi
  bash scripts/watchdog.sh "$@"; local rc=$?
  echo "=== $(date -Is) stage $name exit $rc"
  if [[ $rc -ne 0 || -f data/logs/ALERT ]]; then echo "=== stopping after $name"; exit 1; fi
  if [[ "$killed" != "-" && -f "$killed" ]]; then echo "=== $name KILLED: $(cat "$killed")"; exit 1; fi
}

stage "$RUN" "data/m4/$RUN/KILLED" python -m agents.train --run "$RUN" --episodes "$EPISODES" \
  --seed "$SEED" --warmstart "data/transitions/$WARM_RUN" --probe-every 0
stage eval_seed1 - python -m eval.run_eval --schedule eval/schedules/eval_v1.json --out data/eval/eval_v1_seed1 \
  --policy runbook --policy "sac_per_s1=data/m4/$RUN/ckpt/final.pt"
echo "=== $(date -Is) seed-1 chain done"
