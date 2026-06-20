# M7 Canlı Kabul Kontrol Listesi

Bu testler gerçek GPU, gerçek Whisper modeli, Fish Audio ve browser mikrofonu
gerektirdiği için hedef sunucuda uygulanır.

## Hazırlık

1. `.env` içinde production dışı ilk kabul ortamını yapılandır:

   ```env
   LIVEKIT_PUBLIC_URL=wss://<voice-host>
   LIVEKIT_API_KEY=<generated API key>
   LIVEKIT_API_SECRET=<en az 32 karakter>
   WHISPER_DEVICE=cuda
   WHISPER_COMPUTE_TYPE=float16
   TTS_MODE=fish
   FISH_API_KEY=<secret>
   FISH_TTS_REFERENCE_ID=<approved German voice>
   ```

2. Whisper modelinin `WHISPER_MODEL_PATH` altında bulunduğunu doğrula.
3. TCP `7880/7881` ve UDP `7882` erişimini doğrula.
4. `voice-runtime-worker` logunda worker registration ve model yüklemesinin
   hatasız olduğunu doğrula.

## Fonksiyonel kabul

- Supervisor Panel'de `Sessions → Start Voice Test` ile senaryo seç.
- Aynı panel session'ında yazı yazmadan en az 10 ardışık turn tamamla.
- Her müşteri cümlesinin final transcript'ini Sessions konuşma akışında doğrula.
- Panelde duyulan cevabın aynı turn'deki `agent_response` ile aynı olduğunu
  doğrula.
- Turn index'lerinin 0'dan 9'a kesintisiz ilerlediğini doğrula.
- Almanca sayı, fiyat ve ürün adları içeren en az üç cümle kullan.
- Browser reconnect veya sayfa yenileme bu testte kabul kriteri değildir; M8
  kapsamındadır.

## Gecikme kabulü

Son 10 voice turn için:

```sql
SELECT
  percentile_cont(0.95) WITHIN GROUP (ORDER BY lm.value_ms) AS p95_ms,
  count(*) AS turn_count
FROM latency_metrics lm
JOIN sessions s ON s.id = lm.session_id
WHERE lm.metric_name = 'speech_end_to_first_audio_ms'
  AND s.external_session_id = '<browser-session-id>';
```

Kabul:

- `turn_count >= 10`
- konuşma sonu → first audio `p95_ms < 2500`
- hiçbir turn'de transcript/response mismatch nedeniyle voice metrics isteği
  reddedilmemiş olmalı

## Sonuç kaydı

Test tarihi, commit SHA, GPU, Whisper modeli, Fish modeli/reference ID,
browser sürümü, session ID, p50/p95 değerleri ve başarısız turn notları burada
veya ayrı tarihli operasyon kaydında tutulmalıdır.

Şu anki lokal doğrulama:

- Voice runtime image build: geçti
- LiveKit server/worker registration: geçti
- Token ile room oluşturma ve named-agent dispatch: geçti
- Backend turn + voice metrics persistence smoke testi: geçti
- Gerçek mikrofon + Whisper + Fish Audio 10-turn kabulü: hedef GPU sunucuda bekliyor
