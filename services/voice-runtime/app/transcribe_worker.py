"""Transcribe-worker — Redis consumer for uploaded call recordings.

Pops jobs from agent:transcribe_jobs (brpoplpush + processing-list recovery,
same shape as the eval worker), decodes the file with ffmpeg, transcribes with
faster-whisper and POSTs the timestamped segments back to agent-backend. No
database access — agent-backend owns the schema; this worker only does audio.

Stereo telephony recordings (one speaker per leg) are transcribed per channel
and speaker-tagged by channel; the agent channel is identified by sales-script
markers. Mono recordings get speaker=null — agent-backend runs LLM attribution.
"""
import json
import logging
import time

import httpx
import redis

from app.config import get_settings
from app.audio_decode import AudioDecodeError, decode_channel, probe
from app.transcribe import Seg, load_model, transcribe_segments

logger = logging.getLogger(__name__)

POLL_INTERVAL = 5

# Sales-script markers used to decide which stereo channel carries the agent.
AGENT_MARKERS = (
    "callshield",
    "gold paket",
    "anna weber",
    "14 tage",
    "29,99",
    "risikonummern",
)


def _score_agent_channel(segments: list[Seg], probe_seconds: float = 90.0) -> int:
    """Count agent-script markers in the first ``probe_seconds`` of a channel."""
    text = " ".join(s.text for s in segments if s.start_s <= probe_seconds).lower()
    return sum(text.count(marker) for marker in AGENT_MARKERS)


def merge_stereo_segments(
    channel_segments: list[list[Seg]],
) -> tuple[list[dict], bool]:
    """Merge per-channel segments into one speaker-tagged timeline.

    Returns (segments, speakers_resolved). If neither channel shows script
    markers the channel→role mapping is ambiguous: speakers come back None and
    agent-backend falls back to LLM attribution.
    """
    scores = [_score_agent_channel(segments) for segments in channel_segments]
    resolved = any(score > 0 for score in scores)
    agent_channel = scores.index(max(scores)) if resolved else -1

    tagged: list[tuple[Seg, str | None]] = []
    for channel, segments in enumerate(channel_segments):
        speaker = None
        if resolved:
            speaker = "agent" if channel == agent_channel else "customer"
        tagged.extend((segment, speaker) for segment in segments)
    tagged.sort(key=lambda pair: pair[0].start_s)

    merged = [
        {
            "idx": idx,
            "start_ms": int(segment.start_s * 1000),
            "end_ms": int(segment.end_s * 1000),
            "text": segment.text,
            "speaker": speaker,
            "confidence": segment.confidence,
        }
        for idx, (segment, speaker) in enumerate(tagged)
    ]
    return merged, resolved


def mono_segments(segments: list[Seg]) -> list[dict]:
    return [
        {
            "idx": idx,
            "start_ms": int(segment.start_s * 1000),
            "end_ms": int(segment.end_s * 1000),
            "text": segment.text,
            "speaker": None,
            "confidence": segment.confidence,
        }
        for idx, segment in enumerate(segments)
    ]


class TranscribeWorker:
    def __init__(self, settings):
        self.settings = settings
        self.queue = settings.transcribe_queue
        self.processing_queue = f"{settings.transcribe_queue}:processing"
        self._model = None

    # Lazy: an idle worker holds no GPU memory.
    def _get_model(self):
        if self._model is None:
            self._model = load_model(self.settings)
        return self._model

    def _headers(self) -> dict[str, str]:
        return {"X-API-Key": self.settings.api_key} if self.settings.api_key else {}

    def _post_result(self, recording_id: int, payload: dict) -> None:
        url = f"{self.settings.agent_backend_url}/recordings/{recording_id}/transcript"
        with httpx.Client(timeout=60.0) as client:
            response = client.post(url, json=payload, headers=self._headers())
            response.raise_for_status()

    def process(self, recording_id: int, path: str) -> None:
        info = probe(path)
        duration = info["duration_seconds"]
        channels = info["channels"]
        if duration > self.settings.transcribe_max_duration_seconds:
            raise AudioDecodeError(
                f"Recording is too long ({duration / 60:.0f} min > "
                f"{self.settings.transcribe_max_duration_seconds / 60:.0f} min limit)"
            )

        model = self._get_model()
        started = time.perf_counter()
        if channels >= 2:
            channel_segments = [
                transcribe_segments(model, decode_channel(path, channel), self.settings.whisper_language)
                for channel in (0, 1)
            ]
            segments, resolved = merge_stereo_segments(channel_segments)
            logger.info(
                "Stereo recording %d transcribed: %d segments, agent channel %s",
                recording_id, len(segments), "resolved" if resolved else "ambiguous",
            )
        else:
            segments = mono_segments(
                transcribe_segments(model, decode_channel(path, None), self.settings.whisper_language)
            )
            logger.info("Mono recording %d transcribed: %d segments", recording_id, len(segments))

        logger.info(
            "Recording %d done in %.1fs (audio %.1fs, %d ch)",
            recording_id, time.perf_counter() - started, duration, channels,
        )
        self._post_result(recording_id, {
            "duration_seconds": duration,
            "channels": channels,
            "segments": segments,
        })

    def handle_message(self, raw: str) -> None:
        message = json.loads(raw)
        payload = message.get("payload") or {}
        recording_id = payload.get("recording_id")
        path = payload.get("path")
        if not recording_id or not path:
            logger.warning("Transcribe message missing fields: %s", message)
            return
        try:
            self.process(int(recording_id), str(path))
        except Exception as exc:
            logger.exception("Transcription failed — recording=%s", recording_id)
            try:
                self._post_result(int(recording_id), {"error": str(exc)[:800]})
            except Exception:
                logger.exception("Could not report transcription failure to backend")

    def run(self) -> None:
        client = redis.from_url(self.settings.redis_url, decode_responses=True)
        recovered = 0
        while True:
            item = client.rpoplpush(self.processing_queue, self.queue)
            if item is None:
                break
            recovered += 1
        logger.info("Transcribe worker started. Queue: %s", self.queue)
        if recovered:
            logger.warning("Recovered %d interrupted transcribe job(s)", recovered)

        while True:
            raw = client.brpoplpush(self.queue, self.processing_queue, timeout=POLL_INTERVAL)
            if raw is None:
                continue
            try:
                self.handle_message(raw)
            except Exception:
                logger.exception("Transcribe message processing failed")
            finally:
                # Failures are reported to the backend per-recording; never
                # auto-retry a poison message.
                client.lrem(self.processing_queue, 1, raw)


def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )
    TranscribeWorker(settings).run()


if __name__ == "__main__":
    main()
