#!/usr/bin/env bash
# [B, run ONCE by a human with sudo]  Delegate ONLY the edge netem condition to the unattended runs.
#
# Installs a root-owned, read-only copy of the netem commands with the pre-registered values baked in
# (config/runs/edge_degraded.env + A_IP from config/cluster.env, read now, at install time), and a sudoers
# rule that lets the invoking user run exactly that file with apply|clear|status, without a password.
# Nothing else becomes passwordless. Later edits to the repository cannot change what runs as root.
#
#   /usr/local/sbin/capstone-netem         root:root 0755   apply | clear | status
#   /etc/sudoers.d/capstone-netem          root:root 0440   NOPASSWD for those three invocations only
#
# Usage (in your own terminal on B; sudo asks for your password locally):
#     sudo bash scripts/install_netem_sudo.sh
# Undo:
#     sudo rm /usr/local/sbin/capstone-netem /etc/sudoers.d/capstone-netem
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "run with: sudo bash $0" >&2; exit 1; }
USER_NAME="${SUDO_USER:?run via sudo from your normal user}"
cd "$(dirname "$0")/.."

A_IP=$(grep -oP '^export A_IP=\K[0-9.]+' config/cluster.env)
DELAY=$(grep -oP '^NETEM_DELAY=\K\S+' config/runs/edge_degraded.env)
LOSS=$(grep -oP '^NETEM_LOSS=\K\S+' config/runs/edge_degraded.env)
DEV=$(ip route get "$A_IP" | grep -oP ' dev \K\S+')
TC=$(command -v tc)
[[ "$A_IP" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "bad A_IP '$A_IP'" >&2; exit 1; }
[[ "$DELAY" =~ ^[0-9]+ms$ ]] || { echo "bad delay '$DELAY'" >&2; exit 1; }
[[ "$LOSS" =~ ^[0-9.]+%$ ]] || { echo "bad loss '$LOSS'" >&2; exit 1; }
[[ "$DEV" =~ ^[a-zA-Z0-9]+$ ]] || { echo "bad device '$DEV'" >&2; exit 1; }

TARGET=/usr/local/sbin/capstone-netem
TMP=$(mktemp)
cat > "$TMP" <<EOF
#!/bin/sh
# GENERATED $(date -Is) by scripts/install_netem_sudo.sh — root-owned; do not edit (re-run the installer).
# M5 edge condition: +$DELAY delay, $LOSS loss on B->A control-plane traffic only (:6443 Kube API, :30090
# Prometheus). Locust load (:30080) and everything else are untouched.
set -eu
DEV=$DEV
case "\${1:-}" in
  apply)
    $TC qdisc del dev \$DEV root 2>/dev/null || true
    $TC qdisc add dev \$DEV root handle 1: prio
    $TC qdisc add dev \$DEV parent 1:3 handle 30: netem delay $DELAY loss $LOSS
    for p in 6443 30090; do
      $TC filter add dev \$DEV protocol ip parent 1:0 prio 3 u32 match ip dst $A_IP/32 match ip dport \$p 0xffff flowid 1:3
    done
    echo "netem applied on \$DEV: delay $DELAY, loss $LOSS -> $A_IP ports 6443 30090" ;;
  clear)
    if $TC qdisc del dev \$DEV root 2>/dev/null; then echo "netem cleared on \$DEV"; else echo "nothing to clear on \$DEV"; fi ;;
  status)
    $TC qdisc show dev \$DEV; $TC filter show dev \$DEV ;;
  *)
    echo "usage: capstone-netem apply|clear|status" >&2; exit 2 ;;
esac
EOF
install -o root -g root -m 0755 "$TMP" "$TARGET"; rm -f "$TMP"

SUDOERS=/etc/sudoers.d/capstone-netem
STMP=$(mktemp)
printf '%s ALL=(root) NOPASSWD: %s apply, %s clear, %s status\n' "$USER_NAME" "$TARGET" "$TARGET" "$TARGET" > "$STMP"
visudo -cf "$STMP" >/dev/null || { echo "sudoers syntax check failed; nothing installed for sudo" >&2; rm -f "$STMP"; exit 1; }
install -o root -g root -m 0440 "$STMP" "$SUDOERS"; rm -f "$STMP"

echo "installed $TARGET (root-owned) and $SUDOERS:"
cat "$SUDOERS"
echo "values baked in: dev=$DEV A_IP=$A_IP delay=$DELAY loss=$LOSS (ports 6443, 30090)"
echo "test (no password should be asked):  sudo -n $TARGET status"
