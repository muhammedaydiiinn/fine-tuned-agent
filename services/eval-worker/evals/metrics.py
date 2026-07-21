"""Deterministic metrics for single-turn and multi-turn evaluation results."""
from __future__ import annotations

import math
from statistics import mean
from typing import Any

PRICE_TEMPLATE = (
    "Das Gold Paket ist 14 Tage kostenlos. "
    "Danach kostet es 29,99 Euro monatlich."
)
SECURITY_TEMPLATE = (
    "Nein, das ist kein Virus-Link. "
    "Der Link führt nur zum offiziellen Apple App Store oder Google Play Store."
)


import re as _re

# Euro amounts the agent may state (monthly price, legal cover, check price).
_ALLOWED_EURO_AMOUNTS = {"29.99", "2500", "18"}
_EURO_AMOUNT_RE = _re.compile(r"\d+(?:\.\d+)?(?=\s*(?:euro|eur|€))", _re.IGNORECASE)


def _price_answer_correct(response: str) -> bool:
    """Mirror agent-backend guardrails.price_answer_is_unsafe (inverted)."""
    msg = (response or "").strip()
    if not msg:
        return False
    compact = msg.replace(".", "").replace(",", ".")
    return all(a in _ALLOWED_EURO_AMOUNTS for a in _EURO_AMOUNT_RE.findall(compact))


def _canned_answer(key: str, default: str) -> str:
    """Effective (panel-edited) canned answer from the DB; falls back to `default`.

    Read once per metric computation so template-adherence scoring aligns with
    the wording the live agent is actually served. Any DB/table issue silently
    falls back to the packaged constant.
    """
    try:
        from db import SessionLocal
        from models import PolicyContent

        db = SessionLocal()
        try:
            row = (
                db.query(PolicyContent)
                .filter(PolicyContent.section == "canned_answers")
                .first()
            )
        finally:
            db.close()
        if row and isinstance(row.value_json, dict):
            value = row.value_json.get(key)
            if isinstance(value, str) and value.strip():
                return value
    except Exception:
        pass
    return default
REQUIRED_RESPONSE_PATHS = (
    ("agent_response",),
    ("policy", "intent"),
    ("policy", "next_action"),
    ("policy", "risk"),
    ("policy", "allowed_to_continue"),
    ("state",),
    ("latency", "total_ms"),
)


