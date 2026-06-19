# Voice Runtime

Bu klasör browser voice ve ileride telefon entegrasyonu için gerçek zamanlı ses
katmanını barındırır.

- M7: İlk runtime ile browser microphone, streaming STT/TTS ve latency ölçümü
- M8: VAD, turn-taking, interruption/barge-in ve playback cancellation
- M9: Supervisor interrupt ve replacement cevabın TTS ile gönderilmesi

İlk implementasyonda tek runtime seçilir. LiveKit önerilen başlangıçtır;
Pipecat aynı anda kurulması gereken ikinci runtime değil, alternatif adapter'dır.

## Adapter Olayları

Agent backend, aşağıdaki event formatlarını kabul edecek şekilde tasarlanmıştır:

```json
{"event": "transcript_final",      "session_id": "session-123", "text": "Was kostet das?"}
{"event": "customer_interruption", "session_id": "session-123", "partial_text": "Moment..."}
{"event": "supervisor_interrupt",  "session_id": "session-123", "turn_id": 7}
```

## Adapter seçenekleri

- `adapters/livekit_adapter.md` — LiveKit WebRTC entegrasyon rehberi
- `adapters/pipecat_adapter.md` — Pipecat pipeline entegrasyon rehberi

## Durum

Şu anda tasarım dokümanları mevcuttur; çalışan browser voice runtime henüz
implement edilmemiştir. Kanonik kapsam ve kabul kriterleri kökteki
`MILESTONES.md` dosyasındadır.
