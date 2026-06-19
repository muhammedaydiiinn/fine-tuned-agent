"""Shared training candidate builder — builds a JSONL-compatible messages list from a turn.

Used by both routes/corrections.py and routes/training.py to avoid duplication.
"""
import json
import logging

from app.core.product_facts import format_for_prompt

logger = logging.getLogger(__name__)

SYSTEM_INSTRUCTION = (
    "You are an CallShield Gold Paket sales policy agent. "
    "Return ONLY a valid JSON policy object."
)


def build_candidate_from_turn(
    turn,
    corrected_response: str,
    corrected_next_action: str,
    correction_type: str = "response_correction",
    model_version: str = "unknown",
) -> dict:
    """Build a messages list from a Turn ORM object and corrected response data.

    The return value can be assigned directly to TrainingCandidate.messages_json.
    The caller is responsible for wrapping the result in a TrainingCandidate instance.
    """
    assistant_policy = {
        "intent": turn.intent or "unknown",
        "emotion": turn.emotion or "neutral",
        "risk": turn.risk or "low",
        "next_action": corrected_next_action or turn.next_action or "",
        "behavior_strategy": "corrected",
        "allowed_to_continue": (
            turn.allowed_to_continue if turn.allowed_to_continue is not None else True
        ),
        "agent_response": corrected_response or turn.agent_response or "",
        "voice_style": {"tone": "clear", "pace": "normal", "confidence": "high"},
    }

    messages = [
        {
            "role": "system",
            "content": SYSTEM_INSTRUCTION + "\n\n" + format_for_prompt(),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "customer_message": turn.customer_text,
                    "state": turn.state_before_json or {},
                },
                ensure_ascii=False,
            ),
        },
        {
            "role": "assistant",
            "content": json.dumps(assistant_policy, ensure_ascii=False),
        },
    ]

    metadata = {
        "source": "correction",
        "approved": True,
        "model_version": model_version,
        "correction_type": correction_type,
    }

    logger.debug("candidate built: turn_id=%s intent=%s", getattr(turn, "id", "?"), turn.intent)
    return {"messages": messages, "metadata": metadata}