def _all_turns(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    turns: list[dict[str, Any]] = []
    for result in results:
        if result.get("kind") == "multi_turn":
            turns.extend(result.get("turns") or [])
        else:
            turns.append(result)
    return turns


def _path_present(value: dict[str, Any], path: tuple[str, ...]) -> bool:
    current: Any = value
    for key in path:
        if not isinstance(current, dict) or key not in current or current[key] is None:
            return False
        current = current[key]
    return True


def _rate(values: list[bool], *, empty: float = 0.0) -> float:
    if not values:
        return empty
    return round(sum(values) / len(values), 4)


def _normalise_text(value: str) -> str:
    return " ".join((value or "").casefold().split())


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(0, math.ceil(percentile * len(ordered)) - 1)
    return ordered[rank]


def compute(results: list[dict[str, Any]]) -> dict[str, float]:
    turns = _all_turns(results)

    valid_turns = [
        bool(turn.get("request_ok")) and isinstance(turn.get("response"), dict)
        for turn in turns
    ]

    coverage: list[float] = []
    for turn in turns:
        response = turn.get("response")
        if not isinstance(response, dict):
            coverage.append(0.0)
            continue
        present = sum(_path_present(response, path) for path in REQUIRED_RESPONSE_PATHS)
        coverage.append(present / len(REQUIRED_RESPONSE_PATHS))

    action_checks = [
        turn.get("actual_next_action") == turn.get("expected_next_action")
        for turn in results
        if turn.get("kind") == "single_turn" and turn.get("expected_next_action")
    ]

    decline_checks: list[bool] = []
    for turn in turns:
        if turn.get("expected_intent") != "hard_decline":
            continue
        action = turn.get("actual_next_action")
        allowed = turn.get("allowed_to_continue")
        decline_count = int((turn.get("state_after") or {}).get("hard_decline_count", 0))
        if decline_count >= 2:
            decline_checks.append(action == "close_call" and allowed is False)
        else:
            decline_checks.append(action in {"acknowledge_objection", "close_call"})

    identity_checks: list[bool] = []
    for turn in turns:
        if not turn.get("link_requested"):
            continue
        identity_confirmed = bool((turn.get("state_before") or {}).get("identity_confirmed"))
        action = turn.get("actual_next_action")
        identity_checks.append(
            action == "send_activation_link"
            if identity_confirmed
            else action != "send_activation_link"
        )

    price_checks: list[bool] = []
    security_checks: list[bool] = []
    security_tpl = _normalise_text(_canned_answer("security", SECURITY_TEMPLATE))
    for turn in turns:
        response_text = _normalise_text(turn.get("agent_response") or "")
        guardrail_check = turn.get("guardrail_check")
        if guardrail_check == "price_template" or turn.get("actual_intent") in {
            "price_question",
            "free_question",
        }:
            # Correct = states no wrong Euro amount (mirrors the runtime price
            # guardrail). A nuanced, factually-correct answer counts as correct;
            # we no longer require an exact template match.
            price_checks.append(_price_answer_correct(turn.get("agent_response") or ""))
        if guardrail_check == "security_template" or turn.get("actual_intent") == "security_objection":
            security_checks.append(response_text == security_tpl)

    # Opening greeting: on connect (empty customer_text) the agent must greet and
    # introduce itself, not jump into a mid-call answer (e.g. price). Catches the
    # regression where a fine-tune erodes the opening behavior.
    greeting_checks: list[bool] = []
    for turn in results:
        if turn.get("kind") != "opening":
            continue
        resp = _normalise_text(turn.get("agent_response") or "").lower()
        intent_ok = turn.get("actual_intent") == "greeting"
        looks_greeting = any(
            token in resp for token in ("guten tag", "guten morgen", "hallo", "anna weber")
        )
        greeting_checks.append(bool(intent_ok and looks_greeting))

    loop_windows = 0
    repeated_windows = 0
    for result in results:
        if result.get("kind") != "multi_turn":
            continue
        actions = [turn.get("actual_next_action") for turn in result.get("turns") or []]
        for index in range(3, len(actions)):
            window = actions[index - 3:index + 1]
            if all(window):
                loop_windows += 1
                if len(set(window)) == 1:
                    repeated_windows += 1

    latencies = [
        float(turn["latency_ms"])
        for turn in turns
        if isinstance(turn.get("latency_ms"), (int, float))
    ]

    return {
        "json_validity_rate": _rate(valid_turns),
        "required_key_coverage": round(mean(coverage), 4) if coverage else 0.0,
        "next_action_accuracy": _rate(action_checks),
        "hard_decline_handling": _rate(decline_checks),
        "identity_before_link_pass": _rate(identity_checks, empty=1.0),
        "price_answer_correctness": _rate(price_checks),
        "security_objection_correctness": _rate(security_checks),
        "greeting_correctness": _rate(greeting_checks, empty=1.0),
        "loop_repetition_rate": round(repeated_windows / loop_windows, 4) if loop_windows else 0.0,
        "latency_avg": round(mean(latencies), 2) if latencies else 0.0,
        "latency_p95": round(_percentile(latencies, 0.95), 2),
    }


def quality_score(metrics: dict[str, float]) -> float:
    """Return a 0..1 score. Latency is reported but not mixed into quality."""
    positive = (
        "json_validity_rate",
        "required_key_coverage",
        "next_action_accuracy",
        "hard_decline_handling",
        "identity_before_link_pass",
        "price_answer_correctness",
        "security_objection_correctness",
        "greeting_correctness",
    )
    values = [float(metrics.get(key, 0.0)) for key in positive]
    values.append(1.0 - min(1.0, float(metrics.get("loop_repetition_rate", 1.0))))
    return round(mean(values), 4)
