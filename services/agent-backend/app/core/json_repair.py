"""JSON extraction, missing key completion, and safe fallback for model output."""
import json
import re
import logging

logger = logging.getLogger(__name__)

# Required keys in a valid policy object
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
    "next_action": "clarify",
    "behavior_strategy": "safe_fallback",
    "allowed_to_continue": True,
    "agent_response": (
        "Entschuldigung, ich habe Sie nicht ganz verstanden. "
        "Könnten Sie das bitte wiederholen?"
    ),
    "voice_style": {"tone": "calm", "pace": "slow", "confidence": "medium"},
}


def extract_json(raw_text: str) -> dict:
    """Parse JSON from model output.

    Tries in order:
    1. ```json ... ``` code block
    2. First valid JSON object inside { ... }
    3. Raw parse
    """
    if not raw_text or not raw_text.strip():
        logger.warning("json_repair: empty model output, using fallback")
        return dict(SAFE_FALLBACK_POLICY)

    # 1. Markdown code block
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw_text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # 2. First { ... } block
    brace_match = re.search(r"\{.*\}", raw_text, re.DOTALL)
    if brace_match:
        try:
            return json.loads(brace_match.group(0))
        except json.JSONDecodeError:
            pass

    # 3. Raw parse
    try:
        return json.loads(raw_text.strip())
    except json.JSONDecodeError:
        logger.warning("json_repair: JSON parse failed, using fallback")
        return dict(SAFE_FALLBACK_POLICY)


def repair(policy: dict) -> dict:
    """Fill missing required keys with fallback values."""
    repaired = dict(policy)
    needs_repair = False

    for key in REQUIRED_KEYS:
        if key not in repaired:
            repaired[key] = SAFE_FALLBACK_POLICY[key]
            needs_repair = True
            logger.info("json_repair: missing key filled — %s", key)

    # voice_style must be a dict
    if not isinstance(repaired.get("voice_style"), dict):
        repaired["voice_style"] = SAFE_FALLBACK_POLICY["voice_style"]
        needs_repair = True

    # allowed_to_continue must be bool
    if not isinstance(repaired.get("allowed_to_continue"), bool):
        repaired["allowed_to_continue"] = True
        needs_repair = True

    # agent_response must not be empty
    if not repaired.get("agent_response", "").strip():
        repaired["agent_response"] = SAFE_FALLBACK_POLICY["agent_response"]
        needs_repair = True

    if needs_repair:
        logger.info("json_repair: policy repaired")

    return repaired
