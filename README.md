# Anrufblocker Agent Platform

Anrufblocker Gold Paket sesli satış ajanının sunucu platformu.
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
3. [Model Dosyalarını Sunucuya Taşıma](#3-model-dosyalarını-sunucuya-taşıma)
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

# RedisInsight (kuyruk ve key izleme)
open http://localhost:8001
```

---

## 2. GPU Sunucuda İlk Kurulum

### 2.1 SSH ile Bağlan ve Güvenliği Sağla

```bash
# Sunucuya bağlan (ilk kez root ile)
ssh root@SUNUCU_IP

# Deploy kullanıcısı oluştur
bash infra/scripts/setup_deploy_user.sh

# Artık deploy kullanıcısıyla bağlan
ssh deploy@SUNUCU_IP
```

`setup_deploy_user.sh` şunları yapar:

- `deploy` kullanıcısı oluşturur
- SSH public key'ini kopyalar
- Root SSH girişini kapatır
- `sudo` yetkisi tanır

### 2.2 Sistem ve GPU Bağımlılıklarını Kur

```bash
# Repo'yu çek
git clone <repo_url> /opt/anrufblocker/repo
cd /opt/anrufblocker/repo

# Tüm bağımlılıkları kur (Docker, NVIDIA Container Toolkit, UFW, fail2ban)
sudo bash infra/scripts/install_host_dependencies.sh
```

Script şunları kurar:

- Sistem güncellemesi
- `git curl wget htop tmux jq nvtop fail2ban ufw`
- Docker Engine
- NVIDIA Container Toolkit
- Proje klasörleri: `/opt/anrufblocker/{repo,models,data,backups,logs}`
- UFW: 22/80/443 açık, diğerleri kapalı

### 2.3 NVIDIA Driver Kurulumu (henüz kurulu değilse)

```bash
# Driver durumunu kontrol et
nvidia-smi

# Kurulu değilse:
sudo apt install -y nvidia-driver-550
sudo reboot

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
cd /opt/anrufblocker/repo
cp .env.example .env
nano .env
```

Prod için değiştirilmesi gereken değerler:

```env
ENVIRONMENT=production
POSTGRES_PASSWORD=<güçlü_şifre>
VLLM_MODE=real
MODEL_MERGED_PATH=/opt/anrufblocker/models/merged/anrufblocker-v14
API_KEY=<openssl rand -hex 32 çıktısı>
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
# Postgres yedeği her gece 02:00'de /opt/anrufblocker/backups/ klasörüne alınır
```

---

## 3. Model Dosyalarını Sunucuya Taşıma

### Google Drive'dan Doğrudan Sunucuya

```bash
pip install gdown
# Drive paylaşım linkindeki ID'yi al
gdown "https://drive.google.com/uc?id=FILE_ID" -O /tmp/model.zip
unzip /tmp/model.zip -d /opt/anrufblocker/models/merged/
```

### Mac'ten SCP ile

```bash
# Mac'ten sunucuya kopyala
scp -r ./anrufblocker-v14/ deploy@SUNUCU_IP:/opt/anrufblocker/models/merged/
```

### Beklenen Klasör Yapısı

```
/opt/anrufblocker/models/merged/anrufblocker-v14/
  config.json
  tokenizer.json
  tokenizer_config.json
  special_tokens_map.json
  model-00001-of-000XX.safetensors
  ...
  model.safetensors.index.json
```

Doğrulama:
```bash
ls /opt/anrufblocker/models/merged/anrufblocker-v14/
# config.json ve .safetensors dosyaları görünmeli
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
| vLLM             | 8000             | İç ağ        | Sadece `real` modda            |
| PostgreSQL       | 5432             | İç ağ        | —                              |
| Redis Stack      | 6379             | İç ağ        | —                              |
| RedisInsight     | 127.0.0.1:8001   | Sadece local | Kuyruk/key izleme paneli       |

> Prod'da nginx dışındaki hiçbir port dışarıya açılmamalı.
> agent-backend ve supervisor-panel portları geliştirme kolaylığı için açık;
> prod'da `docker-compose.yml`'den `ports` satırlarını kaldır.

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
| `POSTGRES_PASSWORD` | Prod'da güçlü şifre kullan | ✅ |
| `MODEL_MERGED_PATH` | Merged model klasörü (real modda) | ✅ |
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
  /opt/anrufblocker/backups/anrufblocker_20260622_020000.sql.gz
```

Yedekler `/opt/anrufblocker/backups/` klasörüne kaydedilir.
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
df -h /opt/anrufblocker/

# Postgres'e bağlan
docker compose exec postgres psql -U anrufblocker -d anrufblocker

# Turn sayısını kontrol et
docker compose exec postgres psql -U anrufblocker -d anrufblocker \
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
| 2 | Supervisor panel (Jinja2/HTMX) | 🔜 Sıradaki |
| 3 | Correction flow + training candidate pipeline | ⏳ Bekliyor |
| 4 | Training worker (gerçek LoRA) | ⏳ Bekliyor |
| 5 | Eval worker + metrikler | ⏳ Bekliyor |
| 6 | Model registry + deploy/rollback | ⏳ Bekliyor |
| 7 | Voice adapter (LiveKit/Pipecat) | ⏳ Bekliyor |

---

## Notlar

- Veritabanı migration'ları (Alembic) Milestone 2'de eklenecek. Şu an `create_all` kullanılıyor.
- `training-worker` ve `eval-worker` şu an stub; `--profile workers` ile başlatılıyor.
- vLLM GPU profili: `docker compose --profile gpu up -d`
- RedisInsight sadece `127.0.0.1:8001`'e bind edilmiş — dışarıdan erişilemez.
