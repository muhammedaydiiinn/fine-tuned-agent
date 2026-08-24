import json
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


def _last_agent_message(messages):
    """The prior agent line now travels in the user payload, not as a message."""
    user = _by_role(messages, "user")[-1]
    return json.loads(user).get("last_agent_message", "")


def _serialized(messages):
    return json.dumps(messages, ensure_ascii=False)


class PromptBuilderInterruptionTests(TestCase):
    def test_completed_turn_renders_full_response(self):
        turns = [make_turn("Hallo", "Guten Tag.")]
        messages = prompt_builder.build("Was kostet das?", {}, turns, [])
        # Single-turn format: the last agent line is a field, not a message.
        self.assertEqual(_last_agent_message(messages), "Guten Tag.")

    def test_interrupted_turn_renders_only_heard_prefix_with_note(self):
        full = "Guten Tag, ich bin Anna von CallShield und rufe Sie heute an."
        heard = "Guten Tag, ich bin Anna"
        turns = [make_turn("Hallo", full, was_interrupted=True, spoken=heard)]

        messages = prompt_builder.build("Was kostet das?", {}, turns, [])

        # Only the heard prefix is exposed to the model, never the full reply.
        self.assertEqual(_last_agent_message(messages), heard)
        self.assertNotIn(full, _serialized(messages))
        # An interruption note is injected so the model resumes contextually.
        self.assertTrue(
            any("interrupted by the customer" in m for m in _by_role(messages, "system"))
        )

    def test_interrupted_turn_with_no_audio_still_flags_cutoff(self):
        # Cut off before any audio played → empty prefix, but the note must
        # still appear so the model knows it was interrupted.
        full = "Guten Tag, ich bin Anna."
        turns = [make_turn("Hallo", full, was_interrupted=True, spoken=None)]

        messages = prompt_builder.build("Was kostet das?", {}, turns, [])

        self.assertNotIn(full, _serialized(messages))
        self.assertTrue(
            any("interrupted by the customer" in m for m in _by_role(messages, "system"))
        )


class ObjectionLibrarySelectionTests(TestCase):
    """WP-4: the panel-editable objection library reaches the prompt, per turn."""

    def test_matching_objection_is_injected(self):
        from app.core.prompt_builder import build_user_payload

        payload = build_user_payload("Wie überprüfen Sie das eigentlich?", {}, [])
        library = payload["known_customer_data"].get("objection_library") or []
        self.assertTrue(library, "expected at least one matching objection entry")
        self.assertTrue(any("prüfen" in (e["answer"] or "").lower() for e in library))

    def test_opening_turn_injects_no_objections(self):
        from app.core.prompt_builder import build_user_payload

        payload = build_user_payload("", {}, [])
        self.assertNotIn("objection_library", payload["known_customer_data"])

    def test_small_talk_injects_no_objections(self):
        from app.core.prompt_builder import build_user_payload

        payload = build_user_payload("Schönes Wetter heute, oder?", {}, [])
        self.assertNotIn("objection_library", payload["known_customer_data"])


class NearDuplicateTests(TestCase):
    def test_same_price_sentence_is_duplicate(self):
        a = "Die ersten 14 Tage kostenlos sind kostenlos, danach 29,99 Euro monatlich. Darf ich Ihnen zeigen, wie das geht?"
        b = "Die ersten 14 Tage kostenlos sind kostenlos, danach 29,99 Euro monatlich. Darf ich Ihnen zeigen, wie Sie starten?"
        self.assertTrue(prompt_builder.near_duplicate(a, b))

    def test_different_replies_are_not_duplicates(self):
        a = "Die ersten 14 Tage sind kostenlos, danach 29,99 Euro monatlich."
        b = "Sehr gut, ich sende Ihnen den Link per SMS. Öffnen Sie bitte den App Store."
        self.assertFalse(prompt_builder.near_duplicate(a, b))

    def test_empty_never_duplicate(self):
        self.assertFalse(prompt_builder.near_duplicate("", "irgendwas"))
