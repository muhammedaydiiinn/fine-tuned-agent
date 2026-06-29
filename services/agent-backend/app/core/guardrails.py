"""Deterministic guardrail rules — PDF-aligned enforcement on model output."""
import logging
from typing import Any

from app.core.product_facts import (
    CLOSING_NEXT_ACTIONS,
    DELAY_DEFERRAL_TEMPLATE,
    FORBIDDEN_DATA_TEMPLATE,
    IDENTITY_NEXT_ACTIONS,
    PRICE_TEMPLATE,
    SECURITY_TEMPLATE,
    normalize_next_action,
)

logger = logging.getLogger(__name__)

MAX_REPEATED_ACTION = 3

PRICE_QUESTION_TOKENS = [
    "was kostet", "wie teuer", "preis danach", "und der preis",
    "was zahle ich", "kosten danach", "monatlich", "gratisfase",
    "noch einmal die", "den preis noch",
]


def _lower(text: str) -> str:
    return " ".join((text or "").lower().split())


def customer_wants_delay(text: str) -> bool:
    msg = _lower(text)
    return any(
        p in msg
        for p in (
            "zeit nehmen", "brauche zeit", "brauche etwas zeit",
            "in ruhe überlegen", "überlegen", "bedenkzeit", "später",
            "nicht jetzt", "melde mich", "genug informationen",
        )
    )


def is_closing_price_question(customer_message: str) -> bool:
    msg = (customer_message or "").lower()
    return any(t in msg for t in PRICE_QUESTION_TOKENS)


def customer_asked_price_or_trial(text: str) -> bool:
    msg = (text or "").lower()
    return any(p in msg for p in [
        "was kostet", "kostet", "preis", "monatlich", "euro",
        "nach 14 tagen", "testphase", "probezeit", "14 tagen",
    ])


def apply(policy: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    return apply_with_context(policy, state, customer_message="")


def apply_with_context(
    policy: dict[str, Any],
    state: dict[str, Any],
    customer_message: str = "",
) -> dict[str, Any]:
    p = dict(policy)
    p["next_action"] = normalize_next_action(p.get("next_action", ""))
    p = _rule_hard_decline(p, state)
    p = _rule_identity_before_link(p, state)
    p = _rule_delay_no_phone_collection(p, customer_message)
    p = _rule_price_template(p)
    p = _rule_security_template(p)
    p = _rule_closing_flags(p)
    p = _rule_loop_detection(p, state)
    return p


def _rule_hard_decline(policy: dict, state: dict) -> dict:
    hard_decline_count = state.get("hard_decline_count", 0)
    intent = policy.get("intent", "")
    current_count = hard_decline_count + (1 if intent == "hard_decline" else 0)
    if current_count >= 3:
        logger.info("guardrail: hard_decline >= 3 -> close_call")
        policy["next_action"] = "close_call"
        policy["behavior_strategy"] = "graceful_exit"
        policy["allowed_to_continue"] = False
    return policy


def _rule_identity_before_link(policy: dict, state: dict) -> dict:
    if policy.get("next_action") == "send_activation_link":
        if not state.get("identity_confirmed", False):
            logger.info("guardrail: identity not confirmed -> blocking send_activation_link")
            policy["next_action"] = "qualify_lead"
            policy["behavior_strategy"] = "ask_for_name_first"
            policy["agent_response"] = (
                "Bevor ich Ihnen den Link sende, darf ich kurz Ihren Namen bestätigen?"
            )
    return policy


def _rule_delay_no_phone_collection(policy: dict, customer_message: str) -> dict:
    if not customer_wants_delay(customer_message):
        return policy

    canonical = normalize_next_action(policy.get("next_action", ""))
    response = _lower(policy.get("agent_response", ""))
    if canonical in IDENTITY_NEXT_ACTIONS or "telefonnummer" in response:
        logger.info("guardrail: delay -> block phone/identity collection")
        policy["next_action"] = "handle_objection"
        policy["behavior_strategy"] = "empathize_redirect"
        policy["allowed_to_continue"] = True
        policy["agent_response"] = DELAY_DEFERRAL_TEMPLATE
    return policy


def _rule_closing_flags(policy: dict) -> dict:
    if normalize_next_action(policy.get("next_action", "")) in CLOSING_NEXT_ACTIONS:
        policy["allowed_to_continue"] = False
        policy["behavior_strategy"] = policy.get("behavior_strategy") or "respect_decline"
    return policy


def _rule_price_template(policy: dict) -> dict:
    intent = policy.get("intent", "")
    next_action = normalize_next_action(policy.get("next_action", ""))
    if intent in ("price_question", "free_question") or next_action == "explain_offer_terms":
        logger.info("guardrail: PDF price template applied")
        policy["agent_response"] = PRICE_TEMPLATE
        policy["next_action"] = "explain_offer_terms"
    return policy


def _rule_security_template(policy: dict) -> dict:
    intent = policy.get("intent", "")
    if intent == "security_objection":
        logger.info("guardrail: PDF security template applied")
        policy["agent_response"] = SECURITY_TEMPLATE
        policy["next_action"] = "handle_objection"
    return policy


def _rule_loop_detection(policy: dict, state: dict) -> dict:
    history: list = state.get("last_next_actions", [])
    next_action = policy.get("next_action", "")
    if len(history) >= MAX_REPEATED_ACTION:
        recent = history[-MAX_REPEATED_ACTION:]
        if all(a == next_action for a in recent):
            logger.info("guardrail: next_action '%s' repeated -> pitch_product", next_action)
            policy["next_action"] = "pitch_product"
            policy["behavior_strategy"] = "change_approach"
    return policy
