from __future__ import annotations

import math
from typing import Any


def _extract_metric(turns, metric_name: str) -> list[float]:
    values: list[float] = []
    for turn in turns:
        latency = getattr(turn, "latency_json", None) or {}
        value = latency.get(metric_name)
        if isinstance(value, (int, float)):
            values.append(float(value))
    return values


def _p95(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(len(ordered) * 0.95) - 1)
    return ordered[index]


def build_voice_health(turns, events) -> dict[str, Any]:
    speech_to_audio = _extract_metric(turns, "speech_end_to_first_audio_ms")
    total_turn_ms = _extract_metric(turns, "total_voice_turn_ms")
    stt_ms = _extract_metric(turns, "stt_ms")

    event_counts: dict[str, int] = {}
    for item in events:
        event_type = getattr(item, "event_type", "")
        event_counts[event_type] = event_counts.get(event_type, 0) + 1

    voice_turns = len(speech_to_audio)
    latest_turn = turns[-1] if turns else None
    latest_latency = getattr(latest_turn, "latency_json", None) or {}
    latest_speech_to_audio = latest_latency.get("speech_end_to_first_audio_ms")

    p95_speech_to_audio = _p95(speech_to_audio)
    degraded = any(
        event_counts.get(name, 0) > 0
        for name in ("stt_unavailable", "tts_fallback_activated", "voice_error")
    )
    if isinstance(p95_speech_to_audio, (int, float)) and p95_speech_to_audio > 2500:
        degraded = True

    return {
        "voice_turns": voice_turns,
        "latest_speech_end_to_first_audio_ms": latest_speech_to_audio,
        "p95_speech_end_to_first_audio_ms": p95_speech_to_audio,
        "avg_total_voice_turn_ms": (
            sum(total_turn_ms) / len(total_turn_ms) if total_turn_ms else None
        ),
        "avg_stt_ms": (sum(stt_ms) / len(stt_ms) if stt_ms else None),
        "barge_in_count": event_counts.get("interruption_detected", 0),
        "stt_unavailable_count": event_counts.get("stt_unavailable", 0),
        "tts_fallback_count": event_counts.get("tts_fallback_activated", 0),
        "voice_error_count": event_counts.get("voice_error", 0),
        "degraded": degraded,
    }


def build_recent_voice_turns(turns, *, limit: int = 5) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for turn in reversed(list(turns)):
        latency = getattr(turn, "latency_json", None) or {}
        if "speech_end_to_first_audio_ms" not in latency:
            continue
        rows.append(
            {
                "turn_index": getattr(turn, "turn_index", None),
                "intent": getattr(turn, "intent", None),
                "stt_ms": latency.get("stt_ms"),
                "backend_ms": latency.get("backend_ms"),
                "tts_first_audio_ms": latency.get("tts_first_audio_ms"),
                "speech_end_to_first_audio_ms": latency.get("speech_end_to_first_audio_ms"),
                "total_voice_turn_ms": latency.get("total_voice_turn_ms"),
            }
        )
        if len(rows) >= limit:
            break
    return rows


def build_voice_acceptance(turns, events) -> dict[str, Any]:
    measured_turns = [
        turn
        for turn in turns
        if isinstance(
            (getattr(turn, "latency_json", None) or {}).get(
                "speech_end_to_first_audio_ms"
            ),
            (int, float),
        )
    ]
    indexed_measured_turns = [
        turn
        for turn in measured_turns
        if isinstance(getattr(turn, "turn_index", None), int)
    ]
    latest_measured_turns = measured_turns[-10:]
    baseline_turns = indexed_measured_turns[:10]
    latest_latencies = [
        float(turn.latency_json["speech_end_to_first_audio_ms"])
        for turn in latest_measured_turns
    ]
    baseline_indexes = [turn.turn_index for turn in baseline_turns]
    has_ten_turns = len(latest_measured_turns) >= 10
    has_contiguous_ten = (
        baseline_indexes == list(range(10)) if len(baseline_indexes) == 10 else False
    )
    has_transcript_and_response = bool(baseline_turns) and all(
        bool((getattr(turn, "customer_text", "") or "").strip())
        and bool((getattr(turn, "agent_response", "") or "").strip())
        for turn in baseline_turns
    )
    p95_latest = _p95(latest_latencies)
    p95_ok = bool(has_ten_turns and isinstance(p95_latest, (int, float)) and p95_latest < 2500)

    event_counts: dict[str, int] = {}
    for item in events:
        event_type = getattr(item, "event_type", "")
        event_counts[event_type] = event_counts.get(event_type, 0) + 1

    no_degradation = not any(
        event_counts.get(name, 0) > 0
        for name in ("voice_error", "stt_unavailable", "tts_fallback_activated")
    )

    checks = [
        {
            "label": "10 ölçülen ses turu",
            "status": "pass" if has_ten_turns else "pending",
            "detail": f"{len(latest_measured_turns)}/10 ölçülen tur yakalandı",
        },
        {
            "label": "Tur dizinleri 0-9 arası boşluksuz",
            "status": "pass" if has_contiguous_ten else "pending",
            "detail": (
                "Dizinler 0'dan 9'a kesintisiz"
                if has_contiguous_ten
                else f"Gözlemlenen temel dizinler: {baseline_indexes or 'yok'}"
            ),
        },
        {
            "label": "Transkript ve ajan yanıtı kaydedildi",
            "status": "pass" if has_transcript_and_response and has_ten_turns else "pending",
            "detail": (
                "Tüm ölçülen turların transkripti ve ajan yanıtı var"
                if has_transcript_and_response
                else "Bazı ölçülen turlarda transkript veya yanıt eksik"
            ),
        },
        {
            "label": "P95 konuşma sonundan ilk sese kadar 2500 ms altında",
            "status": "pass" if p95_ok else "pending",
            "detail": (
                f"P95 {round(p95_latest)} ms"
                if p95_latest is not None
                else "Henüz yeterli ölçülen tur yok"
            ),
        },
        {
            "label": "Kaydedilmiş bozulmuş ses olayı yok",
            "status": "pass" if no_degradation else "fail",
            "detail": (
                "STT, TTS yedeği veya çalışma zamanı hatası kaydedilmedi"
                if no_degradation
                else (
                    f"voice_error={event_counts.get('voice_error', 0)}, "
                    f"stt_unavailable={event_counts.get('stt_unavailable', 0)}, "
                    f"tts_fallback={event_counts.get('tts_fallback_activated', 0)}"
                )
            ),
        },
        {
            "label": "Gerçek GPU Whisper + Fish + tarayıcı mikrofon çalıştırması",
            "status": "manual",
            "detail": "Hedef GPU sunucusu ve gerçek bir tarayıcı mikrofon oturumu gerektirir",
        },
        {
            "label": "Almanca sayılar, fiyat ve ürün adları seslendirildi",
            "status": "manual",
            "detail": "Canlı kabul sırasında manuel senaryo kapsamı gerektirir",
        },
        {
            "label": "Duyulan ses, kaydedilen ajan yanıtıyla eşleşiyor",
            "status": "manual",
            "detail": "Canlı görüşme dinlenirken insan onayı gerektirir",
        },
    ]
    auto_passed = sum(1 for item in checks if item["status"] == "pass")
    auto_total = sum(1 for item in checks if item["status"] in {"pass", "fail", "pending"})

    return {
        "checks": checks,
        "latest_measured_turn_count": len(latest_measured_turns),
        "p95_latest_10_ms": p95_latest,
        "auto_passed": auto_passed,
        "auto_total": auto_total,
        "ready_for_gpu_acceptance": auto_passed == auto_total,
    }
