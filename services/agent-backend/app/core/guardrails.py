"""Deterministik guardrail kuralları.

Model çıktısını alır, iş kurallarına göre düzeltir ve güvenli policy döndürür.
Kritik ürün yanıtları burada template olarak uygulanır.
"""
import logging
from typing import Any

from app.core.product_facts import PRICE_TEMPLATE, SECURITY_TEMPLATE

logger = logging.getLogger(__name__)

# Kaç kez aynı next_action tekrar ederse yön değiştirilir
MAX_REPEATED_ACTION = 3


def apply(policy: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    """Tüm guardrail kurallarını sırayla uygular."""
    p = dict(policy)

    p = _rule_hard_decline(p, state)
    p = _rule_identity_before_link(p, state)
    p = _rule_price_template(p)
    p = _rule_security_template(p)
    p = _rule_loop_detection(p, state)

    return p


# ── Kurallar ────────────────────────────────────────────────────────────────

def _rule_hard_decline(policy: dict, state: dict) -> dict:
    """İki veya daha fazla hard decline → kapanışa zorla."""
    hard_decline_count = state.get("hard_decline_count", 0)
    intent = policy.get("intent", "")

    # Mevcut tur da hard decline ise sayacı artır
    current_count = hard_decline_count + (1 if intent == "hard_decline" else 0)

    if current_count >= 2:
        logger.info("guardrail: hard_decline >= 2 → close_call")
        policy["next_action"] = "close_call"
        policy["behavior_strategy"] = "graceful_exit"
        policy["allowed_to_continue"] = False

    return policy


def _rule_identity_before_link(policy: dict, state: dict) -> dict:
    """Kimlik doğrulanmadıysa aktivasyon linki gönderilmesini engelle."""
    if policy.get("next_action") == "send_activation_link":
        if not state.get("identity_confirmed", False):
            logger.info("guardrail: kimlik doğrulanmadı → send_activation_link bloke")
            policy["next_action"] = "confirm_identity"
            policy["behavior_strategy"] = "ask_for_name_first"
            policy["agent_response"] = (
                "Bevor ich Ihnen den Link sende, darf ich kurz Ihren Namen bestätigen?"
            )
    return policy


def _rule_price_template(policy: dict) -> dict:
    """Fiyat sorusunda onaylı şablonu kullan — model yanıtına güvenilmez."""
    intent = policy.get("intent", "")
    next_action = policy.get("next_action", "")

    if intent in ("price_question", "free_question") or next_action in (
        "explain_price", "explain_trial"
    ):
        logger.info("guardrail: price template uygulandı")
        policy["agent_response"] = PRICE_TEMPLATE
        policy["next_action"] = "explain_price"

    return policy


def _rule_security_template(policy: dict) -> dict:
    """Güvenlik itirazında onaylı şablonu kullan."""
    intent = policy.get("intent", "")
    next_action = policy.get("next_action", "")

    if intent == "security_objection" or next_action == "address_security":
        logger.info("guardrail: security template uygulandı")
        policy["agent_response"] = SECURITY_TEMPLATE
        policy["next_action"] = "address_security"

    return policy


def _rule_loop_detection(policy: dict, state: dict) -> dict:
    """Aynı next_action MAX_REPEATED_ACTION kez tekrar ederse yön değiştir."""
    history: list = state.get("last_next_actions", [])
    next_action = policy.get("next_action", "")

    if len(history) >= MAX_REPEATED_ACTION:
        recent = history[-MAX_REPEATED_ACTION:]
        if all(a == next_action for a in recent):
            logger.info(
                "guardrail: next_action '%s' %d kez tekrar etti → reframe_offer",
                next_action,
                MAX_REPEATED_ACTION,
            )
            policy["next_action"] = "reframe_offer"
            policy["behavior_strategy"] = "change_approach"

    return policy
