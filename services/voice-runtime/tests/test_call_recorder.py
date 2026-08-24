import io
import pathlib
import sys
import wave

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import numpy as np

from app.call_recorder import CallRecorder


def _silence(samples: int) -> bytes:
    return b"\x00\x00" * samples


def _tone(samples: int, value: int = 1000) -> bytes:
    return int(value).to_bytes(2, "little", signed=True) * samples


def _read_wav(data: bytes):
    with wave.open(io.BytesIO(data), "rb") as w:
        return w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()


def _samples(rec: CallRecorder) -> np.ndarray:
    with wave.open(io.BytesIO(rec.to_wav_bytes()), "rb") as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)


def test_empty_recorder_produces_valid_empty_wav():
    rec = CallRecorder()
    assert rec.is_empty()
    ch, width, rate, frames = _read_wav(rec.to_wav_bytes())
    assert (ch, width, rate, frames) == (1, 2, 16000, 0)


def test_customer_audio_kept_at_native_rate():
    rec = CallRecorder(out_rate=16000)
    rec.add_customer(_silence(16000), src_rate=16000)  # 1 second
    ch, width, rate, frames = _read_wav(rec.to_wav_bytes())
    assert (ch, width, rate) == (1, 2, 16000)
    assert frames == 16000
    assert abs(rec.duration_seconds() - 1.0) < 1e-6


def test_agent_audio_resampled_to_recorder_rate():
    rec = CallRecorder(out_rate=16000)
    rec.add_agent(_silence(24000), src_rate=24000)  # 1s at 24k -> ~16k samples
    _, _, _, frames = _read_wav(rec.to_wav_bytes())
    assert abs(frames - 16000) <= 2  # ratecv rounding tolerance


def test_agent_audio_mixed_over_customer_not_interleaved():
    """The live pattern: mic frames keep arriving while agent packets stream.

    The agent voice must stay one contiguous block on the shared timeline (the
    old sequential design shredded it into slivers separated by mic silence,
    which played back slowed down and garbled).
    """
    rec = CallRecorder(out_rate=16000)
    rec.add_customer(_silence(8000))  # 0.5s customer-only lead-in
    for _ in range(20):  # 0.4s of agent audio in 20ms packets...
        rec.add_agent(_tone(320), src_rate=16000)
        rec.add_customer(_silence(160))  # ...with 10ms mic frames interleaved
    # Timeline: agent anchored at 0.5s, contiguous until 0.9s.
    assert abs(rec.duration_seconds() - 0.9) < 0.01
    samples = _samples(rec)
    assert (samples[:8000] == 0).all()
    assert (samples[8000:8000 + 6400] == 1000).all()


def test_overlapping_sides_are_summed():
    # Agent playback is anchored first; mic frames arriving during it (echo,
    # a real barge-in) must be summed into the same region, not appended.
    rec = CallRecorder(out_rate=16000)
    rec.add_agent(_tone(1600, value=300), src_rate=16000)
    rec.add_customer(_tone(1600, value=500))
    samples = _samples(rec)
    assert len(samples) == 1600
    assert (samples == 800).all()


def test_new_playback_snaps_to_wall_clock():
    rec = CallRecorder(out_rate=16000)
    rec.add_agent(_tone(1600), src_rate=16000)  # playback #1 at t=0..0.1s
    rec.add_customer(_silence(16000))  # 1s of mic stream
    rec.add_agent(_tone(1600), src_rate=16000)  # playback #2 -> anchored at 1.0s
    assert abs(rec.duration_seconds() - 1.1) < 0.01
    samples = _samples(rec)
    assert (samples[:1600] == 1000).all()
    assert (samples[1600:16000] == 0).all()
    assert (samples[16000:] == 1000).all()


def test_reset_clears_buffer():
    rec = CallRecorder()
    rec.add_customer(_silence(1000))
    rec.add_agent(_silence(1000), src_rate=16000)
    rec.reset()
    assert rec.is_empty()
    # Cursors must reset too: new audio starts at t=0 again.
    rec.add_customer(_silence(1000))
    assert abs(rec.duration_seconds() - 1000 / 16000) < 1e-6


def test_dtx_pause_resyncs_to_wall_clock():
    """WebRTC DTX: the mic stream pauses during silence. Without wall-clock
    resync the frozen customer cursor made later audio pile onto earlier
    positions (garbled long recordings). With an injected clock, a 10s gap in
    mic frames must land the next audio ~10s later on the timeline."""
    fake = {"t": 100.0}
    rec = CallRecorder(out_rate=16000, clock=lambda: fake["t"])
    rec.add_customer(_tone(16000, value=700))          # t=100: 1s speech
    fake["t"] = 111.0                                  # 10s DTX pause
    rec.add_customer(_tone(16000, value=700))          # resumes at wall ~11s
    dur = rec.duration_seconds()
    assert 11.5 < dur < 12.5, dur
    samples = _samples(rec)
    assert (samples[:16000] == 700).all()              # first second intact
    assert (samples[3 * 16000:10 * 16000] == 0).all()  # gap is silence
    assert (samples[-16000:] == 700).all()             # resumed speech at end


def test_agent_lookahead_not_pushed_forward():
    """The agent cursor legitimately runs AHEAD of the wall clock (TTS packets
    stream ~1s before playout) — resync must never fire for a leading cursor."""
    fake = {"t": 50.0}
    rec = CallRecorder(out_rate=16000, clock=lambda: fake["t"])
    rec.add_agent(_tone(32000), src_rate=16000)  # 2s of audio at wall t=0
    # Wall clock has only advanced 0.1s; cursor is 1.9s ahead — stays put.
    fake["t"] = 50.1
    rec.add_agent(_tone(1600), src_rate=16000)
    assert abs(rec.duration_seconds() - 2.1) < 0.01
