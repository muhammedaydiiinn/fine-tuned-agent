import pathlib
import sys
from unittest import IsolatedAsyncioTestCase

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.config import Settings
from app.stt import FasterWhisperSTT, STTError


class STTErrorWrappingTests(IsolatedAsyncioTestCase):
    async def test_model_load_failure_is_wrapped(self):
        stt = FasterWhisperSTT(Settings())

        async def fail_model():
            raise RuntimeError("load failed")

        stt._get_model = fail_model

        with self.assertRaisesRegex(STTError, "could not be loaded"):
            await stt.transcribe(b"\x00\x00" * 1600)

    async def test_transcription_failure_is_wrapped(self):
        stt = FasterWhisperSTT(Settings())

        async def ok_model():
            return object()

        def fail_sync(model, audio):
            raise RuntimeError("decode failed")

        stt._get_model = ok_model
        stt._transcribe_sync = fail_sync

        with self.assertRaisesRegex(STTError, "transcription failed"):
            await stt.transcribe(b"\x00\x00" * 1600)
