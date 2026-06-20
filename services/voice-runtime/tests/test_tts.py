from unittest import TestCase

from app.tts import pace_to_speed


class VoiceStyleTests(TestCase):
    def test_known_paces_map_to_bounded_speeds(self):
        self.assertEqual(pace_to_speed("slow"), 0.9)
        self.assertEqual(pace_to_speed("normal"), 1.0)
        self.assertEqual(pace_to_speed("fast"), 1.08)

    def test_unknown_pace_uses_normal_speed(self):
        self.assertEqual(pace_to_speed("unexpected"), 1.0)
