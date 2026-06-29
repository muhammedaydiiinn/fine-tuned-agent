from unittest import TestCase
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.core.deployment_policy import (
    deployment_state,
    validate_deployment_artifact_evidence,
    validate_deployment_evidence,
)


class DeploymentEvidenceTests(TestCase):
    def setUp(self):
        self.target = {
            "mode": "real",
            "base_url": "http://candidate:8000/v1",
            "model_name": "candidate-v1",
            "slot": "green",
        }
        self.artifact = {
            "sha256": "artifact-checksum",
            "root": "/models/candidates/v1",
        }
        self.metrics = {
            "deployment_evidence": {
                "artifact_sha256": "artifact-checksum",
                "artifact_root": "/models/candidates/v1",
                "serving_target": self.target,
            },
        }

    def test_matching_evidence_is_accepted(self):
        validate_deployment_evidence(self.metrics, self.artifact, self.target)

    def test_artifact_mutation_is_rejected(self):
        changed = dict(self.artifact, sha256="changed")

        with self.assertRaisesRegex(ValueError, "artifact changed"):
            validate_deployment_evidence(self.metrics, changed, self.target)

    def test_serving_target_mutation_is_rejected(self):
        changed_target = dict(self.target, slot="blue")

        with self.assertRaisesRegex(ValueError, "Serving target changed"):
            validate_deployment_evidence(
                self.metrics,
                self.artifact,
                changed_target,
            )

    def test_artifact_evidence_allows_production_target_change(self):
        validate_deployment_artifact_evidence(self.metrics, self.artifact)

    def test_legacy_eval_without_evidence_is_rejected(self):
        metrics = {"deployment_gate": {"passed": True}}

        with self.assertRaisesRegex(ValueError, "no immutable deployment evidence"):
            validate_deployment_evidence(metrics, self.artifact, self.target)


class DeploymentStateTests(TestCase):
    def test_model_can_be_active_in_both_environments(self):
        status, metadata = deployment_state(
            ["staging", "production"],
            {"lifecycle_status": "approved", "deployment_environment": "staging"},
        )

        self.assertEqual(status, "active_production_staging")
        self.assertEqual(metadata["lifecycle_status"], "deployed")
        self.assertEqual(
            metadata["deployment_environments"],
            ["production", "staging"],
        )
        self.assertNotIn("deployment_environment", metadata)

    def test_removing_one_environment_keeps_model_deployed(self):
        status, metadata = deployment_state(
            ["production"],
            {"lifecycle_status": "deployed"},
        )

        self.assertEqual(status, "active_production")
        self.assertEqual(metadata["lifecycle_status"], "deployed")
        self.assertEqual(metadata["deployment_environment"], "production")

    def test_no_active_environment_retires_model(self):
        status, metadata = deployment_state(
            [],
            {"lifecycle_status": "deployed", "deployment_environment": "production"},
        )

        self.assertEqual(status, "inactive")
        self.assertEqual(metadata["lifecycle_status"], "retired")
        self.assertEqual(metadata["deployment_environments"], [])
        self.assertNotIn("deployment_environment", metadata)
