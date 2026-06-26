# CallShield Platform — Canonical Milestone Plan

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

- üç ana çalışma alanı: `Sessions`, `Review & Train`, `Models`
- canlı session/turn, state, raw/repaired JSON ve latency görünümü
- turn-level correction ve session-level review
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

**Durum:** Koşullu tamam. GPU sunucu hazır, kabul testi bekliyor.

Mevcut:

- Redis job queue, DB status/progress/log akışı
- dataset build, LoRA train ve merge job yolları
- mock pipeline ve gerçek training script entegrasyonu
- merged candidate kaydının model registry'ye yazılması
- session review'a ait candidate ID'leriyle izole training batch
- golden/base/candidate kaynak manifestleri ve checksum'lar
- geçici artifact dizinleri ve başarı sonrası atomik yayın
- kritik product fact içeren training örnekleri için dataset validation

Kalan kabul kapısı:

- [ ] Hedef NVIDIA sunucuda gerçek LoRA eğitimi çalıştırılmalı.
- [ ] Gerçek GPU job'unda aynı manifest ve atomik artifact kabul kriterleri
  doğrulanmalı.

## M5 — Evaluation worker ve kalite kapısı

**Durum:** Koşullu tamam. GPU sunucu hazır, kabul testi bekliyor.

Mevcut:

- sabit single-turn ve multi-turn senaryolar
- JSON, policy, safety, product fact, repetition ve latency metrikleri
- eval job progress/log/result ekranları
- seçilen candidate model ID'sine özel agent routing
- `m6-gate-v1` versiyonlu pass/fail eşikleri
- mock training sonrası otomatik kalite kontrolü
- production ortamında mock eval kanıtını reddeden deployment gate

Kalan kabul kapısı:

- [ ] En az bir gerçek vLLM candidate koşusu GPU sunucuda doğrulanmalı.

## M6 — Model registry, candidate serving, deploy ve rollback

**Durum:** Koşullu tamam.

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

Doğrulanan:

- Mock candidate eval'i production aktif modelinden ayrı model ID'siyle
  çalıştı; eval turn kayıtlarının tamamı seçilen candidate sürümünü taşıdı.
- Eval geçmemiş model deploy edilemiyor.
- Ardışık iki deploy ve rollback sonrası normal `/agent-turn` trafiği doğru
  aktif modele yönlendirildi.
- Artifact/serving health, deployment ve rollback audit kayıtları tutuluyor.
- Tek GPU hedefi için blue/green serving slot Compose yapısı hazırlandı.

Kalan kabul kapısı:

- [ ] Blue/green slot değişimi, health/smoke ve rollback gerçek vLLM modelleriyle
  hedef NVIDIA sunucuda doğrulanmalı.

Ek doğrulanan:

- Training worker, `real` modda merge edilen candidate modeli otomatik olarak
  sabit candidate serving path'ine publish ediyor; ops'in elle kopyalama
  ihtiyacı kalktı.
- Candidate serving path swap'ı ModelVersion DB commit'i tamamlanana kadar geri
  alınabilir tutuluyor; commit hatasında önceki candidate otomatik geri geliyor.
- Çalışan vLLM ağırlıkları bellekte tuttuğu için dosya publish işlemi hot reload
  değildir. Candidate eval, publish sonrasında `vllm-candidate` başlatılarak veya
  yeniden başlatılarak yapılır.
- Redis queue kalıcılığı `redis-server --appendonly yes` ile açık.

## M7 — Browser voice foundation

**Durum:** Koşullu tamam.

İlk uygulama için tek runtime seçilir. Önerilen başlangıç LiveKit'tir; Pipecat
aynı milestone içinde ikinci paralel implementasyon değil, alternatif adapter
olarak tutulur.

Kapsam:

- supervisor panel Sessions üzerinden browser microphone/WebRTC testi
- streaming STT
- transcript final → `/agent-turn`
- streaming TTS ve browser playback
- voice session ile backend session eşleştirmesi
- turn bazında `stt_ms`, `backend_ms`, `llm_ms`, `tts_first_audio_ms`,
  `speech_end_to_first_audio_ms` ve `total_voice_turn_ms`

