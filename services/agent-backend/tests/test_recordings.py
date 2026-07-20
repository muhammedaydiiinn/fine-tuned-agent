import pathlib
import sys
from unittest import TestCase, mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.core import recording_pipeline, state_manager
from app.schemas import SegmentPatchRequest, TranscriptCallbackRequest


def seg(idx, speaker, text, corrected=None):
    return {"idx": idx, "speaker": speaker, "text": text, "corrected_text": corrected}


class PairSegmentsTests(TestCase):
    def test_basic_alternation(self):
        pairs = recording_pipeline.pair_segments([
            seg(0, "customer", "Hallo?"),
            seg(1, "agent", "Guten Tag, Anna Weber hier."),
            seg(2, "customer", "Worum geht es?"),
            seg(3, "agent", "Um Ihren Anrufschutz."),
        ])
        self.assertEqual(pairs, [
            ("Hallo?", "Guten Tag, Anna Weber hier."),
            ("Worum geht es?", "Um Ihren Anrufschutz."),
        ])

    def test_leading_agent_yields_empty_customer_opening(self):
        pairs = recording_pipeline.pair_segments([
            seg(0, "agent", "Guten Tag, Anna Weber von Anrufblocker."),
            seg(1, "customer", "Ja, hallo."),
            seg(2, "agent", "Es geht um Betrugsanrufe."),
        ])
        self.assertEqual(pairs[0], ("", "Guten Tag, Anna Weber von Anrufblocker."))
        self.assertEqual(pairs[1], ("Ja, hallo.", "Es geht um Betrugsanrufe."))

    def test_consecutive_same_speaker_segments_merge(self):
        pairs = recording_pipeline.pair_segments([
            seg(0, "customer", "Moment."),
            seg(1, "customer", "Wer ist da?"),
            seg(2, "agent", "Anna Weber."),
        ])
        self.assertEqual(pairs, [("Moment. Wer ist da?", "Anna Weber.")])

    def test_trailing_customer_tail_dropped(self):
        pairs = recording_pipeline.pair_segments([
            seg(0, "customer", "Hallo?"),
            seg(1, "agent", "Guten Tag."),
            seg(2, "customer", "Tschüss."),
        ])
        self.assertEqual(pairs, [("Hallo?", "Guten Tag.")])

    def test_corrected_text_wins(self):
        pairs = recording_pipeline.pair_segments([
            seg(0, "customer", "Was kostet das?"),
            seg(1, "agent", "Nur 19,99 Euro!", corrected="14 Tage kostenlos, danach 29,99 Euro."),
        ])
        self.assertEqual(pairs[0][1], "14 Tage kostenlos, danach 29,99 Euro.")

    def test_unknown_speaker_raises(self):
        with self.assertRaises(recording_pipeline.UnattributedSegmentsError):
            recording_pipeline.pair_segments([
                seg(0, "customer", "Hallo?"),
                seg(1, "unknown", "Guten Tag."),
            ])

    def test_empty_segments_skipped(self):
        pairs = recording_pipeline.pair_segments([
            seg(0, "customer", "  "),
            seg(1, "agent", "Guten Tag."),
        ])
        self.assertEqual(pairs, [("", "Guten Tag.")])


class ValidateTrainingTextTests(TestCase):
    def test_clean_price_response_passes(self):
        warnings = recording_pipeline.validate_training_text(
            "price_question", "Die ersten 14 Tage kostenlos, danach 29,99 Euro monatlich."
        )
        self.assertEqual(warnings, [])

    def test_disallowed_euro_amount_flagged(self):
        warnings = recording_pipeline.validate_training_text(
            "general_inquiry", "Das kostet nur 19,99 Euro."
        )
        self.assertTrue(any("19,99" in w for w in warnings))

    def test_price_intent_without_trial_phrase_flagged(self):
        warnings = recording_pipeline.validate_training_text(
            "price_question", "Das kostet 29,99 Euro."
        )
        self.assertTrue(any("14 Tage" in w for w in warnings))

    def test_discount_claim_flagged(self):
        warnings = recording_pipeline.validate_training_text(
            "general_inquiry", "Heute mit 50% Rabatt!"
        )
        self.assertTrue(any("Rabatt" in w for w in warnings))

    def test_allowed_onetime_check_amount_passes(self):
        warnings = recording_pipeline.validate_training_text(
            "general_inquiry", "Die Einzelprüfung kostet normalerweise 2500 Euro."
        )
        self.assertEqual(warnings, [])


