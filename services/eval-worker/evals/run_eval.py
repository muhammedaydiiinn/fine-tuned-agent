"""Run fixed scenarios through the real agent backend pipeline."""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Callable

import httpx

from evals.metrics import compute, quality_score
from evals.scenario_catalog import get_scenario_catalog

ProgressCallback = Callable[[int, int, str], None]


def _load_single_turn_scenarios(path: str | Path) -> list[dict[str, Any]]:
    scenarios: list[dict[str, Any]] = []
    with Path(path).open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                scenario = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL at line {line_number}: {exc}") from exc
            if not scenario.get("id") or not scenario.get("customer_text"):
                raise ValueError(f"Scenario line {line_number} is missing id or customer_text")
            scenarios.append(scenario)
    return scenarios


def _post_turn(
    client: httpx.Client,
    endpoint: str,
    session_id: str,
    customer_text: str,
    state_before: dict[str, Any],
    *,
    expected_intent: str | None = None,
    expected_next_action: str | None = None,
    guardrail_check: str | None = None,
) -> dict[str, Any]:
    normalised_customer_text = " ".join(customer_text.casefold().split())
    result: dict[str, Any] = {
        "customer_text": customer_text,
        "state_before": state_before,
        "expected_intent": expected_intent,
        "expected_next_action": expected_next_action,
        "guardrail_check": guardrail_check,
        "link_requested": any(phrase in normalised_customer_text for phrase in (
            "schicken sie mir den sicheren link",
            "schicken sie mir den link",
            "ich öffne den link",
        )),
        "request_ok": False,
    }
    try:
        response = client.post(
            endpoint,
            json={"session_id": session_id, "customer_text": customer_text},
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("Agent response must be a JSON object")

        policy = payload.get("policy") if isinstance(payload.get("policy"), dict) else {}
        state_after = payload.get("state") if isinstance(payload.get("state"), dict) else {}
        latency = payload.get("latency") if isinstance(payload.get("latency"), dict) else {}
        result.update({
            "request_ok": True,
            "response": payload,
            "actual_intent": policy.get("intent"),
            "actual_next_action": policy.get("next_action"),
            "allowed_to_continue": policy.get("allowed_to_continue"),
            "agent_response": payload.get("agent_response"),
            "state_after": state_after,
            "latency_ms": latency.get("total_ms"),
        })
    except (httpx.HTTPError, json.JSONDecodeError, ValueError) as exc:
        result["error"] = str(exc)
        result["state_after"] = state_before
    return result


def run(
    model_version_id: int,
    agent_backend_url: str,
    scenarios_path: str | Path,
    progress_cb: ProgressCallback | None = None,
    *,
    eval_run_id: int | None = None,
    api_key: str = "",
    timeout_seconds: float = 45.0,
    model_version_header: int | None = None,
) -> dict[str, Any]:
    single_scenarios = _load_single_turn_scenarios(scenarios_path)
    multi_scenarios = get_scenario_catalog()
    total = len(single_scenarios) + len(multi_scenarios)
    completed = 0
    results: list[dict[str, Any]] = []
    run_token = f"{eval_run_id or model_version_id}-{uuid.uuid4().hex[:10]}"
    headers = {"X-API-Key": api_key} if api_key else {}
    if model_version_header is not None:
        headers["X-Eval-Model-Version-ID"] = str(model_version_header)
    endpoint = f"{agent_backend_url.rstrip('/')}/agent-turn"

    with httpx.Client(timeout=timeout_seconds, headers=headers) as client:
        for scenario in single_scenarios:
            session_id = f"eval-{run_token}-single-{scenario['id']}"
            turn = _post_turn(
                client,
                endpoint,
                session_id,
                scenario["customer_text"],
                {},
                expected_intent=scenario.get("expected_intent"),
                expected_next_action=scenario.get("expected_next_action"),
                guardrail_check=scenario.get("guardrail_check"),
            )
            turn.update({
                "kind": "single_turn",
                "scenario_id": scenario["id"],
                "session_id": session_id,
            })
            results.append(turn)
            completed += 1
            if progress_cb:
                progress_cb(completed, total, f"single:{scenario['id']}")

        for flow_id, customer_turns in multi_scenarios.items():
            session_id = f"eval-{run_token}-flow-{flow_id}"
            state: dict[str, Any] = {}
            flow_turns: list[dict[str, Any]] = []
            for turn_index, customer_text in enumerate(customer_turns):
                expected_intent = (
                    "hard_decline"
                    if flow_id == "decline" and turn_index == len(customer_turns) - 1
                    else None
                )
                turn = _post_turn(
                    client,
                    endpoint,
                    session_id,
                    customer_text,
                    state,
                    expected_intent=expected_intent,
                )
                turn["turn_index"] = turn_index
                flow_turns.append(turn)
                state = turn.get("state_after") or state

            results.append({
                "kind": "multi_turn",
                "scenario_id": flow_id,
                "session_id": session_id,
                "turns": flow_turns,
                "final_state": state,
                "request_ok": all(turn.get("request_ok") for turn in flow_turns),
            })
            completed += 1
            if progress_cb:
                progress_cb(completed, total, f"flow:{flow_id}")

        # Eval sessions remain auditable but must not look like live customer
        # conversations after the run has finished.
        for result in results:
            session_id = result.get("session_id")
            if not session_id:
                continue
            try:
                client.post(
                    f"{agent_backend_url.rstrip('/')}/sessions/{session_id}/close"
                )
            except httpx.HTTPError:
                pass

    metrics = compute(results)
    return {
        "eval_run_id": eval_run_id,
        "model_version_id": model_version_id,
        "scenario_count": total,
        "turn_count": sum(
            len(result.get("turns") or [])
            if result.get("kind") == "multi_turn"
            else 1
            for result in results
        ),
        "quality_score": quality_score(metrics),
        "metrics": metrics,
        "results": results,
    }
