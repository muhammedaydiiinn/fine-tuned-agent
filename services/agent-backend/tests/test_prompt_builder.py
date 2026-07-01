from types import SimpleNamespace
from unittest import TestCase
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.core import prompt_builder


def make_turn(customer, agent, *, was_interrupted=False, spoken=None):
    return SimpleNamespace(
        customer_text=customer,
        agent_response=agent,
        was_interrupted=was_interrupted,
        spoken_response=spoken,
    )


def _by_role(messages, role):
    return [m["content"] for m in messages if m["role"] == role]


class PromptBuilderInterruptionTests(TestCase):
    def test_completed_turn_renders_full_response(self):
        turns = [make_turn("Hallo", "Guten Tag.")]
        messages = prompt_builder.build("Was kostet das?", {}, turns, [])
        self.assertIn("Guten Tag.", _by_role(messages, "assistant"))

    def test_interrupted_turn_renders_only_heard_prefix_with_note(self):
        full = "Guten Tag, ich bin Anna von Anrufblocker und rufe Sie heute an."
        heard = "Guten Tag, ich bin Anna"
        turns = [make_turn("Hallo", full, was_interrupted=True, spoken=heard)]

        messages = prompt_builder.build("Was kostet das?", {}, turns, [])

        assistant = _by_role(messages, "assistant")
        # Only the heard prefix is shown to the model, never the full reply.
        self.assertIn(heard, assistant)
        self.assertNotIn(full, assistant)
        # An interruption note is injected so the model resumes contextually.
        self.assertTrue(
            any("interrupted by the customer" in m for m in _by_role(messages, "system"))
        )

    def test_interrupted_turn_with_no_audio_still_flags_cutoff(self):
        # Cut off before any audio played → no assistant content, but the note
        # must still appear so the model knows it was interrupted.
        full = "Guten Tag, ich bin Anna."
        turns = [make_turn("Hallo", full, was_interrupted=True, spoken=None)]

        messages = prompt_builder.build("Was kostet das?", {}, turns, [])

        self.assertNotIn(full, _by_role(messages, "assistant"))
        self.assertTrue(
            any("interrupted by the customer" in m for m in _by_role(messages, "system"))
        )
