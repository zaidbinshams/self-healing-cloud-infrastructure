#!/usr/bin/env bash
# [B] One-shot, read-only status of the M4 chain for monitoring (/loop checks). Never changes anything.
# Usage:  bash scripts/status.sh
cd "$(dirname "$0")/.." || exit 2
echo "now: $(date -Is)"
chain=$(ps -eo pid,cmd | awk '/[c]hain_m4.sh/ {print $1}' | head -1)
echo "chain: ${chain:-NOT RUNNING}"
echo "stage: $(grep -E '^=== .* stage ' data/logs/chain_m4.log 2>/dev/null | tail -1)"
child=$(ps -eo cmd | grep -E '[a]gents\.(run_policy|train)' | grep -v watchdog | head -1)
echo "running: ${child:-none}"
if [[ -f data/locust.pid ]] && kill -0 "$(cat data/locust.pid)" 2>/dev/null; then echo "locust: up"; else echo "locust: DOWN"; fi
[[ -f data/logs/ALERT ]] && echo "ALERT: $(tail -1 data/logs/ALERT)" || echo "ALERT: none"
for k in data/m4/*/KILLED; do [[ -f "$k" ]] && echo "KILLED: $k: $(cat "$k")"; done
echo "network (last 3 changes):"; tail -3 data/logs/network.log 2>/dev/null | sed 's/^/  /'
for f in data/m3/runbook_eval/episodes.jsonl data/m4/warmstart_runbook/episodes.jsonl \
         data/m4/sac_per_s0/episodes.jsonl data/m4/sac_per_cold_s0/episodes.jsonl; do
  [[ -f "$f" ]] || continue
  python3 - "$f" <<'EOF'
import json, sys
rows = [json.loads(l) for l in open(sys.argv[1]) if l.strip()]
done = [r for r in rows if not r.get("interrupted")]
rec = [r for r in done if r["fault"] != "NULL"]
last = rows[-1]
print(f"{sys.argv[1]}: {len(done)} episodes; recovered {sum(bool(r.get('recovered')) for r in rec)}/{len(rec)} faults; "
      f"stale ticks {sum(r.get('stale_ticks', 0) for r in done)}; last: ep {last['episode']} {last['fault']} "
      f"recovered={last.get('recovered')} mttr={last.get('mttr_s') and round(last['mttr_s'])}"
      + (f" alpha={last['alpha']:.3g}" if "alpha" in last else ""))
EOF
done
[[ -f data/m3/runbook_eval/gates.txt ]] && { echo "gates:"; sed 's/^/  /' data/m3/runbook_eval/gates.txt; }
exit 0
