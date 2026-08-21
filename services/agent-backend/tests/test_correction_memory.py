"""WP-7 safety rails: scoped matching, TTL expiry, circuit breaker."""
import pathlib
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import TestCase

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.core.correction_memory import _is_expired, _matches, apply_override


def _entry(**kwargs) -> SimpleNamespace:
    defaults = {
        "id": 1,
        "trigger_key": "interested",
        "context_json": {},
        "correct_response": "Alles gut.",
        "correct_next_action": None,
        "expires_at": None,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


class MatchScopingTests(TestCase):
    def test_exact_customer_text_matches(self):
        entry = _entry(context_json={"customer_text": "Was kostet das?"})
        self.assertTrue(_matches(entry, "was  kostet das?", {}))

    def test_different_customer_text_does_not_match(self):
        entry = _entry(context_json={"customer_text": "Was kostet das?"})
        self.assertFalse(_matches(entry, "Ist der Link sicher?", {}))

    def test_opening_capture_only_matches_openings(self):
        # The 2026-08-18 incident entry: captured on the opening turn.
        entry = _entry(context_json={"intent": "interested", "customer_text": ""})
        self.assertTrue(_matches(entry, "", {}))
        self.assertFalse(_matches(entry, "Ja, die App ist offen.", {}))

    def test_intent_name_is_not_a_substring_trigger(self):
        # A canonical intent as trigger_key must not act as a phrase trigger.
        entry = _entry(trigger_key="interested", context_json={"customer_text": "x"})
        self.assertFalse(_matches(entry, "ich bin sehr interested", {"last_intent": "interested"}))

    def test_last_intent_fallback_removed(self):
        entry = _entry(context_json={"intent": "interested", "customer_text": "y"})
        self.assertFalse(_matches(entry, "irgendwas anderes", {"last_intent": "interested"}))

    def test_handwritten_phrase_trigger_still_works(self):
        entry = _entry(trigger_key="ratenzahlung", context_json={})
        self.assertTrue(_matches(entry, "Geht das auch mit Ratenzahlung?", {}))


class ExpiryTests(TestCase):
    def test_no_expiry_is_active(self):
        self.assertFalse(_is_expired(_entry(expires_at=None)))

    def test_past_expiry_is_expired(self):
        past = datetime.now(timezone.utc) - timedelta(hours=1)
        self.assertTrue(_is_expired(_entry(expires_at=past)))

    def test_future_expiry_is_active(self):
        future = datetime.now(timezone.utc) + timedelta(days=1)
        self.assertFalse(_is_expired(_entry(expires_at=future)))


class ApplyOverrideTests(TestCase):
    def test_override_carries_entry_id_for_breaker(self):
        policy = {"agent_response": "orig", "next_action": "pitch_product"}
        out = apply_override(
            policy,
            [{"entry_id": 7, "trigger_key": "x", "correct_response": "neu", "correct_next_action": None}],
        )
        self.assertEqual(out["agent_response"], "neu")
        self.assertEqual(out["_correction_entry_id"], 7)
        self.assertEqual(out["behavior_strategy"], "correction_memory_override")

    def test_no_hints_returns_policy_unchanged(self):
        policy = {"agent_response": "orig"}
        self.assertEqual(apply_override(policy, []), policy)
