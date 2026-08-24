"""Headless end-to-end voice call test — a synthetic customer calls the agent.

Joins a LiveKit room exactly like the panel's voice test (agent dispatch via
room configuration), publishes Fish-TTS-synthesized German customer speech,
records the agent's audio replies, and prints a turn-by-turn report. The full
production path is exercised: LiveKit → voice-runtime worker → STT →
agent-backend → TTS → LiveKit.

Run inside the voice-runtime container:

    python3 tools/e2e_call.py
    python3 tools/e2e_call.py --lines "Ja, das bin ich." "Was kostet das?"

The room is named voice-e2e-<hex>; afterwards inspect the session in the panel
or via the DB (external_session_id = room name). Agent audio is written to
/tmp/e2e_agent.wav for offline verification.
"""
from __future__ import annotations

import argparse
import asyncio
import audioop
import json
import time
import uuid
import wave

import numpy as np

DEFAULT_LINES = [
    "Ja, das bin ich.",
    "Was kostet das?",
    "Okay, schicken Sie mir den sicheren Link.",
    "Ich will nichts kaufen.",
]

MIC_RATE = 48000
CAP_RATE = 16000
FRAME_MS = 10
AGENT_SILENCE_DONE_S = 1.5   # agent counts as finished after this much quiet
AGENT_REPLY_TIMEOUT_S = 30.0
RMS_VOICED = 300


def build_token(settings, room_name: str) -> str:
    from livekit import api

    metadata = json.dumps({"session_id": room_name}, separators=(",", ":"))
    return (
        api.AccessToken(settings.livekit_api_key, settings.livekit_api_secret)
        .with_identity("e2e-customer")
        .with_name("E2E synthetic customer")
        .with_grants(api.VideoGrants(
            room_join=True, room=room_name,
            can_publish=True, can_subscribe=True, can_publish_data=True,
        ))
        .with_room_config(api.RoomConfiguration(agents=[
            api.RoomAgentDispatch(agent_name=settings.livekit_agent_name, metadata=metadata),
        ]))
        .to_jwt()
    )


async def synth_lines(settings, texts: list[str]) -> dict[str, bytes]:
    """Fish-TTS each customer line, resampled to the 48 kHz mic rate."""
    from app.tts import FishTTS

    tts = FishTTS(settings)
    clips: dict[str, bytes] = {}
    try:
        for text in texts:
            chunks = []
            async for chunk in tts.stream(text, {"pace": "normal"}):
                chunks.append(chunk)
            pcm = b"".join(chunks)
            pcm48, _ = audioop.ratecv(pcm, 2, 1, settings.tts_sample_rate, MIC_RATE, None)
            clips[text] = pcm48
    finally:
        await tts.aclose()
    return clips


class AgentEar:
    """Collects the agent's audio and tracks when it is speaking."""

    def __init__(self) -> None:
        self.buffer = bytearray()
        self.last_voiced_at: float | None = None
        self.total_voiced_ms = 0.0

    def add(self, frame_bytes: bytes) -> None:
        self.buffer.extend(frame_bytes)
        rms = audioop.rms(frame_bytes, 2) if frame_bytes else 0
        if rms >= RMS_VOICED:
            self.last_voiced_at = time.monotonic()
            self.total_voiced_ms += FRAME_MS

    async def wait_reply_done(self) -> float:
        """Wait until the agent spoke and then went quiet. Returns voiced ms."""
        started_ms = self.total_voiced_ms
        deadline = time.monotonic() + AGENT_REPLY_TIMEOUT_S
        while time.monotonic() < deadline:
            spoke_ms = self.total_voiced_ms - started_ms
            if (
                spoke_ms > 300
                and self.last_voiced_at
                and time.monotonic() - self.last_voiced_at >= AGENT_SILENCE_DONE_S
            ):
                return spoke_ms
            await asyncio.sleep(0.1)
        return self.total_voiced_ms - started_ms


