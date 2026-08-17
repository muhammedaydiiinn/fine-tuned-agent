"""Unit tests for the FLAGGED AgentSession engine (task B5).

The engine module (``app.agent_session_engine``) imports the livekit stack at
module load, which is not installed on the CI/dev host. So the pure logic that
can be exercised without livekit lives in ``app.agent_session_logic`` and is
tested here directly. A guarded block additionally imports the full engine when
livekit IS present (e.g. inside the voice-runtime image), verifying the wrapper
subclasses are wired correctly.
"""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.agent_session_logic import (
    backend_request_id,
    backend_response_chunks,
    latest_user_text,
)


class _Item:
    """Minimal stand-in for a livekit ChatItem/ChatMessage."""

    def __init__(self, type_="message", role="user", text_content=""):
        self.type = type_
        self.role = role
        self.text_content = text_content


class _Ctx:
    def __init__(self, items):
        self.items = items


class LatestUserTextTest(unittest.TestCase):
    def test_returns_most_recent_user_message(self):
        ctx = _Ctx([
            _Item(role="user", text_content="erste Frage"),
            _Item(role="assistant", text_content="Antwort"),
            _Item(role="user", text_content="  zweite Frage  "),
        ])
        self.assertEqual(latest_user_text(ctx), "zweite Frage")

    def test_skips_trailing_assistant_message(self):
        ctx = _Ctx([
            _Item(role="user", text_content="Kundentext"),
            _Item(role="assistant", text_content="Bot-Antwort"),
        ])
        self.assertEqual(latest_user_text(ctx), "Kundentext")

    def test_ignores_non_message_items(self):
        ctx = _Ctx([
            _Item(role="user", text_content="echte Frage"),
            _Item(type_="function_call", role="user", text_content="tool noise"),
        ])
        self.assertEqual(latest_user_text(ctx), "echte Frage")

    def test_empty_context_returns_empty_string(self):
        self.assertEqual(latest_user_text(_Ctx([])), "")

    def test_no_user_message_returns_empty_string(self):
        ctx = _Ctx([_Item(role="assistant", text_content="nur bot")])
        self.assertEqual(latest_user_text(ctx), "")

    def test_missing_items_attribute_is_safe(self):
        self.assertEqual(latest_user_text(object()), "")


class BackendResponseChunksTest(unittest.TestCase):
    def test_maps_agent_response_to_single_chunk(self):
        turn = {"turn_id": 7, "agent_response": "Guten Tag", "voice_style": {}}
        self.assertEqual(backend_response_chunks(turn), ["Guten Tag"])

    def test_strips_whitespace(self):
        self.assertEqual(
            backend_response_chunks({"agent_response": "  Hallo  "}), ["Hallo"]
        )

    def test_empty_response_yields_no_chunks(self):
        self.assertEqual(backend_response_chunks({"agent_response": ""}), [])
        self.assertEqual(backend_response_chunks({"agent_response": "   "}), [])

    def test_missing_key_yields_no_chunks(self):
        self.assertEqual(backend_response_chunks({}), [])

    def test_none_payload_yields_no_chunks(self):
        self.assertEqual(backend_response_chunks(None), [])


class BackendRequestIdTest(unittest.TestCase):
    def test_uses_turn_id_when_present(self):
        self.assertEqual(backend_request_id({"turn_id": 99}, "fallback"), "99")

    def test_turn_id_zero_is_used(self):
        # turn_id 0 is a valid id and must not be replaced by the fallback.
        self.assertEqual(backend_request_id({"turn_id": 0}, "fallback"), "0")

    def test_falls_back_when_no_turn_id(self):
        self.assertEqual(backend_request_id({}, "fb-123"), "fb-123")
        self.assertEqual(backend_request_id(None, "fb-123"), "fb-123")


try:
    import livekit  # noqa: F401

    _HAS_LIVEKIT = True
except Exception:  # pragma: no cover - depends on environment
    _HAS_LIVEKIT = False


@unittest.skipUnless(
    _HAS_LIVEKIT, "livekit not installed on host — run inside the voice-runtime image"
)
class EngineWiringTest(unittest.TestCase):
    """Only runs where the livekit stack is available (e.g. the image)."""

    def test_adapters_are_livekit_subclasses(self):
        from livekit.agents import llm, stt, tts

        from app import agent_session_engine as engine

        self.assertTrue(issubclass(engine.WhisperSTTAdapter, stt.STT))
        self.assertTrue(issubclass(engine.FishTTSAdapter, tts.TTS))
        self.assertTrue(issubclass(engine.FishChunkedStream, tts.ChunkedStream))
        self.assertTrue(issubclass(engine.BackendLLM, llm.LLM))
        self.assertTrue(issubclass(engine.BackendLLMStream, llm.LLMStream))
        self.assertEqual(engine.WhisperSTTAdapter.__abstractmethods__, frozenset())
        self.assertEqual(engine.FishTTSAdapter.__abstractmethods__, frozenset())
        self.assertEqual(engine.BackendLLM.__abstractmethods__, frozenset())


if __name__ == "__main__":
    unittest.main()
