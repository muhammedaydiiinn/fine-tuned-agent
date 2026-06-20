from unittest import TestCase

from app.schemas import VoiceEventRequest


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
