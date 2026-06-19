"""Correction memory — anlık düzeltmelerin modelsiz uygulanması.

Bir correction apply_immediately=True ile kaydedildiğinde, benzer durumlarda
correction_memory tablosundan eşleşen giriş policy'yi override eder.
"""
import logging
from typing import Any

from sqlalchemy.orm import Session as DBSession

logger = logging.getLogger(__name__)


def get_hints(db: DBSession, customer_text: str, state: dict[str, Any]) -> list[dict]:
    """Aktif correction_memory kayıtlarını döndürür (prompt ipucu olarak kullanılır)."""
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
        logger.info("correction_memory: %d eşleşme bulundu", len(hints))

    return hints


def apply_override(
    policy: dict[str, Any],
    hints: list[dict],
) -> dict[str, Any]:
    """Eşleşen correction_memory girişleri varsa policy'yi override eder.

    Birden fazla eşleşme varsa en önce gelen (yüksek priority) kazanır.
    """
    if not hints:
        return policy

    best = hints[0]
    p = dict(policy)

    if best.get("correct_response"):
        logger.info("correction_memory override: agent_response değiştirildi")
        p["agent_response"] = best["correct_response"]
        p["behavior_strategy"] = "correction_memory_override"

    if best.get("correct_next_action"):
        logger.info("correction_memory override: next_action → %s", best["correct_next_action"])
        p["next_action"] = best["correct_next_action"]

    return p


def _matches(entry, customer_text: str, state: dict[str, Any]) -> bool:
    """Trigger key, müşteri metninde ya da state intent'te geçiyor mu?"""
    trigger = (entry.trigger_key or "").lower()
    text_lower = customer_text.lower()

    # Doğrudan anahtar kelime eşleşmesi
    if trigger and trigger in text_lower:
        return True

    # Context JSON'da belirtilen intent eşleşmesi
    context: dict = entry.context_json or {}
    if context.get("intent") and context["intent"] == state.get("last_intent"):
        return True

    return False
