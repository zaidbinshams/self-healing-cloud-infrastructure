#!/usr/bin/env bash
# Restart a long-running run after a failure (CLAUDE.md §6). Usage:
#   nohup bash scripts/watchdog.sh python -m agents.run_policy ... > data/logs/<run>.log 2>&1 &
# The wrapped command must be resumable (run_policy resumes from its episodes.jsonl). Exit 0 ends the
# watch. Any other exit (e.g. 3 = EnvironmentDegraded during a hotspot outage) waits RETRY_WAIT_S and
# restarts; after MAX_FAILS consecutive failures (a run of >= PROGRESS_S before failing restarts the count) it
# writes data/logs/ALERT and stops.
# LAN outages (A unreachable) do not count as failures: after a failure the watchdog waits as long as A is
# unreachable, and once the LAN is back it re-runs scripts/session_start.sh (restore, preflight, steady
# check: catches A having rebooted or run on battery) before resuming; a failed session start writes ALERT.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 2
MAX_FAILS="${MAX_FAILS:-3}"
RETRY_WAIT_S="${RETRY_WAIT_S:-120}"
PROGRESS_S="${PROGRESS_S:-900}"      # a child that ran this long before failing made progress: new count
LAN_POLL_S="${LAN_POLL_S:-30}"
A_IP=$(grep -oP '^export A_IP=\K[0-9.]+' config/cluster.env)
lan_up() { ping -c3 -W2 "$A_IP" >/dev/null 2>&1; }
alert() { mkdir -p data/logs; echo "$(date -Is) $*" >> data/logs/ALERT; echo "watchdog: ALERT written to data/logs/ALERT"; exit 1; }
fails=0
child=0
trap 'echo "watchdog: signal received, forwarding to child $child"; [ "$child" -gt 0 ] && kill -TERM "$child"; wait "$child"; exit 0' TERM INT
while true; do
  echo "watchdog: $(date -Is) start (consecutive failures so far: $fails): $*"
  t_start=$SECONDS
  "$@" &
  child=$!
  wait "$child"; rc=$?
  child=0
  if [ "$rc" -eq 0 ]; then
    echo "watchdog: $(date -Is) command finished (exit 0)"; exit 0
  fi
  if ! lan_up; then
    echo "watchdog: $(date -Is) exit $rc with A unreachable: waiting for the LAN (not counted as a failure)"
    while ! lan_up; do sleep "$LAN_POLL_S"; done
    echo "watchdog: $(date -Is) LAN back: running session_start.sh before resuming"
    if ! bash scripts/session_start.sh > "data/logs/session_start_$(date +%Y%m%dT%H%M).log" 2>&1; then
      alert "watchdog: session_start.sh failed after a LAN outage; not resuming: $*"
    fi
    echo "watchdog: $(date -Is) session start passed; resuming"
    continue
  fi
  if [ $((SECONDS - t_start)) -ge "$PROGRESS_S" ]; then fails=1; else fails=$((fails + 1)); fi
  echo "watchdog: $(date -Is) exit $rc (failure $fails/$MAX_FAILS)"
  if [ "$fails" -ge "$MAX_FAILS" ]; then
    alert "watchdog gave up after $fails consecutive failures (last exit $rc): $*"
  fi
  sleep "$RETRY_WAIT_S"
done
