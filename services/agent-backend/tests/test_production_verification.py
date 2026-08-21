"""WP-1: unverified/bootstrap production models must be flagged, never silent."""
import pathlib
import sys
from types import SimpleNamespace
from unittest import TestCase

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.routes.health import production_verification


def _deployment(action, eval_status, model_meta=None):
    return SimpleNamespace(
        metadata_json={"action": action},
        model_version=SimpleNamespace(eval_status=eval_status, metadata_json=model_meta or {}),
    )


class ProductionVerificationTests(TestCase):
    def test_gated_deploy_is_verified(self):
        verified, warning = production_verification(_deployment("deploy", "passed"))
        self.assertTrue(verified)
        self.assertIsNone(warning)

    def test_bootstrap_without_gate_warns(self):
        verified, warning = production_verification(_deployment("bootstrap", "pending"))
        self.assertFalse(verified)
        self.assertIn("bootstrap", warning)

    def test_bootstrap_with_passing_gate_is_verified(self):
        verified, warning = production_verification(_deployment("bootstrap", "passed"))
        self.assertTrue(verified)

    def test_failed_gate_on_serving_model_alarms(self):
        verified, warning = production_verification(
            _deployment("deploy", "failed", {"gate_alert": {"eval_run_id": 40}})
        )
        self.assertFalse(verified)
        self.assertIn("KALDI", warning)
        self.assertIn("40", warning)

    def test_no_deployment_is_neutral(self):
        self.assertEqual(production_verification(None), (None, None))
