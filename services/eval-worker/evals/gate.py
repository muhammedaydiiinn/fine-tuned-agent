"""Versioned deployment gate for model evaluation results."""
from __future__ import annotations

from typing import Any

POLICY_VERSION = "m6-gate-v1"


def evaluate(metrics: dict[str, float], thresholds: dict[str, float]) -> dict[str, Any]:
    checks: dict[str, dict[str, Any]] = {}
    for metric, minimum in thresholds.items():
        actual = float(metrics.get(metric, 0.0))
        checks[metric] = {
            "operator": ">=",
            "threshold": minimum,
            "actual": actual,
            "passed": actual >= minimum,
        }

    loop_maximum = float(thresholds.get("loop_repetition_rate_max", 0.0))
    if "loop_repetition_rate_max" in thresholds:
        actual_loop = float(metrics.get("loop_repetition_rate", 1.0))
        checks["loop_repetition_rate"] = {
            "operator": "<=",
            "threshold": loop_maximum,
            "actual": actual_loop,
            "passed": actual_loop <= loop_maximum,
        }
        checks.pop("loop_repetition_rate_max", None)

    return {
        "policy_version": POLICY_VERSION,
        "passed": all(check["passed"] for check in checks.values()),
        "checks": checks,
    }
