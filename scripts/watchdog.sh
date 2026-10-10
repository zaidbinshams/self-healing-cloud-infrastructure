#!/usr/bin/env bash
# Restart a long-running run after a failure (CLAUDE.md §6). Usage:
#   nohup bash scripts/watchdog.sh python -m agents.run_policy ... > data/logs/<run>.log 2>&1 &
# The wrapped command must be resumable (run_policy resumes from its episodes.jsonl). Exit 0 ends the
# watch. Any other exit (e.g. 3 = EnvironmentDegraded during a hotspot outage) waits RETRY_WAIT_S and
# restarts; after MAX_FAILS consecutive failures it writes data/logs/ALERT and stops.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 2
MAX_FAILS="${MAX_FAILS:-3}"
RETRY_WAIT_S="${RETRY_WAIT_S:-120}"
fails=0
child=0
trap 'echo "watchdog: signal received, forwarding to child $child"; [ "$child" -gt 0 ] && kill -TERM "$child"; wait "$child"; exit 0' TERM INT
while true; do
  echo "watchdog: $(date -Is) start (consecutive failures so far: $fails): $*"
  "$@" &
  child=$!
  wait "$child"; rc=$?
  child=0
  if [ "$rc" -eq 0 ]; then
    echo "watchdog: $(date -Is) command finished (exit 0)"; exit 0
  fi
  fails=$((fails + 1))
  echo "watchdog: $(date -Is) exit $rc (failure $fails/$MAX_FAILS)"
  if [ "$fails" -ge "$MAX_FAILS" ]; then
    mkdir -p data/logs
    echo "$(date -Is) watchdog gave up after $fails consecutive failures (last exit $rc): $*" >> data/logs/ALERT
    echo "watchdog: ALERT written to data/logs/ALERT"; exit 1
  fi
  sleep "$RETRY_WAIT_S"
done
