"""Deterministic guardrail rules.

Receives model output, applies business rules, and returns a safe policy.
Critical product responses are enforced here as templates.
"""
import logging
from typing import Any

from app.core.product_facts import PRICE_TEMPLATE, SECURITY_TEMPLATE

logger = logging.getLogger(__name__)

MAX_REPEATED_ACTION = 3

# Price-question tokens for detecting premature close guard
PRICE_QUESTION_TOKENS = [
    "was kostet", "wie teuer", "preis danach", "und der preis",
    "was zahle ich", "kosten danach", "monatlich", "gratisfase",
    "noch einmal die", "den preis noch",
]


def is_closing_price_question(customer_message: str) -> bool:
    """Return True if the customer is asking a price question during closing stage."""
    msg = (customer_message or "").lower()
    return any(t in msg for t in PRICE_QUESTION_TOKENS)


def customer_asked_price_or_trial(text: str) -> bool:
    msg = (text or "").lower()
    return any(p in msg for p in [
        "was kostet", "kostet", "preis", "monatlich", "euro",
        "nach 14 tagen", "nach 7 tagen", "testphase", "probezeit",
        "14 tagen", "7 tagen",
    ])


def apply(policy: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    """Apply all guardrail rules in sequence."""
    p = dict(policy)

    p = _rule_hard_decline(p, state)
    p = _rule_identity_before_link(p, state)
    p = _rule_price_template(p)
    p = _rule_security_template(p)
    p = _rule_loop_detection(p, state)

    return p


# ── Rules ────────────────────────────────────────────────────────────────────

def _rule_hard_decline(policy: dict, state: dict) -> dict:
    """Force close when two or more hard declines have occurred."""
    hard_decline_count = state.get("hard_decline_count", 0)
    intent = policy.get("intent", "")

    current_count = hard_decline_count + (1 if intent == "hard_decline" else 0)

    if current_count >= 2:
        logger.info("guardrail: hard_decline >= 2 -> close_call")
        policy["next_action"] = "close_call"
        policy["behavior_strategy"] = "graceful_exit"
        policy["allowed_to_continue"] = False

    return policy


def _rule_identity_before_link(policy: dict, state: dict) -> dict:
    """Block activation link if identity has not been confirmed."""
    if policy.get("next_action") == "send_activation_link":
        if not state.get("identity_confirmed", False):
            logger.info("guardrail: identity not confirmed -> blocking send_activation_link")
            policy["next_action"] = "confirm_identity"
            policy["behavior_strategy"] = "ask_for_name_first"
            policy["agent_response"] = (
                "Bevor ich Ihnen den Link sende, darf ich kurz Ihren Namen bestätigen?"
            )
    return policy


def _rule_price_template(policy: dict) -> dict:
    """Use the approved price template for price-related intents."""
    intent = policy.get("intent", "")
    next_action = policy.get("next_action", "")

    if intent in ("price_question", "free_question") or next_action in (
        "explain_price", "explain_trial"
    ):
        logger.info("guardrail: price template applied")
        policy["agent_response"] = PRICE_TEMPLATE
        policy["next_action"] = "explain_price"

    return policy


def _rule_security_template(policy: dict) -> dict:
    """Use the approved security template for security objections."""
    intent = policy.get("intent", "")
    next_action = policy.get("next_action", "")

    if intent == "security_objection" or next_action == "address_security":
        logger.info("guardrail: security template applied")
        policy["agent_response"] = SECURITY_TEMPLATE
        policy["next_action"] = "address_security"

    return policy


def _rule_loop_detection(policy: dict, state: dict) -> dict:
    """Change direction when the same next_action repeats MAX_REPEATED_ACTION times."""
    history: list = state.get("last_next_actions", [])
    next_action = policy.get("next_action", "")

    if len(history) >= MAX_REPEATED_ACTION:
        recent = history[-MAX_REPEATED_ACTION:]
        if all(a == next_action for a in recent):
            logger.info(
                "guardrail: next_action '%s' repeated %d times -> reframe_offer",
                next_action,
                MAX_REPEATED_ACTION,
            )
            policy["next_action"] = "reframe_offer"
            policy["behavior_strategy"] = "change_approach"

    return policy