Kabul kriteri:

- Supervisor yazı yazmadan Sessions ekranından en az 10 turn konuşabilir.
- Transcript, final policy ve duyulan cevap aynı session altında izlenebilir.
- İlk hedef p95 konuşma sonu → first audio 2.5 saniyenin altındadır.

Doğrulanan:

- LiveKit media server ve named-agent dispatch çalıştı.
- Authenticated Supervisor Panel, session'a bağlı browser token'ı doğrudan
  üretiyor; ayrı voice web servisi veya 8030 portu bulunmuyor.
- Sessions ekranına senaryo seçimi, microphone start/stop, transcript/response
  event akışı, audio playback ve voice latency görünümü eklendi.
- Yerel Faster Whisper STT → `/agent-turn` → Fish Audio streaming PCM TTS
  pipeline'ı implement edildi.
- Voice session ile backend external session ID birebir eşlendi.
- Final transcript ve duyulan cevap backend turn kaydıyla doğrulanmadan voice
  metrikleri kabul edilmiyor.
- Altı voice metriği turn bazında saklanıyor; backend persistence smoke testi
  geçti.
- Konuşma sonu → first audio metriği tüm playback süresinden ayrıldı; p95 kabul
  sorgusu `speech_end_to_first_audio_ms` kullanıyor.

Kalan kabul kapısı:

- [ ] Gerçek GPU Whisper modeli ve Fish Audio ile browser üzerinden en az 10 turn
  konuşulmalı.
- [ ] Aynı testte konuşma sonu → first audio p95 değeri 2.5 saniyenin altında
  doğrulanmalı.
- Canlı kontrol listesi `docs/LIVE_ACCEPTANCE.md` içindedir.

## M8 — Realtime turn-taking ve interruption

**Durum:** Koşullu tamam.

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

Doğrulanan:

- Overlap sırasında utterance düşürmek yerine sınırlı bir input queue kullanılıyor.
- Sürekli müşteri konuşması agent playback'ini iptal ediyor ve yeni turn aynı
  backend session state'i ile işleniyor.
- Kısa Almanca backchannel seti gerçek interruption'dan ayrı sınıflanıyor.
- Duplicate final transcript ve generation tabanlı stale response koruması
  eklendi.
- Sustained overlap backend yanıtı hazırlanırken başlasa bile generation hemen
  ilerletiliyor; eski yanıt playback başlamadan düşürülüyor veya arada playback
  başlamışsa iptal ediliyor.
- `speech_started`, `speech_ended`, `interruption_detected`,
  `playback_cancelled`, `backchannel_detected`, `turn_cancelled` ve
  `stale_response_discarded` event sözleşmeleri browser'a yayınlanıyor.
- Kritik voice event'leri idempotent event ID ile `voice_events` tablosuna
  yazılıyor ve Supervisor Panel timeline'ında izleniyor.
- Panel microphone seviyesi, listening/hearing/processing/speaking/interrupted
  durumlarını ayrı animasyon ve metinlerle gösteriyor.
- Pipeline cancellation/stale-response testleri ile 20 backchannel ve 20 gerçek
  interruption örneğinden oluşan deterministik test seti repoda tutuluyor.

Kalan kabul kapısı:

- [ ] Gerçek browser/Fish Audio akışında interruption-to-cancel latency ölçülmeli
  (`interruption_latency_ms` < 600ms).
- [ ] Backchannel frases ("ja ja", "mhm okay", "ja genau") gerçek mikrofon/STT
  çıktısında `backchannel_detected` olarak sınıflanmalı; "ja aber nein" gibi
  gerçek barge-in ise agent'ı durdurmalı.
- [ ] Streaming partial transcript hipotezleri mevcut Faster Whisper batch
  segmenter'ına güvenli biçimde eklenmeli; şu anda speech boundary ve final
  transcript event'leri vardır.

