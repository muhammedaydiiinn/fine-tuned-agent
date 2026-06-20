import asyncio
import json
import logging
import time

from livekit import rtc

from app.backend import AgentBackend
from app.config import Settings
from app.segmenter import SegmentationConfig, UtteranceSegmenter
from app.stt import FasterWhisperSTT
from app.tts import FishTTS

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
        self._turn_lock = asyncio.Lock()

    async def run(self, room: rtc.Room, participant: rtc.RemoteParticipant) -> None:
        await self.backend.create_session(self.session_id)
        await self._publish_event(
            room,
            {"event": "voice_session_ready", "session_id": self.session_id},
        )
        track = await self._wait_for_audio_track(room, participant)
        stream = rtc.AudioStream(
            track,
            sample_rate=INPUT_SAMPLE_RATE,
            num_channels=1,
        )
        try:
            async for event in stream:
                utterance = self.segmenter.push(bytes(event.frame.data))
                if utterance:
                    await self._process_utterance(room, utterance)
        finally:
            trailing = self.segmenter.flush()
            if trailing:
                await self._process_utterance(room, trailing)

    async def _process_utterance(self, room: rtc.Room, pcm: bytes) -> None:
        if self._turn_lock.locked():
            logger.info("Ignoring overlapping M7 utterance for session %s", self.session_id)
            return
        async with self._turn_lock:
            voice_turn_started = time.perf_counter()
            transcript = await self.stt.transcribe(pcm, sample_rate=INPUT_SAMPLE_RATE)
            if not transcript.text:
                await self._publish_event(room, {"event": "empty_transcript"})
                return

            await self._publish_event(
                room,
                {
                    "event": "transcript_final",
                    "session_id": self.session_id,
                    "text": transcript.text,
                    "stt_ms": transcript.stt_ms,
                },
            )

            backend_started = time.perf_counter()
            turn = await self.backend.agent_turn(self.session_id, transcript.text)
            backend_roundtrip_ms = (time.perf_counter() - backend_started) * 1000
            await self._publish_event(
                room,
                {
                    "event": "agent_response",
                    "turn_id": turn["turn_id"],
                    "turn_index": turn["turn_index"],
                    "text": turn["agent_response"],
                    "policy": turn["policy"],
                    "latency": turn["latency"],
                },
            )

            tts_started = time.perf_counter()
            first_audio_ms = None
            total_voice_turn_ms = None
            audio_source = rtc.AudioSource(self.settings.tts_sample_rate, 1)
            audio_track = rtc.LocalAudioTrack.create_audio_track(
                f"agent-audio-{turn['turn_id']}",
                audio_source,
            )
            publication = await room.local_participant.publish_track(
                audio_track,
                rtc.TrackPublishOptions(
                    source=rtc.TrackSource.SOURCE_MICROPHONE,
                ),
            )
            pcm_buffer = bytearray()
            try:
                async for chunk in self.tts.stream(
                    turn["agent_response"],
                    turn.get("voice_style", {}),
                ):
                    pcm_buffer.extend(chunk)
                    frame_bytes = self.settings.tts_sample_rate // 50 * 2
                    while len(pcm_buffer) >= frame_bytes:
                        packet = bytes(pcm_buffer[:frame_bytes])
                        del pcm_buffer[:frame_bytes]
                        if first_audio_ms is None:
                            first_audio_ms = (time.perf_counter() - tts_started) * 1000
                            total_voice_turn_ms = (
                                time.perf_counter() - voice_turn_started
                            ) * 1000
                        await audio_source.capture_frame(
                            rtc.AudioFrame(
                                data=packet,
                                sample_rate=self.settings.tts_sample_rate,
                                num_channels=1,
                                samples_per_channel=len(packet) // 2,
                            )
                        )
                if pcm_buffer:
                    pcm_buffer.extend(b"\x00" * (-len(pcm_buffer) % 2))
                    if first_audio_ms is None:
                        first_audio_ms = (time.perf_counter() - tts_started) * 1000
                        total_voice_turn_ms = (
                            time.perf_counter() - voice_turn_started
                        ) * 1000
                    await audio_source.capture_frame(
                        rtc.AudioFrame(
                            data=bytes(pcm_buffer),
                            sample_rate=self.settings.tts_sample_rate,
                            num_channels=1,
                            samples_per_channel=len(pcm_buffer) // 2,
                        )
                    )
                await audio_source.wait_for_playout()
            finally:
                await room.local_participant.unpublish_track(publication.sid)

            playout_complete_ms = (time.perf_counter() - voice_turn_started) * 1000
            metrics = {
                "session_id": self.session_id,
                "stt_ms": transcript.stt_ms,
                "backend_ms": turn["latency"]["backend_ms"],
                "llm_ms": turn["latency"]["llm_ms"],
                "tts_first_audio_ms": first_audio_ms or 0.0,
                "total_voice_turn_ms": total_voice_turn_ms or playout_complete_ms,
                "transcript_final": transcript.text,
                "heard_response": turn["agent_response"],
            }
            await self.backend.save_voice_metrics(turn["turn_id"], metrics)
            await self._publish_event(
                room,
                {
                    "event": "voice_turn_complete",
                    "turn_id": turn["turn_id"],
                    "metrics": {
                        **metrics,
                        "backend_roundtrip_ms": backend_roundtrip_ms,
                        "playout_complete_ms": playout_complete_ms,
                    },
                },
            )

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

        def on_track_subscribed(track, publication, remote_participant):
            if (
                remote_participant.sid == participant.sid
                and track.kind == rtc.TrackKind.KIND_AUDIO
                and not future.done()
            ):
                future.set_result(track)

        room.on("track_subscribed", on_track_subscribed)
        try:
            return await asyncio.wait_for(future, timeout=30.0)
        finally:
            room.off("track_subscribed", on_track_subscribed)

    async def _publish_event(self, room: rtc.Room, payload: dict) -> None:
        await room.local_participant.publish_data(
            json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            reliable=True,
            topic=EVENT_TOPIC,
        )
