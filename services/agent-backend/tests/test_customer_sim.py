import pathlib
import sys
from unittest import TestCase, mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.core import customer_sim


def _target(**o):
    t = {"mode": "real", "base_url": "x", "model_name": "prod"}
    t.update(o)
    return t


class MockCustomerTests(TestCase):
    def test_scripted_customer_progresses_and_ends(self):
        persona = customer_sim.PERSONAS[0]  # price_sensitive
        transcript = [{"role": "agent", "text": "Guten Tag."}]
        seen = []
        for _ in range(10):
            r = customer_sim.next_customer_message(persona, transcript)
            if not r["text"]:
                break
            seen.append(r["text"])
            transcript.append({"role": "customer", "text": r["text"]})
            transcript.append({"role": "agent", "text": "..."})
            if r["done"]:
                break
        self.assertEqual(seen[0], "Was kostet das denn genau?")
        self.assertGreaterEqual(len(seen), 2)

    def test_get_personas_count(self):
        self.assertEqual(len(customer_sim.get_personas(3)), 3)
        self.assertEqual(len(customer_sim.get_personas(50)), 50)  # cycles


class RealCustomerGuardTests(TestCase):
    def test_agent_json_output_ends_conversation(self):
        with mock.patch.object(customer_sim.settings, "vllm_mode", "real"), \
             mock.patch("app.core.model_runtime.production_serving_target", return_value=_target()), \
             mock.patch("app.core.vllm_client.chat", return_value='{"agent_response":"x","next_action":"y"}'):
            r = customer_sim.next_customer_message(customer_sim.PERSONAS[0], [{"role": "agent", "text": "hi"}])
        self.assertTrue(r["done"])
        self.assertEqual(r["text"], "")

    def test_ende_marker_detected(self):
        with mock.patch.object(customer_sim.settings, "vllm_mode", "real"), \
             mock.patch("app.core.model_runtime.production_serving_target", return_value=_target()), \
             mock.patch("app.core.vllm_client.chat", return_value="Nein danke. [ENDE]"):
            r = customer_sim.next_customer_message(customer_sim.PERSONAS[0], [{"role": "agent", "text": "hi"}])
        self.assertTrue(r["done"])
        self.assertEqual(r["text"], "Nein danke.")

    def test_normal_reply(self):
        with mock.patch.object(customer_sim.settings, "vllm_mode", "real"), \
             mock.patch("app.core.model_runtime.production_serving_target", return_value=_target()), \
             mock.patch("app.core.vllm_client.chat", return_value="Was kostet das?"):
            r = customer_sim.next_customer_message(customer_sim.PERSONAS[0], [{"role": "agent", "text": "hi"}])
        self.assertFalse(r["done"])
        self.assertEqual(r["text"], "Was kostet das?")
