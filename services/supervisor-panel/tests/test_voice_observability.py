import pathlib
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.voice_observability import (
    build_recent_voice_turns,
    build_voice_acceptance,
    build_voice_health,
)


class VoiceObservabilityTests(unittest.TestCase):
    def test_build_voice_health_summarizes_latency_and_failures(self):
        turns = [
            SimpleNamespace(latency_json={"speech_end_to_first_audio_ms": 1800, "total_voice_turn_ms": 2600, "stt_ms": 420}),
            SimpleNamespace(latency_json={"speech_end_to_first_audio_ms": 2900, "total_voice_turn_ms": 3300, "stt_ms": 510}),
            SimpleNamespace(latency_json={"speech_end_to_first_audio_ms": 2400, "total_voice_turn_ms": 3100, "stt_ms": 470}),
        ]
        events = [
            SimpleNamespace(event_type="interruption_detected"),
            SimpleNamespace(event_type="tts_fallback_activated"),
            SimpleNamespace(event_type="stt_unavailable"),
        ]

        summary = build_voice_health(turns, events)

        self.assertEqual(summary["voice_turns"], 3)
        self.assertEqual(summary["latest_speech_end_to_first_audio_ms"], 2400)
        self.assertEqual(summary["p95_speech_end_to_first_audio_ms"], 2900)
        self.assertEqual(summary["barge_in_count"], 1)
        self.assertEqual(summary["tts_fallback_count"], 1)
        self.assertEqual(summary["stt_unavailable_count"], 1)
        self.assertTrue(summary["degraded"])

    def test_build_voice_health_handles_empty_inputs(self):
        summary = build_voice_health([], [])

        self.assertEqual(summary["voice_turns"], 0)
        self.assertIsNone(summary["p95_speech_end_to_first_audio_ms"])
        self.assertFalse(summary["degraded"])

    def test_build_recent_voice_turns_returns_latest_voice_rows(self):
        turns = [
            SimpleNamespace(turn_index=1, intent="intro", latency_json={}),
            SimpleNamespace(
                turn_index=2,
                intent="price_question",
                latency_json={
                    "stt_ms": 410,
                    "backend_ms": 120,
                    "tts_first_audio_ms": 240,
                    "speech_end_to_first_audio_ms": 860,
                    "total_voice_turn_ms": 1600,
                },
            ),
            SimpleNamespace(
                turn_index=3,
                intent="security_objection",
                latency_json={
                    "stt_ms": 500,
                    "backend_ms": 150,
                    "tts_first_audio_ms": 300,
                    "speech_end_to_first_audio_ms": 980,
                    "total_voice_turn_ms": 1900,
                },
            ),
        ]

        rows = build_recent_voice_turns(turns, limit=2)

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["turn_index"], 3)
        self.assertEqual(rows[1]["turn_index"], 2)

    def test_build_voice_acceptance_summarizes_auto_and_manual_checks(self):
        turns = [
            SimpleNamespace(
                turn_index=index,
                customer_text=f"Kunde {index}",
                agent_response=f"Antwort {index}",
                latency_json={"speech_end_to_first_audio_ms": 1200 + index * 50},
            )
            for index in range(10)
        ]
        events = []

        acceptance = build_voice_acceptance(turns, events)

        self.assertEqual(acceptance["latest_measured_turn_count"], 10)
        self.assertEqual(acceptance["auto_passed"], 5)
        self.assertEqual(acceptance["auto_total"], 5)
        self.assertTrue(acceptance["ready_for_gpu_acceptance"])
        self.assertEqual(acceptance["checks"][0]["status"], "pass")
        self.assertEqual(acceptance["checks"][-1]["status"], "manual")

    def test_build_voice_acceptance_marks_degraded_events_as_failure(self):
        turns = [
            SimpleNamespace(
                turn_index=0,
                customer_text="Hallo",
                agent_response="Guten Tag",
                latency_json={"speech_end_to_first_audio_ms": 1800},
            )
        ]
        events = [SimpleNamespace(event_type="tts_fallback_activated")]

        acceptance = build_voice_acceptance(turns, events)

        self.assertFalse(acceptance["ready_for_gpu_acceptance"])
        self.assertEqual(acceptance["checks"][4]["status"], "fail")

    def test_build_voice_acceptance_keeps_baseline_index_check_after_more_turns(self):
        turns = [
            SimpleNamespace(
                turn_index=index,
                customer_text=f"Kunde {index}",
                agent_response=f"Antwort {index}",
                latency_json={"speech_end_to_first_audio_ms": 1000 + index * 10},
            )
            for index in range(13)
        ]

        acceptance = build_voice_acceptance(turns, [])

        self.assertEqual(acceptance["checks"][1]["status"], "pass")
        self.assertEqual(acceptance["latest_measured_turn_count"], 10)
