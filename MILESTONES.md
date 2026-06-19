# Anrufblocker Platform — Canonical Milestone Plan

Bu dosya projenin tek referans milestone planıdır. Nihai ürün bir chat sistemi
değil; düşük gecikmeli, kesilebilir, canlı düzeltilebilir ve kontrollü biçimde
yeniden eğitilebilir bir voice sales agent'tır.

## Mimari sınırlar

- `voice-runtime` konuşma akışını, streaming STT/TTS'yi, turn detection ve
  interruption/barge-in davranışını yönetir.
- `agent-backend` session/state, policy üretimi, JSON repair, guardrails,
  product facts ve correction memory'den sorumludur.
- `training-worker`, `eval-worker`, model registry ve deployment akışı kalıcı
  öğrenmeyi agent request path'inden ayrı yürütür.
- Canlı düzeltme model ağırlığını görüşme ortasında değiştirmez. Anlık etki
  correction memory/hotfix katmanından, kalıcı etki kontrollü retraining ve
  deploy akışından gelir.
- Kritik ürün bilgileri model hafızasına bırakılmaz; runtime guardrail/template
  katmanından uygulanır.

## Durum tanımları

- **Tamamlandı:** Kabul kriterleri lokal/mock ortamda doğrulandı.
- **Koşullu tamam:** Yazılım akışı mevcut, fakat hedef GPU/serving ortamındaki
  kritik kabul testi henüz yapılmadı.
- **Bekliyor:** Uygulama veya kabul kriterleri tamamlanmadı.

## M1 — Agent backend çekirdeği

**Durum:** Tamamlandı.

Kapsam:

- FastAPI, PostgreSQL, Redis, session/turn persistence
- mock/real vLLM client
- state manager, JSON repair, guardrails ve product facts
- correction memory ve turn latency kaydı

Kabul kriteri:

- `/agent-turn` stateful ve güvenli policy üretir.
- Identity-before-link, hard decline ve kritik product fact kuralları model
  çıktısından bağımsız uygulanır.
- Raw/repaired/final policy ve latency DB'ye yazılır.

## M2 — Supervisor panel çekirdeği

**Durum:** Tamamlandı.

Kapsam:

- session, turn, state, raw/repaired JSON ve latency görünümü
- correction editor
- temel authentication

Kabul kriteri:

- Operatör bir turn'ü inceleyebilir ve düzeltme kaydedebilir.
- Panel iç backend çağrıları güvenli konfigürasyonla çalışır.

Not: Canlı görüşmeyi durdurma ve müşteriye replacement audio gönderme M9
kapsamındadır; M2'nin tamamlanmış sayılması bunları içermez.

## M3 — Correction ve training candidate pipeline

**Durum:** Tamamlandı.

Kapsam:

- correction → correction memory
- correction → training candidate
- approve/reject ve JSONL export

Kabul kriteri:

- `apply_immediately` sonraki benzer policy kararını etkiler.
- Aday veri kaynak turn/correction ile izlenebilir ve tekrar üretilebilir.
- Yalnız approved candidate kayıtları eğitim datasına alınabilir.

## M4 — Training worker ve model candidate üretimi

**Durum:** Koşullu tamam.

Mevcut:

- Redis job queue, DB status/progress/log akışı
- dataset build, LoRA train ve merge job yolları
- mock pipeline ve gerçek training script entegrasyonu
- merged candidate kaydının model registry'ye yazılması

Kalan kabul kapısı:

- Hedef NVIDIA sunucuda gerçek LoRA eğitimi çalıştırılmalı.
- Approved candidates + golden examples + balanced base dataset bileşimi
  artifact manifest ile kanıtlanmalı.
- Job restart/failure durumunda yarım artifact production adayı olmamalı.
- LoRA ve merged model artifact checksum/path bilgileri registry'de tutulmalı.

## M5 — Evaluation worker ve kalite kapısı

**Durum:** Koşullu tamam.

Mevcut:

- sabit single-turn ve multi-turn senaryolar
- JSON, policy, safety, product fact, repetition ve latency metrikleri
- eval job progress/log/result ekranları

Kalan kabul kapısı:

- Eval, production'da aktif modeli değil seçilen candidate model artifact'ını
  izole biçimde çağırmalı.
- Pass/fail eşikleri versiyonlanmalı ve deployment gate olarak uygulanmalı.
- En az bir gerçek vLLM candidate koşusu GPU sunucuda doğrulanmalı.

Bu kapılar M6'nın candidate serving mekanizmasına bağımlıdır.

## M6 — Model registry, candidate serving, deploy ve rollback

**Durum:** Bekliyor.

Kapsam:

- model registry list/detail ve durum geçişleri
- `candidate → evaluated → approved → deployed → retired` yaşam döngüsü
- candidate modeli production modelden izole servis etme
- seçilen candidate'a eval çalıştırma
- yalnız başarılı eval alan model için deploy izni
- deployment kaydı, health/readiness kontrolü ve rollback
- staging/production ayrımı ve işlem audit log'u

