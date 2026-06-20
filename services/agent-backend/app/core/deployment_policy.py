"""Pure deployment evidence and lifecycle rules."""
from __future__ import annotations

from typing import Any


def deployment_evidence(metrics_json: dict | None) -> dict:
    metrics = metrics_json if isinstance(metrics_json, dict) else {}
    evidence = metrics.get("deployment_evidence")
    return evidence if isinstance(evidence, dict) else {}


def validate_deployment_evidence(
    metrics_json: dict | None,
    artifact: dict,
    serving_target: dict,
) -> None:
    evidence = deployment_evidence(metrics_json)
    if not evidence:
        raise ValueError(
            "Evaluation has no immutable deployment evidence; run evaluation again"
        )
    if evidence.get("artifact_sha256") != artifact.get("sha256"):
        raise ValueError("Model artifact changed after evaluation; run evaluation again")
    if evidence.get("artifact_root") != artifact.get("root"):
        raise ValueError("Model artifact path changed after evaluation; run evaluation again")
    if evidence.get("serving_target") != serving_target:
        raise ValueError("Serving target changed after evaluation; run evaluation again")


def deployment_state(
    environments: list[str],
    metadata: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    metadata = dict(metadata)
    environments = sorted(set(environments))
    if environments:
        deployment_status = f"active_{'_'.join(environments)}"
        metadata["lifecycle_status"] = "deployed"
        metadata["deployment_environments"] = environments
        if len(environments) == 1:
            metadata["deployment_environment"] = environments[0]
        else:
            metadata.pop("deployment_environment", None)
    else:
        deployment_status = "inactive"
        metadata["lifecycle_status"] = "retired"
        metadata.pop("deployment_environment", None)
        metadata["deployment_environments"] = []
    return deployment_status, metadata