class InferTurnPolicyTests(TestCase):
    """vllm_mode defaults to mock — _mock_chat returns a parseable policy."""

    def test_mock_mode_returns_contract_fields(self):
        policy = recording_pipeline.infer_turn_policy(
            "Was kostet das?", "14 Tage kostenlos, danach 29,99 Euro.", dict(state_manager.DEFAULT_STATE)
        )
        for key in ("intent", "emotion", "risk", "next_action", "allowed_to_continue", "agent_response"):
            self.assertIn(key, policy)
        # The spoken reply is authoritative — never the model echo.
        self.assertEqual(policy["agent_response"], "14 Tage kostenlos, danach 29,99 Euro.")
        self.assertIn(policy["risk"], ("low", "medium", "high"))

    def test_llm_failure_falls_back_to_defaults(self):
        with mock.patch.object(recording_pipeline.vllm_client, "chat", side_effect=RuntimeError("down")):
            policy = recording_pipeline.infer_turn_policy("Hallo?", "Guten Tag.", {})
        self.assertEqual(policy["intent"], "unknown")
        self.assertEqual(policy["agent_response"], "Guten Tag.")
        self.assertTrue(policy["allowed_to_continue"])


class AttributeSpeakersTests(TestCase):
    def test_parses_llm_mapping(self):
        raw = '{"segments": [{"idx": 0, "speaker": "agent"}, {"idx": 1, "speaker": "customer"}]}'
        with mock.patch.object(recording_pipeline.vllm_client, "chat", return_value=raw):
            mapping = recording_pipeline.attribute_speakers([
                {"idx": 0, "text": "Guten Tag, Anna Weber."},
                {"idx": 1, "text": "Hallo."},
            ])
        self.assertEqual(mapping, {0: "agent", 1: "customer"})

    def test_failure_returns_empty_mapping(self):
        with mock.patch.object(recording_pipeline.vllm_client, "chat", side_effect=RuntimeError("down")):
            mapping = recording_pipeline.attribute_speakers([{"idx": 0, "text": "Hallo."}])
        self.assertEqual(mapping, {})

    def test_invalid_speaker_values_ignored(self):
        raw = '{"segments": [{"idx": 0, "speaker": "narrator"}, {"idx": 1, "speaker": "customer"}]}'
        with mock.patch.object(recording_pipeline.vllm_client, "chat", return_value=raw):
            mapping = recording_pipeline.attribute_speakers([
                {"idx": 0, "text": "a"}, {"idx": 1, "text": "b"},
            ])
        self.assertEqual(mapping, {1: "customer"})


class StateReplayTests(TestCase):
    def test_state_progression_matches_runtime_semantics(self):
        state = dict(state_manager.DEFAULT_STATE)
        policy = {"intent": "price_question", "next_action": "explain_price"}
        new_state = state_manager.update(state, policy, "Was kostet das?")
        self.assertEqual(new_state["turn_count"], 1)
        self.assertTrue(new_state["offer_terms_explained"])

    def test_hard_decline_counter(self):
        state = dict(state_manager.DEFAULT_STATE)
        policy = {"intent": "hard_decline", "next_action": "acknowledge_objection"}
        new_state = state_manager.update(state, policy, "Kein Interesse.")
        self.assertEqual(new_state["hard_decline_count"], 1)


class SchemaContractTests(TestCase):
    def test_transcript_callback_defaults(self):
        body = TranscriptCallbackRequest()
        self.assertEqual(body.segments, [])
        self.assertIsNone(body.error)

    def test_transcript_callback_with_segments(self):
        body = TranscriptCallbackRequest(
            duration_seconds=12.5,
            channels=2,
            segments=[{"idx": 0, "start_ms": 0, "end_ms": 900, "text": "Hallo", "speaker": "agent"}],
        )
        self.assertEqual(body.segments[0].speaker, "agent")

    def test_segment_patch_partial(self):
        body = SegmentPatchRequest(speaker="customer")
        self.assertIsNone(body.text)
        self.assertIsNone(body.corrected_text)
