# Anrufblocker Agent Platform — Gün Gün Tarihli Plan

## Kapsam

Bu plan 20 Haziran 2026 - 6 Temmuz 2026 arasında dedicated GPU sunucuda profesyonel Anrufblocker Agent Platform altyapısını ayağa kaldırmak içindir.

Ana hedef Colab’dan çıkmak ve sunucuda çalışan şu çekirdeği kurmaktır:

```text
vLLM model serving
FastAPI agent backend
PostgreSQL
Redis job queue
Supervisor/control panel
Correction memory
Training candidate pipeline
Training worker
Eval worker
Model registry
Nginx reverse proxy
LiveKit/Pipecat voice adapter hazırlığı
```

Başlangıçta telefon demosu yoktur. Voice/runtime katmanı browser ve ileride LiveKit/Pipecat entegrasyonu için hazırlanır.

---

## 20 Haziran 2026 — Sunucu temel kurulumu

### Hedef

Sunucuyu güvenli, güncel ve proje için hazır hale getirmek.

### Yapılacaklar

- Ubuntu 22.04 veya 24.04 doğrulama
- SSH key ile erişim
- root login kapatma
- deploy kullanıcısı oluşturma
- UFW firewall kurulumu
- fail2ban kurulumu
- sistem update/upgrade
- temel araçların kurulumu
- ana klasör yapısının oluşturulması

### Kurulacak temel araçlar

```bash
git
curl
wget
htop
tmux
jq
unzip
build-essential
python3-dev
python3-venv
nvtop
```

### Klasörler

```text
/opt/anrufblocker
/opt/anrufblocker/repo
/opt/anrufblocker/models
/opt/anrufblocker/data
/opt/anrufblocker/backups
/opt/anrufblocker/logs
```

### Gün sonu çıktısı

```text
Sunucuya güvenli SSH erişimi var.
Proje klasörleri hazır.
Temel Linux ortamı hazır.
```

### Başarı kontrolü

```bash
whoami
hostnamectl
df -h
free -h
```

---

## 21 Haziran 2026 — NVIDIA driver, Docker ve GPU runtime

### Hedef

GPU’nun hem hostta hem Docker container içinde çalıştığını doğrulamak.

### Yapılacaklar

- NVIDIA driver kurulumu
- `nvidia-smi` doğrulama
- Docker Engine kurulumu
- Docker Compose plugin kurulumu
- NVIDIA Container Toolkit kurulumu
- GPU passthrough testi

### Gün sonu çıktısı

```text
GPU hostta ve Docker içinde görünür.
Docker Compose kullanılabilir.
```

### Başarı kontrolü

```bash
nvidia-smi

docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi
```

---

## 22 Haziran 2026 — Repo ve Docker Compose iskeleti

### Hedef

Tüm servislerin yönetileceği proje yapısını kurmak.

### Yapılacaklar

- Git repo oluşturma
- `.env.example`
- `docker-compose.yml`
- backend/panel/worker klasörleri
- nginx klasörü
- model/data bind mount planı
- ilk `/health` endpoint

### Compose servisleri

```text
nginx
postgres
redis
agent-backend
supervisor-panel
vllm-server
training-worker
eval-worker
```

### Gün sonu çıktısı

```text
docker compose up temel servisleri başlatıyor.
agent-backend /health endpoint dönüyor.
PostgreSQL ve Redis çalışıyor.
```

### Başarı kontrolü

```bash
docker compose ps
curl http://localhost:8010/health
```

---

## 23 Haziran 2026 — PostgreSQL şema ve backend DB bağlantısı

### Hedef

Session, turn, correction, training job ve model registry verilerini kalıcı hale getirmek.

### Yapılacaklar

- SQLAlchemy veya migration sistemi
- DB bağlantısı
- tabloların oluşturulması
- health check içine DB check
- ilk seed/test kayıtları

### Tablolar

```text
sessions
turns
corrections
correction_memory
training_candidates
training_jobs
model_versions
eval_runs
deployments
latency_metrics
```

### Gün sonu çıktısı

```text
Backend DB’ye yazabiliyor.
Session ve turn kayıtları oluşturulabiliyor.
```

### Başarı kontrolü

```bash
docker compose exec postgres psql -U anrufblocker -d anrufblocker
```

---

## 24 Haziran 2026 — vLLM model server

### Hedef

Merged Anrufblocker modelini vLLM ile OpenAI-compatible endpoint olarak çalıştırmak.

### Yapılacaklar

- Model klasörünü `/opt/anrufblocker/models/merged/anrufblocker-v14` altına koyma
- vLLM Docker servisini ayarlama
- model name: `anrufblocker-v14`
- `max_model_len` 2048 ile ilk test
- 4096 context test
- GPU memory kullanımını izleme

### İlk vLLM ayarları

```text
port: 8000
served_model_name: anrufblocker-v14
max_model_len: 2048
gpu_memory_utilization: 0.85
```

