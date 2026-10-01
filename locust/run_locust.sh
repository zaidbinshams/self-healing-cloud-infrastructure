#!/usr/bin/env bash
# [B] Launch Locust pinned to contract locust.cpu_cores, web UI on 127.0.0.1:8089 (for /tick and
# /swarm, CLAUDE.md §8.8), autostarted against the frontend NodePort.
# Usage:  source config/cluster.env && bash locust/run_locust.sh --users 50 [--spawn-rate 10] [--background]
set -euo pipefail

: "${FRONTEND_URL:?source config/cluster.env}"
: "${LOCUST_URL:?source config/cluster.env}"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$REPO/.venv/bin/python"

USERS=""; SPAWN_RATE=""; BACKGROUND=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --users) USERS="$2"; shift 2 ;;
    --spawn-rate) SPAWN_RATE="$2"; shift 2 ;;
    --background) BACKGROUND=1; shift ;;
    *) echo "unknown arg $1" >&2; exit 2 ;;
  esac
done
[[ -n "$USERS" ]] || { echo "--users required" >&2; exit 2; }
SPAWN_RATE="${SPAWN_RATE:-$USERS}"     # default: all users within ~1 s

CORES="$("$PY" -c 'import yaml,sys; print(",".join(map(str, yaml.safe_load(open(sys.argv[1]))["locust"]["cpu_cores"])))' "$REPO/config/contract.yaml")"
WEB_HOST="$(echo "$LOCUST_URL" | sed -E 's#^https?://([^:/]+).*#\1#')"
WEB_PORT="$(echo "$LOCUST_URL" | sed -E 's#^https?://[^:/]+:([0-9]+).*#\1#')"

PIDFILE="$REPO/data/locust.pid"
LOG="$REPO/data/logs/locust.log"
mkdir -p "$REPO/data/logs"
if [[ -f "$PIDFILE" ]] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
  echo "locust already running (pid $(cat "$PIDFILE")); stop it first: kill \$(cat $PIDFILE)" >&2
  exit 1
fi

CMD=(taskset -c "$CORES" "$REPO/.venv/bin/locust"
     -f "$REPO/locust/locustfile.py" --host "$FRONTEND_URL"
     --web-host "$WEB_HOST" --web-port "$WEB_PORT"
     --autostart --users "$USERS" --spawn-rate "$SPAWN_RATE"
     --loglevel INFO)

if [[ $BACKGROUND -eq 1 ]]; then
  nohup "${CMD[@]}" >"$LOG" 2>&1 &
  echo $! >"$PIDFILE"
  echo "locust pid $! (cores $CORES), log $LOG, UI $LOCUST_URL"
else
  exec "${CMD[@]}"
fi
