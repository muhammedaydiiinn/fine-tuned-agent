from __future__ import annotations

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
    """Conservative lexical classifier used only while agent audio is active.

    Handles multi-token acknowledgements such as "ja ja", "mhm okay",
    "ja genau": every token in the utterance must belong to the backchannel
    vocabulary for the utterance to be classified as a backchannel. A single
    content word causes an immediate interrupt classification (conservative bias).

    The ``max_tokens`` guard caps greedy scan cost and conservatively treats
    long utterances as real speech (default: 6 tokens).

    All changes are backwards-compatible with the single-phrase exact-match API:
    the original 9 phrases still classify as backchannels; ``max_tokens`` is a
    keyword-only argument so existing callers are unaffected.
    """

    def __init__(
        self,
        phrases: set[str] | None = None,
        *,
        max_tokens: int = 6,
    ):
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
        self.phrases = {normalize_transcript(v) for v in (phrases or defaults)}
        self.max_tokens = max_tokens
        # Partition into single-token and multi-token sets for efficient greedy scan
        self._single_tokens: set[str] = {p for p in self.phrases if " " not in p}
        # Longest first so a 2-token phrase wins over two single-token matches
        self._multi_phrases: list[list[str]] = sorted(
            [p.split() for p in self.phrases if " " in p],
            key=len,
            reverse=True,
        )

    def classify(self, text: str) -> BackchannelDecision:
        normalized = normalize_transcript(text)
        if not normalized:
            return BackchannelDecision(is_backchannel=False, normalized_text="")

        # Fast path: exact whole-string match (handles legacy single+multi-word phrases)
        if normalized in self.phrases:
            return BackchannelDecision(is_backchannel=True, normalized_text=normalized)

        tokens = normalized.split()
        # Utterances longer than max_tokens are conservatively treated as real speech
        if len(tokens) > self.max_tokens:
            return BackchannelDecision(is_backchannel=False, normalized_text=normalized)

        # Greedy left-to-right scan: every token must be consumed by a phrase
        i = 0
        while i < len(tokens):
            # Try longest multi-word phrase first
            matched = False
            for phrase_tokens in self._multi_phrases:
                end = i + len(phrase_tokens)
                if tokens[i:end] == phrase_tokens:
                    i = end
                    matched = True
                    break
            if matched:
                continue
            # Try single token
            if tokens[i] in self._single_tokens:
                i += 1
                continue
            # Content token found → real interruption
            return BackchannelDecision(is_backchannel=False, normalized_text=normalized)

        return BackchannelDecision(is_backchannel=True, normalized_text=normalized)


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
