from unittest import TestCase
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.schemas import (
    VoiceEventRequest,
    VoiceTurnInterruptionRequest,
    VoiceTurnMetricsRequest,
)


class VoiceEventContractTests(TestCase):
    def test_valid_interruption_event(self):
        event = VoiceEventRequest(
            session_id="voice-test-123",
            event_id="voice-test-123:4:abc",
            sequence=4,
            event_type="interruption_detected",
            turn_id=42,
            payload={"text": "Moment bitte"},
        )

        self.assertEqual(event.event_type, "interruption_detected")
        self.assertEqual(event.payload["text"], "Moment bitte")

    def test_negative_sequence_is_rejected(self):
        with self.assertRaises(ValueError):
            VoiceEventRequest(
                session_id="voice-test-123",
                event_id="event-1",
                sequence=-1,
                event_type="voice_error",
            )

    def test_speech_end_metric_is_backward_compatible(self):
        metrics = VoiceTurnMetricsRequest(
            session_id="voice-test-123",
            stt_ms=500,
            backend_ms=100,
            llm_ms=80,
            tts_first_audio_ms=200,
            total_voice_turn_ms=1500,
            transcript_final="Hallo",
            heard_response="Guten Tag",
        )

        self.assertIsNone(metrics.speech_end_to_first_audio_ms)

    def test_interruption_request_allows_empty_spoken_response(self):
        # Playback cancelled before any audio played → empty prefix, 0 ms.
        req = VoiceTurnInterruptionRequest(session_id="voice-test-123")
        self.assertEqual(req.spoken_response, "")
        self.assertEqual(req.spoken_ms, 0.0)

    def test_interruption_request_carries_heard_prefix(self):
        req = VoiceTurnInterruptionRequest(
            session_id="voice-test-123",
            spoken_response="Guten Tag, ich bin Anna",
            spoken_ms=1600.0,
        )
        self.assertEqual(req.spoken_response, "Guten Tag, ich bin Anna")
        self.assertEqual(req.spoken_ms, 1600.0)
