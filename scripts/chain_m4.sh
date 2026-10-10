#!/usr/bin/env bash
# [B] M4 pipeline, unattended (docs/PLAN_OF_ACTION.md §4; docs/PREREGISTRATION.md §2). Mutates the cluster.
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
read -r WARM_EPS WARM_N < <(python -c 'from env.contract import load_contract as l; r=l().runbook; print(r.warmstart_epsilon, r.warmstart_episodes)')

stage() {   # stage <name> <killed-marker or -> <command...>
  local name="$1" killed="$2"; shift 2
  echo "=== $(date -Is) stage $name: $*"
  if [[ -f data/logs/ALERT ]]; then echo "=== ALERT present; stopping before $name"; exit 1; fi
  bash scripts/watchdog.sh "$@"; local rc=$?
  echo "=== $(date -Is) stage $name exit $rc"
  if [[ $rc -ne 0 || -f data/logs/ALERT ]]; then echo "=== stopping after $name"; exit 1; fi
  if [[ "$killed" != "-" && -f "$killed" ]]; then echo "=== $name KILLED: $(cat "$killed")"; exit 1; fi
}

stage warmstart - python -m agents.run_policy --policy runbook --episodes "$WARM_N" --epsilon "$WARM_EPS" \
  --seed "$SEED" --out "data/m4/$WARM_RUN"
stage sac_per_s0 data/m4/sac_per_s0/KILLED python -m agents.train --run sac_per_s0 --episodes "$EPISODES" \
  --seed "$SEED" --warmstart "data/transitions/$WARM_RUN"
stage sac_per_cold_s0 data/m4/sac_per_cold_s0/KILLED python -m agents.train --run sac_per_cold_s0 \
  --episodes "$EPISODES" --seed "$SEED" --cold
echo "=== $(date -Is) chain done"
