import hashlib
import re
import time
from dataclasses import dataclass


_NON_WORD = re.compile(r"[^\wäöüß]+", re.IGNORECASE)


def normalize_transcript(text: str) -> str:
    return " ".join(_NON_WORD.sub(" ", text.casefold()).split())


@dataclass(frozen=True)
class BackchannelDecision:
    is_backchannel: bool
    normalized_text: str


class BackchannelClassifier:
    """Conservative lexical classifier used only while agent audio is active."""

    def __init__(self, phrases: set[str] | None = None):
        defaults = {
            "mhm",
            "hm",
            "ja",
            "okay",
            "ok",
            "alles klar",
            "verstehe",
            "genau",
            "aha",
        }
        self.phrases = {
            normalize_transcript(value) for value in (phrases or defaults)
        }

    def classify(self, text: str) -> BackchannelDecision:
        normalized = normalize_transcript(text)
        return BackchannelDecision(
            is_backchannel=normalized in self.phrases,
            normalized_text=normalized,
        )


class TranscriptDeduplicator:
    """Reject repeated final transcripts caused by reconnect/re-delivery."""

    def __init__(self, window_seconds: float = 2.5):
        self.window_seconds = window_seconds
        self._last_seen: dict[str, float] = {}

    def is_duplicate(self, text: str, now: float | None = None) -> bool:
        normalized = normalize_transcript(text)
        if not normalized:
            return False
        timestamp = time.monotonic() if now is None else now
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        previous = self._last_seen.get(digest)
        self._last_seen[digest] = timestamp
        cutoff = timestamp - self.window_seconds
        self._last_seen = {
            key: seen for key, seen in self._last_seen.items() if seen >= cutoff
        }
        return previous is not None and timestamp - previous <= self.window_seconds
