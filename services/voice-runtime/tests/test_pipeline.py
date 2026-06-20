import asyncio
import time
import unittest

try:
    from app.config import Settings
    from app.pipeline import VoicePipeline
    from app.stt import Transcript
except ModuleNotFoundError as exc:
    if exc.name and exc.name.startswith("livekit"):
        Settings = None
        VoicePipeline = None
        Transcript = None
    else:
        raise


class FakeSTT:
    def __init__(self, text: str):
        self.text = text

    async def transcribe(self, pcm: bytes, sample_rate: int = 16000):
        return Transcript(text=self.text, stt_ms=25.0)


class FakeBackend:
    def __init__(self, pipeline=None, mutate_generation: bool = False):
        self.pipeline = pipeline
        self.mutate_generation = mutate_generation
        self.turn_calls = []
        self.metrics = []

    async def agent_turn(self, session_id: str, transcript: str):
        self.turn_calls.append((session_id, transcript))
        if self.mutate_generation:
            self.pipeline._generation += 1
        return {
            "turn_id": 42,
            "turn_index": 3,
            "agent_response": "Test response",
            "policy": {
                "intent": "general_inquiry",
                "next_action": "continue",
                "risk": "low",
                "allowed_to_continue": True,
            },
            "voice_style": {},
            "latency": {"backend_ms": 15.0, "llm_ms": 10.0},
        }

    async def save_voice_metrics(self, turn_id: int, payload: dict):
        self.metrics.append((turn_id, payload))


class FakeSegmenter:
    speech_active = True


@unittest.skipIf(VoicePipeline is None, "LiveKit runtime is not installed")
class PipelineTurnTakingTests(unittest.IsolatedAsyncioTestCase):
    def make_pipeline(self, transcript: str = "Was kostet das?"):
        settings = Settings(
            tts_mode="mock",
            whisper_device="cpu",
            greeting_mock=False,
            barge_in_min_ms=1,
        )
        pipeline = VoicePipeline(settings, "pipeline-test")
        pipeline.stt = FakeSTT(transcript)
        pipeline.backend = FakeBackend(pipeline)
        pipeline.events = []
        pipeline.speak_calls = []
        pipeline.speak_result = (12.0, False)

        async def emit(room, event_type, *, turn_id=None, **payload):
            pipeline.events.append(
                {"event": event_type, "turn_id": turn_id, **payload}
            )

        async def speak(room, text, voice_style, track_label):
            pipeline.speak_calls.append((text, track_label))
            first_audio_ms, cancelled = pipeline.speak_result
            if not cancelled:
                await asyncio.sleep((first_audio_ms or 0.0) / 1000 + 0.001)
            return pipeline.speak_result

        pipeline._emit = emit
        pipeline._speak = speak
        return pipeline

    async def test_backchannel_during_playback_does_not_create_turn(self):
        pipeline = self.make_pipeline("mhm")

        await pipeline._run_turn(None, b"pcm", overlap_kind="playback")

        self.assertEqual(pipeline.backend.turn_calls, [])
        self.assertEqual(
            [event["event"] for event in pipeline.events],
            ["backchannel_detected"],
        )

    async def test_sustained_overlap_adopts_new_generation(self):
        pipeline = self.make_pipeline("Moment, was kostet das?")
        pipeline._generation = 2
        pipeline._pending_interruption = "active_turn"
        pipeline._interruption_latency_ms = 451.0

        await pipeline._run_turn(None, b"pcm", overlap_kind="active_turn")

        self.assertEqual(
            pipeline.backend.turn_calls,
            [("pipeline-test", "Moment, was kostet das?")],
        )
        interruption = next(
            event
            for event in pipeline.events
            if event["event"] == "interruption_detected"
        )
        self.assertEqual(interruption["interruption_kind"], "active_turn")
        self.assertEqual(interruption["interruption_latency_ms"], 451.0)
        self.assertTrue(pipeline.backend.metrics)
        saved_metrics = pipeline.backend.metrics[0][1]
        self.assertIn("speech_end_to_first_audio_ms", saved_metrics)
        self.assertLessEqual(
            saved_metrics["speech_end_to_first_audio_ms"],
            saved_metrics["total_voice_turn_ms"],
        )

    async def test_backend_response_is_discarded_when_generation_changes(self):
        pipeline = self.make_pipeline()
        pipeline.backend = FakeBackend(pipeline, mutate_generation=True)

        await pipeline._run_turn(None, b"pcm")

        self.assertEqual(pipeline.speak_calls, [])
        self.assertIn(
            "stale_response_discarded",
            [event["event"] for event in pipeline.events],
        )

    async def test_cancelled_playback_does_not_persist_heard_response_metrics(self):
        pipeline = self.make_pipeline()
        pipeline.speak_result = (8.0, True)

        await pipeline._run_turn(None, b"pcm")

        self.assertEqual(pipeline.backend.metrics, [])
        self.assertIn(
            "playback_cancelled",
            [event["event"] for event in pipeline.events],
        )

    async def test_barge_in_probe_active_turn_only_does_not_invalidate_generation(self):
        # When the backend is busy but the agent has not started speaking yet
        # ("active_turn" overlap, _agent_speaking=False), the probe must NOT
        # increment _generation or set _pending_interruption.  Doing so would
        # cause the in-flight backend response to be silently discarded as
        # stale, which is the root cause of "empty response after the first
        # turn".  The latency timestamp is still recorded so it is available
        # if the agent starts speaking before the probe fires.
        pipeline = self.make_pipeline()
        pipeline.segmenter = FakeSegmenter()
        pipeline._speech_overlap_kind = "active_turn"
        pipeline._speech_started_at = time.perf_counter()

        pipeline._schedule_barge_in_probe()
        await asyncio.sleep(0.02)

        self.assertEqual(pipeline._generation, 0)
        self.assertIsNone(pipeline._pending_interruption)
        self.assertIsNotNone(pipeline._interruption_latency_ms)

    async def test_barge_in_probe_cancels_active_playback(self):
        pipeline = self.make_pipeline()
        pipeline.segmenter = FakeSegmenter()
        pipeline._speech_overlap_kind = "playback"
        pipeline._speech_started_at = time.perf_counter()
        pipeline._playback_cancel = asyncio.Event()

        pipeline._schedule_barge_in_probe()
        await asyncio.sleep(0.02)

        self.assertTrue(pipeline._playback_cancel.is_set())
        self.assertEqual(pipeline._pending_interruption, "playback")
        self.assertEqual(pipeline._generation, 1)

    async def test_backend_overlap_cancels_playback_that_starts_during_probe(self):
        pipeline = self.make_pipeline()
        pipeline.segmenter = FakeSegmenter()
        pipeline._speech_overlap_kind = "active_turn"
        pipeline._speech_started_at = time.perf_counter()
        pipeline._playback_cancel = asyncio.Event()
        pipeline._agent_speaking = True

        pipeline._schedule_barge_in_probe()
        await asyncio.sleep(0.02)

        self.assertTrue(pipeline._playback_cancel.is_set())
        self.assertEqual(pipeline._pending_interruption, "active_turn")