## M9 — Canlı supervisor control ve anlık düzeltme

**Durum:** Koşullu tamam.

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

Doğrulanan:

- Session detail içinden canlı transcript/turn/event akışı supervisor panelde
  güncelleniyor.
- `Stop Agent` aksiyonu aktif playback'i iptal edip runtime generation'ını
  ilerletiyor; eski cevap yeniden başlamıyor.
- `Replace Answer` / `Send This Instead` panelden correction kaydı, audit event
  ve room control command olarak hazırlanıyor; replacement cevap aynı session'da
  TTS ile oynatılıyor.
- `Apply Immediately` ve `Send to Training` seçenekleri aynı live correction
  isteğine bağlandı; correction memory ve training candidate üretimi mevcut
  correction pipeline üzerinden izlenebilir kalıyor.
- Supervisor aksiyonları `supervisor_action_requested`,
  `supervisor_stop_applied`, `supervisor_replacement_started`,
  `supervisor_replacement_completed` ve `supervisor_action_ignored` event'leri
  ile audit timeline'a düşüyor.

Kalan kabul kapısı:

- [ ] Gerçek browser mikrofonu + LiveKit oturumunda supervisor replacement akışının
  uçtan uca manuel kabulü hedef GPU host üzerinde alınmalı.
- [ ] Rol/yetki katmanı şu an panel authentication sınırına dayanıyor; production
  rollout öncesi daha dar supervisor izin modeli gerekiyorsa M10 operasyon
  hardening kapsamında netleştirilmeli.

## M10 — Voice performansı ve production hardening

**Durum:** Koşullu tamam.

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

Doğrulanan:

- Voice runtime backend çağrıları için timeout + circuit breaker eklendi.
- STT arızası session'ı düşürmeden `stt_unavailable` ile izole ediliyor.
- Fish TTS hata verdiğinde mock PCM fallback devreye girebiliyor ve panelde
  `tts_fallback_activated` olarak görünür oluyor.
- Session detail sayfasında voice health özeti, son turn latency görünümü ve
  acceptance readiness paneli eklendi.
- Supervisor panel voice console beklenmeyen room disconnect sonrası sınırlı
  otomatik recovery deniyor; manuel stop/end session yolları ise recovery'yi
  bilinçli olarak kapatıyor.
- Session recovery modülü eklendi; servis restart sonrası aktif session state'i
  tutarlı kalıyor.
- CTranslate2 Whisper model path doğrulaması startup'ta fast-fail veriyor.
- Config validation cpu/cuda moduna göre uygun hata/uyarı seviyesi kullanıyor.

Kalan kabul kapısı:

- [ ] Uçtan uca tracing, p50/p95/p99 dashboard, interruption latency trendleri
  hedef GPU host üzerinde doğrulanmalı.
- [ ] Concurrency/load/soak testleri GPU ortamında çalıştırılmalı.
- [ ] PII/retention politikaları, TLS, rate limit ve operasyon runbook'u
  production rollout öncesi netleştirilmeli.

## M12 — Doğal dil düzeltme derleyicisi

**Durum:** Tamamlandı.

Bağlam:

Sistem şu an yapısal düzeltme destekliyor: supervisor tam cevap metnini yazar,
bu training datasına girer. Ancak supervisor "Burada fiyat sormuş, önce 14 gün
ücretsiz demeli" gibi doğal dil talimat yazdığında sistem bunu işleyemiyor.

Yaklaşım — LLM'siz kural tabanlı derleyici:

Supervisor review alanına serbest metin yazar. Sistem bunu kural tabanlı
bir "Review Compiler" ile işler:

1. Anahtar sözcük ve kalıp eşleştirmesi ile düzeltme tipi çıkarılır
   (`product_fact_correction`, `missing_step`, `wrong_next_action` vb.)
2. İlgili product fact şablonları otomatik uygulanır (fiyat, deneme süresi vb.)
3. Panelde önizleme gösterilir: orijinal turn + üretilen yapısal correction
4. Operatör [Onayla] / [Düzenle] / [Reddet] seçer
5. Onaylanırsa mevcut correction pipeline'ına → correction memory +
   training candidate olarak düşer

