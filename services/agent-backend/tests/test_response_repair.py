import pathlib
import sys
from unittest import TestCase

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.core.guardrails import apply_with_context
from app.core.product_facts import CLOSING_BRIEF_TEMPLATE, PRICE_TEMPLATE
from app.core.response_repair import repair_all, repair_invented_facts


class ResponseRepairTests(TestCase):
    def test_link_and_alternatives_are_preserved(self):
        # A2 legal-floor refactor: the app-funnel repair (premature_link) was
        # removed, so the model may mention the link and offer alternatives
        # (self-entry, website) without being stripped.
        model = (
            "Sie können die App selbst im App Store öffnen oder direkt über unsere "
            "Webseite www.callshield-demo.de starten."
        )
        fixed, rules = repair_all(model, {"filled_slots": {}}, "Ich will keinen Link öffnen.")
        self.assertEqual(fixed, model)
        self.assertNotIn("premature_link", rules)

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

    def test_price_inquiry_with_wrong_amount_gets_pdf_template(self):
        # A wrong Euro amount is unsafe -> the PDF price template is enforced.
        policy = apply_with_context(
            {
                "intent": "price_inquiry",
                "next_action": "explain_pricing_model",
                "allowed_to_continue": True,
                "agent_response": "Das kostet vielleicht 9 Euro im Monat.",
            },
            {},
            "Was das Kosteskostes?",
        )
        self.assertEqual(policy["agent_response"], PRICE_TEMPLATE)

    def test_price_inquiry_keeps_correct_answer(self):
        # A factually-correct answer (no wrong amount) is left untouched.
        good = "Es gibt eine 14-tägige kostenlose Testphase, danach 29,99 Euro monatlich."
        policy = apply_with_context(
            {
                "intent": "price_inquiry",
                "next_action": "explain_pricing_model",
                "allowed_to_continue": True,
                "agent_response": good,
            },
            {},
            "Was das Kosteskostes?",
        )
        self.assertEqual(policy["agent_response"], good)

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
