"""Session state management.

State is stored in sessions.state_json (JSONB column).
update() is called after each turn to persist the new state.
"""
import logging
from typing import Any

from sqlalchemy.orm import Session as DBSession

logger = logging.getLogger(__name__)

# All slots in the full sales flow (ordered)
ALL_FLOW_SLOTS: tuple[str, ...] = (
    "identity_confirmed",
    "problem_awareness_created",
    "product_value_explained",
    "safe_link_explained",
    "offer_terms_explained",
    "commitment_requested",
    "final_decision",
)

# Slots that must be filled before closing
CLOSE_REQUIRED_SLOTS: tuple[str, ...] = (
    "identity_confirmed",
    "safe_link_explained",
    "offer_terms_explained",
    "commitment_requested",
)

DEFAULT_STATE: dict[str, Any] = {
    "stage": "initial",
    "goal": "sell_activation",
    "customer_name": "",
    "agent_name": "",
    "agent_role": "",

    "hard_decline_count": 0,
    "identity_confirmed": False,
    "offer_terms_explained": False,
    "price_explained": False,
    "link_sent": False,
    "last_next_actions": [],   # last 5 next_action values for loop detection
    "turn_count": 0,
}

NEXT_ACTION_HISTORY_SIZE = 5


def load(session_model) -> dict[str, Any]:
    """Load state_json from the session model and fill missing keys from DEFAULT_STATE."""
    raw: dict = session_model.state_json or {}
    state = {**DEFAULT_STATE, **raw}
    return state


def update(
    state: dict[str, Any],
    policy: dict[str, Any],
    customer_text: str = "",
) -> dict[str, Any]:
    """Update state based on the policy output of one turn."""
    new_state = {**DEFAULT_STATE, **state}
    intent = policy.get("intent", "")
    next_action = policy.get("next_action", "")

    new_state["turn_count"] = new_state.get("turn_count", 0) + 1

    # Hard decline counter
    if intent == "hard_decline":
        new_state["hard_decline_count"] = new_state.get("hard_decline_count", 0) + 1

    # Identity confirmation must come from the customer's statement, not from
    # the agent merely deciding to ask for identity.
    if customer_signals_identity_confirmation(customer_text):
        new_state["identity_confirmed"] = True

    # Price / offer terms explained
    if next_action in ("explain_price", "explain_offer_terms") or intent in (
        "price_question", "free_question", "price_inquiry",
    ):
        new_state["price_explained"] = True
        new_state["offer_terms_explained"] = True

    # Activation link sent
    if next_action == "send_activation_link":
        new_state["link_sent"] = True

    # Stage update
    if next_action in ("close_call", "respect_decline_and_end_call"):
        new_state["stage"] = "closing"
    elif new_state.get("stage") == "initial" and new_state["turn_count"] > 1:
        new_state["stage"] = "conversation"

    # Next action history
    history: list = list(new_state.get("last_next_actions", []))
    history.append(next_action)
    new_state["last_next_actions"] = history[-NEXT_ACTION_HISTORY_SIZE:]

    new_state["filled_slots"] = _derive_filled_slots(new_state, policy)
    return new_state


def derive_filled_slots(state: dict[str, Any], policy: dict[str, Any] | None = None) -> dict[str, bool]:
    """Public helper for response repair before state is persisted."""
    if policy is None:
        return dict(state.get("filled_slots") or {})
    return _derive_filled_slots(state, policy)


def _derive_filled_slots(state: dict[str, Any], policy: dict[str, Any]) -> dict[str, bool]:
    """Track sales-flow slots for hard response repairs without blocking soft training."""
    filled = dict(state.get("filled_slots") or {})
    if state.get("identity_confirmed"):
        filled["identity_confirmed"] = True
    if state.get("offer_terms_explained") or state.get("price_explained"):
        filled["offer_terms_explained"] = True

    intent = policy.get("intent", "")
    next_action = policy.get("next_action", "")
    pitch_actions = {
        "pitch_product",
        "present_offer",
        "explain_service",
        "differentiate_product",
        "create_problem_awareness",
        "explain_product_value",
    }
    if intent in ("general_inquiry", "why_calling", "already_blocking") or next_action in pitch_actions:
        filled["product_value_explained"] = True
    if intent == "security_objection" or next_action in ("address_security", "explain_safe_app_link"):
        filled["safe_link_explained"] = True
    if next_action in ("ask_for_commitment", "ask_for_activation_commitment"):
        filled["commitment_requested"] = True
    if next_action == "close_call":
        filled["final_decision"] = True
    return filled


def persist(db: DBSession, session_model, new_state: dict[str, Any]) -> None:
    """Write updated state to the database."""
    session_model.state_json = new_state
    db.add(session_model)
    db.commit()
    db.refresh(session_model)


def slots_ready_for_close(filled_slots: dict) -> bool:
    """Return True if all CLOSE_REQUIRED_SLOTS are filled."""
    return all(s in (filled_slots or {}) for s in CLOSE_REQUIRED_SLOTS)


def flow_completion_score(filled_slots: dict) -> float:
    """Return a 0.0-1.0 flow completion ratio."""
    filled = filled_slots or {}
    return sum(1 for s in ALL_FLOW_SLOTS if s in filled) / len(ALL_FLOW_SLOTS)


def customer_signals_app_progress(text: str) -> bool:
    """Return True if the customer indicates they have completed app installation steps."""
    msg = (text or "").lower()
    return any(p in msg for p in [
        "app ist offen", "app geöffnet", "heruntergeladen", "installiert",
        "link geöffnet", "store geöffnet", "sms-code", "code ist da",
        "telefonnummer bestätigen", "schutz aktivieren", "schutz aktiv",
        "bildschirm steht", "auf dem bildschirm",
    ])


def customer_signals_identity_confirmation(text: str) -> bool:
    """Return True when the customer explicitly confirms their identity."""
    msg = " ".join((text or "").lower().split())
    return any(phrase in msg for phrase in [
        "ja, das bin ich",
        "ja das bin ich",
        "ich bin das",
        "das ist richtig",
        "mein name ist",
    ])


def customer_signals_flow_complete(text: str) -> bool:
    """Return True if the customer indicates the flow is complete."""
    msg = (text or "").lower()
    return any(p in msg for p in [
        "schutz ist aktiv", "alles klar", "danke, alles", "fertig",
        "aktiv, danke", "funktioniert", "habe aktiviert",
    ])
