import pathlib
import sys
from unittest import TestCase

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.core.guardrails import apply_with_context
from app.core.product_facts import CLOSING_BRIEF_TEMPLATE, PRICE_TEMPLATE
from app.core.response_repair import repair_all, repair_invented_facts, repair_premature_link


class ResponseRepairTests(TestCase):
    def test_premature_link_keeps_model_explanation(self):
        model = (
            "Wir überprüfen Ihre Nummer über externe Datenbanken, ohne Zugriff auf "
            "persönliche Daten. Ich leite Sie jetzt zum offiziellen App Store."
        )
        fixed, hit = repair_premature_link(model, {}, "Ja und?")
        self.assertTrue(hit)
        self.assertIn("externe Datenbanken", fixed)
        self.assertNotIn("App Store", fixed)
        self.assertNotIn("Bevor wir zur Installation kommen", fixed)

    def test_invented_facts_strips_only_bad_sentences(self):
        model = (
            "Die Nummern stammen aus Datenbanken. Im Ausland ist der Schutz leider nicht aktiv. "
            "Die App verbraucht kaum Akku."
        )
        fixed, hit = repair_invented_facts(model)
        self.assertTrue(hit)
        self.assertIn("Datenbanken", fixed)
        self.assertNotIn("Ausland", fixed)
        self.assertNotIn("Akku", fixed)

    def test_price_inquiry_gets_pdf_template(self):
        policy = apply_with_context(
            {
                "intent": "price_inquiry",
                "next_action": "explain_pricing_model",
                "allowed_to_continue": True,
                "agent_response": "Es gibt eine 14-tägige Testphase.",
            },
            {},
            "Was das Kosteskostes?",
        )
        self.assertEqual(policy["agent_response"], PRICE_TEMPLATE)

    def test_post_close_is_brief_only(self):
        policy = apply_with_context(
            {
                "intent": "closing",
                "next_action": "close_call",
                "allowed_to_continue": False,
                "agent_response": "Vielen Dank für das Gespräch. Ich wünsche Ihnen einen schönen Tag.",
            },
            {"stage": "closing"},
            "Gute und da.",
        )
        self.assertEqual(policy["agent_response"], CLOSING_BRIEF_TEMPLATE)

    def test_repair_all_does_not_replace_good_answer_without_link(self):
        model = (
            "Das ist wichtig: über 7.000 bekannte Risikonummern können Sie bereits jetzt erreichen."
        )
        fixed, rules = repair_all(model, {"filled_slots": {}}, "Ja und?")
        self.assertEqual(fixed, model)
        self.assertNotIn("premature_link", rules)
