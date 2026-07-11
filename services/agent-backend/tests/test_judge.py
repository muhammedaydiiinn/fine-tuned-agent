import pathlib
import sys
from unittest import TestCase, mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.core import judge

_GOOD_JUDGE_JSON = (
    '{"scores":{"semantic_correctness":4,"policy_json_consistency":5,'
    '"persuasion_tone":4,"german_fluency":4,"safety_adherence":5},'
    '"overall":0.9,"suggestion":"","rationale":"ok"}'
)


def _fake_target(**over):
    t = {"mode": "real", "base_url": "http://x/v1", "model_name": "anrufblocker-production", "slot": "prod"}
    t.update(over)
    return t


class JudgeMockTests(TestCase):
    def test_mock_score_valid_schema(self):
        r = judge.score(
            "Was kostet das?", {"stage": "offer"},
            "Nach 14 Tagen kostenlos 29,99 Euro monatlich.",
            {"agent_response": "Nach 14 Tagen kostenlos 29,99 Euro.", "intent": "price_question"},
        )
        self.assertEqual(set(r["scores"]), set(judge.RUBRIC_KEYS))
        self.assertTrue(0.0 <= r["overall"] <= 1.0)
        self.assertIn("passed", r)
        self.assertIsInstance(r["passed"], bool)
        # Valid policy JSON → full consistency in mock.
        self.assertEqual(r["scores"]["policy_json_consistency"], 5)

    def test_mock_empty_response_scores_lower(self):
        empty = judge.score("Hallo?", {}, "", {})
        good = judge.score("Hallo?", {}, "Gute Antwort.", {"agent_response": "x"})
        self.assertLess(empty["overall"], good["overall"])


class JudgeParseTests(TestCase):
    def test_sentinel_on_persistent_garbage(self):
        with mock.patch.object(judge.settings, "vllm_mode", "real"), \
             mock.patch.object(judge.model_runtime, "production_serving_target", return_value=_fake_target()), \
             mock.patch.object(judge.vllm_client, "chat", return_value="not json at all"):
            r = judge.score("x", {}, "y", {})
        self.assertIsNone(r["overall"])
        self.assertFalse(r["passed"])
        self.assertEqual(r["rationale"], "parse_failed")

    def test_retry_recovers_at_temperature_zero(self):
        temps: list = []

        def fake_chat(messages, target=None, *, temperature=None, max_tokens=None):
            temps.append(temperature)
            return "garbage" if len(temps) == 1 else _GOOD_JUDGE_JSON

        with mock.patch.object(judge.settings, "vllm_mode", "real"), \
             mock.patch.object(judge.model_runtime, "production_serving_target", return_value=_fake_target()), \
             mock.patch.object(judge.vllm_client, "chat", side_effect=fake_chat):
            r = judge.score("x", {}, "y", {})
        self.assertEqual(len(temps), 2)
        self.assertEqual(temps[1], 0.0)  # retry forced greedy
        self.assertEqual(r["scores"]["policy_json_consistency"], 5)
        self.assertTrue(0.0 <= r["overall"] <= 1.0)

    def test_judge_targets_production_base_model(self):
        seen = {}

        def fake_chat(messages, target=None, *, temperature=None, max_tokens=None):
            seen["target"] = target
            return _GOOD_JUDGE_JSON

        with mock.patch.object(judge.settings, "vllm_mode", "real"), \
             mock.patch.object(judge.model_runtime, "production_serving_target", return_value=_fake_target()), \
             mock.patch.object(judge.vllm_client, "chat", side_effect=fake_chat):
            judge.score("x", {}, "y", {})
        self.assertEqual(seen["target"]["model_name"], "anrufblocker-production")

    def test_overall_recomputed_not_trusted_from_model(self):
        # Model claims overall 0.99 but scores are mediocre → we recompute.
        bad_overall = (
            '{"scores":{"semantic_correctness":2,"policy_json_consistency":2,'
            '"persuasion_tone":2,"german_fluency":2,"safety_adherence":2},'
            '"overall":0.99,"suggestion":"","rationale":""}'
        )
        with mock.patch.object(judge.settings, "vllm_mode", "real"), \
             mock.patch.object(judge.model_runtime, "production_serving_target", return_value=_fake_target()), \
             mock.patch.object(judge.vllm_client, "chat", return_value=bad_overall):
            r = judge.score("x", {}, "y", {})
        self.assertAlmostEqual(r["overall"], 0.4)  # 2/5 across all dims
