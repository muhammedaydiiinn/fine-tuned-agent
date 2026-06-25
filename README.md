# CallShield Agent Platform

CallShield Gold Paket sesli satış ajanının sunucu platformu.
Colab/notebook ortamından çıkarılmış, dedicated GPU sunucuda çalışan üretim kalitesinde altyapı.

---

## Mimari

```text
Tarayıcı / Ses arayüzü (ilerleyen milestonelarda)
        ↓
Nginx (reverse proxy, port 80/443)
        ↓
Agent Backend API  ←→  Supervisor Panel
        ↓
State Manager + Guardrails + Correction Memory
        ↓
vLLM Model Server  (mock modda atlanır)
        ↓
JSON Policy Output → Response Repair + Runtime Safety Rules → Agent Response

Redis Stack  ←→  Training Worker  ←→  Eval Worker  ←→  Model Registry
```

---

## İçindekiler

1. [Mac'te Lokal Geliştirme](#1-macte-lokal-geliştirme)
2. [GPU Sunucuda İlk Kurulum](#2-gpu-sunucuda-ilk-kurulum)
3. [Model Dosyalarını İndirme](#3-model-dosyalarını-i̇ndirme)
4. [Servisleri Başlatma](#4-servisleri-başlatma)
5. [Servis Portları](#5-servis-portları)
6. [API Endpoint'leri](#6-api-endpointleri)
7. [Ortam Değişkenleri](#7-ortam-değişkenleri)
8. [Yedekleme ve Geri Yükleme](#8-yedekleme-ve-geri-yükleme)
9. [Günlük Operasyon](#9-günlük-operasyon)
10. [Milestone Durumu](#10-milestone-durumu)

---

## 1. Mac'te Lokal Geliştirme

GPU gerekmez. `VLLM_MODE=mock` ile tüm akış test edilebilir.

```bash
# Ortam dosyasını kopyala (VLLM_MODE=mock olarak kalır)
cp .env.example .env

# Servisleri başlat
docker compose up -d postgres redis agent-backend supervisor-panel

# Sağlık kontrolü
curl http://localhost:8010/health
# → {"status":"ok","db":true,"redis":true,"vllm_mode":"mock"}

# Agent turn testi
curl -X POST http://localhost:8010/agent-turn \
  -H "Content-Type: application/json" \
  -d '{"session_id":"test-1","customer_text":"Was kostet das?"}'

# API dokümantasyonu
open http://localhost:8010/docs

```

---

## 2. GPU Sunucuda İlk Kurulum

### 2.1 SSH ile Bağlan

```bash
ssh root@SUNUCU_IP
```

### 2.2 Sistem ve GPU Bağımlılıklarını Kur

```bash
# Repo'yu çek
git clone <repo_url> /opt/fine-tuned-agent/repo
cd /opt/fine-tuned-agent/repo

# Tüm bağımlılıkları kur (Docker, NVIDIA Container Toolkit, UFW, fail2ban)
bash infra/scripts/install_host_dependencies.sh
```

Script şunları kurar:

- Sistem güncellemesi
- `git curl wget htop tmux jq nvtop fail2ban ufw`
- Docker Engine
- NVIDIA Container Toolkit
- Proje klasörleri: `/opt/fine-tuned-agent/{repo,models,data,backups,logs}`
- UFW: 22/80/443 açık, diğerleri kapalı

### 2.3 NVIDIA Driver Kurulumu (henüz kurulu değilse)

```bash
# Driver durumunu kontrol et
nvidia-smi

# Kurulu değilse:
apt install -y nvidia-driver-550
reboot

# Reboot sonrası doğrula
nvidia-smi
bash infra/scripts/check_gpu.sh
```

### 2.4 GPU Docker Testini Doğrula

```bash
bash infra/scripts/check_gpu.sh
# GPU hem hostta hem Docker içinde görünmeli
```

### 2.5 Ortam Dosyasını Ayarla

```bash
cd /opt/fine-tuned-agent/repo
cp .env.example .env
nano .env
```

Prod için değiştirilmesi gereken değerler:

```env
ENVIRONMENT=production
POSTGRES_PASSWORD=<güçlü_şifre>
VLLM_MODE=real
MODEL_MERGED_PATH=/opt/fine-tuned-agent/models/merged/fine-tuned-agent-v14
API_KEY=<openssl rand -hex 32 çıktısı>
EVAL_INTERNAL_TOKEN=<openssl rand -hex 32 çıktısı>
JWT_SECRET=<openssl rand -hex 32 çıktısı>
ADMIN_PASSWORD=<güçlü_şifre>
```

API key üretmek için:
```bash
openssl rand -hex 32
```

### 2.6 Otomatik Yedeklemeyi Kur

```bash
bash infra/scripts/setup_cron_backup.sh
# Postgres yedeği her gece 02:00'de /opt/fine-tuned-agent/backups/ klasörüne alınır
```

---

## 3. Model Dosyalarını İndirme

Platform iki model kullanır:

| Model | Kaynak | Hedef Klasör | Kullanım |
|-------|--------|-------------|----------|
| **fine-tuned-agent-v14** | Google Drive (özel) | `models/merged/fine-tuned-agent-v14/` | vLLM — ana satış ajanı |
| **whisper-large-v3-turbo-german-ct2** | Local (CTranslate2) | `models/whisper/whisper-large-v3-turbo-german-ct2/` | STT — Almanca ses tanıma (M7) |

---

### 3.1 Otomatik İndirme (Önerilen)

İki modeli de tek script ile indir:

```bash
# GPU sunucuda (varsayılan hedef: /opt/fine-tuned-agent/models)
bash infra/scripts/download_models.sh

# Mac'te (lokal test için)
bash infra/scripts/download_models.sh ./models
```

Script şunları yapar:
- `gdown` ve `huggingface_hub` yoksa otomatik kurar
- Model zaten mevcutsa atlar (tekrar indirmez)
- Drive klasörünü `fine-tuned-agent-v14/` olarak yeniden adlandırır
- HuggingFace'den sadece PyTorch ağırlıklarını indirir (flax/tf atlanır)

> **Google Drive erişimi:** Klasör "herkese açık" olarak paylaşılmış olmalı.
> Erişim hatası alırsan Drive'da klasörü sağ tıkla → Paylaş → Bağlantıya sahip herkes → Görüntüleyici yap.

---

### 3.2 Manuel İndirme

**LLM — Google Drive:**

```bash
pip install gdown

# Tüm klasörü indir
gdown --folder "https://drive.google.com/drive/folders/YOUR_GDRIVE_FOLDER_ID" \
      -O /opt/fine-tuned-agent/models/merged/

# Klasör adı farklıysa yeniden adlandır
mv /opt/fine-tuned-agent/models/merged/<drive_klasör_adı> \
   /opt/fine-tuned-agent/models/merged/fine-tuned-agent-v14
```

**Whisper — HuggingFace:**

```bash
pip install huggingface_hub

huggingface-cli download primeline/whisper-large-v3-turbo-german \
  --local-dir /opt/fine-tuned-agent/models/whisper/whisper-large-v3-turbo-german-ct2 \
  --local-dir-use-symlinks False \
  --ignore-patterns "*.msgpack" "flax_model*" "tf_model*"
```

**Mac'ten SCP ile sunucuya taşıma:**

```bash
# LLM modelini Mac'ten sunucuya kopyala
scp -r ./models/merged/fine-tuned-agent-v14/ \
    deploy@SUNUCU_IP:/opt/fine-tuned-agent/models/merged/

# Whisper modelini Mac'ten sunucuya kopyala
scp -r ./models/whisper/whisper-large-v3-turbo-german-ct2/ \
    deploy@SUNUCU_IP:/opt/fine-tuned-agent/models/whisper/
```

---

### 3.3 Beklenen Klasör Yapısı

```
models/
├── merged/
│   └── fine-tuned-agent-v14/          ← LLM (vLLM'e yüklenir)
│       ├── config.json
│       ├── tokenizer.json
│       ├── tokenizer_config.json
│       ├── special_tokens_map.json
│       ├── model-00001-of-000XX.safetensors
│       └── model.safetensors.index.json
│
└── whisper/
    └── whisper-large-v3-turbo-german-ct2/    ← Whisper STT CTranslate2 (Milestone 7)
        ├── config.json
        ├── model.bin
        ├── vocabulary.json
        └── preprocessor_config.json
```

**Doğrulama:**

```bash
# LLM modeli
ls /opt/fine-tuned-agent/models/merged/fine-tuned-agent-v14/config.json
# → dosya görünmeli

# Whisper modeli
ls /opt/fine-tuned-agent/models/whisper/whisper-large-v3-turbo-german-ct2/model.bin
# → dosya görünmeli

# Disk kullanımı özeti
du -sh /opt/fine-tuned-agent/models/merged/fine-tuned-agent-v14/
du -sh /opt/fine-tuned-agent/models/whisper/whisper-large-v3-turbo-german-ct2/
```

---

### 3.4 .env Güncelleme

Model indirildikten sonra `.env` dosyasını kontrol et:

```env
MODEL_MERGED_PATH=/opt/fine-tuned-agent/models/merged/fine-tuned-agent-v14
WHISPER_MODEL_PATH=/opt/fine-tuned-agent/models/whisper/whisper-large-v3-turbo-german-ct2
```

---

## 4. Servisleri Başlatma

### Lokal / Mock Mod (GPU gerekmez)

```bash
docker compose up -d postgres redis agent-backend supervisor-panel
```

### GPU Sunucu — Tüm Servisler

```bash
# Ana servisler + vLLM
docker compose --profile gpu up -d

# Training ve eval worker'ları da başlat
docker compose --profile gpu --profile workers up -d
```

### Servis Durumu

```bash
docker compose ps
docker compose logs -f agent-backend
```

### Servis Yeniden Başlatma

```bash
docker compose restart agent-backend
docker compose up -d --build agent-backend   # Kod değişikliği sonrası
```

---

## 5. Servis Portları

| Servis           | Port             | Erişim       | Açıklama                       |
|------------------|------------------|--------------|-------------------------------|
| nginx            | 80 / 443         | Halka açık   | Tek giriş noktası              |
| agent-backend    | 8010             | İç ağ        | FastAPI                        |
| supervisor-panel | 8020             | İç ağ        | Yönetim paneli                 |
| LiveKit          | 7880/7881 TCP, 7882 UDP | Browser/WebRTC | M7 media server       |
| vLLM             | 8000             | İç ağ        | Sadece `real` modda            |
| PostgreSQL       | 5432             | İç ağ        | —                              |
| Redis Stack      | 6379             | İç ağ        | —                              |

> Prod'da nginx dışındaki hiçbir port dışarıya açılmamalı.
> agent-backend ve supervisor-panel portları geliştirme kolaylığı için açık;
> prod'da `docker-compose.yml`'den `ports` satırlarını kaldır.

Browser voice testi ayrı bir uygulama değildir. Supervisor Panel'de
`Sessions → Start Voice Test` üzerinden senaryo seçilir, mikrofon başlatılır ve
aynı session ekranında konuşma, latency ve test adımları izlenir.

---

## 6. API Endpoint'leri

```
GET  /health                             Sistem sağlık durumu
POST /sessions                           Yeni session oluştur
GET  /sessions/{id}                      Session bilgisi
GET  /sessions/{id}/turns                Session turn listesi
POST /agent-turn                         Ana agent çağrısı (12 adımlı akış)
GET  /docs                               Swagger UI
```

API Key zorunlu olduğunda her isteğe header ekle:
```bash
curl -H "X-API-Key: <api_key>" http://localhost:8010/health
```

---

## 7. Ortam Değişkenleri

Tüm değişkenler `.env.example`'da açıklamalı olarak tanımlıdır.

| Değişken | Açıklama | Önemli |
|----------|----------|--------|
| `VLLM_MODE` | `mock` (local) veya `real` (GPU sunucu) | ✅ |
| `API_KEY` | Boşsa kontrol atlanır; prod'da doldur | ✅ |
| `EVAL_INTERNAL_TOKEN` | Candidate eval routing için servisler arası gizli token | ✅ |
| `POSTGRES_PASSWORD` | Prod'da güçlü şifre kullan | ✅ |
| `MODEL_MERGED_PATH` | LLM model klasörü — `models/merged/fine-tuned-agent-v14` | ✅ |
| `WHISPER_MODEL_PATH` | Whisper STT klasörü — Milestone 7'de kullanılır | — |
| `LIVEKIT_PUBLIC_URL` | Browser'ın erişeceği LiveKit WebSocket URL'si | ✅ |
| `LIVEKIT_API_KEY/SECRET` | LiveKit token imzalama bilgileri | ✅ |
| `FISH_API_KEY` | Streaming TTS erişim anahtarı | ✅ |
| `VLLM_MODEL_NAME` | vLLM'e yüklenen model adı | — |
| `JWT_SECRET` | Panel auth için | — |

---

## 8. Yedekleme ve Geri Yükleme

```bash
# Manuel yedek al
bash infra/scripts/backup_postgres.sh

# Otomatik yedekleme kur (cron, her gece 02:00)
bash infra/scripts/setup_cron_backup.sh

# Yedekten geri yükle
bash infra/scripts/restore_postgres.sh \
  /opt/fine-tuned-agent/backups/fine_tuned_agent_20260622_020000.sql.gz
```

Yedekler `/opt/fine-tuned-agent/backups/` klasörüne kaydedilir.
30 günden eski yedekler otomatik silinir.

---

## 9. Günlük Operasyon

```bash
# Tüm servislerin durumu
docker compose ps

# Logları canlı izle
docker compose logs -f agent-backend
docker compose logs -f vllm-server

# GPU bellek kullanımı
watch -n 2 nvidia-smi

# Disk kullanımı (model dosyaları büyük olabilir)
df -h /opt/fine-tuned-agent/

# Postgres'e bağlan
docker compose exec postgres psql -U fine_tuned_agent -d fine_tuned_agent

# Turn sayısını kontrol et
docker compose exec postgres psql -U fine_tuned_agent -d fine_tuned_agent \
  -c "SELECT count(*) FROM turns;"

# Tüm servisleri durdur
docker compose down

# Tüm servisleri durdur + volume'ları sil (DİKKAT: veri silinir)
docker compose down -v
```

---

## 10. Milestone Durumu

| # | Konu | Durum |
|---|------|-------|
| 1 | Çekirdek (backend + guardrails + correction_memory) | ✅ Tamamlandı |
| 2 | Supervisor panel (Jinja2/HTMX) | ✅ Tamamlandı |
| 3 | Correction flow + training candidate pipeline | ✅ Tamamlandı |
| 4 | Training worker + model candidate üretimi | 🟡 Koşullu tamam — gerçek GPU kabulü bekliyor |
| 5 | Eval worker + kalite kapısı | 🟡 Koşullu tamam — gerçek vLLM kabulü bekliyor |
| 6 | Model lifecycle + blue/green deploy/rollback | 🟡 Koşullu tamam — gerçek GPU kabulü bekliyor |
| 7 | Browser voice foundation (streaming STT/TTS) | 🟡 Koşullu tamam — gerçek 10-turn/p95 kabulü bekliyor |
| 8 | Realtime turn-taking + interruption/barge-in | 🟡 Koşullu tamam — canlı latency/backchannel kabulü bekliyor |
| 9 | Canlı supervisor control + replacement audio | ⏳ Bekliyor |
| 10 | Voice performansı + production hardening | ⏳ Bekliyor |
| 11 | Telefon/pilot entegrasyonu | ⏳ Sonraki faz |

Kapsam, bağımlılıklar ve kabul kriterleri için
[`docs/MILESTONES.md`](./docs/MILESTONES.md) tek referans plandır.

---

## Notlar

- Veritabanı hâlâ `create_all` kullanıyor. M5'in `eval_runs` kolonları başlangıçta idempotent
  `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` ile yükseltiliyor; uzun vadede Alembic'e geçilmeli.
- `training-worker` ve `eval-worker` `--profile workers` ile başlatılır.
- vLLM GPU profili: `docker compose --profile gpu up -d`
