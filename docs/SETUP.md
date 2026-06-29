# GPU Sunucu Kurulum Rehberi

Anrufblocker Agent Platform'u özel bir GPU sunucusuna adım adım kurulum talimatları.
Milestone kapsamı ve kabul kriterleri için `docs/MILESTONES.md` dosyasına bakın.

## Ön Koşullar

- NVIDIA GPU'lu özel sunucu
- Ubuntu 22.04 veya 24.04
- SSH key tabanlı root erişimi

---

## Adım 1 — Temel İşletim Sistemi

```bash
apt update && apt upgrade -y
apt install -y git curl wget htop tmux jq unzip build-essential python3-dev python3-venv nvtop
```

Önerilen dizin yapısı:

```text
/opt/anrufblocker/
  repo/
  models/
  data/
  backups/
  logs/
```

Doğrulama:

```bash
whoami && hostnamectl && df -h && free -h
```

---

## Adım 2 — NVIDIA Driver, Docker ve GPU Runtime

```bash
# NVIDIA driver (GPU neslinize göre ayarlayın)
ubuntu-drivers autoinstall
reboot

# GPU doğrulama
nvidia-smi

# Docker Engine
curl -fsSL https://get.docker.com | sh

# NVIDIA Container Toolkit
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | gpg --dearmor \
  -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
# repo'yu şuradan ekleyin: https://nvidia.github.io/libnvidia-container/stable/deb/$(dpkg --print-architecture)/nvidia-container-toolkit.list
apt update && apt install -y nvidia-container-toolkit
nvidia-ctk runtime configure --runtime=docker
systemctl restart docker

# GPU passthrough doğrulama
docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi
```

---

## Adım 3 — Repo ve .env

```bash
cd /opt/anrufblocker
git clone <repo-url> repo
cd repo
cp .env.example .env
# .env dosyasını düzenleyin — tüm değişkenler için docs/SYSTEM_REFERENCE.md'ye bakın
```

GPU production modu için temel `.env` değerleri:

```bash
WHISPER_DEVICE=cuda
WHISPER_COMPUTE_TYPE=float16
VLLM_MODE=real
TTS_MODE=fish
FISH_API_KEY=<secret>
FISH_TTS_REFERENCE_ID=<onaylı Almanca ses>
LIVEKIT_PUBLIC_URL=wss://<voice-host>
LIVEKIT_API_KEY=<generated>
LIVEKIT_API_SECRET=<en az 32 karakter>
```

---

## Adım 4 — Model Dosyaları

```bash
bash infra/scripts/download_models.sh ./models
```

Aşağıdakiler indirilir:
- LLM (merged Anrufblocker modeli) → `models/merged/anrufblocker-v14/`
- Whisper STT → `models/whisper/whisper-large-v3-turbo-german/`

Seçici indirme için `--llm-only` veya `--whisper-only` kullanın.

---

## Adım 5 — Tüm Servisleri Başlatma

```bash
# Production GPU stack
docker compose up -d --build
```

Doğrulama:

```bash
docker compose ps
curl http://localhost:8010/health
curl http://localhost:8000/v1/models        # vLLM
```

`.env` içindeki başlangıç vLLM ayarları (GPU'nuza göre ayarlayın):

```bash
VLLM_MODEL_NAME=anrufblocker-v14
VLLM_MAX_MODEL_LEN=2048
VLLM_GPU_MEMORY_UTILIZATION=0.85
```

---

## Adım 6 — Smoke Testleri

```bash
# Agent turn
curl -X POST http://localhost:8010/agent-turn \
  -H "Content-Type: application/json" \
  -d '{"session_id":"smoke-1","customer_text":"Was kostet das?"}'

# Unit test suite'i çalıştır
bash scripts/run_unit_tests.sh
```

---

## Adım 7 — Canlı Kabul Testleri

Manuel kabul testleri için `docs/LIVE_ACCEPTANCE.md` dosyasını takip edin:

1. **M7** — Gerçek Whisper + Fish Audio ile 10 turn'lük tarayıcı ses oturumu, p95 < 2500ms
2. **M8** — Barge-in gecikme testi + çok token'lı backchannel doğrulaması

---

## Kapsam Dışındakiler

- Telefon entegrasyonu (M11)
- Çok kullanıcılı production auth
- Tam blue/green deployment
- CRM/tool entegrasyonları

Bunlar, temel platform kararlı hale geldikten sonra eklenir. Tam milestone yol haritası için
`docs/MILESTONES.md` dosyasına bakın.
