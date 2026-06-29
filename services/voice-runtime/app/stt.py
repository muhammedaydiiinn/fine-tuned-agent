import asyncio
import logging
import time
from dataclasses import dataclass

import numpy as np

from app.config import Settings

logger = logging.getLogger(__name__)

_WARMUP_SAMPLES = 8000  # 0.5 s silence at 16 kHz


class STTError(RuntimeError):
    """Raised when the transcription backend is unavailable or fails."""


@dataclass(frozen=True)
class Transcript:
    text: str
    stt_ms: float


class FasterWhisperSTT:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._model = None
        self._load_lock = asyncio.Lock()
        self._transcribe_lock = asyncio.Lock()
        self._warmed = False

    async def warmup(self) -> None:
        """Load the model and run one dummy decode to warm CUDA kernels."""
        if self._warmed:
            return
        model = await self._get_model()
        silence = np.zeros(_WARMUP_SAMPLES, dtype=np.float32)
        await asyncio.to_thread(self._transcribe_sync, model, silence)
        self._warmed = True
        logger.info("Whisper STT warmed up — path=%s", self.settings.whisper_model_path)

    async def transcribe(self, pcm: bytes, sample_rate: int = 16000) -> Transcript:
        started = time.perf_counter()
        async with self._transcribe_lock:
            try:
                model = await self._get_model()
            except Exception:
                logger.exception("Failed to load Whisper model")
                raise STTError("Whisper model could not be loaded")

            audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
            if sample_rate != 16000:
                raise ValueError("FasterWhisperSTT expects 16 kHz mono PCM")

            try:
                text = await asyncio.to_thread(self._transcribe_sync, model, audio)
            except Exception:
                logger.exception("Whisper transcription failed — pcm_bytes=%d", len(pcm))
                raise STTError("Whisper transcription failed")

        return Transcript(
            text=text,
            stt_ms=(time.perf_counter() - started) * 1000,
        )

    async def transcribe_partial(self, pcm: bytes, sample_rate: int = 16000) -> Transcript:
        """Transcribe an in-progress speech buffer for partial hypothesis generation."""
        started = time.perf_counter()
        async with self._transcribe_lock:
            try:
                model = await self._get_model()
            except Exception:
                logger.exception("Failed to load Whisper model for partial transcription")
                raise STTError("Whisper model could not be loaded")

            audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
            if sample_rate != 16000:
                raise ValueError("FasterWhisperSTT expects 16 kHz mono PCM")

            try:
                text = await asyncio.to_thread(self._transcribe_sync, model, audio)
            except Exception:
                logger.exception(
                    "Whisper partial transcription failed — pcm_bytes=%d", len(pcm)
                )
                raise STTError("Whisper partial transcription failed")

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
