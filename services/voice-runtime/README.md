# Voice Runtime

M7 browser voice katmanı LiveKit üzerinde çalışan tek runtime olarak
uygulanmıştır. Browser voice yalnızca Supervisor Panel içindeki test session'ları
için kullanılır; müşteriye açık ayrı bir web arayüzü yoktur.

## Akış

```text
Supervisor Panel / Sessions microphone
  → LiveKit WebRTC room
  → 16 kHz PCM + energy-based turn boundary
  → local Faster Whisper (German)
  → transcript_final
  → POST /agent-turn
  → Fish Audio streaming PCM TTS
  → LiveKit audio track
  → browser playback
```

Room adı ile backend `external_session_id` aynıdır. Her turn için
`stt_ms`, `backend_ms`, `llm_ms`, `tts_first_audio_ms` ve konuşma sonundan
ilk sese kadar geçen `total_voice_turn_ms` backend turn kaydına yazılır.
Backend, kaydedilen final transcript ve agent cevabı ile voice runtime'ın
bildirdiği transcript/duyulan cevap eşleşmezse metriği reddeder.

## Servisler

- `livekit-server`: WebRTC media server (`7880/TCP`, `7881/TCP`, `7882/UDP`)
- `voice-runtime-worker`: LiveKit agent worker, STT/backend/TTS pipeline
- `supervisor-panel`: authenticated token üretimi, senaryo seçimi ve test UI

Test akışı:

```text
Supervisor Panel → Sessions → Start Voice Test
  → scenario seç → Create Test Session
  → Start Microphone → konuş → Stop / End Session & Review
```

## Gerekli ayarlar

Gerçek ses testi için:

```env
LIVEKIT_PUBLIC_URL=ws://localhost:7880
WHISPER_MODEL_PATH=/models/whisper/whisper-large-v3-turbo-german
WHISPER_DEVICE=cuda
WHISPER_COMPUTE_TYPE=float16
TTS_MODE=fish
FISH_API_KEY=...
FISH_TTS_REFERENCE_ID=...
```

`TTS_MODE=mock` sadece transport ve backend smoke testleri içindir; gerçek M7
kabul testi sayılmaz.

## Kapsam sınırı

M7'de konuşma sonu basit enerji/sessizlik sınırıyla belirlenir. Agent
konuşurken barge-in, playback cancellation, backchannel ayrımı ve stale
response koruması M8 kapsamıdır.

Canlı kabul adımları için `LIVE_ACCEPTANCE.md` dosyasını kullan.
