import pathlib
import sys
from unittest import TestCase

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.core.guardrails import apply, apply_with_context
from app.core.product_facts import CLOSING_BRIEF_TEMPLATE, PRICE_TEMPLATE


class HardDeclineGuardrailTests(TestCase):
    def test_first_decline_acknowledges_and_continues(self):
        policy = apply(
            {
                "intent": "hard_decline",
                "next_action": "handle_objection",
                "allowed_to_continue": True,
                "agent_response": "Verstanden.",
            },
            {"hard_decline_count": 0},
        )
        self.assertEqual(policy["next_action"], "acknowledge_objection")
        self.assertTrue(policy["allowed_to_continue"])

    def test_second_decline_forces_close(self):
        # Deploy-gate contract: a repeated clear NO ends the call.
        policy = apply(
            {
                "intent": "hard_decline",
                "next_action": "handle_objection",
                "allowed_to_continue": True,
                "agent_response": "Verstanden.",
            },
            {"hard_decline_count": 1},
        )
        self.assertEqual(policy["next_action"], "close_call")
        self.assertFalse(policy["allowed_to_continue"])

    def test_decline_phrasing_overrides_wrong_intent(self):
        policy = apply_with_context(
            {
                "intent": "price_question",
                "next_action": "explain_price",
                "allowed_to_continue": True,
                "agent_response": "Der Schutz ist 14 Tage kostenlos.",
            },
            {"hard_decline_count": 0},
            customer_message="Ich will nichts kaufen.",
        )
        self.assertEqual(policy["intent"], "hard_decline")
        self.assertEqual(policy["next_action"], "acknowledge_objection")

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

    def test_security_objection_uses_approved_template(self):
        # Deploy-gate contract: security reassurance is the approved template,
        # verbatim (compliance floor; panel-editable via canned_answers).
        from app.core import content_store

        policy = apply(
            {
                "intent": "security_objection",
                "next_action": "handle_objection",
                "allowed_to_continue": True,
                "agent_response": "Keine Sorge, der Link führt nur zum offiziellen App Store.",
            },
            {},
        )
        self.assertEqual(policy["agent_response"], content_store.canned("security"))
        self.assertEqual(policy["next_action"], "address_security")

    def test_link_without_identity_forces_confirm_identity(self):
        policy = apply_with_context(
            {
                "intent": "activation_link_request",
                "next_action": "send_activation_link",
                "allowed_to_continue": True,
                "agent_response": "Ich schicke Ihnen den Link.",
            },
            {"identity_confirmed": False},
            customer_message="Okay, schicken Sie mir den sicheren Link.",
        )
        self.assertEqual(policy["next_action"], "confirm_identity")

    def test_link_with_identity_sends_activation_link(self):
        policy = apply_with_context(
            {
                "intent": "activation_link_request",
                "next_action": "redirect_to_app",
                "allowed_to_continue": True,
                "agent_response": "Ich schicke Ihnen den Link.",
            },
            {"identity_confirmed": True},
            customer_message="Okay, schicken Sie mir den sicheren Link.",
        )
        self.assertEqual(policy["next_action"], "send_activation_link")

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
