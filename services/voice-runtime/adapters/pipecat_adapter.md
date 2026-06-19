# Pipecat Adapter — Tasarım Notları (Milestone 7)

Pipecat pipeline framework ile agent backend entegrasyonu.

## Pipeline Tasarımı

```
TransportFrame (WebRTC/WebSocket)
  → WhisperSTTService (veya Deepgram)
  → CallShieldAgentService  ← /agent-turn endpoint'ini çağırır
  → FishAudioTTSService
  → TransportFrame (ses geri dön)
```

## CallShieldAgentService

Pipecat `AIService` subclass'ı olarak implement edilecek:
- `process_frame(TranscriptionFrame)` → `POST /agent-turn`
- Response'dan `agent_response` alır → `TextFrame` üretir
- `voice_style` bilgisini TTS parametrelerine çevirir

## Entegrasyon Noktası

`POST /agent-turn` değişmez; yalnızca Pipecat frame → HTTP → frame dönüşümü yapılır.
