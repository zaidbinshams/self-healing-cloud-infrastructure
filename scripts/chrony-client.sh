#!/usr/bin/env bash
# [B] Run once on Machine B (WSL2) with sudo:  sudo bash scripts/chrony-client.sh
# Replaces systemd-timesyncd with chrony that follows ONLY Machine A (human-approved 2026-10-01,
# CLAUDE.md §2 Environment Overrides). Run k8s/k3s/chrony-server.sh on A first.
#
# WSL2 NOTE: chronyd-starter.sh adds `-x` under WSL, so this chronyd only MEASURES the A-B offset
# (`chronyc -h 127.0.0.1 sources`); it never adjusts the clock. B's kernel clock is owned by WSL's
# system VM (its own chronyd on [::1]:323, refclock PHC0 = Windows host clock). The clock fix is
# scripts/w32time-follow-a.ps1 on the Windows host. Do not set SYNC_IN_CONTAINER=yes: two daemons
# would fight over one clock.
set -euo pipefail

[[ $EUID -eq 0 ]] || { echo "run with sudo" >&2; exit 1; }
A_IP="${A_IP:-192.168.137.10}"
CONF=/etc/chrony/chrony.conf

# Preflight: A must answer NTP before we drop the internet sources.
python3 - "$A_IP" <<'PY'
import socket, sys
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.settimeout(3)
s.sendto(b"\x23" + 47 * b"\0", (sys.argv[1], 123))
try:
    d, _ = s.recvfrom(48)
except TimeoutError:
    sys.exit(f"ERROR: no NTP reply from {sys.argv[1]}:123 - run k8s/k3s/chrony-server.sh on A first")
print(f"A answers NTP (stratum {d[1]})")
PY

apt-get update
apt-get install -y chrony            # conflicts with and removes systemd-timesyncd

[[ -f $CONF.orig ]] || cp "$CONF" "$CONF.orig"
cat >"$CONF" <<EOF
# capstone: Machine B follows Machine A only (original saved as chrony.conf.orig)
server ${A_IP} iburst minpoll 4 maxpoll 6
# Only effective outside WSL (under WSL chronyd runs with -x, see header).
makestep 0.1 -1
driftfile /var/lib/chrony/chrony.drift
logdir /var/log/chrony
log tracking measurements statistics
EOF

systemctl disable --now systemd-timesyncd 2>/dev/null || true
systemctl enable chrony
systemctl restart chrony
sleep 10
chronyc sources -v
chronyc tracking
