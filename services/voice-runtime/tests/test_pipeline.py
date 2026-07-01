import asyncio
import pathlib
import sys
import time
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

try:
    from app.config import Settings
    from app.pipeline import VoicePipeline
    from app.stt import STTError, Transcript
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

    async def transcribe_partial(self, pcm: bytes, sample_rate: int = 16000):
        return Transcript(text=self.text, stt_ms=10.0)


class FailingSTT:
    def __init__(self, exc):
        self.exc = exc

    async def transcribe(self, pcm: bytes, sample_rate: int = 16000):
        raise self.exc

    async def transcribe_partial(self, pcm: bytes, sample_rate: int = 16000):
        raise self.exc


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

    async def record_voice_event(self, payload: dict) -> None:
        return None

    async def report_interruption(self, turn_id: int, payload: dict) -> None:
        self.interruptions = getattr(self, "interruptions", [])
        self.interruptions.append((turn_id, payload))


class FakeSegmenter:
    """Minimal segmenter stub for pipeline unit tests."""
    speech_active = True
    speech_ms = 600.0

    def snapshot(self) -> bytes:
        return b"\x00" * 320

    def consume_speech_started(self) -> bool:
        return False

    def flush(self) -> bytes | None:
        return None


class LoudFakeSegmenter(FakeSegmenter):
    """Segmenter whose buffered audio is loud (RMS ~3000, above barge_in_loud_rms)."""

    def snapshot(self) -> bytes:
        return (3000).to_bytes(2, "little", signed=True) * 160


class BriefLoudFakeSegmenter(LoudFakeSegmenter):
    """Loud audio that is too SHORT to be a real interruption (emphatic "JA!")."""

    speech_ms = 200.0


# ---------------------------------------------------------------------------
# Sanity: Phase 0 — pipeline must import without livekit
# ---------------------------------------------------------------------------

class ImportSanityTests(unittest.TestCase):
    def test_module_imports_without_livekit(self):
        """VoicePipeline must be importable even when livekit is not installed."""
        self.assertIsNotNone(
            VoicePipeline,
            "VoicePipeline should not be None — the livekit import must be lazy",
        )


# ---------------------------------------------------------------------------
# Pipeline turn-taking logic (Phase 0 gate: runs only when import succeeds)
# ---------------------------------------------------------------------------

