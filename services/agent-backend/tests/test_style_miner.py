"""Style miner leak filter: product facts must never pass through."""
import pathlib
import sys
from unittest import TestCase

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.core.style_miner import leaks_product_facts


class LeakFilterTests(TestCase):
    def test_price_leaks(self):
        self.assertTrue(leaks_product_facts("Nenne früh den Preis von 69 Euro."))
        self.assertTrue(leaks_product_facts("29,99 € pro Monat erwähnen"))

    def test_banking_and_contract_leak(self):
        self.assertTrue(leaks_product_facts("Frage nach den letzten Ziffern der IBAN."))
        self.assertTrue(leaks_product_facts("Erkläre das Widerrufsrecht."))
        self.assertTrue(leaks_product_facts("Die AGB kommen per Post."))

    def test_pure_style_passes(self):
        self.assertFalse(leaks_product_facts(
            "Bestätige den Einwand kurz, bevor du mit einer Gegenfrage die Führung zurückholst."
        ))

    def test_payment_wording_leaks(self):
        self.assertTrue(leaks_product_facts("Danke, Ihre Bankverbindung ist bestätigt."))
        self.assertTrue(leaks_product_facts("Bitte prüfen Sie die Zahlungsmethode."))
