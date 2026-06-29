"""Process-level STT singleton for LiveKit worker prewarm."""
from __future__ import annotations

from app.config import Settings
from app.stt import FasterWhisperSTT

_USERDATA_KEY = "faster_whisper_stt"


def get_or_create_stt(settings: Settings, proc=None) -> FasterWhisperSTT:
    if proc is not None:
        existing = proc.userdata.get(_USERDATA_KEY)
        if isinstance(existing, FasterWhisperSTT):
            return existing
        stt = FasterWhisperSTT(settings)
        proc.userdata[_USERDATA_KEY] = stt
        return stt
    return FasterWhisperSTT(settings)
