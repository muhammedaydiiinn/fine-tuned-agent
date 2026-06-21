# Canlı Ortam Kabul Testleri (GPU Host)

Bu dosya mock/unit-test ile doğrulanamayan, gerçek ses + GPU Whisper gerektiren
kabul kriterlerini listeler. Her milestone sonrası güncellenir.

---

## M8 — Interruption Hardening

Mock testler 43/43 geçti. Aşağıdakiler GPU sunucusunda elle doğrulanır.

### Ortam Hazırlığı

`.env` değiştirilecek değerler:

```bash
WHISPER_DEVICE=cuda
WHISPER_COMPUTE_TYPE=float16
VLLM_MODE=real
```

Servisler ayağa kaldır (GPU profile olmadan ses testi için yeterli):

```bash
docker compose up -d postgres redis livekit-server agent-backend supervisor-panel voice-runtime-worker
```

Seed:

```bash
POSTGRES_PASSWORD=123456 POSTGRES_USER=anrufblocker POSTGRES_DB=anrufblocker \
  python3 tests/seed_test_data.py
```

---

### Test 1 — Barge-in Latency Baseline

**Amaç:** `interruption_latency_ms` metriğinin gerçekçi değer ürettiğini doğrula.

**Adımlar:**
1. Panelden bir voice session aç.
2. Agent konuşurken sözünü kes ("Moment, was kostet das?").
3. Tur tamamlandıktan sonra panelde session detail'e bak.
4. `interruption_latency_ms` metrik hücresini oku.

**Kabul kriteri:** `interruption_latency_ms` < 600ms.
Eğer > 1000ms görüyorsan probe gecikmesi veya STT thread contention var — `barge_in_min_ms` değerini düşür.

---

### Test 2 — Multi-token Backchannel

**Amaç:** "ja ja", "mhm okay" gibi iki-token onaylamaların agent'ı durdurmadığını doğrula.

**Adımlar:**
1. Agent konuşurken "ja ja" de.
2. Agent konuşmayı kesmeden devam etmeli.
3. Panelde son event `backchannel_detected` olmalı, `interruption_detected` olmamalı.

**Test edilecek ifadeler:**
- "ja ja" → backchannel
- "mhm okay" → backchannel
- "ja genau" → backchannel
- "alles klar ja" → backchannel
- "ja aber nein" → interruption (agent durmalı)
- "okay aber warum" → interruption

**Risk:** STT bazen "ja ja" yerine "Jaja" veya "ja, ja" transcribe edebilir.
Normalize ediliyor (noktalama temizleniyor) ama beklenmedik bir form gelirse
`turn_taking_scenarios.jsonl`'e ekle ve kataloğu genişlet.

---

### Test 3 — Adaptive VAD (Opsiyonel)

**Amaç:** Arka plan gürültüsü varken false barge-in oranını ölç.

`.env`'e ekle:
```bash
SPEECH_ADAPTIVE_VAD=true
```

**Adımlar:**
1. Klavye sesi, oda gürültüsü olan ortamda agent konuşurken sessiz kal.
2. Agent sözünü kesmemeli (false barge-in olmamalı).
3. Sonra gerçekten konuşunca agent durmalı.

**Kabul kriteri:** Sessiz kalındığında `speech_started` eventi görünmemeli.
False positive görürsen `SPEECH_NOISE_FLOOR_MARGIN` değerini artır (default 2.5).

---

### Test 4 — Partial Transcript Early Cancel (Opsiyonel)

**Amaç:** Partial transcript ile barge-in latency'nin probe'dan daha erken tetiklenip tetiklenmediğini ölç.

`.env`'e ekle:
```bash
ENABLE_PARTIAL_TRANSCRIPTS=true
PARTIAL_INTERVAL_MS=300
EARLY_INTERRUPT_MIN_SPEECH_MS=500
```

**Adımlar:**
1. Agent uzun bir cümle söylerken 500ms'den uzun konuş.
2. `interruption_latency_ms` değerini Test 1'deki baseline ile karşılaştır.
3. Docker logs'ta `barge-in triggered ... source=partial` satırı görünmeli.

**Risk:** GPU Whisper 300ms interval'de her seferinde decode edebiliyor mu?
CPU contention varsa `PARTIAL_INTERVAL_MS=600` yap veya tamamen kapat.
Eğer partial text flicker yapıyorsa (farklı transcription per interval) early
cancel false positive üretebilir — bu durumda `EARLY_INTERRUPT_MIN_SPEECH_MS`
değerini artır veya özelliği kapat.

---

### Panel Kontrol Listesi

Her testten sonra panelde şunlar görünmeli:

- [ ] `interruption_latency_ms` metrik hücresi dolmuş
- [ ] Barge-in sayacı (voice events header'ında "N barge-ins")
- [ ] `backchannel_detected` eventi yeşil değil accent renginde
- [ ] `interruption_detected` eventi kırmızı

---

## Sonraki Milestones İçin Yer

M9 (model registry + deploy/rollback) kabul testleri buraya eklenecek.
