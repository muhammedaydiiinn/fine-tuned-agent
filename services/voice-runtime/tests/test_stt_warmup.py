import pathlib
import sys
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.config import Settings
from app.stt import FasterWhisperSTT


class STTWarmupTests(IsolatedAsyncioTestCase):
    async def test_warmup_runs_dummy_transcribe_once(self):
        stt = FasterWhisperSTT(Settings())
        calls = {"n": 0}

        async def fake_get_model():
            return object()

        def fake_sync(model, audio):
            calls["n"] += 1
            return ""

        stt._get_model = fake_get_model
        stt._transcribe_sync = fake_sync

        await stt.warmup()
        await stt.warmup()

        self.assertEqual(calls["n"], 1)
        self.assertTrue(stt._warmed)
