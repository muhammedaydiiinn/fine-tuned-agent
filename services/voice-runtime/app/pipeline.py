import asyncio
import contextlib
import json
import logging
import time
import uuid

from livekit import rtc

from app.backend import AgentBackend, BackendError
from app.config import Settings
from app.segmenter import SegmentationConfig, UtteranceSegmenter
from app.stt import FasterWhisperSTT
from app.tts import FishTTS
from app.turn_taking import BackchannelClassifier, TranscriptDeduplicator

logger = logging.getLogger(__name__)
EVENT_TOPIC = "voice.events"
INPUT_SAMPLE_RATE = 16000


class VoicePipeline:
    def __init__(self, settings: Settings, session_id: str):
        self.settings = settings
        self.session_id = session_id
        self.backend = AgentBackend(settings)
        self.stt = FasterWhisperSTT(settings)
        self.tts = FishTTS(settings)
        self.segmenter = UtteranceSegmenter(
            SegmentationConfig(
                sample_rate=INPUT_SAMPLE_RATE,
                rms_threshold=settings.speech_rms_threshold,
                min_speech_ms=settings.speech_min_ms,
                end_silence_ms=settings.speech_end_silence_ms,
                max_speech_ms=settings.speech_max_ms,
                preroll_ms=settings.speech_preroll_ms,
            )
        )
        phrases = {
            phrase.strip()
            for phrase in settings.backchannel_phrases.split(",")
            if phrase.strip()
        }
        self.backchannels = BackchannelClassifier(phrases)
        self.deduplicator = TranscriptDeduplicator(
            settings.duplicate_transcript_window_seconds
        )
        self._utterances: asyncio.Queue[tuple[bytes, bool]] = asyncio.Queue(
            maxsize=settings.utterance_queue_size
        )
        self._active_turn_task: asyncio.Task | None = None
        self._playback_task: asyncio.Task | None = None
        self._playback_cancel: asyncio.Event | None = None
        self._agent_speaking = False
        self._pending_interruption = False
        self._barge_in_probe_task: asyncio.Task | None = None
        self._sequence = 0
        self._generation = 0
        self._speech_started_at: float | None = None
        self._interruption_latency_ms: float | None = None
        self._event_tasks: set[asyncio.Task] = set()

    async def run(self, room: rtc.Room, participant: rtc.RemoteParticipant) -> None:
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

        room.on("disconnected", on_disconnected)
        room.on("participant_disconnected", on_participant_disconnected)
        stream_task: asyncio.Task | None = None
        disconnect_task: asyncio.Task | None = None
        graceful_audio_end = False
        try:
            if self.settings.greeting_mock and self.settings.greeting_text:
                await self._emit(
                    room,
                    "agent_response",
                    turn_id=None,
                    turn_index=0,
                    text=self.settings.greeting_text,
                    policy=None,
                    latency={},
                )
                self._playback_task = asyncio.create_task(
                    self._speak(
                        room,
                        self.settings.greeting_text,
                        {},
                        "agent-greeting",
                    )
                )
                await self._playback_task
                await self._emit(
                    room,
                    "voice_turn_complete",
                    turn_id=None,
                    metrics={},
                    state="listening",
                )

            stream = rtc.AudioStream(
                track,
                sample_rate=INPUT_SAMPLE_RATE,
                num_channels=1,
            )
            stream_task = asyncio.create_task(self._consume_audio_stream(room, stream))
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
            room.off("disconnected", on_disconnected)
            room.off("participant_disconnected", on_participant_disconnected)
            for task in (stream_task, disconnect_task):
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

    async def _consume_audio_stream(
        self,
        room: rtc.Room,
        stream: rtc.AudioStream,
    ) -> None:
        async for event in stream:
            utterance = self.segmenter.push(bytes(event.frame.data))
            if self.segmenter.consume_speech_started():
                self._speech_started_at = time.perf_counter()
                await self._emit(
                    room,
                    "speech_started",
                    state="hearing",
                    agent_speaking=self._agent_speaking,
                )
                if self._agent_speaking:
                    # A short acknowledgement should not stop playback. Wait
                    # for sustained customer speech before cancelling.
                    self._schedule_barge_in_probe()
            if utterance:
                await self._enqueue_utterance(room, utterance)

    async def _enqueue_utterance(self, room: rtc.Room, pcm: bytes) -> None:
        captured_during_playback = self._agent_speaking or self._pending_interruption
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
        await self._utterances.put((pcm, captured_during_playback))
        await self._emit(room, "speech_ended", state="processing")

    async def _consume_utterances(self, room: rtc.Room) -> None:
        while True:
            pcm, captured_during_playback = await self._utterances.get()
            try:
                task = asyncio.create_task(
                    self._run_turn(room, pcm, captured_during_playback)
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
        captured_during_playback: bool = False,
    ) -> None:
        voice_turn_started = time.perf_counter()
        generation = self._generation
        try:
            transcript = await self.stt.transcribe(pcm, sample_rate=INPUT_SAMPLE_RATE)
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
            interrupted_playback = self._pending_interruption
            self._pending_interruption = False
            if captured_during_playback and decision.is_backchannel:
                await self._emit(
                    room,
                    "backchannel_detected",
                    text=transcript.text,
                    state="listening",
                )
                return
            if interrupted_playback:
                self._generation += 1
                generation = self._generation
                await self._emit(
                    room,
                    "interruption_detected",
                    text=transcript.text,
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
                await self._emit(
                    room,
                    "stale_response_discarded",
                    turn_id=turn.get("turn_id"),
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
                state="speaking",
            )

            self._playback_task = asyncio.create_task(
                self._speak(
                    room,
                    turn["agent_response"],
                    turn.get("voice_style", {}),
                    f"agent-audio-{turn['turn_id']}",
                )
            )
            first_audio_ms, cancelled = await self._playback_task
            self._playback_task = None
            if cancelled:
                await self._emit(
                    room,
                    "playback_cancelled",
                    turn_id=turn["turn_id"],
                    reason="customer_speech",
                    state="processing",
                )
                return
            if generation != self._generation:
                await self._emit(
                    room,
                    "stale_response_discarded",
                    turn_id=turn["turn_id"],
                    state="listening",
                )
                return

            total_voice_turn_ms = (time.perf_counter() - voice_turn_started) * 1000
            metrics = {
                "session_id": self.session_id,
                "stt_ms": transcript.stt_ms,
                "backend_ms": turn["latency"]["backend_ms"],
                "llm_ms": turn["latency"]["llm_ms"],
                "tts_first_audio_ms": first_audio_ms or 0.0,
                "total_voice_turn_ms": total_voice_turn_ms,
                "transcript_final": transcript.text,
                "heard_response": turn["agent_response"],
            }
            await self.backend.save_voice_metrics(turn["turn_id"], metrics)
            await self._emit(
                room,
                "voice_turn_complete",
                turn_id=turn["turn_id"],
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
    ) -> tuple[float | None, bool]:
        tts_started = time.perf_counter()
        first_audio_ms: float | None = None
        self._playback_cancel = asyncio.Event()
        self._agent_speaking = True
        audio_source = rtc.AudioSource(self.settings.tts_sample_rate, 1)
        audio_track = rtc.LocalAudioTrack.create_audio_track(track_label, audio_source)
        publication = None
        cancelled = False
        try:
            publication = await room.local_participant.publish_track(
                audio_track,
                rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE),
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
                        rtc.AudioFrame(
                            data=packet,
                            sample_rate=self.settings.tts_sample_rate,
                            num_channels=1,
                            samples_per_channel=len(packet) // 2,
                        )
                    )
                if cancelled:
                    break
            if pcm_buffer and not cancelled:
                pcm_buffer.extend(b"\x00" * (-len(pcm_buffer) % 2))
                if first_audio_ms is None:
                    first_audio_ms = (time.perf_counter() - tts_started) * 1000
                await audio_source.capture_frame(
                    rtc.AudioFrame(
                        data=bytes(pcm_buffer),
                        sample_rate=self.settings.tts_sample_rate,
                        num_channels=1,
                        samples_per_channel=len(pcm_buffer) // 2,
                    )
                )
            if not cancelled:
                await audio_source.wait_for_playout()
        finally:
            self._agent_speaking = False
            if publication is not None:
                with contextlib.suppress(Exception):
                    await room.local_participant.unpublish_track(publication.sid)
        return first_audio_ms, cancelled

    def _request_playback_cancel(self) -> None:
        if self._playback_cancel is not None:
            self._playback_cancel.set()

    def _schedule_barge_in_probe(self) -> None:
        if self._barge_in_probe_task and not self._barge_in_probe_task.done():
            return

        async def probe() -> None:
            await asyncio.sleep(self.settings.barge_in_min_ms / 1000)
            if self._agent_speaking and self.segmenter.speech_active:
                self._pending_interruption = True
                if self._speech_started_at is not None:
                    self._interruption_latency_ms = (
                        time.perf_counter() - self._speech_started_at
                    ) * 1000
                self._request_playback_cancel()

        self._barge_in_probe_task = asyncio.create_task(probe())

    async def _cancel_active_work(self, room: rtc.Room, reason: str) -> None:
        self._request_playback_cancel()
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

    async def _wait_for_audio_track(
        self,
        room: rtc.Room,
        participant: rtc.RemoteParticipant,
    ) -> rtc.RemoteAudioTrack:
        for publication in participant.track_publications.values():
            if publication.track and publication.kind == rtc.TrackKind.KIND_AUDIO:
                return publication.track

        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        disconnected = asyncio.Event()

        def on_track_subscribed(track, publication, remote_participant):
            if (
                remote_participant.sid == participant.sid
                and track.kind == rtc.TrackKind.KIND_AUDIO
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
        if event_type not in {"speech_started", "speech_ended"}:
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
