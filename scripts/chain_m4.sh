#!/usr/bin/env bash
# [B] M4 pipeline, unattended (docs/PLAN_OF_ACTION.md §4; docs/PREREGISTRATION.md §2). Mutates the cluster.
#   0. M3 runbook validation: runbook, ε = 0, VALIDATION_EPISODES episodes, then gates G1–G7
#      (scripts/gates.py; report in data/m3/runbook_eval/gates.txt). Failing gates do NOT stop the
#      warm-start collection (useful data either way) but DO stop the chain before training, unless a
#      human has reviewed them and reruns with GATES_REVIEWED=1.
#   1. warm-start collection: runbook, ε = runbook.warmstart_epsilon, runbook.warmstart_episodes episodes
#   2. sac_per_s0:      PER-SAC, warm start from (1), seed 0, 200 episodes
#   3. sac_per_cold_s0: PER-SAC, cold start, seed 0, 200 episodes
# Each stage runs under scripts/watchdog.sh and is resumable, so rerunning this script after an
# interruption continues where it stopped (finished stages return immediately). The chain stops at the
# first stage that fails, or when data/logs/ALERT or a stage's KILLED marker appears.
#
# To stop it: kill the chain script FIRST, then TERM the watchdog (a TERMed watchdog exits 0, which would
# otherwise start the next stage).
#
# Usage:  source config/cluster.env   (Locust must be running at U_base)
#         nohup bash scripts/chain_m4.sh > data/logs/chain_m4.log 2>&1 &
set -uo pipefail
cd "$(dirname "$0")/.."
source .venv/bin/activate
set -a; source config/cluster.env; set +a

EPISODES=200
SEED=0
WARM_RUN=warmstart_runbook
VALIDATION_EPISODES=20
VAL_OUT=data/m3/runbook_eval
read -r WARM_EPS WARM_N < <(python -c 'from env.contract import load_contract as l; r=l().runbook; print(r.warmstart_epsilon, r.warmstart_episodes)')

# Locust keeper: Locust is not under the watchdog; if its process dies, restart it at U_base so the
# stages' resets (which require Locust to answer) can recover. Stops with the chain.
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
trap 'kill $KEEPER 2>/dev/null' EXIT

stage() {   # stage <name> <killed-marker or -> <command...>
  local name="$1" killed="$2"; shift 2
  echo "=== $(date -Is) stage $name: $*"
  if [[ -f data/logs/ALERT ]]; then echo "=== ALERT present; stopping before $name"; exit 1; fi
  bash scripts/watchdog.sh "$@"; local rc=$?
  echo "=== $(date -Is) stage $name exit $rc"
  if [[ $rc -ne 0 || -f data/logs/ALERT ]]; then echo "=== stopping after $name"; exit 1; fi
  if [[ "$killed" != "-" && -f "$killed" ]]; then echo "=== $name KILLED: $(cat "$killed")"; exit 1; fi
}

stage validation - python -m agents.run_policy --policy runbook --episodes "$VALIDATION_EPISODES" --epsilon 0 \
  --seed "$SEED" --out "$VAL_OUT"
python -m scripts.gates --run "$VAL_OUT" > "$VAL_OUT/gates.txt" 2>&1; gates_rc=$?
echo "=== $(date -Is) gates exit $gates_rc"; cat "$VAL_OUT/gates.txt"

stage warmstart - python -m agents.run_policy --policy runbook --episodes "$WARM_N" --epsilon "$WARM_EPS" \
  --seed "$SEED" --out "data/m4/$WARM_RUN"
if [[ $gates_rc -ne 0 && "${GATES_REVIEWED:-0}" != "1" ]]; then
  echo "=== $(date -Is) gates failed: stopping before training; review $VAL_OUT/gates.txt, then rerun with GATES_REVIEWED=1"
  exit 1
fi
stage sac_per_s0 data/m4/sac_per_s0/KILLED python -m agents.train --run sac_per_s0 --episodes "$EPISODES" \
  --seed "$SEED" --warmstart "data/transitions/$WARM_RUN"
stage sac_per_cold_s0 data/m4/sac_per_cold_s0/KILLED python -m agents.train --run sac_per_cold_s0 \
  --episodes "$EPISODES" --seed "$SEED" --cold
echo "=== $(date -Is) chain done"
