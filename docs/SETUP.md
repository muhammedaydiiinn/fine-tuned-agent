# GPU Server Setup Guide

Step-by-step instructions for setting up the CallShield Agent Platform on a dedicated
GPU server. For milestone scope and acceptance criteria see `docs/MILESTONES.md`.

## Prerequisites

- Dedicated server with an NVIDIA GPU
- Ubuntu 22.04 or 24.04
- SSH key-based root access

---

## Step 1 — Base OS

```bash
apt update && apt upgrade -y
apt install -y git curl wget htop tmux jq unzip build-essential python3-dev python3-venv nvtop
```

Recommended directory layout:

```text
/opt/fine-tuned-agent/
  repo/
  models/
  data/
  backups/
  logs/
```

Verify:

```bash
whoami && hostnamectl && df -h && free -h
```

---

## Step 2 — NVIDIA Driver, Docker, and GPU Runtime

```bash
# NVIDIA driver (adjust to your GPU generation)
ubuntu-drivers autoinstall
reboot

# Verify GPU
nvidia-smi

# Docker Engine
curl -fsSL https://get.docker.com | sh

# NVIDIA Container Toolkit
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | gpg --dearmor \
  -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
# add repo from https://nvidia.github.io/libnvidia-container/stable/deb/$(dpkg --print-architecture)/nvidia-container-toolkit.list
apt update && apt install -y nvidia-container-toolkit
nvidia-ctk runtime configure --runtime=docker
systemctl restart docker

# Verify GPU passthrough
docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi
```

---

## Step 3 — Repo and .env

```bash
cd /opt/fine-tuned-agent
git clone <repo-url> repo
cd repo
cp .env.example .env
# Edit .env — see docs/SYSTEM_REFERENCE.md for all variables
```

Key `.env` values for GPU production mode:

```bash
WHISPER_DEVICE=cuda
WHISPER_COMPUTE_TYPE=float16
VLLM_MODE=real
TTS_MODE=fish
FISH_API_KEY=<secret>
FISH_TTS_REFERENCE_ID=<approved German voice>
LIVEKIT_PUBLIC_URL=wss://<voice-host>
LIVEKIT_API_KEY=<generated>
LIVEKIT_API_SECRET=<at least 32 characters>
```

---

## Step 4 — Model Files

```bash
bash infra/scripts/download_models.sh ./models
```

This downloads:
- LLM (merged CallShield model) → `models/merged/fine-tuned-agent-v14/`
- Whisper STT → `models/whisper/whisper-large-v3-turbo-german/`

Use `--llm-only` or `--whisper-only` to download selectively.

---

## Step 5 — Start All Services

```bash
# Core services (no GPU profile needed for most testing)
docker compose up -d postgres redis livekit-server agent-backend supervisor-panel voice-runtime-worker

# Add vLLM (GPU profile)
docker compose --profile gpu up -d vllm-server
```

Verify:

```bash
docker compose ps
curl http://localhost:8010/health
curl http://localhost:8000/v1/models        # vLLM
```

vLLM initial settings in `.env` (tune to your GPU):

```bash
VLLM_MODEL_NAME=fine-tuned-agent-v14
VLLM_MAX_MODEL_LEN=2048
VLLM_GPU_MEMORY_UTILIZATION=0.85
```

---

## Step 6 — Smoke Tests

```bash
# Agent turn
curl -X POST http://localhost:8010/agent-turn \
  -H "Content-Type: application/json" \
  -d '{"session_id":"smoke-1","customer_text":"Was kostet das?"}'

# Run unit test suite
bash scripts/run_unit_tests.sh
```

---

## Step 7 — Live Acceptance

Follow the manual acceptance tests in `docs/LIVE_ACCEPTANCE.md`:

1. **M7** — 10-turn browser voice session with real Whisper + Fish Audio, p95 < 2500ms
2. **M8** — Barge-in latency baseline + multi-token backchannel verification

---

## What is NOT included here

- Telephone integration (M11)
- Multi-user production auth
- Full blue/green deployment
- CRM/tool integrations

These are added after the core platform stabilises. For the full milestone roadmap
see `docs/MILESTONES.md`.
