# Canlı Kabul Testleri (GPU Host)

Bu dosya, mock/birim testlerle doğrulanamayan kabul kriterlerini listeler — gerçek ses,
GPU Whisper ve Fish Audio TTS gerektirir. Her milestone sonrasında güncellenir.

---

## Production vLLM Baseline — Tamamlandı

23 Haziran 2026 tarihinde `6f20ad7` commit'i ve NVIDIA RTX PRO 6000
Blackwell GPU (97887 MiB VRAM) ile test edildi.

Ortam:

```env
VLLM_MODE=real
VLLM_BASE_URL=http://vllm-server:8000/v1
VLLM_MODEL_NAME=anrufblocker-v14
```

Doğrulandı:

- `vllm-server`, `anrufblocker-v14` modelini maksimum 4096 token uzunluğuyla yükledi.
- `/v1/models` beklenen servis model kimliğini döndürdü.
- Doğrudan `/v1/chat/completions` isteği, thinking devre dışıyken HTTP 200 döndürdü.
- Uygulama `/agent-turn` endpoint'i, kalıcı bir gerçek model politikası döndürdü.
- Arka arkaya yirmi uygulama turu, upstream hatası olmadan tamamlandı.

Latency baseline:

| Metric | Result |
|---|---:|
| LLM minimum | 1526 ms |
| LLM average | 1897 ms |
| LLM p50 | 2072 ms |
| LLM p95 | 2118 ms |
| LLM maximum | 2135 ms |
| Total minimum | 1542 ms |
| Total average | 1926 ms |
| Total p50 | 2094 ms |
| Total p95 | 2146 ms |
| Total maximum | 2155 ms |
| Request failures | 0 / 20 |

Gözlemler:

- Backend işleme yaklaşık 28 ms ekliyor; model üretimi baskın süreyi oluşturuyor.
- Yaklaşık 340 karakter uzunluğundaki çıktılar yaklaşık 1.53 saniyede tamamlanıyor.
- 510–549 karakter arasındaki çıktılar yaklaşık 2.03–2.14 saniyede tamamlanıyor.
- Canlı model, `price_inquiry`, `pricing_inquiry` ve `explain_pricing` gibi standart dışı
  alias'lar üretti. Niyet/aksiyon normalizasyonu ve daha katı kısa çıktı sözleşmesi, nihai
  production kabulünden önce tamamlanmalıdır.
- Bu baseline M5'i tamamlamıyor: izole aday değerlendirmesi yapılmadı.
- Bu baseline M7/M8'i tamamlamıyor: mikrofon, GPU Whisper, streaming TTS, ilk ses veya
  kesinti ölçümü dahil edilmedi.

---

## M7 — Tarayıcı Ses Temeli

### Ön Koşullar

1. Kabul ortamını `.env` dosyasında yapılandırın:

   ```env
   LIVEKIT_PUBLIC_URL=wss://<voice-host>
   LIVEKIT_API_KEY=<generated API key>
   LIVEKIT_API_SECRET=<at least 32 characters>
   WHISPER_DEVICE=cuda
   WHISPER_COMPUTE_TYPE=float16
   TTS_MODE=fish
   FISH_API_KEY=<secret>
   FISH_TTS_REFERENCE_ID=<approved German voice>
   ```

2. Whisper modelinin `WHISPER_MODEL_PATH` konumunda mevcut olduğunu doğrulayın.
3. TCP `7880/7881` ve UDP `7882` erişimini doğrulayın.
4. Worker kaydı ve model yükleme başarısı için `voice-runtime-worker` loglarını kontrol edin.

### Fonksiyonel Kabul

