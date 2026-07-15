"""ffmpeg/ffprobe helpers — decode uploaded audio files to 16 kHz mono float32.

Uses subprocess streaming (f32le on stdout) so large files never materialise
as temp WAVs. Channel extraction enables per-channel transcription of stereo
telephony recordings (one speaker per leg).
"""
import json
import logging
import subprocess

import numpy as np

logger = logging.getLogger(__name__)

TARGET_SAMPLE_RATE = 16000


class AudioDecodeError(RuntimeError):
    """Raised when ffprobe/ffmpeg cannot handle the uploaded file."""


def probe(path: str) -> dict:
    """Return {"channels": int, "duration_seconds": float} for an audio file."""
    command = [
        "ffprobe", "-v", "error",
        "-select_streams", "a:0",
        "-show_entries", "stream=channels",
        "-show_entries", "format=duration",
        "-of", "json",
        path,
    ]
    try:
        completed = subprocess.run(command, capture_output=True, timeout=60, check=True)
        payload = json.loads(completed.stdout)
    except subprocess.CalledProcessError as exc:
        raise AudioDecodeError(f"ffprobe failed: {exc.stderr.decode('utf-8', 'replace')[:300]}") from exc
    except (subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        raise AudioDecodeError(f"ffprobe failed: {exc}") from exc

    streams = payload.get("streams") or []
    if not streams:
        raise AudioDecodeError("No audio stream found in the uploaded file")
    channels = int(streams[0].get("channels") or 1)
    try:
        duration = float((payload.get("format") or {}).get("duration") or 0.0)
    except (TypeError, ValueError):
        duration = 0.0
    return {"channels": channels, "duration_seconds": duration}


def decode_channel(path: str, channel: int | None = None) -> np.ndarray:
    """Decode one channel (or a mono downmix when channel is None) to float32 @16 kHz."""
    command = ["ffmpeg", "-v", "error", "-i", path]
    if channel is None:
        command += ["-ac", "1"]
    else:
        # Extract a single channel from the first audio stream.
        command += ["-af", f"pan=mono|c0=c{channel}"]
    command += ["-ar", str(TARGET_SAMPLE_RATE), "-f", "f32le", "-"]
    try:
        completed = subprocess.run(command, capture_output=True, timeout=1800, check=True)
    except subprocess.CalledProcessError as exc:
        raise AudioDecodeError(f"ffmpeg decode failed: {exc.stderr.decode('utf-8', 'replace')[:300]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise AudioDecodeError("ffmpeg decode timed out") from exc

    audio = np.frombuffer(completed.stdout, dtype=np.float32)
    if audio.size == 0:
        raise AudioDecodeError("Decoded audio is empty")
    return audio
