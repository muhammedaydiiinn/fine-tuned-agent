# LiveKit Adapter — Tasarım Notları (Milestone 7)

LiveKit WebRTC üzerinden gelen ses akışını agent backend'e bağlayan adapter.

## Akış

```
Tarayıcı/Telefon
  → LiveKit Room (WebRTC)
  → STT (Speech-to-Text)
  → transcript_final event → POST /agent-turn
  → agent_response
  → TTS (Fish Audio veya ElevenLabs)
  → LiveKit Room → Kullanıcı
```

## Gereksinimler (planlanan)

- `livekit-server-sdk-python`
- Fish Audio TTS (FISH_API_KEY env var mevcut)
- VAD (Voice Activity Detection) — silero-vad veya LiveKit built-in

## Entegrasyon Noktası

`POST /agent-turn` endpoint'i değişmeden kalır.
Adapter yalnızca `customer_text` çıkarıp POST gönderir,
dönen `agent_response` metnini TTS'e gönderir.