- Supervisor Panel'de `Sessions → Start Voice Test` üzerinden bir oturum açın.
- Klavyeye dokunmadan en az 10 ardışık tur tamamlayın.
- Her müşteri cümlesinin konuşma akışında nihai transkript olarak göründüğünü doğrulayın.
- Duyulan yanıtın aynı tur için `agent_response` ile eşleştiğini doğrulayın.
- Tur indekslerinin 0'dan 9'a boşluk olmadan ilerlediğini doğrulayın.
- Almanca sayılar, fiyatlar ve ürün adları içeren en az üç cümle kullanın.
- Tarayıcı yeniden bağlantısı / sayfa yenileme burada kriter DEĞİLDİR; bu M8 kapsamında ele alınmaktadır.

### Latency Kabulü

Son 10 ses turu için:

```sql
SELECT
  percentile_cont(0.95) WITHIN GROUP (ORDER BY lm.value_ms) AS p95_ms,
  count(*) AS turn_count
FROM latency_metrics lm
JOIN sessions s ON s.id = lm.session_id
WHERE lm.metric_name = 'speech_end_to_first_audio_ms'
  AND s.external_session_id = '<browser-session-id>';
```

Kabul kriterleri:

- `turn_count >= 10`
- Konuşma sonu → ilk ses `p95_ms < 2500`
- Transkript/yanıt uyuşmazlığı nedeniyle reddedilen ses metrik isteği yok

### Sonuç Kaydı

Test tarihini, commit SHA'sını, GPU'yu, Whisper modelini, Fish modeli/referans ID'sini,
tarayıcı sürümünü, oturum ID'sini, p50/p95 değerlerini ve başarısız turlara ilişkin notları
buraya veya ayrı bir tarihli operasyon loguna kaydedin.

Mevcut yerel doğrulama:

- Voice runtime image build: passed
- LiveKit server/worker registration: passed
- Token tabanlı oda oluşturma ve adlandırılmış agent dispatch: passed
- Backend tur + ses metrik kalıcılığı duman testi: passed
- Production vLLM uygulama latency baseline: passed (`total p95 = 2146 ms`)
- Gerçek mikrofon + Whisper + Fish Audio 10-tur kabulü: **kısmi — aşağıya bakın**

### İlk Canlı Ses Testi — 23 Haziran 2026

`d642208` commit'i, CUDA 12.9.2 base image, GPU Whisper
`whisper-large-v3-turbo-german-ct2`, Fish Audio TTS ve gerçek mikrofon ile test edildi.

Latency (ısınmış turlar, ilk soğuk tur hariç):

| Turn | STT ms | Backend ms | TTS first ms | Speech→audio ms |
|---|---:|---:|---:|---:|
| 23 | 87 | 25 | 491 | 603 |
| 24 | 128 | 17 | 444 | 589 |
| 27 | 193 | 24 | 485 | 702 |
| **p95** | **193** | **25** | **666** | **702** |

