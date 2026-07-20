"""Tests for the DB-backed editable policy content store."""
import pathlib
import sys
import time
from unittest import TestCase

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.core import content_store
from app.core import guardrails
from app.core import product_facts as pf
from app.core.policy_prompt import build_system_content
from app.core.product_facts import format_for_prompt


def _set_cache(value: dict) -> None:
    """Seed the in-memory cache directly so accessors read `value`, not the DB."""
    content_store._cache = value
    content_store._cache_ts = time.monotonic()


class ContentStoreFallbackTests(TestCase):
    def tearDown(self):
        content_store.invalidate()

    def test_defaults_when_table_empty(self):
        _set_cache({})
        self.assertTrue(content_store.system_instruction().strip())
        self.assertEqual(content_store.product_facts(), dict(pf.PRODUCT_FACTS))
        self.assertEqual(content_store.pdf_rules(), list(pf.PDF_RULES))
        self.assertEqual(content_store.canned("price"), pf.CANNED_ANSWERS["price"])
        faq = content_store.objection_faq()
        self.assertEqual(len(faq), len(pf.OBJECTION_FAQ))

    def test_db_values_layer_over_defaults(self):
        _set_cache({
            "product_facts": {"monthly_price": "9,99 Euro monatlich"},
            "canned_answers": {"price": "Sonderpreis."},
        })
        facts = content_store.product_facts()
        self.assertEqual(facts["monthly_price"], "9,99 Euro monatlich")
        # Untouched keys keep their defaults.
        self.assertEqual(facts["trial_period"], pf.PRODUCT_FACTS["trial_period"])
        self.assertEqual(content_store.canned("price"), "Sonderpreis.")
        # Canned keys not overridden keep their default.
        self.assertEqual(content_store.canned("security"), pf.CANNED_ANSWERS["security"])

    def test_blank_value_falls_back(self):
        _set_cache({"system_instruction": {"text": "   "}})
        self.assertTrue(content_store.system_instruction().strip())


class PromptReflectsEditsTests(TestCase):
    def tearDown(self):
        content_store.invalidate()

    def test_format_for_prompt_uses_edited_values(self):
        _set_cache({
            "product_facts": {"monthly_price": "42 Euro monatlich"},
            "objection_faq": {"items": [{"trigger": "Zu teuer", "answer": "Testphase betonen."}]},
        })
        block = format_for_prompt()
        self.assertIn("42 Euro monatlich", block)
        self.assertIn('"Zu teuer" → Testphase betonen.', block)

    def test_build_system_content_includes_edited_instruction(self):
        _set_cache({"system_instruction": {"text": "EDITED PERSONA"}})
        self.assertIn("EDITED PERSONA", build_system_content())


class GuardrailsUseEditedAnswersTests(TestCase):
    def tearDown(self):
        content_store.invalidate()

    def test_price_guardrail_uses_edited_canned_answer_when_model_is_wrong(self):
        # A wrong Euro amount is unsafe -> guardrail replaces it with the (edited) template.
        _set_cache({"canned_answers": {"price": "Neuer Preis-Text."}})
        policy = guardrails.apply(
            {
                "intent": "price_question",
                "next_action": "pitch_product",
                "allowed_to_continue": True,
                "agent_response": "Das kostet vielleicht 9 Euro.",
            },
            {},
        )
        self.assertEqual(policy["agent_response"], "Neuer Preis-Text.")

    def test_price_guardrail_keeps_correct_model_answer(self):
        # A factually-correct nuanced answer (no wrong amount) is left untouched.
        _set_cache({"canned_answers": {"price": "Neuer Preis-Text."}})
        good = "Es gibt keine versteckten Kosten. Testen Sie es 14 Tage kostenlos."
        policy = guardrails.apply(
            {
                "intent": "price_question",
                "next_action": "pitch_product",
                "allowed_to_continue": True,
                "agent_response": good,
            },
            {},
        )
        self.assertEqual(policy["agent_response"], good)
        self.assertEqual(policy["next_action"], "explain_price")
