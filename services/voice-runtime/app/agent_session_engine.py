"""FLAGGED LiveKit AgentSession voice engine (task B5).

This is an OPT-IN alternative to the hand-rolled ``app.pipeline.VoicePipeline``.
It is only used when ``settings.voice_engine == "agentsession"`` (see
``app.worker``); the legacy pipeline remains the default and is untouched.

The engine wires OUR existing components into a LiveKit ``AgentSession``:

* STT  — :class:`app.stt.FasterWhisperSTT` wrapped as a ``stt.STT`` plugin.
* LLM  — :class:`app.backend.AgentBackend` (our server-side brain, guardrails
         and dialogue state) wrapped as a ``llm.LLM`` plugin. Only the latest
         user message is forwarded; ``chat_ctx`` history is ignored because the
         backend owns conversation state per ``session_id``.
* TTS  — :class:`app.tts.FishTTS` wrapped as a ``tts.TTS`` plugin streaming raw
         PCM frames.

Turn detection uses the multilingual turn-detector plus Silero VAD. Those two
plugins are lazily imported inside :func:`run_agent_session` so this module
still imports cleanly even when the plugins are not present in the image; a
clear ``RuntimeError`` is raised only when the flag is actually enabled and the
plugins are missing.

NOTE: verified by import-check + unit tests of the pure wrapper logic. A real
audio session has NOT been run — see the task report for what needs live
verification on the GPU host.
"""
from __future__ import annotations

import logging
from collections.abc import AsyncIterable
from typing import Any

import numpy as np
from livekit import rtc
from livekit.agents import (
    APIConnectOptions,
    llm,
    stt,
    tts,
    utils,
)
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS, NOT_GIVEN, NotGivenOr

from app.agent_session_logic import (
    backend_request_id,
    backend_response_chunks,
    latest_user_text,
)
from app.backend import AgentBackend, BackendError
from app.config import Settings
from app.stt import FasterWhisperSTT
from app.tts import FishTTS

__all__ = [
    "WhisperSTTAdapter",
    "BackendLLM",
    "BackendLLMStream",
    "FishTTSAdapter",
    "FishChunkedStream",
    "run_agent_session",
    "latest_user_text",
    "backend_response_chunks",
]

logger = logging.getLogger(__name__)

# Whisper always consumes 16 kHz mono PCM (see app.stt.FasterWhisperSTT).
_WHISPER_SAMPLE_RATE = 16000


