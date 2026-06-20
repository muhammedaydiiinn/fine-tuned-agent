from unittest import TestCase

from app.schemas import VoiceEventRequest, VoiceTurnMetricsRequest


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
