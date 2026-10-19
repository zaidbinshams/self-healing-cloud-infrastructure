#!/usr/bin/env bash
# [B] Start-of-session checks before (re)launching the M4 chain. Does NOT launch anything long-running
# except Locust at U_base (the steady load every check and episode needs).
#   1. A reachable; single node Ready at the expected IP; hostname noted (it changed once, 13 Oct)
#   2. boutique: all deployments available; leftover StressChaos/HPA or drift from golden -> restored by an
#      env reset (no fault) after Locust is up, so the checks measure the true baseline
#   3. Locust at U_base (started if not running), then preflight m2 (Prometheus, Locust, A memory, A-B clock skew)
#   4. steady check against the frozen calibration (--quick skips it: only after a break in which A was
#      not used, unplugged or rebooted)
# Exit 0 = ready to launch:  GATES_REVIEWED=1 nohup bash scripts/chain_m4.sh >> data/logs/chain_m4.log 2>&1 &
#
# Usage:  bash scripts/session_start.sh [--quick]
set -uo pipefail
cd "$(dirname "$0")/.."
source .venv/bin/activate
set -a; source config/cluster.env; set +a
K="kubectl --kubeconfig config/kube/admin.kubeconfig --request-timeout=10s"
QUICK=0; [[ "${1:-}" == "--quick" ]] && QUICK=1
fail() { echo "SESSION START: FAIL - $*"; exit 1; }

echo "== 1. Machine A"
ping -c3 -W2 "$A_IP" >/dev/null 2>&1 || fail "A ($A_IP) not reachable: check the hotspot and A"
nodes=$($K get nodes -o jsonpath='{range .items[*]}{.metadata.name} {.status.conditions[?(@.type=="Ready")].status} {.status.addresses[?(@.type=="InternalIP")].address}{"\n"}{end}') \
  || fail "Kubernetes API not answering"
echo "$nodes"
[[ $(echo "$nodes" | grep -c .) -eq 1 ]] || fail "expected exactly one node (a hostname change leaves a stale node object; see memory/CLAUDE.md)"
echo "$nodes" | grep -q " True $A_IP" || fail "node not Ready at $A_IP"

echo "== 2. boutique state"
notready=$($K -n boutique get deploy -o jsonpath='{range .items[*]}{.metadata.name} {.status.availableReplicas}/{.spec.replicas}{"\n"}{end}' | awk -F'[ /]' '$2 != $3')
[[ -z "$notready" ]] || fail "deployments not available: $notready"
left=$($K -n boutique get stresschaos,hpa --no-headers 2>/dev/null | grep -c .)
[[ "$left" -eq 0 ]] && echo "  no leftover StressChaos/HPA" || echo "  NOTE: $left leftover StressChaos/HPA (the next reset removes them)"
drift=$(python - <<'EOF'
from env.contract import load_contract
from scripts.calibrate import verify_golden
print(len(verify_golden(load_contract())))
EOF
) || fail "golden check could not run"
echo "  golden drift: $drift deployment(s)"
NEED_RESTORE=0; [[ "$left" -gt 0 || "$drift" -gt 0 ]] && NEED_RESTORE=1

echo "== 3. Locust + preflight"
if [[ -f data/locust.pid ]] && kill -0 "$(cat data/locust.pid)" 2>/dev/null; then echo "  Locust running"; else
  U=$(python -c 'import json; print(json.load(open("config/calibration.json"))["u_base"])')
  bash locust/run_locust.sh --users "$U" --background; echo "  waiting 60 s for the load to settle"; sleep 60; fi
if [[ $NEED_RESTORE -eq 1 ]]; then
  echo "  restoring golden state (env reset, no fault) so the checks measure the baseline"
  python - <<'EOF' || fail "golden restore failed"
from env.boutique_env import make_env
env = make_env("session-start-restore", 0, faults_enabled=False)
try:
    env.reset()
    print("  restore done")
finally:
    env.close()
EOF
fi
PF=$(mktemp); python -m scripts.preflight --stage m2 > "$PF" 2>&1; pf_rc=$?
tail -16 "$PF"; rm -f "$PF"
[[ $pf_rc -eq 0 ]] || fail "preflight m2 has a FAIL (see above)"

if [[ $QUICK -eq 1 ]]; then
  echo "== 4. steady check SKIPPED (--quick: A was untouched since the last passing check)"
else
  echo "== 4. steady check (10 min)"
  python -m scripts.steady_check || fail "steady check failed: find the cause (battery? power mode? other load on A?)"
fi
echo
echo "SESSION START: READY. Launch:  GATES_REVIEWED=1 nohup bash scripts/chain_m4.sh >> data/logs/chain_m4.log 2>&1 &"
