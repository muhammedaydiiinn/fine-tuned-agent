#!/usr/bin/env bash
# GPU durumunu hem hostta hem Docker içinde kontrol eder
set -euo pipefail

echo "=== Host GPU Durumu ==="
if command -v nvidia-smi &>/dev/null; then
    nvidia-smi
else
    echo "UYARI: nvidia-smi bulunamadı. NVIDIA sürücüsü kurulu değil."
    exit 1
fi

echo ""
echo "=== Docker İçi GPU Testi ==="
if command -v docker &>/dev/null; then
    docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi
else
    echo "UYARI: Docker bulunamadı."
    exit 1
fi

echo ""
echo "=== NVIDIA Container Toolkit ==="
if command -v nvidia-ctk &>/dev/null || dpkg -l 2>/dev/null | grep -q nvidia-container-toolkit; then
    echo "OK: nvidia-container-toolkit kurulu."
else
    echo "UYARI: nvidia-container-toolkit kurulu görünmüyor."
fi
