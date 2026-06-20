from collections import deque
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class SegmentationConfig:
    sample_rate: int = 16000
    rms_threshold: int = 350
    min_speech_ms: int = 250
    end_silence_ms: int = 700
    max_speech_ms: int = 20000
    preroll_ms: int = 240


class UtteranceSegmenter:
    """Energy-based utterance boundary detector for M7 supervisor voice tests."""

    def __init__(self, config: SegmentationConfig):
        self.config = config
        self._preroll: deque[np.ndarray] = deque()
        self._preroll_samples = 0
        self._speech_chunks: list[np.ndarray] = []
        self._speech_samples = 0
        self._silence_samples = 0
        self._speech_started = False

    def push(self, pcm: bytes) -> bytes | None:
        samples = np.frombuffer(pcm, dtype=np.int16).copy()
        if samples.size == 0:
            return None

        rms = float(np.sqrt(np.mean(samples.astype(np.float32) ** 2)))
        is_speech = rms >= self.config.rms_threshold

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

    def consume_speech_started(self) -> bool:
        started = self._speech_started
        self._speech_started = False
        return started

    def flush(self) -> bytes | None:
        if not self._speech_chunks:
            return None
        return self._flush()

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
        return result
