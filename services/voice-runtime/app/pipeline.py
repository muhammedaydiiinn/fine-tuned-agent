from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import time
import uuid

import numpy as np
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from livekit import rtc

from app.backend import AgentBackend, BackendError
from app.config import Settings
from app.segmenter import SegmentationConfig, UtteranceSegmenter
from app.stt import FasterWhisperSTT, STTError
from app.tts import FishTTS
from app.turn_taking import BackchannelClassifier, TranscriptDeduplicator

logger = logging.getLogger(__name__)
EVENT_TOPIC = "voice.events"
CONTROL_TOPIC = "voice.control"
INPUT_SAMPLE_RATE = 16000


def _rtc():
    """Lazy livekit.rtc accessor — lets pipeline.py be imported without livekit installed."""
    from livekit import rtc  # noqa: PLC0415
    return rtc


class VoicePipeline:
    def __init__(
        self,
        settings: Settings,
        session_id: str,
        *,
        stt: FasterWhisperSTT | None = None,
    ):
        self.settings = settings
        self.session_id = session_id
        self.backend = AgentBackend(settings)
        self.stt = stt or FasterWhisperSTT(settings)
        self.tts = FishTTS(settings)
        self.segmenter = UtteranceSegmenter(
            SegmentationConfig(
                sample_rate=INPUT_SAMPLE_RATE,
                rms_threshold=settings.speech_rms_threshold,
                min_speech_ms=settings.speech_min_ms,
                end_silence_ms=settings.speech_end_silence_ms,
                max_speech_ms=settings.speech_max_ms,
                preroll_ms=settings.speech_preroll_ms,
                adaptive_threshold=settings.speech_adaptive_vad,
                noise_floor_margin=settings.speech_noise_floor_margin,
                noise_ema_alpha=settings.speech_noise_ema_alpha,
                absolute_floor_rms=settings.speech_rms_threshold,
                exit_threshold_ratio=settings.speech_exit_threshold_ratio,
            )
        )
        phrases = {
            phrase.strip()
            for phrase in settings.backchannel_phrases.split(",")
            if phrase.strip()
        }
        self.backchannels = BackchannelClassifier(
            phrases, max_tokens=settings.backchannel_max_tokens
        )
        self.deduplicator = TranscriptDeduplicator(
            settings.duplicate_transcript_window_seconds
        )
        self._utterances: asyncio.Queue[tuple[bytes, str | None]] = asyncio.Queue(
            maxsize=settings.utterance_queue_size
        )
        self._active_turn_task: asyncio.Task | None = None
        self._playback_task: asyncio.Task | None = None
        self._partial_task: asyncio.Task | None = None
        self._playback_cancel: asyncio.Event | None = None
        self._agent_speaking = False
        self._pending_interruption: str | None = None
        self._speech_overlap_kind: str | None = None
        self._current_agent_text: str = ""
        # Last thing the agent said, kept AFTER playback ends (unlike
        # _current_agent_text). Lets us tell whether the agent just asked a
        # question when the customer's "ja" arrives — a real answer, not a
        # droppable backchannel.
        self._last_agent_text: str = ""
        self._barge_in_probe_task: asyncio.Task | None = None
        self._sequence = 0
        self._generation = 0
        self._playback_seq = 0
        self._current_playback_id: str | None = None
        # Milliseconds of agent audio actually played out in the last _speak
        # call (used to reconstruct what the customer heard before a barge-in).
        self._last_spoken_ms: float = 0.0
        self._room: rtc.Room | None = None
        self._opening_task: asyncio.Task | None = None
        self._speech_started_at: float | None = None
        self._interruption_latency_ms: float | None = None
        self._last_partial_text: str = ""
        self._event_tasks: set[asyncio.Task] = set()
        self._supervisor_lock = asyncio.Lock()

    async def run(self, room: rtc.Room, participant: rtc.RemoteParticipant) -> None:
        self._room = room
        try:
            await self.backend.create_session(self.session_id)
        except BackendError:
            logger.exception(
                "Failed to create backend session — session=%s", self.session_id
            )
            await self._emit(
                room,
                "voice_error",
                detail="Could not initialise session with agent backend",
            )
            return

        await self._emit(room, "voice_session_ready", state="listening")
        track = await self._wait_for_audio_track(room, participant)

        consumer = asyncio.create_task(
            self._consume_utterances(room),
            name=f"voice-consumer-{self.session_id}",
        )
        disconnected = asyncio.Event()

        def on_disconnected(*_args) -> None:
            disconnected.set()

        def on_participant_disconnected(remote_participant) -> None:
            if remote_participant.sid == participant.sid:
                disconnected.set()

        def on_data_received(*event_args) -> None:
            if not event_args:
                logger.warning(
                    "Received data_received event without payload — session=%s",
                    self.session_id,
                )
                return
            payload = event_args[0]
            remote_participant = event_args[1] if len(event_args) > 1 else None
            topic = event_args[-1] if len(event_args) > 2 else None
            task = asyncio.create_task(
                self._handle_room_data(
                    room,
                    payload,
                    remote_participant,
                    topic,
                )
            )
            self._event_tasks.add(task)
            task.add_done_callback(self._event_tasks.discard)

        room.on("disconnected", on_disconnected)
        room.on("participant_disconnected", on_participant_disconnected)
        room.on("data_received", on_data_received)
        stream_task: asyncio.Task | None = None
        disconnect_task: asyncio.Task | None = None
        warmup_task: asyncio.Task | None = None
        graceful_audio_end = False
        try:
            stream = _rtc().AudioStream(
                track,
                sample_rate=INPUT_SAMPLE_RATE,
                num_channels=1,
            )
            stream_task = asyncio.create_task(
                self._consume_audio_stream(room, stream),
                name=f"voice-stream-{self.session_id}",
            )
            warmup_task = asyncio.create_task(
                self.stt.warmup(),
                name=f"voice-stt-warmup-{self.session_id}",
            )
            self._opening_task = asyncio.create_task(
                self._run_opening_turn(room),
                name=f"voice-opening-{self.session_id}",
            )
            disconnect_task = asyncio.create_task(disconnected.wait())
            done, _ = await asyncio.wait(
                {stream_task, disconnect_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if stream_task in done:
                await stream_task
                graceful_audio_end = True
            else:
                logger.info("Voice room disconnected — session=%s", self.session_id)
        except asyncio.CancelledError:
            logger.info("Voice pipeline cancelled — session=%s", self.session_id)
        finally:
            self._room = None
            room.off("disconnected", on_disconnected)
            room.off("participant_disconnected", on_participant_disconnected)
            room.off("data_received", on_data_received)
            for task in (stream_task, disconnect_task, warmup_task, self._opening_task):
                if task and not task.done():
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await task
            if graceful_audio_end:
                trailing = self.segmenter.flush()
                if trailing:
                    await self._enqueue_utterance(room, trailing)
                await self._utterances.join()
            consumer.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await consumer
            await self._cancel_active_work(room, reason="session_closed")

    async def _run_opening_turn(self, room: rtc.Room) -> None:
        """Play the agent greeting while microphone capture is already active."""
        try:
            opening = await self.backend.agent_turn(self.session_id, "")
            opening_text = opening["agent_response"]
            response_generation_id = self._generation
            await self._emit(
                room,
                "agent_response",
                turn_id=opening["turn_id"],
                turn_index=opening["turn_index"],
                text=opening_text,
                policy=opening.get("policy"),
                latency=opening.get("latency", {}),
                response_generation_id=response_generation_id,
            )
            self._playback_task = asyncio.create_task(
                self._speak(
                    room,
                    opening_text,
                    opening.get("voice_style", {}),
                    "agent-greeting",
                    turn_id=opening["turn_id"],
                    response_generation_id=response_generation_id,
                )
            )
            first_audio_ms, cancelled, playback_id = await self._playback_task
            self._playback_task = None
            if cancelled:
                await self._emit(
                    room,
                    "playback_cancelled",
                    turn_id=opening["turn_id"],
                    playback_id=self._current_playback_id,
                    response_generation_id=response_generation_id,
                    reason="customer_speech",
                    state="processing",
                )
                return
            await self._emit(
                room,
                "voice_turn_complete",
                turn_id=opening["turn_id"],
                playback_id=self._current_playback_id,
                response_generation_id=response_generation_id,
                metrics={"tts_first_audio_ms": first_audio_ms or 0.0},
                state="listening",
            )
        except BackendError:
            logger.warning(
                "Opening turn backend call failed — session=%s", self.session_id
            )
        except asyncio.CancelledError:
            raise

    async def _consume_audio_stream(
        self,
        room: rtc.Room,
        stream: rtc.AudioStream,
    ) -> None:
        async for event in stream:
            utterance = self.segmenter.push(bytes(event.frame.data))
            if self.segmenter.consume_speech_started():
                self._speech_started_at = time.perf_counter()
                self._last_partial_text = ""
                if self._agent_speaking:
                    self._speech_overlap_kind = "playback"
                elif self._active_turn_task and not self._active_turn_task.done():
                    self._speech_overlap_kind = "active_turn"
                else:
                    self._speech_overlap_kind = None
                await self._emit(
                    room,
                    "speech_started",
                    state="hearing",
                    agent_speaking=self._agent_speaking,
                )
                if self._speech_overlap_kind:
                    # A short acknowledgement should not stop playback. Wait
                    # for sustained customer speech before cancelling.
                    self._schedule_barge_in_probe()
                # Start partial transcript loop if enabled (default OFF)
                if self.settings.enable_partial_transcripts:
                    if self._partial_task is None or self._partial_task.done():
                        self._partial_task = asyncio.create_task(
                            self._emit_partials(room)
                        )
            if utterance:
                await self._enqueue_utterance(room, utterance)

    async def _enqueue_utterance(self, room: rtc.Room, pcm: bytes) -> None:
        # Cancel partial task — final transcript takes over
        if self._partial_task and not self._partial_task.done():
            self._partial_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._partial_task
        self._partial_task = None

        overlap_kind = self._speech_overlap_kind
        self._speech_overlap_kind = None
        if self._utterances.full():
            await self._emit(
                room,
                "voice_error",
                detail="Voice input queue is full; oldest utterance was dropped",
            )
            try:
                self._utterances.get_nowait()
                self._utterances.task_done()
            except asyncio.QueueEmpty:
                pass
        await self._utterances.put((pcm, overlap_kind))
        await self._emit(room, "speech_ended", state="processing")

    async def _consume_utterances(self, room: rtc.Room) -> None:
        while True:
            pcm, overlap_kind = await self._utterances.get()
            try:
                task = asyncio.create_task(
                    self._run_turn(room, pcm, overlap_kind)
                )
                self._active_turn_task = task
                await task
            except asyncio.CancelledError:
                raise
            finally:
                self._active_turn_task = None
                self._utterances.task_done()

    async def _run_turn(
        self,
        room: rtc.Room,
        pcm: bytes,
        overlap_kind: str | None = None,
    ) -> None:
        voice_turn_started = time.perf_counter()
        generation = self._generation
        try:
            transcript = await self.stt.transcribe(pcm, sample_rate=INPUT_SAMPLE_RATE)
        except STTError as exc:
            logger.warning(
                "STT unavailable for turn — session=%s detail=%s",
                self.session_id,
                exc,
            )
            await self._emit(
                room,
                "stt_unavailable",
                detail=str(exc),
                state="listening",
            )
            return
        try:
            if not transcript.text:
                await self._emit(room, "empty_transcript", state="listening")
                return
            if self.deduplicator.is_duplicate(transcript.text):
                await self._emit(
                    room,
                    "duplicate_transcript_ignored",
                    text=transcript.text,
                    state="listening",
                )
                return

            decision = self.backchannels.classify(transcript.text)
            interruption_kind = self._pending_interruption
            self._pending_interruption = None
            if (
                overlap_kind == "playback"
                and decision.is_backchannel
                and not self._agent_just_asked()
            ):
                await self._emit(
                    room,
                    "backchannel_detected",
                    text=transcript.text,
                    state="listening",
                )
                return
            if interruption_kind:
                generation = self._generation
                await self._emit(
                    room,
                    "interruption_detected",
                    text=transcript.text,
                    interruption_kind=interruption_kind,
                    interruption_latency_ms=self._interruption_latency_ms,
                    state="processing",
                )
                self._interruption_latency_ms = None

            await self._emit(
                room,
                "transcript_final",
                text=transcript.text,
                stt_ms=transcript.stt_ms,
                state="processing",
            )

            backend_started = time.perf_counter()
            turn = await self.backend.agent_turn(self.session_id, transcript.text)
            backend_roundtrip_ms = (time.perf_counter() - backend_started) * 1000
            if generation != self._generation:
                logger.info(
                    "stale_response_discarded (post-backend) — session=%s"
                    " captured_gen=%d current_gen=%d overlap=%s",
                    self.session_id,
                    generation,
                    self._generation,
                    overlap_kind,
                )
                await self._emit(
                    room,
                    "stale_response_discarded",
                    turn_id=turn.get("turn_id"),
                    response_generation_id=generation,
                    state="listening",
                )
                return

            await self._emit(
                room,
                "agent_response",
                turn_id=turn["turn_id"],
                turn_index=turn["turn_index"],
                text=turn["agent_response"],
                policy=turn["policy"],
                latency=turn["latency"],
                response_generation_id=generation,
                state="speaking",
            )

            tts_started_from_turn = time.perf_counter()
            self._playback_task = asyncio.create_task(
                self._speak(
                    room,
                    turn["agent_response"],
                    turn.get("voice_style", {}),
                    f"agent-audio-{turn['turn_id']}",
                    turn_id=turn["turn_id"],
                    response_generation_id=generation,
                )
            )
            first_audio_ms, cancelled, playback_id = await self._playback_task
            self._playback_task = None
            if cancelled:
                spoken_ms = self._last_spoken_ms
                spoken_prefix = self._estimate_spoken_prefix(
                    turn["agent_response"], spoken_ms
                )
                # Persist what the customer actually heard so the next prompt
                # can resume contextually instead of assuming the full reply
                # was delivered. Best-effort: never fail the session over it.
                await self.backend.report_interruption(
                    turn["turn_id"],
                    {
                        "session_id": self.session_id,
                        "spoken_response": spoken_prefix,
                        "spoken_ms": spoken_ms,
                    },
                )
                await self._emit(
                    room,
                    "playback_cancelled",
                    turn_id=turn["turn_id"],
                    playback_id=playback_id,
                    response_generation_id=generation,
                    reason="customer_speech",
                    spoken_ms=spoken_ms,
                    state="processing",
                )
                return
            if getattr(self.tts, "last_stream_used_fallback", False):
                await self._emit(
                    room,
                    "tts_fallback_activated",
                    turn_id=turn["turn_id"],
                    provider="mock_pcm",
                    state="speaking",
                )
            if generation != self._generation:
                logger.info(
                    "stale_response_discarded (post-playback) — session=%s"
                    " captured_gen=%d current_gen=%d overlap=%s",
                    self.session_id,
                    generation,
                    self._generation,
                    overlap_kind,
                )
                await self._emit(
                    room,
                    "stale_response_discarded",
                    turn_id=turn["turn_id"],
                    playback_id=playback_id,
                    response_generation_id=generation,
                    state="listening",
                )
                return

            total_voice_turn_ms = (time.perf_counter() - voice_turn_started) * 1000
            speech_end_to_first_audio_ms = (
                (tts_started_from_turn - voice_turn_started) * 1000
                + (first_audio_ms or 0.0)
            )
            metrics = {
                "session_id": self.session_id,
                "stt_ms": transcript.stt_ms,
                "backend_ms": turn["latency"]["backend_ms"],
                "llm_ms": turn["latency"]["llm_ms"],
                "tts_first_audio_ms": first_audio_ms or 0.0,
                "speech_end_to_first_audio_ms": speech_end_to_first_audio_ms,
                "total_voice_turn_ms": total_voice_turn_ms,
                "transcript_final": transcript.text,
                "heard_response": turn["agent_response"],
            }
            await self.backend.save_voice_metrics(turn["turn_id"], metrics)
            await self._emit(
                room,
                "voice_turn_complete",
                turn_id=turn["turn_id"],
                playback_id=playback_id,
                response_generation_id=generation,
                metrics={**metrics, "backend_roundtrip_ms": backend_roundtrip_ms},
                state="listening",
            )
            logger.info(
                "voice turn completed — session=%s turn=%s stt_ms=%.0f "
                "backend_ms=%.0f tts_first_ms=%.0f total_ms=%.0f",
                self.session_id,
                turn.get("turn_id"),
                transcript.stt_ms,
                turn["latency"]["backend_ms"],
                first_audio_ms or 0.0,
                total_voice_turn_ms,
            )
        except asyncio.CancelledError:
            await self._emit(room, "turn_cancelled", state="processing")
            raise
        except Exception as exc:
            logger.exception(
                "voice turn failed — session=%s detail=%s", self.session_id, exc
            )
            with contextlib.suppress(Exception):
                await self._emit(
                    room,
                    "voice_error",
                    detail=str(exc),
                    state="error",
                )

    async def _speak(
        self,
        room: rtc.Room,
        text: str,
        voice_style: dict,
        track_label: str,
        *,
        turn_id: int | None = None,
        response_generation_id: int | None = None,
    ) -> tuple[float | None, bool, str | None]:
        tts_started = time.perf_counter()
        first_audio_ms: float | None = None
        spoken_samples = 0
        self._last_spoken_ms = 0.0
        self._playback_cancel = asyncio.Event()
        self._current_agent_text = text
        self._last_agent_text = text
        self._agent_speaking = True
        self._playback_seq += 1
        playback_id = f"{self.session_id}:pb:{self._playback_seq}"
        self._current_playback_id = playback_id
        generation_id = (
            self._generation if response_generation_id is None else response_generation_id
        )
        await self._emit(
            room,
            "agent_playback_started",
            turn_id=turn_id,
            playback_id=playback_id,
            response_generation_id=generation_id,
            state="speaking",
        )
        audio_source = _rtc().AudioSource(self.settings.tts_sample_rate, 1)
        audio_track = _rtc().LocalAudioTrack.create_audio_track(track_label, audio_source)
        publication = None
        cancelled = False
        try:
            publication = await room.local_participant.publish_track(
                audio_track,
                _rtc().TrackPublishOptions(source=_rtc().TrackSource.SOURCE_MICROPHONE),
            )
            pcm_buffer = bytearray()
            async for chunk in self.tts.stream(text, voice_style):
                if self._playback_cancel.is_set():
                    cancelled = True
                    break
                pcm_buffer.extend(chunk)
                frame_bytes = self.settings.tts_sample_rate // 50 * 2
                while len(pcm_buffer) >= frame_bytes:
                    if self._playback_cancel.is_set():
                        cancelled = True
                        break
                    packet = bytes(pcm_buffer[:frame_bytes])
                    del pcm_buffer[:frame_bytes]
                    if first_audio_ms is None:
                        first_audio_ms = (time.perf_counter() - tts_started) * 1000
                    await audio_source.capture_frame(
                        _rtc().AudioFrame(
                            data=packet,
                            sample_rate=self.settings.tts_sample_rate,
                            num_channels=1,
                            samples_per_channel=len(packet) // 2,
                        )
                    )
                    spoken_samples += len(packet) // 2
                if cancelled:
                    break
            if pcm_buffer and not cancelled:
                pcm_buffer.extend(b"\x00" * (-len(pcm_buffer) % 2))
                if first_audio_ms is None:
                    first_audio_ms = (time.perf_counter() - tts_started) * 1000
                await audio_source.capture_frame(
                    _rtc().AudioFrame(
                        data=bytes(pcm_buffer),
                        sample_rate=self.settings.tts_sample_rate,
                        num_channels=1,
                        samples_per_channel=len(pcm_buffer) // 2,
                    )
                )
                spoken_samples += len(pcm_buffer) // 2
            if not cancelled:
                await audio_source.wait_for_playout()
        finally:
            self._last_spoken_ms = spoken_samples * 1000 / self.settings.tts_sample_rate
            self._agent_speaking = False
            self._current_agent_text = ""
            if publication is not None:
                with contextlib.suppress(Exception):
                    await room.local_participant.unpublish_track(publication.sid)
        return first_audio_ms, cancelled, playback_id

    def _request_playback_cancel(self) -> None:
        if self._playback_cancel is not None:
            self._playback_cancel.set()

    def _trigger_barge_in(self, overlap_kind: str, *, source: str = "probe") -> None:
        """Increment generation and cancel playback — idempotent within one speech episode.

        Both the partial-transcript fast-path and the 450ms probe call this.
        The _pending_interruption guard ensures only one fires per episode.
        """
        if self._pending_interruption is not None:
            # Already fired this episode — no-op (idempotency)
            return
        if self._speech_started_at is not None:
            self._interruption_latency_ms = (
                time.perf_counter() - self._speech_started_at
            ) * 1000
        self._pending_interruption = overlap_kind
        captured_generation = self._generation
        self._generation += 1
        logger.info(
            "barge-in triggered — session=%s overlap=%s source=%s new_generation=%d",
            self.session_id,
            overlap_kind,
            source,
            self._generation,
        )
        if self._room is not None:
            task = asyncio.create_task(
                self._emit(
                    self._room,
                    "possible_barge_in",
                    overlap_kind=overlap_kind,
                    source=source,
                    playback_id=self._current_playback_id,
                    response_generation_id=captured_generation,
                    interruption_latency_ms=self._interruption_latency_ms,
                    state="hearing" if self._agent_speaking else "processing",
                )
            )
            self._event_tasks.add(task)
            task.add_done_callback(self._event_tasks.discard)
        self._request_playback_cancel()

    def _probe_delay_ms(self, overlap_kind: str) -> int:
        """Return the probe sleep duration for the given overlap kind.

        Per-overlap windows are optional (None = fall back to barge_in_min_ms).
        """
        if overlap_kind == "playback":
            return self.settings.backchannel_window_ms or self.settings.barge_in_min_ms
        return self.settings.interrupt_confirm_ms or self.settings.barge_in_min_ms

    def _schedule_barge_in_probe(self) -> None:
        if self._barge_in_probe_task and not self._barge_in_probe_task.done():
            return

        # Capture overlap kind BEFORE the sleep so the delay matches the kind
        overlap_kind = self._speech_overlap_kind

        async def probe() -> None:
            delay = self._probe_delay_ms(overlap_kind) / 1000
            await asyncio.sleep(delay)
            # Re-read live state after sleep
            current_overlap = self._speech_overlap_kind
            if not ((current_overlap or overlap_kind) and self.segmenter.speech_active):
                return
            # Only cancel playback and invalidate the in-flight backend
            # response when audio is actually audible. A pure
            # "active_turn" overlap (backend busy but agent not yet
            # speaking) must NOT discard the in-flight turn — doing so
            # causes every turn after the first to be silently dropped as
            # stale_response_discarded. In that case the new utterance
            # is already queued and will run naturally after the current
            # turn completes.
            effective = current_overlap or overlap_kind
            if not (effective == "playback" or self._agent_speaking):
                # active_turn only, not yet speaking — record latency only
                if self._speech_started_at is not None:
                    self._interruption_latency_ms = (
                        time.perf_counter() - self._speech_started_at
                    ) * 1000
                return
            # About to cancel audible playback. Energy alone can be self-echo
            # (the agent hearing its own voice through the caller's speaker) or
            # ambient noise, so confirm there is genuine customer speech first.
            if self.settings.barge_in_verify_content and not await self._confirm_customer_speech():
                return
            self._trigger_barge_in(effective, source="probe")

        self._barge_in_probe_task = asyncio.create_task(probe())

    async def _confirm_customer_speech(self) -> bool:
        """Return True only for a genuine customer interruption during playback.

        Combines content and energy because the in-flight STT pass is
        unreliable here — short, shouted or echo-mixed audio often decodes to an
        empty string. Decision:

        - real words that are not a backchannel and not the agent's own line
          -> interrupt (loud or quiet);
        - no decodable words but the audio is loud (>= barge_in_loud_rms)
          -> interrupt (the customer is clearly talking; STT just failed);
        - no words and quiet -> suppress (self-echo / ambient noise).

        On STT failure it falls back to the energy test alone.
        """
        pcm = self.segmenter.snapshot()
        if pcm is None:
            return False
        rms = self._rms(pcm)
        try:
            partial = await self.stt.transcribe_partial(pcm, sample_rate=INPUT_SAMPLE_RATE)
            text = (partial.text or "").strip()
        except Exception:
            logger.debug(
                "barge-in verification STT failed — session=%s rms=%.0f",
                self.session_id,
                rms,
            )
            text = ""

        if text:
            if self.backchannels.classify(text).is_backchannel and not self._agent_just_asked():
                logger.info(
                    "barge-in suppressed (backchannel) — session=%s rms=%.0f text=%s",
                    self.session_id,
                    rms,
                    text,
                )
                return False
            if self._looks_like_self_echo(text):
                logger.info(
                    "barge-in suppressed (self-echo) — session=%s rms=%.0f text=%s",
                    self.session_id,
                    rms,
                    text,
                )
                return False
            logger.info(
                "barge-in confirmed (speech) — session=%s rms=%.0f text=%s",
                self.session_id,
                rms,
                text,
            )
            return True

        # No decodable words — fall back to loudness, but only when the loud
        # audio is also SUSTAINED. A brief loud burst is almost always an
        # emphatic backchannel ("JA!") that must not silence the agent.
        if rms >= self.settings.barge_in_loud_rms:
            speech_ms = self.segmenter.speech_ms
            if speech_ms >= self.settings.barge_in_loud_min_ms:
                logger.info(
                    "barge-in confirmed (loud sustained, no transcript) — "
                    "session=%s rms=%.0f speech_ms=%.0f",
                    self.session_id,
                    rms,
                    speech_ms,
                )
                return True
            logger.info(
                "barge-in suppressed (loud but brief, likely backchannel) — "
                "session=%s rms=%.0f speech_ms=%.0f",
                self.session_id,
                rms,
                speech_ms,
            )
            return False
        logger.info(
            "barge-in suppressed (quiet, no speech content) — session=%s rms=%.0f",
            self.session_id,
            rms,
        )
        return False

    @staticmethod
    def _rms(pcm: bytes) -> float:
        samples = np.frombuffer(pcm, dtype=np.int16)
        if samples.size == 0:
            return 0.0
        return float(np.sqrt(np.mean(samples.astype(np.float32) ** 2)))

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        return [t for t in re.sub(r"[^\w\s]", " ", text.lower()).split() if t]

    def _looks_like_self_echo(self, text: str) -> bool:
        """True only when every word heard is one the agent is currently saying.

        Self-echo transcribes back to the agent's own words; a real customer
        interruption introduces at least one word the agent did not say. We bias
        toward letting barge-in through: customer speech mixed with the agent's
        own echo must still cancel playback, so a single novel word is enough.
        """
        agent_tokens = set(self._tokenize(self._current_agent_text))
        if not agent_tokens:
            return False
        heard = self._tokenize(text)
        if not heard:
            return False
        novel = [token for token in heard if token not in agent_tokens]
        return not novel

    def _estimate_spoken_prefix(self, text: str, spoken_ms: float) -> str:
        """Best-effort reconstruction of how much of ``text`` the customer heard.

        Maps elapsed playback time to a character offset using the configured
        speaking rate, then trims back to the nearest word boundary so the
        prefix is a clean fragment. Returns the full text when the estimate
        covers (almost) all of it, and "" when nothing was audibly played.
        """
        text = (text or "").strip()
        if not text or spoken_ms <= 0:
            return ""
        chars_per_ms = self.settings.speaking_chars_per_second / 1000.0
        est_chars = int(spoken_ms * chars_per_ms)
        if est_chars >= len(text):
            return text
        if est_chars <= 0:
            return ""
        cutoff = text[:est_chars]
        # Trim to the last word boundary so we don't cut mid-word, unless the
        # cut already landed exactly on whitespace.
        if " " in cutoff and not text[est_chars].isspace():
            cutoff = cutoff.rsplit(" ", 1)[0]
        return cutoff.strip()

    def _agent_just_asked(self) -> bool:
        """True when the agent's last line was a question awaiting an answer.

        A bare "ja"/"okay" overlapping such a line is a real answer, not a
        mid-explanation backchannel, so it must NOT be dropped. Whether that
        "ja" actually confirms anything is the model's call (SOFT SIGNALS in
        the prompt), not ours — we only avoid silently swallowing a possible
        answer. Reads the agent's own act, not a customer keyword list.
        """
        return self._last_agent_text.strip().endswith("?")

    async def _emit_partials(self, room: rtc.Room) -> None:
        """Emit partial transcript events at regular intervals during speech.

        Only active when settings.enable_partial_transcripts=True (default OFF).
        Does NOT persist to backend — browser-only for live UI feedback and
        early barge-in detection.
        """
        interval = self.settings.partial_interval_ms / 1000
        min_speech_ms = self.settings.partial_min_speech_ms
        early_cancel_ms = self.settings.early_interrupt_min_speech_ms
        try:
            while True:
                await asyncio.sleep(interval)
                if not self.segmenter.speech_active:
                    break
                if self.segmenter.speech_ms < min_speech_ms:
                    continue
                pcm = self.segmenter.snapshot()
                if pcm is None:
                    continue
                try:
                    partial = await self.stt.transcribe_partial(
                        pcm, sample_rate=INPUT_SAMPLE_RATE
                    )
                except Exception:
                    logger.debug(
                        "Partial transcription failed — session=%s", self.session_id
                    )
                    continue
                if not partial.text or partial.text == self._last_partial_text:
                    continue
                self._last_partial_text = partial.text
                await self._emit(
                    room,
                    "partial_transcript",
                    text=partial.text,
                    stt_ms=partial.stt_ms,
                    is_final=False,
                    state="hearing",
                )
                # Early barge-in: non-backchannel partial during active playback
                if (
                    self._agent_speaking
                    and self._speech_overlap_kind == "playback"
                    and not self.backchannels.classify(partial.text).is_backchannel
                    and not self._looks_like_self_echo(partial.text)
                    and self.segmenter.speech_ms >= early_cancel_ms
                ):
                    self._trigger_barge_in("playback", source="partial")
        except asyncio.CancelledError:
            pass

    async def _cancel_active_work(self, room: rtc.Room, reason: str) -> None:
        self._request_playback_cancel()
        if self._partial_task and not self._partial_task.done():
            self._partial_task.cancel()
        if self._barge_in_probe_task and not self._barge_in_probe_task.done():
            self._barge_in_probe_task.cancel()
        if self._active_turn_task and not self._active_turn_task.done():
            self._active_turn_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._active_turn_task
        with contextlib.suppress(Exception):
            await self._emit(room, "voice_session_closed", reason=reason)
        if self._event_tasks:
            await asyncio.gather(*tuple(self._event_tasks), return_exceptions=True)
        with contextlib.suppress(Exception):
            await self.tts.aclose()

    async def _handle_room_data(
        self,
        room: rtc.Room,
        payload: bytes,
        participant,
        topic: str | None,
    ) -> None:
        if topic != CONTROL_TOPIC:
            return

        identity = getattr(participant, "identity", "")
        if identity and not str(identity).startswith("supervisor-"):
            logger.warning(
                "Ignoring control payload from unexpected participant — session=%s identity=%s",
                self.session_id,
                identity,
            )
            return

        try:
            command = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            logger.warning("Ignoring malformed control payload — session=%s", self.session_id)
            await self._emit(
                room,
                "supervisor_action_ignored",
                action="invalid_payload",
                state="listening",
            )
            return

        await self._apply_supervisor_command(room, command, actor=identity or "supervisor")

    async def _apply_supervisor_command(
        self,
        room: rtc.Room,
        command: dict,
        *,
        actor: str,
    ) -> None:
        action = str(command.get("action") or "").strip()
        action_id = str(command.get("action_id") or uuid.uuid4().hex)
        async with self._supervisor_lock:
            if action == "stop_agent":
                await self._stop_active_agent_turn(room, action_id=action_id, actor=actor)
                return
            if action == "replace_answer":
                text = str(command.get("text") or "").strip()
                if not text:
                    await self._emit(
                        room,
                        "supervisor_action_ignored",
                        action=action,
                        action_id=action_id,
                        actor=actor,
                        reason="empty_replacement",
                        state="listening",
                    )
                    return
                await self._replace_active_answer(
                    room,
                    text,
                    action_id=action_id,
                    actor=actor,
                )
                return

            await self._emit(
                room,
                "supervisor_action_ignored",
                action=action or "unknown",
                action_id=action_id,
                actor=actor,
                reason="unsupported_action",
                state="listening",
            )

    async def _stop_active_agent_turn(
        self,
        room: rtc.Room,
        *,
        action_id: str,
        actor: str,
    ) -> None:
        self._generation += 1
        self._request_playback_cancel()
        await self._emit(
            room,
            "supervisor_stop_applied",
            action="stop_agent",
            action_id=action_id,
            actor=actor,
            generation=self._generation,
            state="processing" if self._active_turn_task and not self._active_turn_task.done() else "listening",
        )

    async def _replace_active_answer(
        self,
        room: rtc.Room,
        text: str,
        *,
        action_id: str,
        actor: str,
    ) -> None:
        self._generation += 1
        self._request_playback_cancel()
        await self._emit(
            room,
            "supervisor_replacement_started",
            action="replace_answer",
            action_id=action_id,
            actor=actor,
            text=text,
            generation=self._generation,
            state="speaking",
        )
        first_audio_ms, cancelled, playback_id = await self._speak(
            room,
            text,
            {},
            f"supervisor-replacement-{action_id}",
            response_generation_id=self._generation,
        )
        if cancelled:
            await self._emit(
                room,
                "supervisor_replacement_cancelled",
                action="replace_answer",
                action_id=action_id,
                actor=actor,
                state="processing",
            )
            return
        await self._emit(
            room,
            "supervisor_replacement_completed",
            action="replace_answer",
            action_id=action_id,
            actor=actor,
            text=text,
            tts_first_audio_ms=first_audio_ms or 0.0,
            state="listening",
        )

    async def _wait_for_audio_track(
        self,
        room: rtc.Room,
        participant: rtc.RemoteParticipant,
    ) -> rtc.RemoteAudioTrack:
        for publication in participant.track_publications.values():
            if (
                publication.track
                and publication.kind == _rtc().TrackKind.KIND_AUDIO
            ):
                return publication.track

        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        disconnected = asyncio.Event()

        def on_track_subscribed(track, publication, remote_participant):
            if (
                remote_participant.sid == participant.sid
                and track.kind == _rtc().TrackKind.KIND_AUDIO
                and not future.done()
            ):
                future.set_result(track)

        def on_disconnected(*_args):
            disconnected.set()

        def on_participant_disconnected(remote_participant):
            if remote_participant.sid == participant.sid:
                disconnected.set()

        room.on("track_subscribed", on_track_subscribed)
        room.on("disconnected", on_disconnected)
        room.on("participant_disconnected", on_participant_disconnected)
        disconnect_task = asyncio.create_task(disconnected.wait())
        try:
            done, _ = await asyncio.wait(
                {future, disconnect_task},
                timeout=30.0,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if future in done:
                return future.result()
            if disconnect_task in done:
                raise RuntimeError("Voice room disconnected before audio track")
            raise asyncio.TimeoutError("Timed out waiting for microphone track")
        finally:
            room.off("track_subscribed", on_track_subscribed)
            room.off("disconnected", on_disconnected)
            room.off("participant_disconnected", on_participant_disconnected)
            if not disconnect_task.done():
                disconnect_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await disconnect_task

    async def _emit(
        self,
        room: rtc.Room,
        event_type: str,
        *,
        turn_id: int | None = None,
        **payload,
    ) -> None:
        self._sequence += 1
        event_id = f"{self.session_id}:{self._sequence}:{uuid.uuid4().hex[:10]}"
        event = {
            "event": event_type,
            "event_id": event_id,
            "sequence": self._sequence,
            "session_id": self.session_id,
            "turn_id": turn_id,
            **payload,
        }
        await room.local_participant.publish_data(
            json.dumps(event, ensure_ascii=False).encode("utf-8"),
            reliable=True,
            topic=EVENT_TOPIC,
        )
        # High-frequency / non-authoritative events: browser only, not persisted
        _browser_only = {
            "speech_started",
            "speech_ended",
            "partial_transcript",
            "possible_barge_in",
        }
        if event_type not in _browser_only:
            task = asyncio.create_task(
                self.backend.record_voice_event(
                    {
                        "session_id": self.session_id,
                        "event_id": event_id,
                        "sequence": self._sequence,
                        "event_type": event_type,
                        "turn_id": turn_id,
                        "payload": payload,
                    }
                )
            )
            self._event_tasks.add(task)
            task.add_done_callback(self._event_tasks.discard)
