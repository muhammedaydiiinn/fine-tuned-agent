# Voice Runtime Adapter

Bu klasör sesli arayüz entegrasyonu için adapter katmanını hazırlar.
Milestone 7'de LiveKit ve Pipecat entegrasyonu burada implement edilecek.

## Adapter Olayları

Agent backend, aşağıdaki event formatlarını kabul edecek şekilde tasarlanmıştır:

```json
{"event": "transcript_final",      "session_id": "session-123", "text": "Was kostet das?"}
{"event": "customer_interruption", "session_id": "session-123", "partial_text": "Moment..."}
{"event": "supervisor_interrupt",  "session_id": "session-123", "turn_id": 7}
```

## Planlanan Adaptörler

- `adapters/livekit_adapter.md` — LiveKit WebRTC entegrasyon rehberi
- `adapters/pipecat_adapter.md` — Pipecat pipeline entegrasyon rehberi

## Durum

Milestone 1–6 tamamlanana kadar sesli entegrasyon yapılmayacak.
Çekirdek platform kararlı olduğunda bu adım başlar.
