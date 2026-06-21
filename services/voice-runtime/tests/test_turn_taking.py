import json
from pathlib import Path
from unittest import TestCase

from app.turn_taking import BackchannelClassifier, TranscriptDeduplicator


class BackchannelClassifierTests(TestCase):
    def setUp(self):
        self.classifier = BackchannelClassifier()

    # ------------------------------------------------------------------
    # Legacy single-phrase exact-match (regression)
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Multi-token backchannel (M8 Aşama 2)
    # ------------------------------------------------------------------

    def test_multi_token_all_backchannel_words(self):
        """Utterances composed entirely of backchannel vocabulary are backchannels."""
        for phrase in ("ja ja", "mhm okay", "ja genau", "hm mhm", "okay okay"):
            with self.subTest(phrase=phrase):
                self.assertTrue(self.classifier.classify(phrase).is_backchannel)

    def test_multi_token_multiword_phrase_is_backchannel(self):
        """Multi-word phrases like 'alles klar' are treated as a single token unit."""
        for phrase in ("alles klar ja", "mhm alles klar", "ja alles klar ja"):
            with self.subTest(phrase=phrase):
                self.assertTrue(self.classifier.classify(phrase).is_backchannel)

    def test_prefix_ack_plus_content_is_interrupt(self):
        """Any content word after acknowledgement tokens → real interruption."""
        for phrase in ("ja aber nein", "okay aber warum", "mhm das stimmt nicht"):
            with self.subTest(phrase=phrase):
                self.assertFalse(self.classifier.classify(phrase).is_backchannel)

    def test_multiword_phrase_then_content_is_interrupt(self):
        """'alles klar' consumed as unit, but trailing content word → interrupt."""
        self.assertFalse(
            self.classifier.classify("alles klar aber ich lehne ab").is_backchannel
        )

    def test_utterance_longer_than_max_tokens_is_interrupt(self):
        """Conservative: utterances with > max_tokens words are real speech."""
        # "ja ja ja ja ja ja ja" = 7 tokens, default max_tokens=6
        result = self.classifier.classify("ja ja ja ja ja ja ja")
        self.assertFalse(result.is_backchannel)

    def test_empty_text_is_not_backchannel(self):
        result = self.classifier.classify("")
        self.assertFalse(result.is_backchannel)
        self.assertEqual(result.normalized_text, "")

    def test_whitespace_only_is_not_backchannel(self):
        result = self.classifier.classify("   ")
        self.assertFalse(result.is_backchannel)

    def test_custom_max_tokens_limits_greedy_scan(self):
        """max_tokens=2 rejects 3-token utterances even if all words are ack."""
        classifier = BackchannelClassifier(max_tokens=2)
        # 3 tokens: "ja ja ja" → conservative interrupt
        self.assertFalse(classifier.classify("ja ja ja").is_backchannel)
        # 2 tokens: "ja ja" → backchannel
        self.assertTrue(classifier.classify("ja ja").is_backchannel)


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

        # IDs must be unique
        ids = [row["id"] for row in classified]
        self.assertEqual(len(ids), len(set(ids)), "Duplicate IDs in catalog")

        backchannel_rows = [row for row in classified if row["kind"] == "backchannel"]
        interrupt_rows = [row for row in classified if row["kind"] == "interruption"]

        # Counts grow as catalog expands; >= 20 locks in the original baseline
        self.assertGreaterEqual(len(backchannel_rows), 20)
        self.assertGreaterEqual(len(interrupt_rows), 20)

        for row in classified:
            with self.subTest(scenario=row["id"]):
                decision = classifier.classify(row["transcript"])
                self.assertEqual(
                    not decision.is_backchannel,
                    row["expect_interrupt"],
                )
