# CallShield Platform — Teknik Referans Dökümantasyonu

> Bu dosya sistemdeki her bileşeni, her .env değişkenini, her senaryoyu ve değişiklik yaptığında ne olacağını açıklar.

---

## İçerik

1. [Genel Mimari](#1-genel-mimari)
2. [Servisler](#2-servisler)
3. [Veri Akışı](#3-veri-akışı)
4. [Veritabanı Modelleri](#4-veritabanı-modelleri)
5. [API Endpoint'leri](#5-api-endpointleri)
6. [.env Değişkenleri — Tam Açıklama](#6-env-değişkenleri--tam-açıklama)
7. [Docker Compose Profilleri](#7-docker-compose-profilleri)
8. [Senaryolar — Ne Yaparsam Ne Olur](#8-senaryolar--ne-yaparsam-ne-olur)
9. [Dosya Yapısı](#9-dosya-yapısı)

---

## 1. Genel Mimari

```
                     ┌─────────────┐
  Browser/Phone ────►│    Nginx    │◄──── 80/443
                     └──────┬──────┘
                             │
              ┌──────────────┼──────────────────┐
              ▼              ▼                   ▼
     ┌──────────────┐ ┌─────────────┐   ┌───────────────┐
     │agent-backend │ │supervisor-  │   │ livekit-server│
     │  :8010       │ │panel :8020  │   │  :7880/7881   │
     └──────┬───────┘ └──────┬──────┘   └──────┬────────┘
            │                │                  │
            ▼                ▼                  ▼
     ┌──────────────────────────────────────────────────┐
     │              PostgreSQL  :5432                   │
     │              Redis       :6379                   │
     └──────────────────────────────────────────────────┘
                                                │
                     ┌──────────────────────────┘
                     ▼
            ┌─────────────────┐
            │ voice-runtime-  │  (livekit agent worker)
            │ worker          │  STT → agent-backend → TTS
            └────────┬────────┘
                     │
          ┌──────────┴──────────────┐
          ▼                         ▼
  ┌──────────────┐        ┌──────────────────┐
  │ Whisper STT  │        │  Fish Audio TTS   │
  │ (local GPU)  │        │  (API/cloud)      │
  └──────────────┘        └──────────────────┘
```

### Kim kime konuşur?

| Kaynak | Hedef | Protokol | Ne İçin |
|--------|-------|----------|---------|
| Nginx | agent-backend | HTTP | `/api/*` proxy |
| Nginx | supervisor-panel | HTTP | `/` proxy |
| supervisor-panel | agent-backend | HTTP (httpx) | Session start, turn proxy |
| voice-runtime | agent-backend | HTTP (aiohttp) | `/agent-turn` çağrısı |
| voice-runtime | livekit-server | WebSocket (SDK) | Ses yayını |
| Browser | livekit-server | WebRTC/WebSocket | Supervisor mic/audio |
| training-worker | Redis | Redis protocol | Job kuyruğu polling |
| eval-worker | Redis | Redis protocol | Job kuyruğu polling |
| eval-worker | agent-backend | HTTP | `/agent-turn` isolation test |

---

## 2. Servisler

### 2.1 `agent-backend` (`:8010`)

Ana iş mantığı servisi. FastAPI ile yazılmış, stateless HTTP API.

**Ne yapar:**
- Her telefon görüşmesi turunu işler (`POST /agent-turn`)
- Session state'ini PostgreSQL'de tutar
- vLLM'e chat request atar, yanıtı JSON parse eder
- Guardrail'leri uygular (ürün fiyatları, güvenlik kuralları)
- Correction memory'den hotfix'leri uygular
- Latency metriklerini kaydeder

**Kritik bileşenler (`app/core/`):**
- `state_manager.py` — Session state yükle/güncelle/kaydet
- `prompt_builder.py` — LLM için sistem+geçmiş+hint mesajı oluşturur
- `vllm_client.py` — OpenAI-compat API üzerinden vLLM'e bağlanır
- `json_repair.py` — LLM çıktısını JSON'a zorlar, eksik alanları tamamlar
- `guardrails.py` — Fiyat/güvenlik/ürün fact kontrolü
- `correction_memory.py` — DB'deki correction kayıtlarını trigger key ile eşler
- `model_runtime.py` — Hangi model versiyonunun kullanılacağını belirler

**12 Adımlı Turn Akışı:**
```
1. Session yükle/oluştur (PostgreSQL)
2. State yükle (state_json JSONB)
3. Correction memory'den hint'leri al
4. Prompt oluştur (sistem + son 5 turn + hint'ler)
5. Model versiyonu resolve et (prod deployment veya eval isolation)
6. vLLM'e POST at → raw string al
7. JSON çıkar (regex + fallback)
8. JSON onar (eksik alanları doldur)
9. Correction memory override uygula
10. Guardrail uygula (fiyat doğrulama, güvenlik)
11. State güncelle ve PostgreSQL'e yaz
12. Turn kaydı + latency metrik yaz
13. Response dön
```

---

### 2.2 `supervisor-panel` (`:8020`)

Süpervizörlerin kullandığı web arayüzü. FastAPI + Jinja2 SSR.

**Sayfalar:**
| URL | Ne Gösterir |
|-----|-------------|
| `/` | Session listesi (DataTables AJAX) |
| `/sessions/{id}` | Session detayı + voice console |
| `/review` | Review & Train kuyruğu |
| `/review/{session_id}` | Tek session review ekranı |
| `/model-registry` | Model versiyonları + deployment'lar |
| `/corrections` | Tüm correction kayıtları |
| `/training-candidates` | Eğitim datasetine gidecek örnekler |
| `/training-jobs` | Training job listesi |
| `/eval-jobs` | Eval run listesi |

**CSRF Koruması:** Her mutating POST'ta `_csrf` hidden input veya `X-CSRF-Token` header zorunlu. Token session'a bağlı.

**Auth:** JWT tabanlı cookie. `ADMIN_USER` + `ADMIN_PASSWORD` ile login.

---

### 2.3 `voice-runtime-worker`

LiveKit Agent SDK ile çalışan ses pipeline worker'ı.

**Ne yapar:**
1. LiveKit server'a `LIVEKIT_AGENT_NAME` adıyla register olur
2. Bir browser participant oda açtığında dispatcher tetikler
3. Worker bir `VoicePipeline` instance'ı oluşturur
4. Her utterance için: `ses → Whisper STT → /agent-turn → Fish TTS → ses`
5. Metrikleri `voice.events` DataChannel topic'ine yayınlar → browser'da anlık gösterilir

**Ses segmentasyonu (`UtteranceSegmenter`):**
- RMS amplitude ölçer
- `SPEECH_RMS_THRESHOLD` altında ses = sessizlik
- `SPEECH_END_SILENCE_MS` kadar sessizlik → utterance tamamlandı sayılır
- `SPEECH_MAX_MS` aşılırsa force-cut

---

### 2.4 `livekit-server`

WebRTC medya sunucusu. Browser ↔ worker arasındaki ses trafiğini yönetir.

- Port 7880: HTTP/WebSocket API
- Port 7881: TURN/STUN
- Port 7882/UDP: WebRTC media (RTP/RTCP)

---

### 2.5 `vllm-server` (GPU profili)

OpenAI-compat API sunan model inference sunucusu. Sadece `--profile gpu` ile başlar.

`VLLM_MODE=mock` ile agent-backend kendi mock yanıtını üretir, vllm-server gerekmez.

---

### 2.6 `vllm-candidate`

Pre-deploy evaluation için izole edilmiş candidate model sunucusudur. Tek GPU
hedefinde production vLLM veya training worker ile aynı anda çalıştırılmaz.
Training worker modeli sabit publish yoluna koyar; `vllm-candidate` bu modeli
başlangıçta belleğe yükler. Publish işlemi çalışan vLLM sürecinde hot reload
yapmaz.

---

### 2.7 `training-worker`

LoRA fine-tune ve model merge işlerini Redis kuyruğundan alıp çalıştırır. `--profile workers` ile başlar. GPU gerektirir.

---

### 2.8 `eval-worker`

Training sonrası model kalitesini otomatik test eder. Eval dataset'ini agent-backend'e atar, sonuçları analiz eder, `quality_score` üretir. `--profile workers` ile başlar.

---

## 3. Veri Akışı

### 3.1 Normal Telefon Turu

```
Telefon sistemi
  → POST /agent-turn {session_id, customer_text}
  → agent-backend
      → state_manager.load()    # PostgreSQL'den session state
      → correction_memory.get() # Varsa hotfix hint'leri
      → prompt_builder.build()  # LLM mesajları oluştur
      → vllm_client.chat()      # vLLM'e at (veya mock)
      → json_repair.extract()   # Raw string → JSON
      → guardrails.apply()      # Fact/güvenlik kontrolü
      → state_manager.persist() # Yeni state'i kaydet
      → Turn kaydı yaz          # turns tablosuna
  ← AgentTurnResponse
      {agent_response, policy, state, latency}
Telefon sistemi agent_response'u seslendiriyor
```

### 3.2 Voice Test (Supervisor Panel)

```
Supervisor browser
  → POST /sessions/{id}/voice-token   # LiveKit JWT al
  → LiveKit room join (WebRTC)
  → voice-runtime-worker tetiklenir
      → Whisper: ses → transkript
      → POST /agent-turn → agent-backend
      → Fish Audio TTS: metin → ses
      → LiveKit üzerinden sesi browser'a yayınla
      → voice.events DataChannel → browser'da latency göster
```

### 3.3 Correction → Training Akışı

```
Süpervizör hatalı turn'ü düzeltiyor
  → POST /corrections (session_detail ekranı)
  → Correction kaydı oluşur
  → Opsiyonel: send_to_training=true → TrainingCandidate oluşur
  → Süpervizör training-candidates listesinde approve eder
  → Export JSONL → /data/training_candidates/*.jsonl
  → Training job başlatılır (supervisor-panel üzerinden)
  → training-worker jobu alır → LoRA fine-tune
  → Tamamlanınca ModelVersion kaydı oluşur
  → Eval worker yeni versiyonu test eder
  → quality_score >= EVAL_PASS_THRESHOLD → Deploy edilebilir
  → Süpervizör gerçek eval kanıtını inceler, modeli approve eder ve inactive
    blue/green slot üzerinden deploy eder
```

### 3.4 Doğal Dil Review Compiler

Review & Train ekranında supervisor bir turn için serbest metin talimat yazar.
Panel talimatı agent-backend `/review-compiler/compile` endpoint'ine gönderir.
Derleyici LLM kullanmaz; test edilebilir kurallarla correction tipi, Almanca
yanıt ve next action önizlemesi üretir.

- Fiyat/deneme ve link güvenliği yanıtları `product_facts.py` şablonlarından gelir.
- Önizleme kalıcı veri oluşturmaz.
- Approve mevcut correction akışını kullanır; memory/training seçenekleri aynıdır.
- Reject önizlemeyi kaldırır ve kayıt oluşturmaz.
- Eşleşmeyen talimatlarda sistem tahmin yürütmez, manuel edit ister.

---

## 4. Veritabanı Modelleri

### `sessions`
| Kolon | Tip | Açıklama |
|-------|-----|---------|
| id | PK | İç ID |
| external_session_id | varchar(128) | Telefon sistemi session ID'si (unique değil) |
| status | varchar(32) | `active` / `closed` |
| current_stage | varchar(64) | Son turn'deki satış aşaması |
| state_json | JSONB | Tüm session state'i (turn_count, hard_decline_count, vb.) |
| created_at | timestamptz | — |
| updated_at | timestamptz | — |

**`state_json` içeriği:** `stage`, `turn_count`, `hard_decline_count`, `call_attempt`, `last_intent`, `emotion_trend`, vb. State manager bunu her turda günceller.

---

### `turns`
| Kolon | Açıklama |
|-------|---------|
| session_id | FK → sessions |
| turn_index | Kaçıncı tur |
| customer_text | Müşterinin söyledikleri |
| agent_response | Agent'ın verdiği yanıt |
| intent | LLM'in tespit ettiği niyet: `buy`, `cancel`, `hard_decline`, vb. |
| emotion | Duygu: `neutral`, `angry`, `happy`, vb. |
| risk | `low` / `medium` / `high` |
| next_action | `continue`, `close`, `escalate`, `hard_block`, vb. |
| allowed_to_continue | Boolean — agent görüşmeye devam edebilir mi |
| state_before_json | Turn öncesi state snapshot |
| state_after_json | Turn sonrası state snapshot |
| raw_model_json | LLM'in ham JSON çıktısı |
| repaired_model_json | json_repair sonrası |
| latency_json | `{llm_ms, backend_ms, total_ms}` |
| model_version | Hangi model versiyonu kullanıldı |

---

### `corrections`
Süpervizörün hatalı turn'e yaptığı düzeltmeler.

| Kolon | Açıklama |
|-------|---------|
| correction_type | `response_edit`, `next_action_override`, `full_reroute`, `tone_fix` |
| old_agent_response | Orijinal yanıt |
| corrected_agent_response | Doğru yanıt |
| apply_immediately | `true` → correction memory'ye ekle, canlıda hemen etkili |
| send_to_training | `true` → TrainingCandidate oluştur |

---

### `correction_memory`
`apply_immediately=true` olan correction'lardan türetilen aktif hotfix'ler.

Her turn'de `correction_memory.py` bu tabloyu kontrol eder. `trigger_key` müşteri metnindeki kelime/intent ile eşleşirse `correct_response` ve `correct_next_action` override eder.

---

### `training_candidates`
Fine-tune için onaylanmış conversation örnekleri.

`messages_json` formatı (OpenAI chat format):
```json
[
  {"role": "system", "content": "..."},
  {"role": "user",   "content": "müşteri metni"},
  {"role": "assistant", "content": "doğru yanıt"}
]
```

---

### `training_jobs`
| status | Anlamı |
|--------|--------|
| `pending` | Kuyruğa girdi, worker almadı |
| `running` | Worker aktif olarak işliyor |
| `completed` | `output_json.version_name` dolu — ModelVersion kaydı oluştu |
| `failed` | `error_message` dolu |

`progress_current` / `progress_total` → training step sayısı.

---

### `model_versions`
| deployment_status | Anlamı |
|-------------------|--------|
| `inactive` | Var ama servis edilmiyor |
| `active` | Prod'da servis edilen versiyon (`VLLM_MODEL_NAME` ile eşleşmeli) |
| `deprecated` | Eski, artık kullanılmıyor |

`eval_status`: `pending` → `running` → `completed` / `failed`

---

### `eval_runs`
Bir model versiyonuna karşı çalıştırılan evaluation. `metrics_json.quality_score` 0.0–1.0 arası.

---

### `deployments`
Model versiyonunun bir environment'a deploy edilme kaydı. `status`: `pending` → `active` / `failed` / `rolled_back`.

---

## 5. API Endpoint'leri

### agent-backend (`:8010`)

| Method | Path | Açıklama |
|--------|------|---------|
| GET | `/health` | Healthcheck — `{"status": "ok"}` |
| POST | `/sessions` | Yeni session oluştur |
| GET | `/sessions/{id}` | Session detayı |
| POST | `/agent-turn` | Ana turn endpoint'i — 12 adımlı akış |
| POST | `/corrections` | Correction kaydet |
| GET | `/corrections` | Correction listesi |
| POST | `/review-compiler/compile` | Supervisor talimatından güvenli correction önizlemesi üret |
| POST | `/training/candidates` | Training candidate ekle |
| GET | `/training/candidates` | Candidate listesi |
| POST | `/training/jobs` | Training job başlat |
| GET | `/training/jobs/{id}` | Job durumu |
| GET | `/model-registry/versions` | Model versiyonları |
| POST | `/model-registry/versions/{id}/deploy` | Versiyonu aktif et |
| POST | `/evals/runs` | Eval run başlat |
| GET | `/evals/runs/{id}` | Eval run durumu |
| POST | `/voice/metrics` | Voice turn metriklerini kaydet |

### supervisor-panel (`:8020`)

| Method | Path | Açıklama |
|--------|------|---------|
| GET | `/sessions/data` | DataTables AJAX JSON kaynağı |
| POST | `/sessions/start` | Voice test session başlat |
| POST | `/sessions/{id}/voice-token` | LiveKit JWT (yeni session + agent dispatch) |
| POST | `/sessions/{id}/voice-token-resume` | LiveKit JWT (agent dispatch olmadan, reconnect) |
| POST | `/sessions/{id}/close` | Session kapat → review'a yönlendir |

---

## 6. .env Değişkenleri — Tam Açıklama

### Genel

| Değişken | Default | Açıklama |
|----------|---------|---------|
| `PROJECT_NAME` | `fine-tuned-agent` | Log prefix'i, metadata |
| `ENVIRONMENT` | `staging` | `staging` veya `production`. `production`'da bazı güvenlik kontrolleri daha katı hale gelir (örn. `EVAL_INTERNAL_TOKEN` zorunlu olur) |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR`. `DEBUG` tüm vLLM request/response'larını da loglar |
| `API_KEY` | _(boş)_ | Boş bırakılırsa X-API-Key kontrolü tamamen atlanır — **sadece local dev'de boş bırak**. Prod: `openssl rand -hex 32` ile üret |
| `EVAL_INTERNAL_TOKEN` | _(boş)_ | Eval worker'ın izole model test endpoint'ini kullanması için token. `ENVIRONMENT=production`'da zorunlu |

---

### PostgreSQL

| Değişken | Açıklama |
|----------|---------|
| `POSTGRES_DB` | Veritabanı adı |
| `POSTGRES_USER` | Kullanıcı adı |
| `POSTGRES_PASSWORD` | **Production'da mutlaka değiştir** |
| `POSTGRES_HOST` | Docker network içinde `postgres`, dışarıdan `127.0.0.1` |
| `POSTGRES_PORT` | 5432 |

---

### Redis

| Değişken | Açıklama |
|----------|---------|
| `REDIS_URL` | `redis://redis:6379/0`. DB 0 = ana kuyruk. İleride farklı DB'ler ayrılabilir |

---

### vLLM / Model

| Değişken | Default | Açıklama |
|----------|---------|---------|
| `VLLM_MODE` | `mock` | **`mock`**: GPU yok, sabit bir test yanıtı döner. **`real`**: `VLLM_BASE_URL`'e gerçek istek atar. Değiştirince agent-backend restart ister |
| `VLLM_BASE_URL` | `http://vllm-server:8000/v1` | vLLM'in OpenAI-compat endpoint'i |
| `VLLM_MODEL_NAME` | `fine-tuned-agent-v14` | vLLM'e atılan `model` parametresi. vllm-server'daki `--served-model-name` ile eşleşmeli |
| `MODEL_HEALTH_TIMEOUT_SECONDS` | `15` | Startup'ta vLLM sağlık kontrolü için bekleme süresi |
| `ALLOW_MOCK_PRODUCTION_DEPLOY` | `false` | `true` yapılırsa `VLLM_MODE=mock` + `ENVIRONMENT=production` kombinasyonuna izin verir. **Normalde false bırak** |
| `MODEL_ACTIVE_VERSION` | `fine-tuned-agent-v14` | Model registry'de hangi versiyonun "active" sayıldığını belirler |
| `MODEL_DIR` | `/models` | Container içindeki model dizini |
| `MODEL_MERGED_PATH` | `/models/merged/fine-tuned-agent-v14` | Aktif merged modelin tam yolu. vllm-server bu dizini `/model` olarak mount eder |

---

### Candidate Model (Pre-Deploy Eval Isolation)

| Değişken | Açıklama |
|----------|---------|
| `CANDIDATE_MODEL_PATH` | Test edilecek yeni modelin dizini. `vllm-candidate` bunu mount eder |
| `CANDIDATE_MODEL_NAME` | `vllm-candidate`'in serve edeceği isim |
| `CANDIDATE_VLLM_BASE_URL` | `vllm-candidate`'in endpoint'i. Eval worker bunu kullanır |
| `CANDIDATE_PUBLISH_PATH` | Training worker'ın yeni merged candidate artifact'ı atomik olarak publish ettiği sabit runtime dizini. ModelVersion DB commit'i başarısız olursa önceki içerik geri yüklenir. Varsayılan: `/models/candidates/current` |

---

### LiveKit

| Değişken | Default | Açıklama |
|----------|---------|---------|
| `LIVEKIT_URL` | `ws://livekit-server:7880` | Docker network içi URL — voice-runtime-worker bu adrese bağlanır |
| `LIVEKIT_PUBLIC_URL` | `ws://localhost:7880` | **Browser**'ın bağlandığı URL. Prod'da `wss://voice.example.com` olmalı. HTTP/HTTPS farkı önemli: mixed content hatası verir |
| `LIVEKIT_API_KEY` | `devkey` | Room oluşturma/token imzalama anahtarı. **Prod'da değiştir** |
| `LIVEKIT_API_SECRET` | `devsecret...` | Token imzalama secret'ı. **Prod'da min 32 char, `openssl rand -hex 32`** |
| `LIVEKIT_AGENT_NAME` | `fine-tuned-agent-voice` | Worker'ın register olduğu agent adı. Token'daki `RoomAgentDispatch.agent_name` ile eşleşmeli |

---

### Whisper STT

| Değişken | Default | Açıklama |
|----------|---------|---------|
| `WHISPER_MODEL_PATH` | `/models/whisper/whisper-large-v3-turbo-german-ct2` | Container içi yol. CTranslate2 formatı (`model.bin` içermeli) |
| `WHISPER_DEVICE` | `cuda` | `cuda` (GPU) veya `cpu` (çok yavaş, sadece test) |
| `WHISPER_COMPUTE_TYPE` | `float16` | `float16` (GPU, hızlı), `int8` (daha hızlı/düşük kalite), `float32` (CPU) |
| `WHISPER_LANGUAGE` | `de` | Alman Almancası. Değiştirme — model German fine-tune |
| `WHISPER_BEAM_SIZE` | `1` | 1 = greedy decoding (en hızlı). Artırırsan kalite yükselir ama latency artar |

---

### Ses Segmentasyonu (VAD)

| Değişken | Default | Açıklama |
|----------|---------|---------|
| `SPEECH_RMS_THRESHOLD` | `350` | Ses/sessizlik eşiği (0–32768 arası). **Çok düşük**: arka plan gürültüsünü konuşma sayar. **Çok yüksek**: hafif konuşmaları kaçırır |
| `SPEECH_MIN_MS` | `250` | Bu süreden kısa utterance'ları yoksay (gürültü filtresi) |
| `SPEECH_END_SILENCE_MS` | `700` | Bu kadar sessizlik sonrası utterance bitti sayılır. **Azaltırsan** agent daha çabuk yanıtlar ama cümle ortasında keser. **Artırırsan** doğal pause'ları bekler ama latency artar |
| `SPEECH_MAX_MS` | `20000` | Utterance bu süreden uzun olamaz. Force-cut gelir |
| `SPEECH_PREROLL_MS` | `240` | Sessizliğin bitiminden geriye doğru bu kadar ses eklenir — konuşmanın başlangıcını kaçırmamak için |

---

### Fish Audio TTS

| Değişken | Default | Açıklama |
|----------|---------|---------|
| `TTS_MODE` | `fish` | `fish` (gerçek API) veya `mock` (sessiz buffer, sadece test) |
| `FISH_API_KEY` | _(boş)_ | Fish Audio API key. **`TTS_MODE=fish`'te zorunlu** — yoksa worker startup'ta crash eder |
| `FISH_TTS_REFERENCE_ID` | `446c1d...` | Klonlanan ses profili ID'si. Fish Audio panel'inden alınır. **Ses karakterini belirler** |
| `FISH_TTS_MODEL` | `s2-pro` | `s1` (hızlı/ucuz) veya `s2-pro` (daha doğal) |
| `FISH_TTS_URL` | `https://api.fish.audio/v1/tts` | Fish Audio streaming endpoint |
| `TTS_SAMPLE_RATE` | `24000` | 24kHz. LiveKit `AudioSource` ve Fish Audio aynı rate'i kullanmalı. Değiştirme |
| `TTS_REQUEST_TIMEOUT_SECONDS` | `45` | Fish API timeout |

---

### Auth

| Değişken | Açıklama |
|----------|---------|
| `JWT_SECRET` | Supervisor panel cookie'lerini imzalar. **Prod'da `openssl rand -hex 32`** |
| `ADMIN_USER` | Panel login kullanıcı adı |
| `ADMIN_PASSWORD` | **Prod'da değiştir** |

---

### CORS

| Değişken | Default | Açıklama |
|----------|---------|---------|
| `CORS_ORIGINS` | _(boş)_ | Boş = tüm origin'lere izin ver (sadece local dev uygun). Prod: `https://panel.example.com` |

---

### Eval Eşikleri

Bu değerlerin hepsi eval-worker tarafından kullanılır. Bir eval run başlatıldığında her metrik bu eşikle karşılaştırılır. Hepsi geçerse `quality_score` hesaplanır.

| Değişken | Default | Açıklama |
|----------|---------|---------|
| `EVAL_REQUEST_TIMEOUT_SECONDS` | `45` | Agent turn timeout |
| `EVAL_PASS_THRESHOLD` | `0.80` | Overall kalite skoru bu değerin üzerindeyse model deploy edilebilir |
| `EVAL_JSON_VALIDITY_MIN` | `1.0` | LLM çıktısının kaçta kaçı geçerli JSON olmalı |
| `EVAL_REQUIRED_KEY_COVERAGE_MIN` | `1.0` | JSON'da zorunlu alanların coverage'ı |
| `EVAL_NEXT_ACTION_ACCURACY_MIN` | `0.80` | `next_action` doğruluğu |
| `EVAL_HARD_DECLINE_MIN` | `1.0` | `hard_decline` durumlarının doğru işlenme oranı |
| `EVAL_IDENTITY_BEFORE_LINK_MIN` | `1.0` | Kimlik doğrulama sırasının doğruluğu |
| `EVAL_PRICE_CORRECTNESS_MIN` | `1.0` | Fiyat bilgisinin doğruluğu |
| `EVAL_SECURITY_CORRECTNESS_MIN` | `1.0` | Güvenlik kurallarına uyum |
| `EVAL_LOOP_REPETITION_MAX` | `0.0` | Döngüsel tekrar oranı — bu değerin **altında** kalmalı |

---

### Backend Timeout'ları

| Değişken | Default | Açıklama |
|----------|---------|---------|
| `BACKEND_TIMEOUT_SECONDS` | `120` | Voice-runtime'ın agent-backend'e bekleme süresi |
| `BACKEND_CONNECT_TIMEOUT_SECONDS` | `15` | TCP bağlantı kurma timeout'u |

---

## 7. Docker Compose Profilleri

```bash
# Sadece core servisler (local dev, GPU yok)
docker compose up

# Production model serving ekle
docker compose --profile gpu up

# Training + eval worker ekle
docker compose --profile workers up

# Candidate model eval sunucusunu başlat
docker compose --profile candidate-eval up -d vllm-candidate
```

| Profil | Eklenen Servisler |
|--------|-------------------|
| _(yok)_ | nginx, postgres, redis, agent-backend, supervisor-panel, livekit-server, voice-runtime-worker |
| `gpu` | + vllm-server |
| `candidate-eval` | + vllm-candidate |
| `workers` | + training-worker, eval-worker |

---

## 8. Senaryolar — Ne Yaparsam Ne Olur

### 8.1 `VLLM_MODE=mock` → `real` değiştirme

1. `vllm-server` container'ının `gpu` profiliyle ayakta olması gerekir
2. `MODEL_MERGED_PATH` doğru model dizinini göstermeli
3. `VLLM_MODEL_NAME` vllm-server'ın `--served-model-name` ile aynı olmalı
4. agent-backend restart et: `docker compose restart agent-backend`
5. **Dikkat:** vLLM model yükleme 1–3 dakika sürebilir. Bu süre içinde gelen request'ler 503 alır

---

### 8.2 `API_KEY` ayarlama (production'a geçiş)

1. `.env`'e `API_KEY=<openssl rand -hex 32 çıktısı>` ekle
2. Tüm sistemin yeniden başlaması gerekir
3. Artık her API çağrısında `X-API-Key: <key>` header'ı zorunlu
4. supervisor-panel kendi header'ını zaten `settings.api_key` ile gönderir
5. voice-runtime-worker da aynı key'i kullanır
6. Üçüncü taraf telefonla sistemler varsa onlara da key'i iletmen gerekir

---

### 8.3 Yeni candidate modeli değerlendirmek ve deploy etmek

```
1. Training job başlat (supervisor panel → Review & Train)
2. Real training tamamlanınca merged model `/models/candidates/current` yoluna
   atomik publish edilir
3. Tek GPU hostta training-worker ve production vLLM'i durdur
4. Candidate sunucusunu yeni artifact ile başlat veya yeniden oluştur:
   `docker compose --profile candidate-eval up -d --force-recreate vllm-candidate`
5. Candidate readiness ve `/v1/models` served-model kimliğini doğrula
6. Model Registry'den candidate için gerçek quality check çalıştır
7. Gate geçerse modeli approve et; inactive blue/green slot'a deploy et
8. Health/smoke başarısızsa önceki deployment'a rollback uygula
```

`current` dizininin değişmesi çalışan vLLM sürecini güncellemez. Restart/recreate
adımı bilerek GPU kabul prosedürünün parçasıdır.

---

### 8.4 `SPEECH_END_SILENCE_MS` ayarlama

| Senaryo | Önerilen Değer |
|---------|---------------|
| Telefon çağrısı (düşük latency öncelikli) | 400–500ms |
| Supervisor panel testi (doğallık öncelikli) | 700–900ms |
| Çok konuşkan müşteri (natural pause'lar uzun) | 900–1200ms |

Değişiklik sonrası voice-runtime-worker restart et.

---

### 8.5 Correction Memory nasıl çalışır?

1. Süpervizör bir turn'deki yanıtı düzeltir
2. `apply_immediately=true` seçilirse `CorrectionMemory` kaydı oluşur
3. Bir sonraki turn'de `correction_memory.get_hints()` çalışır
4. Müşteri metni veya intent, `trigger_key` ile eşleşirse hint olarak prompt'a eklenir
5. LLM yanıtından sonra `apply_override()` gerekirse doğrudan override eder
6. **Kaldırmak için:** DB'de `correction_memory` tablosunda `active=false` yap

---

### 8.6 LiveKit `LIVEKIT_PUBLIC_URL` yanlışsa ne olur?

Browser `WebSocket connection failed` hatası alır. Senaryo:
- Sunucu IP'si `10.0.0.5` ise `LIVEKIT_PUBLIC_URL=ws://10.0.0.5:7880` olmalı
- HTTPS üzerindeyse `wss://` kullanmalısın (mixed content bloğu)
- `ws://localhost:7880` sadece local makine için çalışır — başka cihazdan erişiliyorsa localhost değil gerçek IP/domain olmalı

---

### 8.7 PostgreSQL'e local bağlanma

```bash
psql -h 127.0.0.1 -p 5432 -U fine_tuned_agent -d fine_tuned_agent
# password: .env'deki POSTGRES_PASSWORD
```

Port `127.0.0.1:5432:5432` olarak expose edildiği için sadece local makineden erişilebilir (0.0.0.0 değil).

---

### 8.8 Redis inspect

```bash
# Container'a bağlan
docker compose exec redis redis-cli

# Job kuyruğunu gör
KEYS *
LRANGE training_jobs 0 -1
```

RedisInsight web UI: `http://localhost:8001`

---

### 8.9 `EVAL_PASS_THRESHOLD` ayarlama

`0.80` = tüm metrik eşiklerinin ağırlıklı ortalaması bu değerin üzerindeyse model geçer.

Düşürürsen (örn. `0.70`) daha az kaliteli modeller de deploy edilebilir hale gelir.  
Artırırsan (örn. `0.90`) çok daha titiz bir kalite filtresi olur ama modeller sık sık fail olabilir.

---

### 8.10 `ALLOW_MOCK_PRODUCTION_DEPLOY=true` — tehlikeli!

Normalde `ENVIRONMENT=production` + `VLLM_MODE=mock` kombinasyonu agent-backend startup'ta `RuntimeError` fırlatır. Bu değişkeni `true` yaparak bu kontrolü bypass edebilirsin — ama production'da mock yanıtlar döner. **Sadece test senaryolarında kullan.**

---

### 8.11 Training worker restart sonrası job durumu

Training worker çöker veya restart edilirse:
- `running` status'undaki joblar **takılı kalır** (otomatik recovery yok)
- Manuel: DB'de `UPDATE training_jobs SET status='pending', started_at=NULL WHERE status='running'`
- Worker restart sonrası pending job'ları otomatik alır

---

## 9. Dosya Yapısı

```
fine-tuned-agent/
├── .env                          # Gerçek konfigürasyon — gitignore'd
├── .env.example                  # Template — değişken listesi
├── docker-compose.yml            # Tüm servisler
├── SYSTEM_REFERENCE.md           # Bu dosya
│
├── infra/
│   ├── nginx/nginx.conf          # Reverse proxy konfigürasyonu
│   └── scripts/
│       ├── backup_postgres.sh    # DB yedeği al
│       ├── restore_postgres.sh   # DB'yi geri yükle
│       ├── download_models.sh    # Whisper + base model indir
│       ├── check_gpu.sh          # GPU varlığını kontrol et
│       └── setup_cron_backup.sh  # Otomatik yedek cron'u kur
│
├── models/                       # Gitignore'd (binary dosyalar)
│   ├── base/                     # İndirilmiş temel model
│   ├── lora/                     # LoRA adapter'ları
│   ├── merged/                   # Merge edilmiş production modelleri
│   ├── candidates/               # Pre-deploy test için adaylar
│   ├── approved/                 # Onaylanmış versiyonlar
│   └── whisper/                  # Whisper STT modeli
│
├── data/                         # Gitignore'd (runtime verisi)
│   ├── datasets/                 # Export edilmiş JSONL dataset'leri
│   ├── training_candidates/      # Ham training örnekleri
│   ├── training_logs/            # Fine-tune log dosyaları
│   ├── eval_logs/                # Eval run logları
│   ├── eval_results/             # Eval sonuç JSON'ları
│   ├── golden/                   # Ürün gerçekleri (product_facts.jsonl)
│   └── base/                     # Base conversation örnekleri
│
├── services/
│   ├── agent-backend/
│   │   └── app/
│   │       ├── core/             # İş mantığı modülleri
│   │       ├── routes/           # FastAPI endpoint'leri
│   │       ├── models.py         # SQLAlchemy ORM
│   │       ├── schemas.py        # Pydantic request/response
│   │       └── config.py         # Pydantic settings
│   │
│   ├── supervisor-panel/
│   │   └── app/
│   │       ├── routes/           # FastAPI + Jinja2 routes
│   │       ├── templates/        # HTML şablonları
│   │       ├── static/           # CSS, JS, vendor
│   │       ├── auth.py           # JWT session
│   │       ├── csrf.py           # CSRF koruması
│   │       └── config.py         # Panel ayarları
│   │
│   ├── voice-runtime/
│   │   └── app/
│   │       ├── pipeline.py       # Ana ses pipeline (STT→backend→TTS)
│   │       ├── worker.py         # LiveKit agent worker
│   │       ├── stt.py            # Whisper wrapper
│   │       ├── tts.py            # Fish Audio wrapper
│   │       ├── backend.py        # agent-backend HTTP client
│   │       ├── segmenter.py      # VAD/utterance segmentasyonu
│   │       └── config.py         # Voice ayarları
│   │
│   ├── training-worker/
│   │   └── worker.py             # LoRA fine-tune + merge
│   │
│   └── eval-worker/
│       └── worker.py             # Otomatik kalite değerlendirme
│
└── tests/                        # Gitignore'd — local scriptler
    └── seed_test_data.py         # Test verisi oluştur
```