### Gün sonu çıktısı

```text
vLLM /v1/chat/completions endpoint çalışıyor.
Model basit Almanca testlerde JSON üretiyor.
```

### Başarı kontrolü

```bash
curl http://localhost:8000/v1/models
```

Test input:

```text
Was kostet das?
```

Beklenen:

```text
valid JSON
intent / next_action
Almanca agent_response
```

---

## 25 Haziran 2026 — Agent backend v1 ve vLLM entegrasyonu

### Hedef

Raw vLLM çağrısı yerine stateful agent backend kurmak.

### Endpointler

```text
GET /health
POST /sessions
GET /sessions/{id}
GET /sessions/{id}/turns
POST /agent-turn
```

### Yapılacaklar

- prompt builder
- vLLM client
- JSON extraction
- JSON repair
- state load/save
- turn logging
- latency ölçümü

### Gün sonu çıktısı

```text
POST /agent-turn ile stateful cevap alınabiliyor.
Her turn DB’ye kaydediliyor.
```

### Başarı kontrolü

```bash
curl -X POST http://localhost:8010/agent-turn \
  -H "Content-Type: application/json" \
  -d '{"session_id":"test-1","customer_text":"Was kostet das?"}'
```

---

## 26 Haziran 2026 — Runtime guardrails ve product facts

### Hedef

Kritik ürün bilgilerini ve satış kurallarını modelden bağımsız güvenceye almak.

### Product facts

```text
14 Tage kostenlos
danach 29,99 Euro monatlich
Apple App Store oder Google Play Store
über 7.000 bekannte Risikonummern
Unterstützung bei Anwalts- und Gerichtskosten bis zu 2.500 Euro
Support über die App
```

### Guardrail kuralları

```text
identity yoksa link gönderme
hard decline varsa kapanışa git
aynı next_action tekrar ederse yön değiştir
price_question için approved price template
security objection için approved security template
JSON invalid ise safe fallback
```

### Gün sonu çıktısı

```text
Model yanlış veya eksik cevap üretse bile runtime güvenli cevap verebiliyor.
```

---

## 27 Haziran 2026 — Supervisor panel v1

### Hedef

Canlı olmasa bile session, turn, model JSON ve correction işlemlerini görebileceğimiz paneli kurmak.

### Panel bölümleri

```text
Sessions
Session detail
Turn detail
Raw JSON
Repaired JSON
State viewer
Latency viewer
Correction editor
```

### İlk butonlar

```text
Mark Good
Mark Bad
Correct Response
Save Correction
Apply Immediately
Send to Training
```

### Gün sonu çıktısı

```text
Panelden session ve turn kayıtları görülebiliyor.
Correction oluşturulabiliyor.
```

---

## 28 Haziran 2026 — Correction memory / hotfix layer

### Hedef

Düzeltmelerin model yeniden eğitilmeden hemen sonraki davranışa etki etmesini sağlamak.

### Yapılacaklar

- `corrections` kaydı
- `correction_memory` kaydı
- apply immediately mantığı
- trigger/context matching
- prompt/rule injection
- correction hit/miss logging

### Gün sonu çıktısı

```text
Bir cevap düzeltildiğinde, benzer durumda sistem correction memory’yi dikkate alıyor.
```

### Demo senaryosu

```text
Müşteri: Was kostet das?
Eski cevap eksik.
Correction eklenir.
Sonraki fiyat sorusunda onaylı cevap kullanılır.
```

---

## 29 Haziran 2026 — Training candidate pipeline

### Hedef

Correction kayıtlarını training datası adaylarına çevirmek.

### Yapılacaklar

- correction → training_candidate dönüşümü
- assistant JSON üretimi
- metadata ekleme
- approved/rejected durumu
- JSONL export endpoint
- panelden training candidate görüntüleme

### Endpointler

```text
POST /training-candidates/from-correction/{correction_id}
GET /training-candidates
POST /training-candidates/export-jsonl
```

### Gün sonu çıktısı

```text
Correction verisi eğitim datası adayına dönüşebiliyor.
JSONL export alınabiliyor.
```

---

## 30 Haziran 2026 — Training worker skeleton

### Hedef

Arka planda eğitim job’larının başlatılabileceği job altyapısını kurmak.

### Teknoloji

```text
Redis queue
training-worker process
job status DB kaydı
job log dosyaları
panelde job progress
```

### Job tipleri

```text
build_dataset
train_lora_dry_run
merge_model_dry_run
run_eval_dry_run
```

### Gün sonu çıktısı

```text
Panelden training job başlatılabiliyor.
Job status ve loglar izlenebiliyor.
```

---

## 1 Temmuz 2026 — Gerçek training script entegrasyonu

### Hedef

Notebook training kodunu sunucuda çalışabilen script haline getirmek.

### Yapılacaklar

- `train_lora.py`
- config/env tabanlı path okuma
- DATA_PATH / EVAL_PATH parametreleri
- output model version parametresi
- logların dosyaya ve DB’ye yazılması
- job failure handling