# ---------------------------------------------------------------------------
# STT wrapper
# ---------------------------------------------------------------------------
class WhisperSTTAdapter(stt.STT):
    """Adapt :class:`FasterWhisperSTT` to the livekit-agents ``stt.STT`` API.

    faster-whisper is an OFFLINE recogniser, so we advertise
    ``streaming=False``. ``AgentSession`` (given a VAD) will buffer each
    speech segment and call :meth:`_recognize_impl` once per utterance via the
    built-in VAD stream adapter.
    """

    def __init__(self, whisper: FasterWhisperSTT, *, language: str) -> None:
        super().__init__(
            capabilities=stt.STTCapabilities(
                streaming=False,
                interim_results=False,
            )
        )
        self._whisper = whisper
        self._language = language

    async def _recognize_impl(
        self,
        buffer: utils.AudioBuffer,
        *,
        language: NotGivenOr[str] = NOT_GIVEN,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> stt.SpeechEvent:
        pcm = _buffer_to_whisper_pcm(buffer)
        transcript = await self._whisper.transcribe(pcm, sample_rate=_WHISPER_SAMPLE_RATE)
        lang = language if isinstance(language, str) else self._language
        return stt.SpeechEvent(
            type=stt.SpeechEventType.FINAL_TRANSCRIPT,
            alternatives=[
                stt.SpeechData(language=lang, text=transcript.text),
            ],
        )


def _buffer_to_whisper_pcm(buffer: utils.AudioBuffer) -> bytes:
    """Combine + downmix + resample an AudioBuffer to 16 kHz mono int16 PCM."""
    frame = utils.combine_frames(buffer)
    frame = _to_mono(frame)
    if frame.sample_rate != _WHISPER_SAMPLE_RATE:
        frame = _resample(frame, _WHISPER_SAMPLE_RATE)
    return bytes(frame.data)


def _to_mono(frame: rtc.AudioFrame) -> rtc.AudioFrame:
    if frame.num_channels == 1:
        return frame
    samples = np.frombuffer(frame.data, dtype=np.int16).reshape(
        -1, frame.num_channels
    )
    mono = samples.mean(axis=1).astype(np.int16)
    return rtc.AudioFrame(
        data=mono.tobytes(),
        sample_rate=frame.sample_rate,
        num_channels=1,
        samples_per_channel=mono.shape[0],
    )


def _resample(frame: rtc.AudioFrame, target_rate: int) -> rtc.AudioFrame:
    resampler = rtc.AudioResampler(
        input_rate=frame.sample_rate,
        output_rate=target_rate,
        num_channels=frame.num_channels,
    )
    frames = resampler.push(frame)
    frames.extend(resampler.flush())
    if not frames:
        # No output (e.g. empty input) — return silence at the target rate.
        return rtc.AudioFrame(
            data=b"",
            sample_rate=target_rate,
            num_channels=frame.num_channels,
            samples_per_channel=0,
        )
    return utils.combine_frames(frames)


# ---------------------------------------------------------------------------
# LLM wrapper — our agent-backend brain
# ---------------------------------------------------------------------------
class BackendLLMStream(llm.LLMStream):
    def __init__(
        self,
        backend_llm: "BackendLLM",
        *,
        chat_ctx: llm.ChatContext,
        tools: list[llm.Tool],
        conn_options: APIConnectOptions,
    ) -> None:
        super().__init__(
            backend_llm,
            chat_ctx=chat_ctx,
            tools=tools,
            conn_options=conn_options,
        )
        self._backend = backend_llm.backend
        self._session_id = backend_llm.session_id

    async def _run(self) -> None:
        user_text = latest_user_text(self._chat_ctx)
        try:
            turn = await self._backend.agent_turn(self._session_id, user_text)
        except BackendError:
            logger.warning(
                "agent-backend turn failed — session=%s", self._session_id
            )
            raise
        request_id = backend_request_id(turn, utils.shortuuid())
        chunks = backend_response_chunks(turn)
        for text in chunks:
            self._event_ch.send_nowait(
                llm.ChatChunk(
                    id=request_id,
                    delta=llm.ChoiceDelta(role="assistant", content=text),
                )
            )


class BackendLLM(llm.LLM):
    """Wrap :class:`AgentBackend` as a livekit-agents ``llm.LLM``.

    Each ``chat()`` call triggers exactly one ``/agent-turn`` request for the
    latest user utterance; the returned ``agent_response`` is streamed back.
    """

    def __init__(self, backend: AgentBackend, session_id: str) -> None:
        super().__init__()
        self.backend = backend
        self.session_id = session_id

    def chat(
        self,
        *,
        chat_ctx: llm.ChatContext,
        tools: list[llm.Tool] | None = None,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
        parallel_tool_calls: NotGivenOr[bool] = NOT_GIVEN,
        tool_choice: NotGivenOr[Any] = NOT_GIVEN,
        extra_kwargs: NotGivenOr[dict[str, Any]] = NOT_GIVEN,
    ) -> BackendLLMStream:
        return BackendLLMStream(
            self,
            chat_ctx=chat_ctx,
            tools=tools or [],
            conn_options=conn_options,
        )


# ---------------------------------------------------------------------------
# TTS wrapper — Fish
# ---------------------------------------------------------------------------
class FishChunkedStream(tts.ChunkedStream):
    def __init__(
        self,
        *,
        tts_plugin: "FishTTSAdapter",
        input_text: str,
        conn_options: APIConnectOptions,
    ) -> None:
        super().__init__(
            tts=tts_plugin, input_text=input_text, conn_options=conn_options
        )
        self._fish = tts_plugin.fish
        self._sample_rate = tts_plugin.sample_rate

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        output_emitter.initialize(
            request_id=utils.shortuuid(),
            sample_rate=self._sample_rate,
            num_channels=1,
            mime_type="audio/pcm",
        )
        # voice_style is empty here: the backend brain already shaped the reply,
        # and AgentSession does not thread our per-turn voice_style through the
        # TTS wrapper. Fish falls back to "normal" pace for an empty dict.
        async for chunk in self._fish.stream(self.input_text, {}):
            if chunk:
                output_emitter.push(chunk)
        output_emitter.flush()


class FishTTSAdapter(tts.TTS):
    """Adapt :class:`FishTTS` to the livekit-agents ``tts.TTS`` API."""

    def __init__(self, fish, *, sample_rate: int) -> None:
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=sample_rate,
            num_channels=1,
        )
        self.fish = fish

    def synthesize(
        self,
        text: str,
        *,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> FishChunkedStream:
        return FishChunkedStream(
            tts_plugin=self, input_text=text, conn_options=conn_options
        )


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------
def _load_turn_detection_and_vad():
    """Lazily import the turn-detector + Silero VAD plugins.

    Kept out of module import so this module loads even when the plugins are
    absent from the image. Raises a clear RuntimeError only when the flag is
    actually enabled and a plugin is missing (needs an image rebuild).
    """
    try:
        from livekit.plugins.turn_detector.multilingual import MultilingualModel
    except ImportError as exc:  # pragma: no cover - depends on image contents
        raise RuntimeError(
            "voice_engine=agentsession requires livekit-plugins-turn-detector, "
            "which is not installed in this image. Add it to requirements.txt "
            "and rebuild the voice-runtime image."
        ) from exc
    try:
        from livekit.plugins import silero
    except ImportError as exc:  # pragma: no cover - depends on image contents
        raise RuntimeError(
            "voice_engine=agentsession requires livekit-plugins-silero, which "
            "is not installed in this image. Add it to requirements.txt and "
            "rebuild the voice-runtime image."
        ) from exc
    return MultilingualModel(), silero.VAD.load()


async def run_agent_session(
    ctx,
    session_id: str,
    settings: Settings,
    stt: FasterWhisperSTT,
) -> None:
    """Run one voice call via a LiveKit ``AgentSession``.

    Mirrors what ``app.worker`` needs for the legacy path: it connects to the
    room, waits for the caller, then drives the session until disconnect.
    """
    # Imported lazily so this module (and the whole worker) still imports when
    # the AgentSession plugins are not baked into the image.
    from livekit import agents
    from livekit.agents import Agent, AgentSession

    turn_detection, vad = _load_turn_detection_and_vad()

    await ctx.connect(auto_subscribe=agents.AutoSubscribe.AUDIO_ONLY)
    try:
        participant = await ctx.wait_for_participant()
    except RuntimeError:
        logger.info("Voice room closed before the browser participant was ready")
        return
    logger.info(
        "AgentSession voice participant connected — session=%s participant=%s",
        session_id,
        participant.identity,
    )

    backend = AgentBackend(settings)
    stt_adapter = WhisperSTTAdapter(stt, language=settings.whisper_language)
    llm_adapter = BackendLLM(backend, session_id)
    tts_adapter = FishTTSAdapter(FishTTS(settings), sample_rate=settings.tts_sample_rate)

    session = AgentSession(
        stt=stt_adapter,
        llm=llm_adapter,
        tts=tts_adapter,
        vad=vad,
        turn_detection=turn_detection,
    )
    agent = Agent(instructions="")

    try:
        await session.start(agent, room=ctx.room)
        # Opening turn: the backend produces the greeting for an empty prompt,
        # mirroring the legacy pipeline's opening agent_turn("").
        try:
            opening = await backend.agent_turn(session_id, "")
            opening_text = (opening or {}).get("agent_response") or ""
            if opening_text.strip():
                await session.say(opening_text.strip())
        except BackendError:
            logger.warning(
                "Opening agent_turn failed — session=%s", session_id
            )
        # Keep the job alive until the room disconnects.
        await _wait_for_disconnect(ctx)
    finally:
        await session.aclose()
        await backend.aclose()
        await tts_adapter.fish.aclose()


async def _wait_for_disconnect(ctx) -> None:
    import asyncio

    disconnected = asyncio.Event()
    room = ctx.room

    def _on_disconnected(*_args) -> None:
        disconnected.set()

    room.on("disconnected", _on_disconnected)
    try:
        if room.connection_state == rtc.ConnectionState.CONN_DISCONNECTED:
            return
        await disconnected.wait()
    finally:
        room.off("disconnected", _on_disconnected)