async def run_call(lines: list[str]) -> None:
    from livekit import rtc

    from app.config import Settings

    settings = Settings()
    room_name = f"voice-e2e-{uuid.uuid4().hex[:10]}"
    token = build_token(settings, room_name)
    print(f"oda: {room_name}")

    print("müşteri replikleri sentezleniyor (Fish TTS)…")
    real_texts = [l.lstrip("!^") for l in lines if l.lstrip("!^") != "@blip"]
    clips = await synth_lines(settings, real_texts)

    room = rtc.Room()
    ear = AgentEar()
    capture_tasks: list[asyncio.Task] = []

    @room.on("track_subscribed")
    def _on_track(track, publication, participant):
        if track.kind != rtc.TrackKind.KIND_AUDIO:
            return
        print(f"ajan sesi yakalanıyor — participant={participant.identity}")
        stream = rtc.AudioStream(track, sample_rate=CAP_RATE, num_channels=1)

        async def _pump() -> None:
            async for event in stream:
                ear.add(bytes(event.frame.data))

        capture_tasks.append(asyncio.create_task(_pump()))

    await room.connect(settings.livekit_url, token)
    print("odaya bağlanıldı; ajan bekleniyor…")

    source = rtc.AudioSource(MIC_RATE, 1)
    track = rtc.LocalAudioTrack.create_audio_track("mic", source)
    await room.local_participant.publish_track(track)

    frame_bytes = MIC_RATE // 100 * 2  # 10 ms
    speech_queue: asyncio.Queue[bytes] = asyncio.Queue()

    async def mic_pump() -> None:
        """Real callers stream mic frames continuously (silence included) —
        the call recorder's wall clock depends on it. Pump silence between
        scripted lines and splice queued speech in, 10 ms at a time."""
        silence = b"\x00" * frame_bytes
        current: bytes | None = None
        offset = 0
        while True:
            if current is None and not speech_queue.empty():
                current = speech_queue.get_nowait()
                offset = 0
            if current is not None:
                chunk = current[offset:offset + frame_bytes]
                offset += frame_bytes
                if offset >= len(current):
                    current = None
                if len(chunk) < frame_bytes:
                    chunk = chunk + b"\x00" * (frame_bytes - len(chunk))
            else:
                chunk = silence
            frame = rtc.AudioFrame(
                data=chunk, sample_rate=MIC_RATE,
                num_channels=1, samples_per_channel=len(chunk) // 2,
            )
            await source.capture_frame(frame)

    mic_task = asyncio.create_task(mic_pump())

    async def speak(pcm48: bytes) -> None:
        await speech_queue.put(pcm48)
        # Wait until the clip has fully played out of the mic pump.
        await asyncio.sleep(len(pcm48) / 2 / MIC_RATE + 0.2)

    def blip() -> bytes:
        # 0.5 s noise burst: white noise loses ~2/3 of its energy in the
        # 48k->16k capture resample, so synthesize hot (RMS ~2000) to land
        # above the 350 VAD threshold after resampling. Undecodable for
        # whisper — simulates a cough / rustle / bad mic.
        rng = np.random.default_rng(3)
        samples = (rng.normal(0, 2000, int(0.5 * MIC_RATE))).astype(np.int16)
        return samples.tobytes()

    async def wait_agent_started(timeout: float = 20.0) -> bool:
        base = ear.total_voiced_ms
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if ear.total_voiced_ms - base > 400:
                return True
            await asyncio.sleep(0.05)
        return False

    # Opening: the agent greets on connect — unless the script says the
    # customer speaks FIRST ('^' prefix on the first line), which exercises
    # the customer-spoke-first / opening-skipped race.
    if lines and lines[0].startswith("^"):
        first = lines.pop(0).lstrip("^")
        await asyncio.sleep(0.4)
        print(f"[müşteri, AJANDAN ÖNCE] {first!r}")
        await speak(clips[first])
        reply_ms = await ear.wait_reply_done()
        print(f"[ajan]    {reply_ms:.0f} ms ses — {'OK' if reply_ms > 300 else 'CEVAP YOK'}")
    else:
        greeting_ms = await ear.wait_reply_done()
        print(f"[açılış] ajan konuştu: {greeting_ms:.0f} ms ses")

    for index, line in enumerate(lines):
        text = line.lstrip("!")
        print(f"[müşteri] {line!r}")
        await speak(blip() if text == "@blip" else clips[text])
        next_is_bargein = index + 1 < len(lines) and lines[index + 1].startswith("!")
        if next_is_bargein:
            # Only wait for the reply to START — the next line cuts in over it.
            started = await wait_agent_started()
            await asyncio.sleep(1.0)
            print(f"[ajan]    konuşmaya başladı: {started} — söz kesiliyor")
        else:
            reply_ms = await ear.wait_reply_done()
            status = "OK" if reply_ms > 300 else "CEVAP YOK"
            print(f"[ajan]    {reply_ms:.0f} ms ses — {status}")

    # Give a still-processing final turn a SHORT window to answer, then hang
    # up promptly — lingering only pads the call recording with silence.
    baseline = ear.total_voiced_ms
    for _ in range(40):
        if ear.total_voiced_ms - baseline > 300:
            extra = await ear.wait_reply_done()
            print(f"[ajan, geç cevap] {extra:.0f} ms ses")
            break
        await asyncio.sleep(0.1)

    mic_task.cancel()
    await room.disconnect()
    for task in capture_tasks:
        task.cancel()

    out = "/tmp/e2e_agent.wav"
    with wave.open(out, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(CAP_RATE)
        writer.writeframes(bytes(ear.buffer))
    print(f"ajan sesi kaydedildi: {out} ({len(ear.buffer) / 2 / CAP_RATE:.1f}s)")
    print(f"oturum DB'de: external_session_id={room_name}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lines", nargs="*", default=DEFAULT_LINES)
    args = parser.parse_args()
    asyncio.run(run_call(args.lines))


if __name__ == "__main__":
    main()