Kabul kriteri:

- Production trafiği etkilenmeden candidate model eval edilebilir.
- Failed/unevaluated model deploy edilemez.
- Deploy sonrası health veya smoke test başarısızsa önceki sürüme otomatik ya
  da tek işlemle dönülür.
- Her deployment aktif ve önceki model sürümünü kaydeder.

## M7 — Browser voice foundation

**Durum:** Bekliyor.

İlk uygulama için tek runtime seçilir. Önerilen başlangıç LiveKit'tir; Pipecat
aynı milestone içinde ikinci paralel implementasyon değil, alternatif adapter
olarak tutulur.

Kapsam:

- browser microphone/WebRTC demo
- streaming STT
- transcript final → `/agent-turn`
- streaming TTS ve browser playback
- voice session ile backend session eşleştirmesi
- turn bazında `stt_ms`, `backend_ms`, `llm_ms`, `tts_first_audio_ms` ve
  `total_voice_turn_ms`

Kabul kriteri:

- Kullanıcı yazı yazmadan browser üzerinden en az 10 turn konuşabilir.
- Transcript, final policy ve duyulan cevap aynı session altında izlenebilir.
- İlk hedef p95 konuşma sonu → first audio 2.5 saniyenin altındadır.

## M8 — Realtime turn-taking ve interruption

**Durum:** Bekliyor.

Kapsam:

- VAD/turn detection
- agent konuşurken customer barge-in
- TTS/playback cancellation
- backchannel (`mhm`, `ja`, `okay`) ile gerçek interruption ayrımı
- partial/final transcript ve interruption event'leri
- reconnect, duplicate event ve stale response koruması

Kabul kriteri:

- Gerçek interruption algılandığında agent playback ölçülebilir biçimde kesilir
  ve eski response tekrar başlamaz.
- Yeni müşteri utterance'ı aynı session state'i ile yeni agent turn üretir.
- Backchannel test seti kabul edilen false-interrupt eşiğini aşmaz.
- Interruption, cancellation ve resumed turn event'leri panel/audit log'da
  görünür.

## M9 — Canlı supervisor control ve anlık düzeltme

**Durum:** Bekliyor.

Kapsam:

- canlı transcript/turn/event akışı
- `Stop Agent`
- `Replace Answer` / `Send This Instead`
- `Mark Good` / `Mark Bad`
- `Apply Immediately`
- `Send to Training`
- supervisor replacement cevabının TTS ile müşteriye gönderilmesi
- rol yetkisi ve supervisor action audit log'u

Kabul kriteri:

- Supervisor aktif playback'i durdurabilir.
- Replacement cevap aynı görüşmede TTS ile gönderilir.
- Tek işlem correction, correction memory ve isteğe bağlı training candidate
  kayıtlarını birbirine bağlı ve izlenebilir biçimde oluşturur.
- Sonraki benzer durumda hotfix etkisi retraining beklemeden görülür.

## M10 — Voice performansı ve production hardening

**Durum:** Bekliyor.

Kapsam:

- uçtan uca tracing ve voice latency dashboard
- p50/p95/p99 latency, interruption latency ve hata oranları
- timeout/retry/circuit breaker ve degraded fallbacks
- concurrency/load/soak testleri
- session recovery ve servis restart senaryoları
- PII, transcript/audio retention ve erişim politikaları
- secrets, TLS, rate limit, backup/restore ve operasyon runbook'ları

Kabul kriteri:

- Hedef concurrency altında belirlenmiş latency ve hata bütçeleri karşılanır.
- STT, LLM veya TTS arızasında görüşme kontrollü fallback ile sonlanır.
- Restart sonrası deployment ve session verisi tutarlı kalır.
- Production checklist ve rollback provası tamamlanır.

## M11 — Telefon/pilot entegrasyonu

**Durum:** M7–M10 sonrasına bırakıldı.

Kapsam:

- telephony/SIP provider entegrasyonu
- çağrı lifecycle ve numara/consent kuralları
- pilot traffic, kalite takibi ve operasyon prosedürleri
- gerekiyorsa CRM/tool entegrasyonları

Browser voice demo ve güvenilir interruption tamamlanmadan telefon entegrasyonu
başlatılmaz.

## Önerilen uygulama sırası

```text
M6 candidate serving + deploy gate
  → M4/M5 gerçek GPU kabul testleri
  → M7 browser voice
  → M8 interruption
  → M9 live supervisor correction
  → M10 production hardening
  → M11 telephony pilot
```

M7 için temel hazırlık M6 ile paralel ilerleyebilir; ancak M9 ve production
pilot, güvenilir M6 deploy/rollback kapısı olmadan tamamlanmış sayılmaz.
