import pathlib
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.voice_actions import prepare_voice_action


class VoiceActionPreparationTests(unittest.TestCase):
    def make_turn(self, **overrides):
        base = {
            "id": 42,
            "session_id": 7,
            "turn_index": 3,
            "agent_response": "Original response",
            "next_action": "explain_price",
        }
        base.update(overrides)
        return SimpleNamespace(**base)

    def test_prepare_replace_answer_builds_command_correction_and_audit(self):
        prepared = prepare_voice_action(
            action="replace_answer",
            action_id="replace-1",
            actor="admin",
            external_session_id="voice-test-7",
            session_id=7,
            latest_turn=self.make_turn(),
            replacement_text=" Supervisor answer ",
            corrected_next_action="send_activation_link",
            apply_immediately=True,
            send_to_training=True,
            notes="fix now",
        )

        self.assertEqual(prepared.command["action"], "replace_answer")
        self.assertEqual(prepared.command["text"], "Supervisor answer")
        self.assertEqual(prepared.correction_payload["turn_id"], 42)
        self.assertTrue(prepared.correction_payload["apply_immediately"])
        self.assertTrue(prepared.correction_payload["send_to_training"])
        self.assertEqual(
            prepared.audit_payload["event_type"],
            "supervisor_action_requested",
        )
        self.assertEqual(
            prepared.audit_payload["payload"]["replacement_text"],
            "Supervisor answer",
        )

    def test_prepare_stop_agent_has_no_correction_payload(self):
        prepared = prepare_voice_action(
            action="stop_agent",
            action_id="stop-1",
            actor="admin",
            external_session_id="voice-test-7",
            session_id=7,
            latest_turn=self.make_turn(),
            replacement_text="",
            corrected_next_action="",
            apply_immediately=False,
            send_to_training=False,
            notes="",
        )

        self.assertEqual(prepared.command["action"], "stop_agent")
        self.assertIsNone(prepared.correction_payload)
        self.assertEqual(prepared.audit_payload["turn_id"], 42)

    def test_prepare_replace_answer_requires_latest_turn(self):
        with self.assertRaisesRegex(LookupError, "No turn is available"):
            prepare_voice_action(
                action="replace_answer",
                action_id="replace-1",
                actor="admin",
                external_session_id="voice-test-7",
                session_id=7,
                latest_turn=None,
                replacement_text="Supervisor answer",
                corrected_next_action="",
                apply_immediately=False,
                send_to_training=False,
                notes="",
            )

    def test_prepare_replace_answer_requires_text(self):
        with self.assertRaisesRegex(ValueError, "Replacement text is required"):
            prepare_voice_action(
                action="replace_answer",
                action_id="replace-1",
                actor="admin",
                external_session_id="voice-test-7",
                session_id=7,
                latest_turn=self.make_turn(),
                replacement_text="   ",
                corrected_next_action="",
                apply_immediately=False,
                send_to_training=False,
                notes="",
            )


if __name__ == "__main__":
    unittest.main()