Tur 25 ilk tur cold load: STT 5986 ms (Whisper GPU warmup — p95'ten hariç tutuldu).

Doğrulandı:

- GPU Whisper yüklendi ve doğru şekilde transkribe etti.
- Fish Audio TTS Almanca ses oynatımı üretti.
- Barge-in tetiklendi ve üretim sayacı ilerledi (1→2→3).
- Speech-end → first-audio p95 **702 ms** — 2500 ms hedefinin çok altında.

Bulunan sorunlar:

- `source=probe`, kullanıcı konuşmadığında sahte barge-in kesintilerine yol açtı. M8
  tamamlanmadan önce probe VAD eşiği ayarlanmalıdır.
- LLM, fiyat sorusunda bozuk Almanca üretti
  (`"Haben wir bieten Ihnen eine Kosten?"`). Niyet/aksiyon normalizasyonu gerekli.
- LLM bir turda İngilizce'ye geçti (`"Thanks a lot to the app."`). Dil kısıtlaması
  sistem prompt'unda veya eğitimde zorunlu kılınmalıdır.

### İkinci Canlı Ses Testi — 29 Haziran 2026 (kısmi)

Panel latency metriklerinde gözlemlendi (tek oturum, `20d5f42` commit'i):

| Metric | Value |
|---|---:|
| STT | 210 ms |
| Backend | 20 ms |
| LLM | 2304 ms |
| TTS First Audio | 513 ms |
| End → First Audio | 3185 ms |
| Total Turn | **22677 ms** ← anormal |
| Barge-in Latency | — (kaydedilmedi) |

Sorunlar:

- End → First Audio 3185 ms, 2500 ms kabul hedefinin üzerinde.
- Total Turn 22677 ms anormal — first audio ile tur sonu arasında ~19 saniyelik boşluk.
  Kök neden bilinmiyor; muhtemelen TTS streaming'in tüm sesi Total Turn'e dahil etmesi
  veya `turn_end` zaman damgasının son TTS chunk gönderildikten sonra değil, ses oynatımı
  bittikten sonra yazılması.
- Barge-in latency metriği hâlâ kaydedilmedi (test bir kesinti turu değildi).
- LLM p95 yükseliyor (2304 ms, 2135 ms baseline'a karşı). Soğuk cache veya daha uzun
  çıktı olabilir; ısınmış bir oturumda yeniden ölçün.

### Kalan Geçiş Kriterleri

- [ ] Total Turn 22677 ms'yi araştır — `turn_end`'in audio-dispatch'te mi yoksa
      audio-playback tamamlanmasında mı damgalandığını belirle; ikinciyse düzelt.
- [ ] 10 turlu bir oturumda End → First Audio değerini p95 < 2500 ms'ye çek.
      Mevcut darboğaz: LLM 2304 ms. Seçenekler: ilk cümle için TTS streaming veya
      daha kısa LLM çıktı sözleşmesi.
- [ ] Tek bir oturumda kesinti hatası olmadan 10 ardışık turu tamamla.
- [ ] `source=probe` sahte barge-in'i düzelt (M8 kapsamı).
- [ ] LLM yalnızca Almanca kısıtlamasını ve fiyat yanıt kalitesini düzelt.
- [ ] Temiz bir 10 turlu oturumda latency ölçümünü yeniden yap ve p50/p95'i kaydet.
- [ ] En az bir barge-in turunda `interruption_latency_ms` değerini kaydet.

---

## M8 — Kesinti Sertleştirme

Birim testleri: 56/56 passed (voice-runtime 56 + 76 alt test). Aşağıdakiler GPU host'ta manuel olarak doğrulanır.

### Ortam

Güncellenecek `.env` değerleri:

```bash
WHISPER_DEVICE=cuda
WHISPER_COMPUTE_TYPE=float16
VLLM_MODE=real
```

Gerekli servisleri başlatın (ses testi için GPU profile gerekmez):

```bash
docker compose up -d postgres redis livekit-server agent-backend supervisor-panel voice-runtime-worker
```

Seed:

```bash
POSTGRES_PASSWORD=123456 POSTGRES_USER=anrufblocker POSTGRES_DB=anrufblocker \
  python3 tests/seed_test_data.py
```

### Test 1 — Barge-in Latency Baseline

**Amaç:** `interruption_latency_ms` metriğinin gerçekçi değerler ürettiğini doğrula.

**Adımlar:**
1. Panel'de bir ses oturumu aç.
2. Agent konuşurken onu kes ("Moment, was kostet das?").
3. Tur tamamlandıktan sonra oturum detayındaki `interruption_latency_ms` metriğini oku.

**Kabul:** `interruption_latency_ms` < 600ms.
1000ms üzerindeyse probe gecikmesi veya STT thread çakışması var — `barge_in_min_ms` değerini düşür.

### Test 2 — Çok Token Backchannel

**Amaç:** "ja ja" ve "mhm okay" gibi iki token'lık onaylamaların agent'ı durdurmadığını doğrula.

**Adımlar:**
1. Agent konuşurken "ja ja" söyle.
2. Agent kesintisiz devam etmeli.
3. Panel'deki son event `interruption_detected` değil `backchannel_detected` olmalı.

**Test edilecek ifadeler:**

| Utterance | Expected |
|-----------|----------|
| "ja ja" | backchannel |
| "mhm okay" | backchannel |
| "ja genau" | backchannel |
| "alles klar ja" | backchannel |
| "ja aber nein" | interruption (agent durmalı) |
| "okay aber warum" | interruption |

**Risk:** STT, "ja ja"yı "Jaja" veya "ja, ja" olarak transkribe edebilir. Noktalama normalleştirilir;
ancak beklenmedik bir form belirirse `turn_taking_scenarios.jsonl` dosyasına ekleyin.

### Test 3 — Adaptif VAD (İsteğe Bağlı)

**Amaç:** Arka plan gürültüsü varlığında sahte barge-in oranını ölç.

`.env` dosyasına ekle:

```bash
SPEECH_ADAPTIVE_VAD=true
```

**Adımlar:**
1. Agent konuşurken klavye gürültüsü veya ortam gürültüsü olan bir ortamda sessiz kal.
2. Agent kesintiye uğramamalı (sahte barge-in olmamalı).
3. Gerçekten konuştuğunda agent durmalı.

**Kabul:** Sessizken hiçbir `speech_started` event'i yok.
Yanlış pozitifler oluşursa `SPEECH_NOISE_FLOOR_MARGIN` değerini artır (varsayılan 2.5).

### Test 4 — Kısmi Transkript Erken İptal (İsteğe Bağlı)

**Amaç:** Kısmi transkript barge-in'inin probe'dan daha erken tetiklenip tetiklenmediğini ölç.

`.env` dosyasına ekle:

```bash
ENABLE_PARTIAL_TRANSCRIPTS=true
PARTIAL_INTERVAL_MS=300
EARLY_INTERRUPT_MIN_SPEECH_MS=500
```

**Adımlar:**
1. Agent uzun bir cümle sunarken 500ms'den fazla konuş.
2. `interruption_latency_ms` değerini Test 1 baseline'ıyla karşılaştır.
3. Docker logları `barge-in triggered ... source=partial` içermeli.

**Risk:** GPU Whisper, CPU yükü altında 300ms aralıklarla decode edemezse
`PARTIAL_INTERVAL_MS=600` yap veya özelliği tamamen devre dışı bırak. Kısmi metin
aralıklar arasında titreşiyorsa `EARLY_INTERRUPT_MIN_SPEECH_MS` artırılması gerekebilir.

### Panel Kontrol Listesi

Her testten sonra panel'de doğrula:

- [ ] `interruption_latency_ms` metrik hücresi dolu
- [ ] Barge-in sayacı ses event başlığında görünüyor ("N barge-ins")
- [ ] `backchannel_detected` event'i vurgu rengiyle gösteriliyor (yeşil değil)
- [ ] `interruption_detected` event'i kırmızıyla gösteriliyor

---

## Yarınki Test Planı — 30 Haziran 2026

Tüm birim testleri geçiyor (toplam 111). Aşağıdakiler GPU host'ta yapılmalıdır.

### Uçuş Öncesi Kontrol (5 dk)

```bash
docker compose up -d postgres redis livekit-server agent-backend supervisor-panel voice-runtime-worker
# Doğrula:
curl http://localhost:8001/health          # agent-backend → {"status":"ok"}
curl http://localhost:8000/v1/models       # vllm-server → anrufblocker-v14 listed
```

---

### Kontrol 1 — Total Turn Anomalisi (En Yüksek Öncelik)

**Amaç:** Total Turn = 22677 ms nedenini bul.

**Adımlar:**

1. Bir ses oturumu aç ve 2–3 turu normal şekilde tamamla.
2. `voice-runtime-worker` için docker loglarını kontrol et:
   ```bash
   docker logs voice-runtime-worker 2>&1 | grep -E "turn_end|turn_start|total_turn"
   ```
3. Panel oturum detayını kontrol et: "End → First Audio" ile "Total Turn"ü yan yana karşılaştır.
4. Total Turn = End→FirstAudio + tam TTS oynatma süresi ise, `turn_end` zaman damgası
   gönderimden sonra değil oynatma bittikten sonra yazılıyor demektir.

**Kabul:** Total Turn < End→FirstAudio + 2000 ms.
Başarısız olursa: `voice_pipeline.py` dosyasında `turn_end`'in nerede emit edildiğini bul ve
bunu oynatma sonrasına değil son TTS chunk'ı gönderildikten hemen sonraya taşı.

---

### Kontrol 2 — End → First Audio < 2500 ms (M7 geçiş kriteri)

Mevcut: 3185 ms. Hedef: p95 < 2500 ms.

**Adımlar:**

1. 10 ardışık tur çalıştır (ısınmış oturum, soğuk ilk turu atla).
2. Oturum sonrası panel DB'de çalıştır:
   ```sql
   SELECT
     percentile_cont(0.5) WITHIN GROUP (ORDER BY value_ms) AS p50,
     percentile_cont(0.95) WITHIN GROUP (ORDER BY value_ms) AS p95,
     count(*) AS n
   FROM latency_metrics
   WHERE metric_name = 'speech_end_to_first_audio_ms'
     AND session_id = <your_session_id>;
   ```
3. p95 > 2500 ms ise, LLM darboğazdır (2304 ms).
   Hızlı kazanım: sistem prompt'unda daha kısa bir çıktı sözleşmesi uygula
   (maksimum 2 cümle) ve yeniden ölç.

**Kabul:** `p95 < 2500`, `n >= 10`.

---

### Kontrol 3 — Barge-in Latency (M8 Test 1)

**Adımlar:**

1. Agent'ın uzun bir cümle sunmasına izin ver (fiyat açıklaması).
2. "Moment, was kostet das genau?" diyerek kes.
3. Panel oturum detayında `interruption_latency_ms` değerini oku.

**Kabul:** `interruption_latency_ms < 600 ms`.

---

### Kontrol 4 — Backchannel ve Kesinti (M8 Test 2)

Her ifadeyi test et, beklenen event'in panel ses eventlerinde göründüğünü doğrula:

| Utterance | Expected event |
|---|---|
| "ja ja" | `backchannel_detected` |
| "mhm okay" | `backchannel_detected` |
| "ja genau" | `backchannel_detected` |
| "ja aber nein" | `interruption_detected` |
| "okay aber warum" | `interruption_detected` |

**Kabul:** 5 ifadenin tamamı doğru event'i üretiyor. Agent, backchannel ifadelerinde durmuyor.

---

### Kontrol 5 — Review → Pipeline Veri Akışı

**Raporlanan sorunu yeniden üret:**

1. Tamamlanmış turları olan bir oturum aç.
2. Review'e git, **Good** seç, düzeltme yapmadan kaydet.
3. Pipeline'a git — Training Data sayısının > 0 olduğunu doğrula.

**Sayı 0 kalırsa:**  
`_collect_review_candidates` fonksiyonu yalnızca `rating == "good"` VEYA düzeltme mevcut olduğunda aday ekliyor.
Test sırasında rating'in "Good" olduğunu doğrula. Düzeltme olmadan "Mixed" veya "Bad" ise, 0 mevcut
kasıtlı davranıştır — ancak bunun değişip değişmemesi gerektiğini değerlendirin.

---

### Panel Kontrol Listesi (Her Ses Testinin Ardından)

- [ ] Oturum detayında `interruption_latency_ms` dolu
- [ ] Oturum detayında `speech_end_to_first_audio_ms` dolu
- [ ] `total_turn_ms` değeri anormal derecede büyük değil (normal bir tur için < 8000 ms)
- [ ] Barge-in sayacı görünüyor ("N barge-ins")
- [ ] `backchannel_detected` vurgu rengiyle gösteriliyor
- [ ] `interruption_detected` kırmızıyla gösteriliyor
- [ ] Review → Pipeline: "Good" review sonrası Training Data sayısı artıyor

---

## Yaklaşan Milestone'lar

M9 (canlı supervisor kontrolü) kabul testleri buraya eklenecek.