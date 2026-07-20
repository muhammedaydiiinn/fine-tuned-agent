import pathlib
import sys
from unittest import TestCase

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.core.guardrails import apply, apply_with_context
from app.core.product_facts import CLOSING_BRIEF_TEMPLATE, PRICE_TEMPLATE, SECURITY_TEMPLATE


class HardDeclineGuardrailTests(TestCase):
    def test_second_decline_is_not_forced_closed(self):
        policy = apply(
            {
                "intent": "hard_decline",
                "next_action": "handle_objection",
                "allowed_to_continue": True,
                "agent_response": "Verstanden.",
            },
            {"hard_decline_count": 1},
        )
        self.assertNotEqual(policy["next_action"], "close_call")
        self.assertTrue(policy["allowed_to_continue"])

    def test_third_decline_forces_close(self):
        policy = apply(
            {
                "intent": "hard_decline",
                "next_action": "handle_objection",
                "allowed_to_continue": True,
                "agent_response": "Alles klar.",
            },
            {"hard_decline_count": 2},
        )
        self.assertEqual(policy["next_action"], "close_call")
        self.assertFalse(policy["allowed_to_continue"])


class PdfGuardrailTests(TestCase):
    def test_price_question_uses_pdf_template(self):
        policy = apply(
            {
                "intent": "price_question",
                "next_action": "pitch_product",
                "allowed_to_continue": True,
                "agent_response": "Das kostet vielleicht 9 Euro.",
            },
            {},
        )
        self.assertEqual(policy["agent_response"], PRICE_TEMPLATE)
        self.assertEqual(policy["next_action"], "explain_price")

    def test_security_objection_uses_pdf_template(self):
        policy = apply(
            {
                "intent": "security_objection",
                "next_action": "handle_objection",
                "allowed_to_continue": True,
                "agent_response": "Keine Sorge.",
            },
            {},
        )
        self.assertEqual(policy["agent_response"], SECURITY_TEMPLATE)

    def test_delay_blocks_phone_collection(self):
        policy = apply_with_context(
            {
                "intent": "hesitate",
                "next_action": "qualify_lead",
                "allowed_to_continue": True,
                "agent_response": "Bitte nennen Sie mir Ihre Telefonnummer.",
            },
            {},
            "Ich brauche Zeit und melde mich später.",
        )
        self.assertEqual(policy["next_action"], "handle_time_objection")
        self.assertNotIn("telefonnummer", policy["agent_response"].lower())

    def test_price_inquiry_via_customer_text(self):
        policy = apply_with_context(
            {
                "intent": "skeptical_interest",
                "next_action": "pitch_product",
                "allowed_to_continue": True,
                "agent_response": "Das kostet vielleicht 9 Euro.",
            },
            {},
            "Was kostet das?",
        )
        self.assertEqual(policy["agent_response"], PRICE_TEMPLATE)

    def test_post_close_brief(self):
        policy = apply_with_context(
            {
                "intent": "closing",
                "next_action": "close_call",
                "allowed_to_continue": False,
                "agent_response": "Langer Abschied.",
            },
            {"stage": "closing"},
            "Danke, tschüss.",
        )
        self.assertEqual(policy["agent_response"], CLOSING_BRIEF_TEMPLATE)
