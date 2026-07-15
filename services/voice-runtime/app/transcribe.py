"""Synchronous batch transcription with timestamped segments.

Deliberately separate from FasterWhisperSTT (app/stt.py): that class is async,
joins segment text and discards timestamps for the realtime pipeline. Batch
jobs need per-segment timing, VAD filtering for long silences and no event
loop.
"""
import logging
import math
from dataclasses import dataclass

import numpy as np

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Seg:
    start_s: float
    end_s: float
    text: str
    confidence: float | None


def load_model(settings):
    from faster_whisper import WhisperModel

    logger.info(
        "Loading batch Whisper model from %s (compute_type=%s)",
        settings.whisper_model_path,
        settings.transcribe_compute_type,
    )
    return WhisperModel(
        settings.whisper_model_path,
        device=settings.whisper_device,
        compute_type=settings.transcribe_compute_type,
        local_files_only=True,
    )


def transcribe_segments(model, audio: np.ndarray, language: str = "de") -> list[Seg]:
    """Transcribe float32 16 kHz mono audio into timestamped segments."""
    segments, _info = model.transcribe(
        audio,
        language=language,
        beam_size=1,
        vad_filter=True,
        condition_on_previous_text=False,
        word_timestamps=False,
    )
    results: list[Seg] = []
    for segment in segments:
        text = (segment.text or "").strip()
        if not text:
            continue
        confidence = None
        avg_logprob = getattr(segment, "avg_logprob", None)
        if avg_logprob is not None:
            confidence = round(min(1.0, math.exp(avg_logprob)), 4)
        results.append(Seg(
            start_s=float(segment.start or 0.0),
            end_s=float(segment.end or 0.0),
            text=text,
            confidence=confidence,
        ))
    return results
