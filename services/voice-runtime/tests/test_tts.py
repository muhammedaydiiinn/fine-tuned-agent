import pathlib
import sys
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import patch

import httpx

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.config import Settings
from app.tts import FishTTS, pace_to_speed
from app.tts import pace_to_speed


class VoiceStyleTests(TestCase):
    def test_known_paces_map_to_bounded_speeds(self):
        self.assertEqual(pace_to_speed("slow"), 0.9)
        self.assertEqual(pace_to_speed("normal"), 1.0)
        self.assertEqual(pace_to_speed("fast"), 1.08)

    def test_unknown_pace_uses_normal_speed(self):
        self.assertEqual(pace_to_speed("unexpected"), 1.0)


class TTSFallbackTests(IsolatedAsyncioTestCase):
    async def test_fish_tts_falls_back_to_mock_when_enabled(self):
        settings = Settings(
            tts_mode="fish",
            fish_api_key="x",
            fish_tts_reference_id="ref",
            tts_fallback_to_mock=True,
        )
        tts = FishTTS(settings)

        class FailingClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return None

            def stream(self, *args, **kwargs):
                raise httpx.ConnectError("tts down")

        with patch("app.tts.httpx.AsyncClient", return_value=FailingClient()):
            chunks = [chunk async for chunk in tts.stream("Hallo Welt", {"pace": "normal"})]

        self.assertTrue(tts.last_stream_used_fallback)
        self.assertGreaterEqual(len(chunks), 2)

    async def test_fish_tts_raises_when_fallback_disabled(self):
        settings = Settings(
            tts_mode="fish",
            fish_api_key="x",
            fish_tts_reference_id="ref",
            tts_fallback_to_mock=False,
        )
        tts = FishTTS(settings)

        class FailingClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return None

            def stream(self, *args, **kwargs):
                raise httpx.ConnectError("tts down")

        with patch("app.tts.httpx.AsyncClient", return_value=FailingClient()):
            with self.assertRaises(httpx.ConnectError):
                _ = [chunk async for chunk in tts.stream("Hallo Welt", {"pace": "normal"})]
