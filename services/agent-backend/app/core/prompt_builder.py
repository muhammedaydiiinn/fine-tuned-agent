"""Builds the message list for vLLM.

System instruction + product facts + current state + recent turns +
correction hints + customer text -> OpenAI chat messages format.
"""
import json
from typing import Any

from app.core.policy_prompt import build_system_content

# Number of previous turns included in the prompt
HISTORY_WINDOW = 5


def build(
    customer_text: str,
    state: dict[str, Any],
    recent_turns: list,
    correction_hints: list[dict],
) -> list[dict]:
    """Return the OpenAI chat messages list."""
    messages: list[dict] = []

    messages.append({"role": "system", "content": build_system_content()})

    state_summary = _format_state(state)
    if state_summary:
        messages.append({
            "role": "system",
            "content": f"Current state:\n{state_summary}",
        })

    if correction_hints:
        hints_text = "Correction memory (these instructions take priority):\n"
        for h in correction_hints:
            if h.get("correct_response"):
                hints_text += f"- Trigger '{h['trigger_key']}' -> response: {h['correct_response']}\n"
            if h.get("correct_next_action"):
                hints_text += f"  next_action: {h['correct_next_action']}\n"
        messages.append({"role": "system", "content": hints_text.strip()})

    for turn in recent_turns[-HISTORY_WINDOW:]:
        messages.append({"role": "user", "content": turn.customer_text})
        if turn.agent_response:
            messages.append({"role": "assistant", "content": turn.agent_response})

    if not customer_text:
        customer_name = (state.get("customer_name") or "").strip()
        if customer_name:
            opening = (
                f"The call just connected — you are calling {customer_name}. "
                "The customer may already be speaking; keep the greeting to one or two "
                "short sentences. Address {name} by name, introduce yourself as Anna Weber, "
                "Sicherheitsberaterin von Anrufblocker, and briefly mention that their "
                "number may be exposed to fraud misuse and you will help them check it safely."
            ).format(name=customer_name)
        else:
            opening = (
                "The call just connected. The customer may already be speaking; keep the "
                "greeting to one or two short sentences. Introduce yourself as Anna Weber, "
                "Sicherheitsberaterin von Anrufblocker, and briefly mention safe number "
                "protection."
            )
        messages.append({"role": "system", "content": opening})
    user_payload = json.dumps(
        {"customer_message": customer_text, "state": _compact_state(state)},
        ensure_ascii=False,
    )
    messages.append({"role": "user", "content": user_payload})

    return messages


def _format_state(state: dict[str, Any]) -> str:
    lines = []
    if state.get("hard_decline_count", 0) > 0:
        lines.append(f"- hard_decline_count: {state['hard_decline_count']}")
    if state.get("identity_confirmed"):
        lines.append("- identity confirmed")
    if state.get("offer_terms_explained"):
        lines.append("- offer terms explained")
    if state.get("stage") != "initial":
        lines.append(f"- stage: {state.get('stage')}")
    return "\n".join(lines)


def _compact_state(state: dict[str, Any]) -> dict:
    """Return only the fields that matter for prompt size."""
    compact = {
        "stage": state.get("stage"),
        "hard_decline_count": state.get("hard_decline_count", 0),
        "identity_confirmed": state.get("identity_confirmed", False),
        "offer_terms_explained": state.get("offer_terms_explained", False),
        "price_explained": state.get("price_explained", False),
    }
    if (state.get("customer_name") or "").strip():
        compact["customer_name"] = state["customer_name"]
    return compact
