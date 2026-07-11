import pathlib
import sys
from unittest import TestCase, mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import worker
from evals import judge_batch


class _FakeDB:
    def __init__(self):
        self.added = []

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        pass


def _result(sid, intent="i", na="n"):
    return {
        "scenario_id": sid,
        "customer_text": "kunde",
        "state_before": {},
        "agent_response": "antwort",
        "actual_intent": intent,
        "actual_next_action": na,
        "allowed_to_continue": True,
    }


class JudgeScenariosTests(TestCase):
    def test_aggregate_and_rows(self):
        verdicts = [
            {"scores": {"x": 5}, "overall": 0.9, "suggestion": "", "rationale": "", "passed": True},
            {"scores": {"x": 2}, "overall": 0.5, "suggestion": "besser", "rationale": "", "passed": False},
        ]
        db = _FakeDB()
        with mock.patch.object(worker, "call_judge_score", side_effect=verdicts):
            agg = worker._judge_scenarios(db, 1, 2, [_result("s1"), _result("s2")])
        self.assertEqual(agg["count"], 2)
        self.assertEqual(agg["judged"], 2)
        self.assertEqual(agg["errors"], 0)
        self.assertAlmostEqual(agg["mean_overall"], 0.7)
        # Two TurnEvaluation rows, both source='scenario'.
        self.assertEqual(len(db.added), 2)
        self.assertTrue(all(te.source == "scenario" for te in db.added))
        self.assertEqual(db.added[0].scenario_id, "s1")

    def test_judge_error_is_counted_not_fatal(self):
        db = _FakeDB()
        with mock.patch.object(worker, "call_judge_score", side_effect=RuntimeError("judge down")):
            agg = worker._judge_scenarios(db, 1, 2, [_result("s1")])
        self.assertEqual(agg["errors"], 1)
        self.assertEqual(agg["judged"], 0)
        self.assertIsNone(agg["mean_overall"])
        self.assertEqual(len(db.added), 1)  # row still written with null scores


class CallJudgeScoreTests(TestCase):
    def test_posts_body_and_returns_json(self):
        captured = {}

        class _Resp:
            def raise_for_status(self):
                pass

            def json(self):
                return {"overall": 0.8}

        class _Client:
            def __init__(self, *a, **k):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def post(self, url, json=None, headers=None):
                captured["url"] = url
                captured["json"] = json
                return _Resp()

        with mock.patch.object(judge_batch.httpx, "Client", _Client):
            out = judge_batch.call_judge_score("kunde", {"s": 1}, "antwort", {"intent": "i"})
        self.assertEqual(out, {"overall": 0.8})
        self.assertEqual(captured["json"]["customer_text"], "kunde")
        self.assertEqual(captured["json"]["policy_json"], {"intent": "i"})
