import asyncio
import logging
import time
from dataclasses import dataclass

import numpy as np

from app.config import Settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Transcript:
    text: str
    stt_ms: float


class FasterWhisperSTT:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._model = None
        self._load_lock = asyncio.Lock()

    async def warmup(self) -> None:
        await self._get_model()

    async def transcribe(self, pcm: bytes, sample_rate: int = 16000) -> Transcript:
        started = time.perf_counter()
        try:
            model = await self._get_model()
        except Exception:
            logger.exception("Failed to load Whisper model")
            raise

        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        if sample_rate != 16000:
            raise ValueError("FasterWhisperSTT expects 16 kHz mono PCM")

        try:
            text = await asyncio.to_thread(self._transcribe_sync, model, audio)
        except Exception:
            logger.exception("Whisper transcription failed — pcm_bytes=%d", len(pcm))
            raise

        return Transcript(
            text=text,
            stt_ms=(time.perf_counter() - started) * 1000,
        )

    async def transcribe_partial(self, pcm: bytes, sample_rate: int = 16000) -> Transcript:
        """Transcribe an in-progress speech buffer for partial hypothesis generation.

        Uses the same model and decode settings as ``transcribe()``. The result
        is non-authoritative; the final transcript from ``transcribe()`` is
        always used for turn logic and backend persistence.
        """
        started = time.perf_counter()
        try:
            model = await self._get_model()
        except Exception:
            logger.exception("Failed to load Whisper model for partial transcription")
            raise

        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        if sample_rate != 16000:
            raise ValueError("FasterWhisperSTT expects 16 kHz mono PCM")

        try:
            text = await asyncio.to_thread(self._transcribe_sync, model, audio)
        except Exception:
            logger.exception(
                "Whisper partial transcription failed — pcm_bytes=%d", len(pcm)
            )
            raise

        return Transcript(
            text=text,
            stt_ms=(time.perf_counter() - started) * 1000,
        )

    async def _get_model(self):
        if self._model is not None:
            return self._model
        async with self._load_lock:
            if self._model is None:
                from faster_whisper import WhisperModel

                logger.info("Loading Whisper model from %s", self.settings.whisper_model_path)
                self._model = await asyncio.to_thread(
                    WhisperModel,
                    self.settings.whisper_model_path,
                    device=self.settings.whisper_device,
                    compute_type=self.settings.whisper_compute_type,
                    local_files_only=True,
                )
        return self._model

    def _transcribe_sync(self, model, audio: np.ndarray) -> str:
        segments, _ = model.transcribe(
            audio,
            language=self.settings.whisper_language,
            beam_size=self.settings.whisper_beam_size,
            condition_on_previous_text=False,
            vad_filter=False,
            word_timestamps=False,
        )
        return " ".join(segment.text.strip() for segment in segments).strip()
