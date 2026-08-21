"""Correction memory — applies hotfixes to policy without model retraining.

When a correction is saved with apply_immediately=True, matching entries in
the correction_memory table override the policy on subsequent turns.

Safety rails (WP-7, after the 2026-08-18 incident where one live answer
replacement froze every call into the same sentence):
- An entry only fires in the situation it was created from: the (normalized)
  customer text must match exactly. An entry captured on the opening turn
  (empty customer text) fires only on openings, never mid-call.
- Entries expire (``expires_at``) — a hotfix is a bridge until retraining,
  not a permanent rule.
- A circuit breaker deactivates any entry that fires too often within 24h.
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session as DBSession

from app.config import settings

logger = logging.getLogger(__name__)

# An entry firing this many times inside the 24h window is a runaway rule.
TRIP_WINDOW = timedelta(hours=24)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize(text: str | None) -> str:
    return " ".join((text or "").lower().split())


def _is_expired(entry) -> bool:
    expires_at = getattr(entry, "expires_at", None)
    return expires_at is not None and expires_at <= _now()


def _active_entries(db: DBSession, trigger_key: str | None = None) -> list:
    from app.models import CorrectionMemory

    query = db.query(CorrectionMemory).filter(CorrectionMemory.active == True)  # noqa: E712
    if trigger_key is not None:
        query = query.filter(CorrectionMemory.trigger_key == trigger_key)
    entries = query.order_by(CorrectionMemory.priority.desc()).all()
    return [entry for entry in entries if not _is_expired(entry)]


def _hint(entry) -> dict:
    return {
        "entry_id": entry.id,
        "trigger_key": entry.trigger_key,
        "correct_response": entry.correct_response,
        "correct_next_action": entry.correct_next_action,
    }


def get_hints(db: DBSession, customer_text: str, state: dict[str, Any]) -> list[dict]:
    """Return active correction_memory entries that match the current turn."""
    hints = [
        _hint(entry)
        for entry in _active_entries(db)
        if _matches(entry, customer_text, state)
    ]
    if hints:
        logger.info("correction_memory: %d match(es) found", len(hints))
    return hints


def get_policy_hints(
    db: DBSession,
    policy: dict[str, Any],
    customer_text: str = "",
) -> list[dict]:
    """Return active corrections keyed to the repaired policy intent.

    Intent alone is far too broad (the 08-18 incident rule was keyed to
    ``interested`` and hijacked every call), so the entry's captured
    customer text must also match the current turn exactly.
    """
    intent = (policy.get("intent") or "").strip().lower()
    if not intent:
        return []
    text_now = _normalize(customer_text)
    hints = []
    for entry in _active_entries(db, trigger_key=intent):
        context: dict = entry.context_json or {}
        if _normalize(context.get("customer_text")) == text_now:
            hints.append(_hint(entry))
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

    if best.get("entry_id") is not None:
        p["_correction_entry_id"] = best["entry_id"]

    return p


def record_trigger(db: DBSession, entry_id: int) -> None:
    """Count one firing of an entry; trip the breaker on runaway rules.

    Called after an override was actually applied. The count resets when the
    last firing is older than the 24h window; reaching the trip threshold
    deactivates the entry so a bad hotfix can hijack at most a handful of
    turns instead of every call for days.
    """
    from app.models import CorrectionMemory

    entry = db.query(CorrectionMemory).filter(CorrectionMemory.id == entry_id).first()
    if entry is None:
        return
    now = _now()
    last = entry.last_triggered_at
    if last is None or (now - last) > TRIP_WINDOW:
        entry.trigger_count = 1
    else:
        entry.trigger_count = (entry.trigger_count or 0) + 1
    entry.last_triggered_at = now
    if entry.trigger_count >= settings.correction_memory_trip_count:
        entry.active = False
        context = dict(entry.context_json or {})
        context["deactivated_reason"] = (
            f"circuit_breaker: {entry.trigger_count} firings within 24h"
        )
        entry.context_json = context
        logger.warning(
            "correction_memory circuit breaker tripped: id=%d trigger=%s count=%d -> deactivated",
            entry.id,
            entry.trigger_key,
            entry.trigger_count,
        )
    db.commit()


def _matches(entry, customer_text: str, state: dict[str, Any]) -> bool:
    """Exact captured-context match, or an explicit hand-written trigger phrase.

    The legacy ``context.intent == state.last_intent`` fallback was removed:
    matching a whole intent class made one bad correction global.
    """
    text_lower = _normalize(customer_text)

    context: dict = entry.context_json or {}
    if _normalize(context.get("customer_text")) == text_lower:
        return True

    # Hand-written trigger phrases still work as substring matches, but a bare
    # intent name must not act as one (it would re-open the incident path).
    from app.core.product_facts import CANONICAL_INTENTS

    trigger = (entry.trigger_key or "").lower()
    if trigger and trigger not in CANONICAL_INTENTS and trigger in text_lower:
        return True

    return False
