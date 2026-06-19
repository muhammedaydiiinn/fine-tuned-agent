#!/usr/bin/env bash
# GPU sunucusuna temel bağımlılıkları kurar (Ubuntu 22.04 / 24.04)
# daily_plan 20-21 Haziran adımlarına karşılık gelir
set -euo pipefail

echo "=== Sistem güncelleme ==="
apt-get update && apt-get upgrade -y

echo "=== Temel araçlar ==="
apt-get install -y \
    git curl wget htop tmux jq unzip \
    build-essential python3-dev python3-venv \
    nvtop fail2ban ufw

echo "=== Docker Engine ==="
if ! command -v docker &>/dev/null; then
    curl -fsSL https://get.docker.com | sh
    systemctl enable --now docker
else
    echo "Docker zaten kurulu."
fi

echo "=== NVIDIA Container Toolkit ==="
if ! dpkg -l | grep -q nvidia-container-toolkit; then
    distribution=$(. /etc/os-release; echo "$ID$VERSION_ID")
    curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
        | gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
    curl -s -L "https://nvidia.github.io/libnvidia-container/$distribution/libnvidia-container.list" \
        | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
        | tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
    apt-get update
    apt-get install -y nvidia-container-toolkit
    nvidia-ctk runtime configure --runtime=docker
    systemctl restart docker
else
    echo "NVIDIA Container Toolkit zaten kurulu."
fi

echo "=== Proje klasörleri ==="
mkdir -p /opt/anrufblocker/{repo,models,data,backups,logs}
echo "Klasörler oluşturuldu: /opt/anrufblocker/"

echo "=== UFW Firewall ==="
ufw allow ssh
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable
ufw status

echo ""
echo "Kurulum tamamlandı. GPU kontrolü için: bash infra/scripts/check_gpu.sh"
