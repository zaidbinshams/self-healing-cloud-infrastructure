#!/usr/bin/env bash
# [A] Run once on Machine A as a sudo-capable user. Not run from Machine B.
# Swap off, chrony, pinned K3s with traefik disabled and the LAN IP in the cert SANs.
set -euo pipefail

: "${A_IP:?set A_IP to Machine A's static LAN IP}"
K3S_VERSION="${K3S_VERSION:-v1.36.4+k3s1}"

sudo apt-get update
sudo apt-get install -y chrony curl
sudo systemctl enable --now chrony

sudo swapoff -a
sudo sed -i.bak '/\sswap\s/ s/^/#/' /etc/fstab

curl -sfL https://get.k3s.io | INSTALL_K3S_VERSION="${K3S_VERSION}" \
  INSTALL_K3S_EXEC="server --disable traefik --tls-san ${A_IP} --write-kubeconfig-mode 644" sh -

# metrics-server ships with K3s and stays enabled (HPA baseline needs it).
# Do NOT install NVIDIA drivers or the device plugin.
sudo k3s kubectl get nodes -o wide