LLM kullanılmama nedenleri:

- Yanlış training data üretme riski yok
- Product fact hataları daha az (şablondan geliyor)
- Tahmin edilebilir ve debug edilebilir
- Panelde açıklaması kolay

Kapsam:

- Review Compiler modülü (kural tabanlı, anahtar sözcük eşleştirici)
- Desteklenen correction tipleri: `product_fact_correction`,
  `missing_step`, `wrong_next_action`, `tone_correction`
- Panel önizleme UI: orijinal turn yan yana + üretilen correction
- Onayla/Düzenle/Reddet akışı
- Onaylanan correction mevcut pipeline'a enjekte edilir
- Derleyici kural seti test edilebilir ve genişletilebilir olmalı

Kabul kriteri:

- Supervisor fiyat ile ilgili doğal dil yorum yazar → derleyici doğru
  `product_fact_correction` tipi ve Almanca şablon cevabı üretir.
- Yanlış eşleşmede operatör düzenleyebilir veya reddedebilir; sistem
  yanlış veriyi training'e almaz.
- Onaylanan correction correction memory ve training candidate tablosuna
  mevcut M3 pipeline'ı üzerinden izlenebilir biçimde yazılır.

Doğrulanan:

- Fiyat/deneme ve link güvenliği talimatları agent-backend'deki authoritative
  product fact şablonlarından derleniyor; panelde şablon kopyası bulunmuyor.
- `product_fact_correction`, `missing_step`, `wrong_next_action` ve
  `tone_correction` kuralları deterministik testlerle doğrulandı.
- Review ekranı orijinal yanıt ile derlenmiş correction'ı yan yana gösteriyor;
  operatör metni ve next action'ı onay öncesinde düzenleyebiliyor.
- Approve mevcut correction endpoint'i üzerinden correction memory ve training
  candidate kayıtlarını oluşturuyor. Reject yalnız önizlemeyi kaldırıyor.
- Güvenli kuralla eşleşmeyen talimat tahmin edilmiyor ve manuel düzeltmeye
  yönlendiriliyor.

## M11 — Telefon/pilot entegrasyonu

**Durum:** M7–M10 sonrasına bırakıldı.

Kapsam:

- telephony/SIP provider entegrasyonu
- çağrı lifecycle ve numara/consent kuralları
- pilot traffic, kalite takibi ve operasyon prosedürleri
- gerekiyorsa CRM/tool entegrasyonları

Browser voice demo ve güvenilir interruption tamamlanmadan telefon entegrasyonu
başlatılmaz.

## GPU Kabul Testi Sırası

GPU sunucu hazır (89.105.220.109, NVIDIA RTX PRO 6000 Black, 97887 MiB VRAM).
Aşağıdaki sırayla ilerlenir:

```text
1. [ ] M4 — Gerçek LoRA eğitimi (training-worker-gpu)
2. [ ] M5 — Gerçek vLLM candidate eval koşusu
3. [ ] M6 — Blue/green slot swap + rollback (vllm-candidate)
4. [ ] M7 — 10-turn browser voice (GPU Whisper + Fish Audio, p95 < 2500ms)
5. [ ] M8 — Barge-in latency < 600ms + backchannel sınıflandırma
6. [ ] M9 — Supervisor replacement uçtan uca (canlı browser)
7. [ ] M10 — Load/soak + tracing dashboard + runbook
8. [ ] M11 — Telefon/SIP pilot
```

Candidate artifact publish ve Redis kalıcılığı GPU öncesinde tamamlandı.
`vllm-candidate`, yeni publish edilen modeli process başlangıcında yükler; GPU
kabul prosedürü bu nedenle container start/restart, readiness ve served-model
kimliği kontrolünü içerir. M12 yerel geliştirme ve testleri tamamlanmıştır;
telefon/pilot öncesinde ayrıca bir M12 geliştirme kapısı kalmamıştır.
