"""Builds the message list for vLLM.

System instruction + product facts + current state + recent turns +
correction hints + customer text -> OpenAI chat messages format.
"""
import json
from typing import Any

from app.core.product_facts import format_for_prompt

# Number of previous turns included in the prompt
HISTORY_WINDOW = 5

SYSTEM_INSTRUCTION = """You are Anna Weber, a sales agent for Anrufblocker Gold Paket.
For each customer message return ONLY a single JSON object in this format:

{
  "intent": "<customer intent>",
  "emotion": "<customer emotion>",
  "risk": "<low|medium|high>",
  "next_action": "<next sales step>",
  "behavior_strategy": "<approach strategy>",
  "allowed_to_continue": <true|false>,
  "agent_response": "<German customer response>",
  "voice_style": {"tone": "clear", "pace": "normal", "confidence": "high"}
}

Rules:
- Return only JSON, nothing else.
- agent_response must always be in German.
- Use the product facts below for all pricing information.
- If the customer declines twice, set next_action="close_call".
""".strip()


def build(
    customer_text: str,
    state: dict[str, Any],
    recent_turns: list,
    correction_hints: list[dict],
) -> list[dict]:
    """Return the OpenAI chat messages list."""
    messages: list[dict] = []

    # 1. System instruction
    system_content = SYSTEM_INSTRUCTION + "\n\n" + format_for_prompt()
    messages.append({"role": "system", "content": system_content})

    # 2. Current state summary (separate system message)
    state_summary = _format_state(state)
    if state_summary:
        messages.append({
            "role": "system",
            "content": f"Current state:\n{state_summary}",
        })

    # 3. Correction hints
    if correction_hints:
        hints_text = "Correction memory (these instructions take priority):\n"
        for h in correction_hints:
            if h.get("correct_response"):
                hints_text += f"- Trigger '{h['trigger_key']}' -> response: {h['correct_response']}\n"
            if h.get("correct_next_action"):
                hints_text += f"  next_action: {h['correct_next_action']}\n"
        messages.append({"role": "system", "content": hints_text.strip()})

    # 4. Conversation history (last N turns)
    for turn in recent_turns[-HISTORY_WINDOW:]:
        messages.append({"role": "user", "content": turn.customer_text})
        if turn.agent_response:
            messages.append({"role": "assistant", "content": turn.agent_response})

    # 5. Current customer message
    if not customer_text:
        customer_name = (state.get("customer_name") or "").strip()
        if customer_name:
            opening = (
                f"The call just connected — you are calling {customer_name}. "
                "There is no customer input yet. Generate your opening greeting: "
                f"address {customer_name} by name, introduce yourself as Anna Weber "
                "from Anrufblocker, and briefly state why you are calling."
            )
        else:
            opening = (
                "The call just connected. No customer input yet. Generate your opening "
                "greeting and introduce yourself as Anna Weber from Anrufblocker."
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
