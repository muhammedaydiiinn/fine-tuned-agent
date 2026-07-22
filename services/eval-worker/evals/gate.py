"""Versioned deployment gate for model evaluation results."""
from __future__ import annotations

from typing import Any

POLICY_VERSION = "m8-gate-v1"


def evaluate(metrics: dict[str, float], thresholds: dict[str, float]) -> dict[str, Any]:
    checks: dict[str, dict[str, Any]] = {}
    for key, bound in thresholds.items():
        if key.endswith("_max"):
            # Upper-bound metric (lower is better): loop / response repetition rates.
            metric = key[: -len("_max")]
            actual = float(metrics.get(metric, 1.0))
            checks[metric] = {
                "operator": "<=",
                "threshold": float(bound),
                "actual": actual,
                "passed": actual <= float(bound),
            }
        else:
            actual = float(metrics.get(key, 0.0))
            checks[key] = {
                "operator": ">=",
                "threshold": float(bound),
                "actual": actual,
                "passed": actual >= float(bound),
            }

    return {
        "policy_version": POLICY_VERSION,
        "passed": all(check["passed"] for check in checks.values()),
        "checks": checks,
    }
