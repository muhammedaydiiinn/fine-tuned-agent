"""Call recorder — captures the customer + agent audio of a live call into a
single mono WAV for supervisor playback and training review.

Both sides are written to one mono timeline in arrival order (calls are
turn-based, so sequential interleave is a faithful-enough review recording).
Agent audio (TTS, typically 24 kHz) is resampled to the customer rate (16 kHz)
before appending. Pure-stdlib (audioop + wave) so the core is unit-testable
without the live pipeline.
"""
from __future__ import annotations

import audioop
import io
import threading
import wave


class CallRecorder:
    def __init__(self, out_rate: int = 16000) -> None:
        self._rate = out_rate
        self._buf = bytearray()
        self._lock = threading.Lock()
        self._agent_resample_state = None

    def add_customer(self, pcm: bytes, src_rate: int = 16000) -> None:
        """Append customer PCM (16-bit mono). Resampled if src_rate differs."""
        if not pcm:
            return
        with self._lock:
            if src_rate != self._rate:
                pcm, _ = audioop.ratecv(pcm, 2, 1, src_rate, self._rate, None)
            self._buf.extend(pcm)

    def add_agent(self, pcm: bytes, src_rate: int) -> None:
        """Append agent TTS PCM (16-bit mono), resampled to the recorder rate."""
        if not pcm:
            return
        with self._lock:
            if src_rate != self._rate:
                pcm, self._agent_resample_state = audioop.ratecv(
                    pcm, 2, 1, src_rate, self._rate, self._agent_resample_state
                )
            self._buf.extend(pcm)

    def duration_seconds(self) -> float:
        with self._lock:
            return len(self._buf) / 2.0 / self._rate

    def is_empty(self) -> bool:
        with self._lock:
            return len(self._buf) == 0

    def to_wav_bytes(self) -> bytes:
        with self._lock:
            data = bytes(self._buf)
        out = io.BytesIO()
        with wave.open(out, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(self._rate)
            wav.writeframes(data)
        return out.getvalue()

    def reset(self) -> None:
        with self._lock:
            self._buf.clear()
            self._agent_resample_state = None
