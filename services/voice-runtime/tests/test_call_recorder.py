import io
import pathlib
import sys
import wave

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.call_recorder import CallRecorder


def _silence(samples: int) -> bytes:
    return b"\x00\x00" * samples


def _read_wav(data: bytes):
    with wave.open(io.BytesIO(data), "rb") as w:
        return w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()


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


def test_both_sides_appended_in_order():
    rec = CallRecorder(out_rate=16000)
    rec.add_customer(_silence(8000), src_rate=16000)   # 0.5s
    rec.add_agent(_silence(12000), src_rate=24000)     # 0.5s -> ~8000 samples
    rec.add_customer(_silence(8000), src_rate=16000)   # 0.5s
    _, _, _, frames = _read_wav(rec.to_wav_bytes())
    assert abs(frames - 24000) <= 2  # 8000 + ~8000 + 8000


def test_reset_clears_buffer():
    rec = CallRecorder()
    rec.add_customer(_silence(1000))
    rec.reset()
    assert rec.is_empty()
