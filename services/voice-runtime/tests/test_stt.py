import pathlib
import sys
import tempfile
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


class RuntimeConfigValidationTests(IsolatedAsyncioTestCase):
    async def test_validate_runtime_rejects_non_ct2_whisper_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pathlib.Path(tmpdir, "config.json").write_text("{}", encoding="utf-8")
            settings = Settings(
                tts_mode="mock",
                whisper_model_path=tmpdir,
            )

            with self.assertRaisesRegex(RuntimeError, "model.bin"):
                settings.validate_runtime()