@unittest.skipIf(VoicePipeline is None, "LiveKit runtime is not installed")
class PipelineTurnTakingTests(unittest.IsolatedAsyncioTestCase):
    def make_pipeline(self, transcript: str = "Was kostet das?"):
        settings = Settings(
            tts_mode="mock",
            whisper_device="cpu",
            greeting_mock=False,
            barge_in_min_ms=1,
            # Keep per-overlap windows unset so the probe delay collapses to the
            # tiny barge_in_min_ms above; the production defaults (600/800) are
            # exercised by the dedicated _probe_delay_ms tests below.
            backchannel_window_ms=None,
            interrupt_confirm_ms=None,
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

        async def speak(room, text, voice_style, track_label, **kwargs):
            pipeline.speak_calls.append((text, track_label))
            first_audio_ms, cancelled = pipeline.speak_result
            if not cancelled:
                await asyncio.sleep((first_audio_ms or 0.0) / 1000 + 0.001)
            playback_id = f"test-pb-{len(pipeline.speak_calls)}"
            return first_audio_ms, cancelled, playback_id

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

    async def test_barge_in_suppressed_when_audio_is_self_echo(self):
        # The caller's speaker leaks the agent's own greeting back into the
        # microphone. The buffered audio transcribes to the agent's own words,
        # so the probe must NOT cancel its own playback.
        pipeline = self.make_pipeline("hier ist der CallShield")
        pipeline.segmenter = FakeSegmenter()
        pipeline._current_agent_text = "Guten Tag, hier ist der CallShield."
        pipeline._speech_overlap_kind = "playback"
        pipeline._speech_started_at = time.perf_counter()
        pipeline._playback_cancel = asyncio.Event()

        pipeline._schedule_barge_in_probe()
        await asyncio.sleep(0.02)

        self.assertFalse(pipeline._playback_cancel.is_set())
        self.assertIsNone(pipeline._pending_interruption)
        self.assertEqual(pipeline._generation, 0)

    async def test_barge_in_suppressed_for_backchannel_content(self):
        pipeline = self.make_pipeline("ja")
        pipeline.segmenter = FakeSegmenter()
        pipeline._speech_overlap_kind = "playback"
        pipeline._speech_started_at = time.perf_counter()
        pipeline._playback_cancel = asyncio.Event()

        pipeline._schedule_barge_in_probe()
        await asyncio.sleep(0.02)

        self.assertFalse(pipeline._playback_cancel.is_set())
        self.assertEqual(pipeline._generation, 0)

    async def test_confirmation_ja_when_agent_asked_question_creates_turn(self):
        # Agent just asked a yes/no question; a "ja" overlapping the tail is a
        # real answer, not a droppable backchannel — it must open a turn so the
        # LLM (with SOFT SIGNALS) can interpret it.
        pipeline = self.make_pipeline("ja")
        pipeline._last_agent_text = "Möchten Sie das jetzt aktivieren?"

        await pipeline._run_turn(None, b"pcm", overlap_kind="playback")

        self.assertEqual(pipeline.backend.turn_calls, [("pipeline-test", "ja")])
        self.assertNotIn(
            "backchannel_detected",
            [event["event"] for event in pipeline.events],
        )

    async def test_ja_still_suppressed_when_agent_made_a_statement(self):
        # No question → "ja" during playback stays a backchannel (P3 preserved).
        pipeline = self.make_pipeline("ja")
        pipeline._last_agent_text = "Ich erkläre Ihnen kurz den Ablauf."

        await pipeline._run_turn(None, b"pcm", overlap_kind="playback")

        self.assertEqual(pipeline.backend.turn_calls, [])
        self.assertIn(
            "backchannel_detected",
            [event["event"] for event in pipeline.events],
        )

    async def test_backchannel_confirms_barge_in_when_agent_asked(self):
        # During a question's playback, a "ja" should cancel playback (answer),
        # not be suppressed as a backchannel.
        pipeline = self.make_pipeline("ja")
        pipeline.segmenter = FakeSegmenter()
        pipeline._last_agent_text = "Möchten Sie das aktivieren?"
        pipeline._current_agent_text = "Möchten Sie das aktivieren?"
        pipeline._speech_overlap_kind = "playback"
        pipeline._speech_started_at = time.perf_counter()
        pipeline._playback_cancel = asyncio.Event()

        pipeline._schedule_barge_in_probe()
        await asyncio.sleep(0.02)

        self.assertTrue(pipeline._playback_cancel.is_set())
        self.assertEqual(pipeline._pending_interruption, "playback")

    async def test_barge_in_suppressed_when_quiet_without_transcript(self):
        # Empty STT + quiet audio = self-echo / ambient noise -> do not cancel.
        pipeline = self.make_pipeline("")
        pipeline.segmenter = FakeSegmenter()
        pipeline._speech_overlap_kind = "playback"
        pipeline._speech_started_at = time.perf_counter()
        pipeline._playback_cancel = asyncio.Event()

        pipeline._schedule_barge_in_probe()
        await asyncio.sleep(0.02)

        self.assertFalse(pipeline._playback_cancel.is_set())
        self.assertEqual(pipeline._generation, 0)

    async def test_barge_in_confirmed_when_loud_without_transcript(self):
        # Shouted "nein nein nein" that the STT fails to decode: empty text but
        # loud audio must still cancel playback.
        pipeline = self.make_pipeline("")
        pipeline.segmenter = LoudFakeSegmenter()
        pipeline._speech_overlap_kind = "playback"
        pipeline._speech_started_at = time.perf_counter()
        pipeline._playback_cancel = asyncio.Event()

        pipeline._schedule_barge_in_probe()
        await asyncio.sleep(0.02)

        self.assertTrue(pipeline._playback_cancel.is_set())
        self.assertEqual(pipeline._pending_interruption, "playback")
        self.assertEqual(pipeline._generation, 1)

    async def test_real_customer_speech_cancels_despite_agent_text(self):
        # A genuine interruption introduces words not in the agent's line, so
        # echo rejection must let it through and cancel playback.
        pipeline = self.make_pipeline("nein warten Sie einen Moment")
        pipeline.segmenter = FakeSegmenter()
        pipeline._current_agent_text = "Guten Tag, hier ist der CallShield."
        pipeline._speech_overlap_kind = "playback"
        pipeline._speech_started_at = time.perf_counter()
        pipeline._playback_cancel = asyncio.Event()

        pipeline._schedule_barge_in_probe()
        await asyncio.sleep(0.02)

        self.assertTrue(pipeline._playback_cancel.is_set())
        self.assertEqual(pipeline._pending_interruption, "playback")
        self.assertEqual(pipeline._generation, 1)

    async def test_barge_in_suppressed_when_loud_but_brief(self):
        # An emphatic short "JA!" is loud but lasts < barge_in_loud_min_ms, so
        # it must NOT silence the agent (P3: backchannels don't stop playback).
        pipeline = self.make_pipeline("")
        pipeline.segmenter = BriefLoudFakeSegmenter()
        pipeline._speech_overlap_kind = "playback"
        pipeline._speech_started_at = time.perf_counter()
        pipeline._playback_cancel = asyncio.Event()

        pipeline._schedule_barge_in_probe()
        await asyncio.sleep(0.02)

        self.assertFalse(pipeline._playback_cancel.is_set())
        self.assertEqual(pipeline._generation, 0)

    async def test_estimate_spoken_prefix(self):
        pipeline = self.make_pipeline()
        text = "Guten Tag, hier ist der CallShield Sicherheitsdienst."
        # ~14 chars/s → 1000 ms ≈ 14 chars, trimmed to a word boundary.
        prefix = pipeline._estimate_spoken_prefix(text, 1000.0)
        self.assertTrue(prefix)
        self.assertLess(len(prefix), len(text))
        self.assertTrue(text.startswith(prefix))
        self.assertFalse(prefix.endswith(" "))
        # Plenty of time → whole line; no time → nothing heard.
        self.assertEqual(pipeline._estimate_spoken_prefix(text, 60_000.0), text)
        self.assertEqual(pipeline._estimate_spoken_prefix(text, 0.0), "")

    async def test_cancelled_playback_reports_spoken_prefix(self):
        pipeline = self.make_pipeline()

        async def speak(room, text, voice_style, track_label, **kwargs):
            pipeline.speak_calls.append((text, track_label))
            pipeline._last_spoken_ms = 400.0  # heard part of the reply
            return 8.0, True, "test-pb-1"

        pipeline._speak = speak

        await pipeline._run_turn(None, b"pcm")

        self.assertTrue(getattr(pipeline.backend, "interruptions", None))
        turn_id, payload = pipeline.backend.interruptions[0]
        self.assertEqual(turn_id, 42)
        self.assertEqual(payload["spoken_ms"], 400.0)
        # "Test response" with 400 ms heard → a non-empty prefix of the reply.
        self.assertTrue(payload["spoken_response"])
        self.assertTrue("Test response".startswith(payload["spoken_response"]))

    async def test_barge_in_falls_back_to_loudness_when_stt_unavailable(self):
        # If verification STT fails we fall back to the energy test so loud
        # customer speech still interrupts instead of silently disabling barge-in.
        pipeline = self.make_pipeline()
        pipeline.stt = FailingSTT(STTError("stt down"))
        pipeline.segmenter = LoudFakeSegmenter()
        pipeline._speech_overlap_kind = "playback"
        pipeline._speech_started_at = time.perf_counter()
        pipeline._playback_cancel = asyncio.Event()

        pipeline._schedule_barge_in_probe()
        await asyncio.sleep(0.02)

        self.assertTrue(pipeline._playback_cancel.is_set())
        self.assertEqual(pipeline._generation, 1)

    # -----------------------------------------------------------------------
    # _trigger_barge_in — idempotency and latency recording (M8 Aşama 3)
    # -----------------------------------------------------------------------

    async def test_trigger_barge_in_sets_pending_and_bumps_generation(self):
        pipeline = self.make_pipeline()
        pipeline._speech_started_at = time.perf_counter()
        pipeline._playback_cancel = asyncio.Event()

        pipeline._trigger_barge_in("playback", source="test")

        self.assertEqual(pipeline._pending_interruption, "playback")
        self.assertEqual(pipeline._generation, 1)
        self.assertTrue(pipeline._playback_cancel.is_set())
        self.assertIsNotNone(pipeline._interruption_latency_ms)

    async def test_trigger_barge_in_is_idempotent(self):
        """Second call within the same speech episode must not increment generation
        again — only the first barge-in wins."""
        pipeline = self.make_pipeline()
        pipeline._speech_started_at = time.perf_counter()
        pipeline._playback_cancel = asyncio.Event()

        pipeline._trigger_barge_in("playback", source="partial")
        pipeline._trigger_barge_in("playback", source="probe")

        self.assertEqual(pipeline._generation, 1)

    # -----------------------------------------------------------------------
    # Per-overlap probe windows (M8 Aşama 4)
    # -----------------------------------------------------------------------

    async def test_probe_delay_uses_backchannel_window_for_playback(self):
        """When backchannel_window_ms is set, playback overlap uses it."""
        settings = Settings(
            tts_mode="mock",
            whisper_device="cpu",
            greeting_mock=False,
            barge_in_min_ms=450,
            backchannel_window_ms=200,
            interrupt_confirm_ms=600,
        )
        pipeline = VoicePipeline(settings, "window-test")
        self.assertEqual(pipeline._probe_delay_ms("playback"), 200)

    async def test_probe_delay_uses_interrupt_confirm_for_active_turn(self):
        settings = Settings(
            tts_mode="mock",
            whisper_device="cpu",
            greeting_mock=False,
            barge_in_min_ms=450,
            backchannel_window_ms=200,
            interrupt_confirm_ms=600,
        )
        pipeline = VoicePipeline(settings, "window-test")
        self.assertEqual(pipeline._probe_delay_ms("active_turn"), 600)

    async def test_probe_delay_falls_back_to_barge_in_min_ms_when_none(self):
        """None windows fall back to the global barge_in_min_ms default."""
        settings = Settings(
            tts_mode="mock",
            whisper_device="cpu",
            greeting_mock=False,
            barge_in_min_ms=450,
            backchannel_window_ms=None,
            interrupt_confirm_ms=None,
        )
        pipeline = VoicePipeline(settings, "fallback-test")
        self.assertEqual(pipeline._probe_delay_ms("playback"), 450)
        self.assertEqual(pipeline._probe_delay_ms("active_turn"), 450)

    # -----------------------------------------------------------------------
    # Multi-token backchannel (M8 Aşama 2 — pipeline integration)
    # -----------------------------------------------------------------------

    async def test_multi_token_backchannel_during_playback_does_not_create_turn(self):
        """'ja ja' must be classified as backchannel and not hit the backend."""
        pipeline = self.make_pipeline("ja ja")

        await pipeline._run_turn(None, b"pcm", overlap_kind="playback")

        self.assertEqual(pipeline.backend.turn_calls, [])
        events = [e["event"] for e in pipeline.events]
        self.assertIn("backchannel_detected", events)

    async def test_ack_plus_content_during_playback_is_interruption(self):
        """'ja aber nein' has a content word and must create a real turn."""
        pipeline = self.make_pipeline("ja aber nein")

        await pipeline._run_turn(None, b"pcm", overlap_kind="playback")

        self.assertEqual(
            pipeline.backend.turn_calls,
            [("pipeline-test", "ja aber nein")],
        )

    async def test_supervisor_stop_increments_generation_and_emits_event(self):
        pipeline = self.make_pipeline()
        pipeline._playback_cancel = asyncio.Event()

        await pipeline._apply_supervisor_command(
            None,
            {"action": "stop_agent", "action_id": "stop-1"},
            actor="supervisor-test",
        )

        self.assertEqual(pipeline._generation, 1)
        self.assertTrue(pipeline._playback_cancel.is_set())
        stop_event = next(
            event
            for event in pipeline.events
            if event["event"] == "supervisor_stop_applied"
        )
        self.assertEqual(stop_event["action_id"], "stop-1")

    async def test_supervisor_replacement_plays_text_and_emits_completion(self):
        pipeline = self.make_pipeline()

        await pipeline._apply_supervisor_command(
            None,
            {
                "action": "replace_answer",
                "action_id": "replace-1",
                "text": "Bitte hören Sie kurz zu.",
            },
            actor="supervisor-test",
        )

        self.assertEqual(
            pipeline.speak_calls,
            [("Bitte hören Sie kurz zu.", "supervisor-replacement-replace-1")],
        )
        events = [event["event"] for event in pipeline.events]
        self.assertIn("supervisor_replacement_started", events)
        self.assertIn("supervisor_replacement_completed", events)

    async def test_tts_fallback_event_is_emitted_after_playback(self):
        pipeline = self.make_pipeline()
        pipeline.tts.last_stream_used_fallback = True

        await pipeline._run_turn(None, b"pcm")

        events = [event["event"] for event in pipeline.events]
        self.assertIn("tts_fallback_activated", events)

    async def test_stt_unavailable_emits_specific_event_and_skips_backend(self):
        pipeline = self.make_pipeline()
        pipeline.stt = FailingSTT(STTError("Whisper transcription failed"))

        await pipeline._run_turn(None, b"pcm")

        self.assertEqual(pipeline.backend.turn_calls, [])
        events = [event["event"] for event in pipeline.events]
        self.assertIn("stt_unavailable", events)
        self.assertNotIn("voice_error", events)
