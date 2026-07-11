import pathlib
import sys
from unittest import TestCase, mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.core import customer_sim, sim_runner


class RunOneConversationTests(TestCase):
    def test_collects_turn_ids_and_stops_on_done(self):
        state = {"n": 0}

        def fake_agent_turn(session_id, customer_text):
            state["n"] += 1
            return {"turn_id": state["n"], "agent_response": f"antwort {state['n']}"}

        # vllm_mode defaults to mock → customer_sim uses the scripted persona.
        with mock.patch.object(sim_runner, "_agent_turn", side_effect=fake_agent_turn):
            turn_ids = sim_runner._run_one_conversation(
                "tok", customer_sim.PERSONAS[0], 1, max_turns=8
            )
        # Opening turn + at least one customer↔agent exchange.
        self.assertGreaterEqual(len(turn_ids), 2)
        self.assertEqual(turn_ids[0], 1)
        # Never exceeds opening + max_turns.
        self.assertLessEqual(len(turn_ids), 1 + 8)

    def test_session_id_shape(self):
        captured = {}

        def fake_agent_turn(session_id, customer_text):
            captured.setdefault("session_id", session_id)
            return {"turn_id": 1, "agent_response": "x"}

        with mock.patch.object(sim_runner, "_agent_turn", side_effect=fake_agent_turn):
            sim_runner._run_one_conversation("abc", customer_sim.PERSONAS[1], 3, max_turns=2)
        self.assertTrue(captured["session_id"].startswith("sim-abc-3-"))
        self.assertIn(customer_sim.PERSONAS[1]["id"], captured["session_id"])
