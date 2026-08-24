"""Replay a call recording through the LIVE segmenter+STT stack.

The definitive voice-path regression test: instead of guessing why a live call
"heard nothing", feed its recorded audio through exactly the code the worker
runs and see every utterance boundary and decode result.

Usage (inside the voice-runtime container):

    python3 tools/replay_recording.py /tmp/r34.wav [--skip-seconds 13.5]
"""
from __future__ import annotations

import argparse
import asyncio
import wave

import numpy as np

from app.config import Settings
from app.segmenter import SegmentationConfig, UtteranceSegmenter
from app.stt import FasterWhisperSTT


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wav", help="16 kHz mono WAV (a call recording)")
    parser.add_argument("--skip-seconds", type=float, default=0.0,
                        help="skip the leading agent greeting")
    args = parser.parse_args()

    settings = Settings()
    reader = wave.open(args.wav, "rb")
    rate = reader.getframerate()
    data = np.frombuffer(reader.readframes(reader.getnframes()), dtype=np.int16)
    data = data[int(args.skip_seconds * rate):]

    segmenter = UtteranceSegmenter(SegmentationConfig(
        sample_rate=rate,
        rms_threshold=settings.speech_rms_threshold,
        min_speech_ms=settings.speech_min_ms,
        end_silence_ms=settings.speech_end_silence_ms,
        max_speech_ms=settings.speech_max_ms,
        preroll_ms=settings.speech_preroll_ms,
        adaptive_threshold=settings.speech_adaptive_vad,
    ))
    frame = int(rate * 0.01)  # 10 ms live frame size
    utterances: list[bytes] = []
    for i in range(0, len(data) - frame, frame):
        out = segmenter.push(data[i:i + frame].tobytes())
        if out:
            utterances.append(out)
    tail = segmenter.flush()
    if tail:
        utterances.append(tail)

    stt = FasterWhisperSTT(settings)
    decoded = 0
    for pcm in utterances:
        result = await stt.transcribe(pcm, sample_rate=rate)
        decoded += bool(result.text)
        print(f"{len(pcm) / 2 / rate:6.2f}s  stt={result.stt_ms:5.0f}ms  {result.text!r}")
    print(f"\n{decoded}/{len(utterances)} utterance çözüldü")


if __name__ == "__main__":
    asyncio.run(main())
