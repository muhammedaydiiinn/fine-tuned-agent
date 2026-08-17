"""Call recorder — captures the customer + agent audio of a live call into a
single mono WAV for supervisor playback and training review.

Both sides are MIXED onto one shared timeline instead of being appended in
arrival order. The customer microphone stream is continuous and realtime, so
its write cursor doubles as the wall clock. Agent TTS packets are captured
faster than they play out; each playback is anchored to the wall clock at its
first packet and laid down contiguously from there, then summed into whatever
customer audio already occupies that region. Appending both sides sequentially
(the previous design) chopped the agent voice into 20 ms slivers interleaved
with mic silence — playback sounded slowed down, stuttery and garbled.

Agent audio (TTS, typically 24 kHz) is resampled to the customer rate (16 kHz)
before mixing. Pure-stdlib (audioop + wave) so the core is unit-testable
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
        self._customer_pos = 0
        self._agent_pos = 0
        self._customer_resample_state = None
        self._agent_resample_state = None

    def _mix_at(self, pcm: bytes, pos: int) -> int:
        """Sum ``pcm`` into the timeline at byte offset ``pos``.

        Extends the buffer with silence when the write reaches past its end.
        Returns the byte offset just after the written region.
        """
        end = pos + len(pcm)
        if end > len(self._buf):
            self._buf.extend(b"\x00" * (end - len(self._buf)))
        self._buf[pos:end] = audioop.add(bytes(self._buf[pos:end]), pcm, 2)
        return end

    def add_customer(self, pcm: bytes, src_rate: int = 16000) -> None:
        """Mix customer PCM (16-bit mono) at the customer cursor (wall clock)."""
        if not pcm:
            return
        with self._lock:
            if src_rate != self._rate:
                pcm, self._customer_resample_state = audioop.ratecv(
                    pcm, 2, 1, src_rate, self._rate, self._customer_resample_state
                )
                if not pcm:
                    return
            self._customer_pos = self._mix_at(pcm, self._customer_pos)

    def add_agent(self, pcm: bytes, src_rate: int) -> None:
        """Mix agent TTS PCM (16-bit mono) at the position it played out.

        Within one playback the packets are contiguous audio, so the agent
        cursor simply advances. TTS capture runs ahead of realtime, which
        leaves the cursor in the future between playbacks; a cursor that fell
        BEHIND the wall clock therefore marks the start of a new playback (or
        a TTS stall, where real playout gapped too) and snaps forward to now.
        """
        if not pcm:
            return
        with self._lock:
            if src_rate != self._rate:
                pcm, self._agent_resample_state = audioop.ratecv(
                    pcm, 2, 1, src_rate, self._rate, self._agent_resample_state
                )
                if not pcm:
                    return
            if self._agent_pos < self._customer_pos:
                self._agent_pos = self._customer_pos
            self._agent_pos = self._mix_at(pcm, self._agent_pos)

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
            self._customer_pos = 0
            self._agent_pos = 0
            self._customer_resample_state = None
            self._agent_resample_state = None
