"""Pure parts of the objection miner: pair filtering and dedup similarity."""
import pathlib
import sys
from unittest import TestCase

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.core.objection_miner import _similar


class SimilarityTests(TestCase):
    def test_same_answer_wording_is_duplicate(self):
        a = "Der Link führt ausschließlich zum offiziellen Apple App Store oder Google Play Store."
        b = "Der Link führt nur zum offiziellen Apple App Store oder Google Play Store, keine Sorge."
        self.assertTrue(_similar(a, b))

    def test_different_topics_are_not_duplicates(self):
        a = "Die ersten 14 Tage sind kostenlos, danach 29,99 Euro monatlich."
        b = "Wir prüfen über Schnittstellen, in welchen Datenbanken Ihre Nummer registriert wurde."
        self.assertFalse(_similar(a, b))

    def test_empty_never_matches(self):
        self.assertFalse(_similar("", "irgendwas"))
