import json
from pathlib import Path
from unittest import TestCase

from app.turn_taking import BackchannelClassifier, TranscriptDeduplicator


class BackchannelClassifierTests(TestCase):
    def setUp(self):
        self.classifier = BackchannelClassifier()

    def test_short_acknowledgements_are_backchannels(self):
        for phrase in ("mhm", "Ja.", "okay", "Alles klar"):
            with self.subTest(phrase=phrase):
                self.assertTrue(self.classifier.classify(phrase).is_backchannel)

    def test_customer_intent_is_not_a_backchannel(self):
        for phrase in (
            "Ja, aber was kostet das?",
            "Okay, ich möchte kündigen.",
            "Moment, das stimmt nicht.",
        ):
            with self.subTest(phrase=phrase):
                self.assertFalse(self.classifier.classify(phrase).is_backchannel)


class TranscriptDeduplicatorTests(TestCase):
    def test_same_final_is_rejected_inside_window(self):
        deduplicator = TranscriptDeduplicator(window_seconds=2.5)

        self.assertFalse(deduplicator.is_duplicate("Was kostet das?", now=10.0))
        self.assertTrue(deduplicator.is_duplicate("was kostet das", now=11.0))

    def test_same_final_is_accepted_after_window(self):
        deduplicator = TranscriptDeduplicator(window_seconds=2.5)

        self.assertFalse(deduplicator.is_duplicate("Nein danke", now=10.0))
        self.assertFalse(deduplicator.is_duplicate("Nein danke", now=13.0))


class TurnTakingScenarioCatalogTests(TestCase):
    def test_backchannel_catalog_matches_expected_interruptions(self):
        classifier = BackchannelClassifier()
        catalog = Path(__file__).with_name("turn_taking_scenarios.jsonl")
        rows = [
            json.loads(line)
            for line in catalog.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        classified = [
            row
            for row in rows
            if row["kind"] in {"backchannel", "interruption"}
        ]

        self.assertEqual(
            len([row for row in classified if row["kind"] == "backchannel"]),
            20,
        )
        self.assertEqual(
            len([row for row in classified if row["kind"] == "interruption"]),
            20,
        )
        for row in classified:
            with self.subTest(scenario=row["id"]):
                decision = classifier.classify(row["transcript"])
                self.assertEqual(
                    not decision.is_backchannel,
                    row["expect_interrupt"],
                )
