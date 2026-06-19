"""Correction memory — applies hotfixes to policy without model retraining.

When a correction is saved with apply_immediately=True, matching entries in
the correction_memory table override the policy on subsequent turns.
"""
import logging
from typing import Any

from sqlalchemy.orm import Session as DBSession

logger = logging.getLogger(__name__)


def get_hints(db: DBSession, customer_text: str, state: dict[str, Any]) -> list[dict]:
    """Return active correction_memory entries that match the current turn."""
    from app.models import CorrectionMemory

    active_entries = (
        db.query(CorrectionMemory)
        .filter(CorrectionMemory.active == True)  # noqa: E712
        .order_by(CorrectionMemory.priority.desc())
        .all()
    )

    hints = []
    for entry in active_entries:
        if _matches(entry, customer_text, state):
            hints.append({
                "trigger_key": entry.trigger_key,
                "correct_response": entry.correct_response,
                "correct_next_action": entry.correct_next_action,
            })

    if hints:
        logger.info("correction_memory: %d match(es) found", len(hints))

    return hints


def apply_override(
    policy: dict[str, Any],
    hints: list[dict],
) -> dict[str, Any]:
    """Override policy fields using matched correction_memory entries.

    When multiple entries match, the first (highest priority) wins.
    """
    if not hints:
        return policy

    best = hints[0]
    p = dict(policy)

    if best.get("correct_response"):
        logger.info("correction_memory override: agent_response replaced")
        p["agent_response"] = best["correct_response"]
        p["behavior_strategy"] = "correction_memory_override"

    if best.get("correct_next_action"):
        logger.info("correction_memory override: next_action -> %s", best["correct_next_action"])
        p["next_action"] = best["correct_next_action"]

    return p


def _matches(entry, customer_text: str, state: dict[str, Any]) -> bool:
    """Check if the trigger key appears in customer text or state intent."""
    trigger = (entry.trigger_key or "").lower()
    text_lower = customer_text.lower()

    if trigger and trigger in text_lower:
        return True

    context: dict = entry.context_json or {}
    if context.get("intent") and context["intent"] == state.get("last_intent"):
        return True

    return False
