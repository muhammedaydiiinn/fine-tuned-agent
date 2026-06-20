# LiveKit Adapter — Milestone 7

LiveKit, M7 için seçilen ve çalışan tek voice runtime'dır.

## Uygulanan akış

```text
Supervisor Panel / Sessions microphone
  → LiveKit room
  → 16 kHz mono PCM
  → energy/silence utterance boundary
  → Faster Whisper German final transcript
  → POST /agent-turn
  → Fish Audio low-latency 24 kHz PCM stream
  → LiveKit LocalAudioTrack
  → browser playback
```

Authenticated Supervisor Panel, `RoomAgentDispatch` içeren token'ı üretir ve
`anrufblocker-voice` named worker'ını room'a çağırır. Room adı, voice session ID
ve backend external session ID aynı değerdir.

Browser'a `voice.events` topic'i üzerinden şu event'ler gönderilir:

```json
{"event":"voice_session_ready","session_id":"voice-123"}
{"event":"transcript_final","session_id":"voice-123","text":"Was kostet das?","stt_ms":210}
{"event":"agent_response","turn_id":42,"turn_index":0,"text":"..."}
{"event":"voice_turn_complete","turn_id":42,"metrics":{"total_voice_turn_ms":980}}
```

Agent backend `/agent-turn` endpoint'i değiştirilmeden kullanılır. Ek
`POST /voice/turns/{turn_id}/metrics` endpoint'i transcript/response
bütünlüğünü doğrular ve voice latency ölçümlerini aynı turn'e yazar.

Gerçek interruption ve playback cancellation M8 kapsamıdır.
