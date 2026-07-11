import json
import pathlib
import sys
from unittest import TestCase, mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from jobs import build_dataset


def _msgs(customer_message="Hallo", agent_response="Guten Tag.", customer_name=None, system="OLD SYSTEM"):
    state = {}
    if customer_name is not None:
        state["customer_name"] = customer_name
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps({"customer_message": customer_message, "state": state})},
        {"role": "assistant", "content": json.dumps({"agent_response": agent_response, "intent": "x"})},
    ]


class ForceCanonicalTests(TestCase):
    def setUp(self):
        p = mock.patch.object(build_dataset, "_canonical_system_content", return_value="CANON")
        p.start()
        self.addCleanup(p.stop)

    def test_replaces_existing_system(self):
        out = build_dataset._force_canonical_system(_msgs(system="DIVERGENT"))
        self.assertEqual(out[0], {"role": "system", "content": "CANON"})
        self.assertEqual(out[1]["role"], "user")

    def test_prepends_when_missing(self):
        no_system = [{"role": "user", "content": "{}"}, {"role": "assistant", "content": "{}"}]
        out = build_dataset._force_canonical_system(no_system)
        self.assertEqual(out[0], {"role": "system", "content": "CANON"})
        self.assertEqual(len(out), 3)


class SyntheticFilterTests(TestCase):
    def test_customer_name_gpt_is_synthetic(self):
        self.assertTrue(build_dataset._is_synthetic(_msgs(customer_name="gpt")))
        self.assertTrue(build_dataset._is_synthetic(_msgs(customer_name="GPT")))

    def test_empty_assistant_is_synthetic(self):
        self.assertTrue(build_dataset._is_synthetic(_msgs(agent_response="")))

    def test_empty_customer_message_alone_is_kept(self):
        # Legitimate opening turn: empty customer_message but a real response.
        self.assertFalse(build_dataset._is_synthetic(_msgs(customer_message="", agent_response="Guten Tag.")))

    def test_normal_turn_is_kept(self):
        self.assertFalse(build_dataset._is_synthetic(_msgs()))

    def test_too_short_is_kept(self):
        self.assertFalse(build_dataset._is_synthetic([{"role": "user", "content": "{}"}]))