### Eğitim datası stratejisi

```text
approved training candidates
+ stable golden examples
+ balanced base dataset
```

### Gün sonu çıktısı

```text
Training worker gerçek LoRA eğitimini başlatabiliyor veya en azından kontrollü şekilde scripti çalıştırabiliyor.
```

---

## 2 Temmuz 2026 — Merge job ve model registry

### Hedef

Eğitilen LoRA adapter’ı merged modele çevirip model registry’ye kaydetmek.

### Yapılacaklar

- `merge_model.py`
- merged_16bit export
- model metadata
- model_versions tablosu
- candidate model status
- panelde model registry ekranı

### Model registry alanları

```text
version_name
base_model
lora_path
merged_path
dataset_version
eval_status
deployment_status
metadata_json
```

### Gün sonu çıktısı

```text
Yeni model candidate registry’de görünüyor.
```

---

## 3 Temmuz 2026 — Eval worker ve sabit test seti

### Hedef

Yeni model yayına alınmadan önce otomatik test edilsin.

### Eval metrikleri

```text
JSON validity
required key coverage
next_action accuracy
hard decline handling
identity-before-link
price answer correctness
security objection correctness
loop repetition rate
latency average / p95
```

### İlk test senaryoları

```text
Was kostet das?
Ist das ein Virus-Link?
Warum haben Sie meine Nummer?
Ich blockiere schon alles.
Ich habe keine Zeit.
Schicken Sie mir das per SMS.
Ich will nichts kaufen.
Ist das kostenlos?
Was passiert nach 14 Tagen?
Können Sie mir das später erklären?
```

### Gün sonu çıktısı

```text
Her model candidate için eval raporu üretilebiliyor.
Panelde eval sonuçları görülebiliyor.
```

---

## 4 Temmuz 2026 — Deploy ve rollback mekanizması

### Hedef

Approved modelin kontrollü şekilde vLLM’e alınmasını sağlamak.

### Basit deploy

```text
vLLM container restart
new model path
health check
rollback path
```

### İleri deploy hazırlığı

```text
staging model
production model
candidate test endpoint
```

### Gün sonu çıktısı

```text
Panelden model deploy edilebiliyor.
Başarısız olursa önceki modele rollback yapılabiliyor.
```

---

## 5 Temmuz 2026 — Latency paneli, observability ve demo polish

### Hedef

Sistemin hızını, kararlarını ve eğitim pipeline’ını görünür hale getirmek.

### Metrikler

```text
llm_ms
backend_ms
guardrail_ms
total_turn_ms
model_version
token count
correction hit/miss
JSON repair needed/not needed
```

### Hazırlanacak dokümanlar

```text
README
kurulum adımları
servis portları
env değişkenleri
backup/restore
demo script
known issues
next steps
```

### Gün sonu çıktısı

```text
Sistem sunuma/provaya hazır.
Panel üzerinden tüm ana akış gösterilebiliyor.
```

---

## 6 Temmuz 2026 — Sunum ve sistem gösterimi

### Hedef

Sistemin Colab’dan çıktığını ve sunucuda profesyonel bir platform çekirdeği olarak çalıştığını göstermek.

### Gösterilecek akış

```text
1. vLLM model server çalışıyor.
2. /agent-turn ile model stateful cevap veriyor.
3. Supervisor panelde session/turn/model JSON görünüyor.
4. Cevap correction ile düzeltiliyor.
5. Correction memory’ye kaydediliyor.
6. Benzer durumda sistem düzeltmeyi kullanıyor.
7. Correction training candidate oluyor.
8. Training job queue’ya düşüyor.
9. Model registry ve eval ekranı gösteriliyor.
10. Deploy/rollback mantığı gösteriliyor.
```

### Ana mesaj

```text
Bu sistem artık notebook denemesi değil.
Sunucuda çalışan, ölçülen, düzeltilebilen, eğitime veri üreten ve model versiyonlarını yönetebilen agent platformu çekirdeğidir.
```

---

## 6 Temmuz’a kadar minimum başarılı çıktı

```text
- Dedicated GPU sunucuda çalışan Docker/servis altyapısı
- vLLM ile çalışan merged Anrufblocker modeli
- FastAPI agent backend
- PostgreSQL turn/session/correction kayıtları
- Redis job queue
- Supervisor panel v1
- Correction memory / hotfix layer
- Training candidate export
- Training worker job status
- Eval worker temel testleri
- Model registry
- Deploy/rollback tasarımı
```

---

## Bilerek sonraya bırakılanlar

```text
- Telefon entegrasyonu
- Full LiveKit/Pipecat voice runtime
- Production-grade multi-user auth
- Tam blue/green deployment
- Tam otomatik quantization pipeline
- CRM/tool entegrasyonları
```

Bu parçalar çekirdek platform sağlamlaştıktan sonra eklenecek.

