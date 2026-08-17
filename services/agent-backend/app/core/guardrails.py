"""Deterministic guardrail rules — PDF-aligned enforcement on model output."""
import logging
import re
from typing import Any

from app.core import content_store
from app.core.product_facts import (
    CLOSING_NEXT_ACTIONS,
    IDENTITY_NEXT_ACTIONS,
    PRICE_INTENT_ALIASES,
    normalize_next_action,
)

logger = logging.getLogger(__name__)

MAX_REPEATED_ACTION = 3

PRICE_QUESTION_TOKENS = [
    "was kostet", "wie teuer", "preis danach", "und der preis",
    "was zahle ich", "kosten danach", "monatlich", "gratisfase",
    "noch einmal die", "den preis noch", "kosteskostes", "kostet das",
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


def customer_not_ready_for_install(text: str) -> bool:
    msg = _lower(text)
    return any(
        p in msg
        for p in (
            "noch nicht", "nicht einrichten", "nicht installieren",
            "noch nichts", "nur info", "mehr info", "mehr erfahren",
            "erst noch", "will noch nicht",
        )
    )


def customer_skeptical(text: str) -> bool:
    msg = _lower(text)
    return any(
        p in msg
        for p in ("ja und", "und?", "was genau", "warum rufen", "was wollen sie")
    )


def is_closing_price_question(customer_message: str) -> bool:
    msg = (customer_message or "").lower()
    return any(t in msg for t in PRICE_QUESTION_TOKENS)


def customer_asked_price_or_trial(text: str) -> bool:
    msg = (text or "").lower()
    return any(p in msg for p in [
        "was kostet", "kostet", "kosteskostes", "preis", "monatlich", "euro",
        "nach 14 tagen", "testphase", "probezeit", "14 tagen",
    ])


def _is_price_turn(intent: str, customer_message: str) -> bool:
    if intent in PRICE_INTENT_ALIASES:
        return True
    return customer_asked_price_or_trial(customer_message)


def apply(policy: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    return apply_with_context(policy, state, customer_message="")


def apply_with_context(
    policy: dict[str, Any],
    state: dict[str, Any],
    customer_message: str = "",
) -> dict[str, Any]:
    # Legal/accuracy floor only — persuasion, alternatives, flow and closing are
    # left to the model's own intelligence (learned via fine-tuning). The old
    # stylistic overrides (verbatim security template, identity-before-link
    # funnel) were removed so the model can offer alternatives and phrase freely.
    p = dict(policy)
    p["next_action"] = normalize_next_action(p.get("next_action", ""))
    p = _rule_post_close_brief(p, state, customer_message)
    p = _rule_hard_decline(p, state)
    p = _rule_delay_no_phone_collection(p, customer_message)
    p = _rule_price_template(p, customer_message)
    p = _rule_closing_flags(p)
    p = _rule_loop_detection(p, state)
    return p


def _rule_post_close_brief(
    policy: dict,
    state: dict,
    customer_message: str,
) -> dict:
    if state.get("stage") != "closing" or not (customer_message or "").strip():
        return policy
    policy["next_action"] = "close_call"
    policy["allowed_to_continue"] = False
    policy["agent_response"] = content_store.canned("closing_brief")
    logger.info("guardrail: post-close -> brief farewell only")
    return policy


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


def _rule_delay_no_phone_collection(policy: dict, customer_message: str) -> dict:
    if not customer_wants_delay(customer_message):
        return policy

    canonical = normalize_next_action(policy.get("next_action", ""))
    response = _lower(policy.get("agent_response", ""))
    if canonical in IDENTITY_NEXT_ACTIONS or "telefonnummer" in response:
        logger.info("guardrail: delay -> block phone/identity collection")
        policy["next_action"] = "handle_time_objection"
        policy["behavior_strategy"] = "empathize_redirect"
        policy["allowed_to_continue"] = True
        policy["agent_response"] = content_store.canned("delay_deferral")
    return policy


def _rule_closing_flags(policy: dict) -> dict:
    if normalize_next_action(policy.get("next_action", "")) in CLOSING_NEXT_ACTIONS:
        policy["allowed_to_continue"] = False
        policy["behavior_strategy"] = policy.get("behavior_strategy") or "respect_decline"
    return policy


# Euro amounts the agent is allowed to state (monthly price, legal cover, check).
_ALLOWED_EURO_AMOUNTS = {"29.99", "2500", "18"}
_EURO_AMOUNT_RE = re.compile(r"\d+(?:\.\d+)?(?=\s*(?:euro|eur|€))", re.IGNORECASE)


def price_answer_is_unsafe(response: str) -> bool:
    """True if a price answer must be replaced by the approved template.

    Only genuinely wrong answers are overridden: an empty reply, or one that
    states a Euro amount outside the approved set. A well-trained model that
    addresses the objection correctly (hidden costs, cancellation, trial) is
    left untouched — the guardrail no longer flattens good answers.
    """
    msg = (response or "").strip()
    if not msg:
        return True
    compact = msg.replace(".", "").replace(",", ".")
    return any(a not in _ALLOWED_EURO_AMOUNTS for a in _EURO_AMOUNT_RE.findall(compact))


def _rule_price_template(policy: dict, customer_message: str) -> dict:
    intent = (policy.get("intent") or "").strip()
    next_action = normalize_next_action(policy.get("next_action", ""))
    if not _is_price_turn(intent, customer_message) and next_action != "explain_price":
        return policy
    # Trust a correct model answer; only enforce the template when it is wrong.
    if price_answer_is_unsafe(policy.get("agent_response", "")):
        logger.info("guardrail: unsafe price answer -> PDF template")
        policy["agent_response"] = content_store.canned("price")
    policy["next_action"] = "explain_price"
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
