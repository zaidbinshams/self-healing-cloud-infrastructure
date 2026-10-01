#!/usr/bin/env bash
# [A] Run once on Machine A with sudo. Makes A's chrony the NTP server for Machine B.
# Human-approved 2026-10-01: A serves time, B follows (CLAUDE.md §2 Environment Overrides).
# B sits behind WSL2 NAT + Windows hotspot/ICS, so its packets arrive from the hotspot subnet.
set -euo pipefail

SUBNET="${SUBNET:-192.168.137.0/24}"
CONF=/etc/chrony/conf.d/capstone-server.conf

command -v chronyd >/dev/null || { sudo apt-get update && sudo apt-get install -y chrony; }

if ! grep -Eq '^\s*confdir\s+/etc/chrony/conf.d' /etc/chrony/chrony.conf; then
  echo "ERROR: /etc/chrony/chrony.conf has no 'confdir /etc/chrony/conf.d'; add the lines in $CONF by hand." >&2
  exit 1
fi

sudo tee "$CONF" >/dev/null <<EOF
# capstone: serve NTP to Machine B over the hotspot link
allow ${SUBNET}
# keep serving (as stratum 10) even if A loses its own upstream sources
local stratum 10 orphan
EOF

if command -v ufw >/dev/null && sudo ufw status | grep -q "Status: active"; then
  sudo ufw allow from "${SUBNET}" to any port 123 proto udp
fi

sudo systemctl enable chrony
sudo systemctl restart chrony
sleep 2
chronyc tracking
sudo chronyc serverstats
echo "OK: A is serving NTP to ${SUBNET} on udp/123"
