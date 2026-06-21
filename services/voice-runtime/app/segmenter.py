from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

import numpy as np


@dataclass(frozen=True)
class SegmentationConfig:
    sample_rate: int = 16000
    rms_threshold: int = 350
    min_speech_ms: int = 250
    end_silence_ms: int = 700
    max_speech_ms: int = 20000
    preroll_ms: int = 240
    # Adaptive VAD (default False = legacy fixed-threshold, bit-for-bit identical)
    adaptive_threshold: bool = False
    noise_floor_margin: float = 2.5
    noise_ema_alpha: float = 0.05
    absolute_floor_rms: int = 350
    exit_threshold_ratio: float = 0.6
    noise_init_rms: float | None = None


class UtteranceSegmenter:
    """Energy-based utterance boundary detector.

    When ``adaptive_threshold=False`` (default) the detector is identical to
    the original M7 implementation: ``is_speech = rms >= rms_threshold``.

    When ``adaptive_threshold=True`` an EMA noise floor tracks ambient energy
    and enter/exit hysteresis thresholds are computed from it. The noise floor
    is updated only during non-voiced frames to avoid rising with speech energy.
    """

    def __init__(self, config: SegmentationConfig):
        self.config = config
        self._preroll: deque[np.ndarray] = deque()
        self._preroll_samples = 0
        self._speech_chunks: list[np.ndarray] = []
        self._speech_samples = 0
        self._silence_samples = 0
        self._speech_started = False
        # Adaptive VAD state
        self._noise_floor: float | None = config.noise_init_rms
        self._in_speech_region: bool = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def push(self, pcm: bytes) -> bytes | None:
        samples = np.frombuffer(pcm, dtype=np.int16).copy()
        if samples.size == 0:
            return None

        rms = float(np.sqrt(np.mean(samples.astype(np.float32) ** 2)))
        is_speech = self._frame_is_speech(rms)

        if not self._speech_chunks:
            if is_speech:
                self._speech_chunks = [*self._preroll, samples]
                self._speech_samples = sum(chunk.size for chunk in self._speech_chunks)
                self._silence_samples = 0
                self._speech_started = True
                self._preroll.clear()
                self._preroll_samples = 0
            else:
                self._append_preroll(samples)
            return None

        self._speech_chunks.append(samples)
        self._speech_samples += samples.size
        self._silence_samples = 0 if is_speech else self._silence_samples + samples.size

        elapsed_ms = self._speech_samples * 1000 / self.config.sample_rate
        silence_ms = self._silence_samples * 1000 / self.config.sample_rate
        if elapsed_ms >= self.config.max_speech_ms:
            return self._flush()
        if (
            elapsed_ms >= self.config.min_speech_ms
            and silence_ms >= self.config.end_silence_ms
        ):
            return self._flush(trim_silence_samples=self._silence_samples)
        return None

    @property
    def speech_active(self) -> bool:
        return bool(self._speech_chunks)

    @property
    def speech_ms(self) -> float:
        """Elapsed milliseconds of accumulated speech in the current segment."""
        return self._speech_samples * 1000 / self.config.sample_rate

    def consume_speech_started(self) -> bool:
        started = self._speech_started
        self._speech_started = False
        return started

    def flush(self) -> bytes | None:
        if not self._speech_chunks:
            return None
        return self._flush()

    def snapshot(self) -> bytes | None:
        """Return in-progress speech buffer without flushing (for partial transcription).

        Concatenates the accumulated speech chunks non-destructively. Returns
        ``None`` when no speech is being tracked.
        """
        if not self._speech_chunks:
            return None
        return np.concatenate(self._speech_chunks).astype(np.int16, copy=False).tobytes()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _frame_is_speech(self, rms: float) -> bool:
        """Classify a single audio frame as speech or non-speech.

        In legacy mode (``adaptive_threshold=False``) this is a simple
        threshold comparison. In adaptive mode the threshold tracks ambient
        RMS via an EMA with enter/exit hysteresis to reduce chatter.
        """
        if not self.config.adaptive_threshold:
            return rms >= self.config.rms_threshold

        # Seed the noise floor from the very first frame if not pre-configured
        if self._noise_floor is None:
            self._noise_floor = max(rms, 1.0)

        enter = max(
            float(self.config.absolute_floor_rms),
            self._noise_floor * self.config.noise_floor_margin,
        )
        exit_ = enter * self.config.exit_threshold_ratio

        if not self._in_speech_region:
            if rms >= enter:
                self._in_speech_region = True
        else:
            if rms < exit_:
                self._in_speech_region = False

        # Update noise floor only during non-voiced frames so speech energy
        # does not corrupt the ambient estimate
        if not self._in_speech_region:
            alpha = self.config.noise_ema_alpha
            self._noise_floor = max(1.0, (1 - alpha) * self._noise_floor + alpha * rms)

        return self._in_speech_region

    def _append_preroll(self, samples: np.ndarray) -> None:
        self._preroll.append(samples)
        self._preroll_samples += samples.size
        max_samples = int(self.config.sample_rate * self.config.preroll_ms / 1000)
        while self._preroll and self._preroll_samples > max_samples:
            removed = self._preroll.popleft()
            self._preroll_samples -= removed.size

    def _flush(self, trim_silence_samples: int = 0) -> bytes:
        audio = np.concatenate(self._speech_chunks)
        if trim_silence_samples and trim_silence_samples < audio.size:
            audio = audio[:-trim_silence_samples]
        result = audio.astype(np.int16, copy=False).tobytes()
        self._speech_chunks = []
        self._speech_samples = 0
        self._silence_samples = 0
        self._speech_started = False
        self._preroll.clear()
        self._preroll_samples = 0
        # Reset adaptive VAD hysteresis state so each utterance starts fresh
        self._in_speech_region = False
        self._noise_floor = self.config.noise_init_rms
        return result
