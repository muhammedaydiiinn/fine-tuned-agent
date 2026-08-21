"""Shared training candidate builder — builds a JSONL-compatible messages list from a turn.

Used by both routes/corrections.py and routes/training.py to avoid duplication.
"""
import json
import logging

from app.core import prompt_builder
from app.core.policy_prompt import build_system_content

logger = logging.getLogger(__name__)


def _prior_turns(turn) -> list:
    """The session turns before this one (newest 8), oldest-first.

    Uses the ORM object's own session; returns [] for detached/test objects so
    unit tests and ad-hoc builders keep working without a DB.
    """
    try:
        from sqlalchemy.orm import object_session

        from app.models import Turn

        db = object_session(turn)
        if db is None or turn.session_id is None:
            return []
        rows = (
            db.query(Turn)
            .filter(Turn.session_id == turn.session_id, Turn.turn_index < turn.turn_index)
            .order_by(Turn.turn_index.desc())
            .limit(8)
            .all()
        )
        return rows[::-1]
    except Exception:  # noqa: BLE001 — history is an enrichment, never a blocker
        logger.exception("candidate_builder: prior-turn lookup failed")
        return []


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

    Training examples must look exactly like inference prompts (WP-3), so the
    same conversation history the model saw live is rebuilt from the turn's
    session — the turns before this one, newest 8.
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

    state = turn.state_before_json or {}
    recent_turns = _prior_turns(turn)
    user_payload = prompt_builder.build_user_payload(turn.customer_text, state, recent_turns)
    messages = [
        {
            "role": "system",
            "content": build_system_content(state.get("agent_name"), state.get("agent_role")),
        },
        {
            "role": "user",
            "content": json.dumps(user_payload, ensure_ascii=False),
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
