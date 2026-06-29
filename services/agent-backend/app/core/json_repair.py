"""JSON extraction, missing key completion, and safe fallback for model output."""
import json
import re
import logging

from app.core.product_facts import normalize_next_action

logger = logging.getLogger(__name__)

REQUIRED_KEYS = {
    "intent",
    "emotion",
    "risk",
    "next_action",
    "behavior_strategy",
    "allowed_to_continue",
    "agent_response",
    "voice_style",
}

SAFE_FALLBACK_POLICY: dict = {
    "intent": "unknown",
    "emotion": "neutral",
    "risk": "low",
    "next_action": "clarify_unclear_info",
    "behavior_strategy": "safe_fallback",
    "allowed_to_continue": True,
    "agent_response": (
        "Entschuldigung, ich habe Sie nicht ganz verstanden. "
        "Könnten Sie das bitte wiederholen?"
    ),
    "voice_style": {"tone": "calm", "pace": "slow", "confidence": "medium"},
}


def extract_json(raw_text: str) -> dict:
    if not raw_text or not raw_text.strip():
        logger.warning("json_repair: empty model output, using fallback")
        return dict(SAFE_FALLBACK_POLICY)

    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw_text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    brace_match = re.search(r"\{.*\}", raw_text, re.DOTALL)
    if brace_match:
        try:
            return json.loads(brace_match.group(0))
        except json.JSONDecodeError:
            pass

    try:
        return json.loads(raw_text.strip())
    except json.JSONDecodeError:
        logger.warning("json_repair: JSON parse failed, using fallback")
        return dict(SAFE_FALLBACK_POLICY)


def repair(policy: dict) -> dict:
    repaired = dict(policy)
    needs_repair = False

    for key in REQUIRED_KEYS:
        if key not in repaired:
            repaired[key] = SAFE_FALLBACK_POLICY[key]
            needs_repair = True
            logger.info("json_repair: missing key filled — %s", key)

    if not isinstance(repaired.get("voice_style"), dict):
        repaired["voice_style"] = SAFE_FALLBACK_POLICY["voice_style"]
        needs_repair = True

    if not isinstance(repaired.get("allowed_to_continue"), bool):
        repaired["allowed_to_continue"] = True
        needs_repair = True

    if not repaired.get("agent_response", "").strip():
        repaired["agent_response"] = SAFE_FALLBACK_POLICY["agent_response"]
        needs_repair = True

    repaired["next_action"] = normalize_next_action(repaired.get("next_action", ""))

    if needs_repair:
        logger.info("json_repair: policy repaired")

    return repaired
