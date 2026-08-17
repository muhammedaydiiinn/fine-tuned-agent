"""Builds the message list for vLLM.

Single-turn rich payload matching the fine-tuning format:
system (mission persona, name filled) + one user message carrying
{task, customer, known_customer_data, company_policy, last_agent_message,
customer_message}. Facts/script/objections travel in known_customer_data, not
in the system prompt.
"""
import json
from typing import Any

from app.core import state_manager
from app.core.policy_prompt import build_system_content


def build(
    customer_text: str,
    state: dict[str, Any],
    recent_turns: list,
    correction_hints: list[dict],
) -> list[dict]:
    """Return the OpenAI chat messages list."""
    agent_name = (state.get("agent_name") or "").strip()
    agent_role = (state.get("agent_role") or "").strip()

    messages: list[dict] = [
        {"role": "system", "content": build_system_content(agent_name, agent_role)},
    ]

    if correction_hints:
        hints_text = "Correction memory (these instructions take priority):\n"
        for h in correction_hints:
            if h.get("correct_response"):
                hints_text += f"- Trigger '{h['trigger_key']}' -> response: {h['correct_response']}\n"
            if h.get("correct_next_action"):
                hints_text += f"  next_action: {h['correct_next_action']}\n"
        messages.append({"role": "system", "content": hints_text.strip()})

    if recent_turns and getattr(recent_turns[-1], "was_interrupted", False):
        # last_agent_message carries only the heard prefix; flag the cut-off so
        # the model resumes contextually instead of repeating the unheard rest.
        messages.append({
            "role": "system",
            "content": (
                "(Your previous reply was interrupted by the customer; they did "
                "not hear the rest. Do not repeat what you already said — address "
                "their new input, then continue naturally.)"
            ),
        })

    user_payload = build_user_payload(customer_text, state, recent_turns)
    messages.append({"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)})
    return messages


def _last_agent_message(recent_turns: list) -> str:
    if not recent_turns:
        return ""
    turn = recent_turns[-1]
    if getattr(turn, "was_interrupted", False):
        return (getattr(turn, "spoken_response", None) or "").strip()
    return (getattr(turn, "agent_response", None) or "").strip()


def _build_task(state: dict[str, Any]) -> dict:
    required = list(state_manager.ALL_FLOW_SLOTS)
    filled = state.get("filled_slots") or {}
    return {
        "type": "sales",
        "goal": state.get("goal", "sell_activation"),
        "stage": state.get("stage", "initial"),
        "required_slots": required,
        "filled_slots": {k: "yes" for k in required if filled.get(k)},
        "missing_slots": [k for k in required if not filled.get(k)],
        "status": "in_progress",
    }


def _build_known_customer_data(state: dict[str, Any]) -> dict:
    from app.core import content_store, product_facts

    facts = content_store.product_facts()
    agent_name = (state.get("agent_name") or "Anna Weber").strip()
    agent_role = (state.get("agent_role") or "").strip() or "Sicherheitsberaterin"
    customer_name = (state.get("customer_name") or "").strip() or "der Kunde"
    website = facts.get("website", "")

    product = {
        "name": "CallShield Gold Paket",
        "trial_period": facts.get("trial_period", ""),
        "monthly_price": facts.get("monthly_price", ""),
        "check_price_normal": facts.get("check_price_normal", ""),
        "check_price_today": facts.get("check_price_today", ""),
        "platforms": facts.get("app_stores", ""),
        "risk_entries_example": facts.get("risk_entries_example", ""),
        "risk_entries_range": facts.get("risk_entries_range", ""),
        "blocked_numbers": facts.get("blocked_numbers", ""),
        "legal_support": facts.get("legal_support", ""),
        "support_channel": facts.get("support_channel", ""),
        "website": website,
    }
    sales_script = {
        "opening": (
            f"Guten Tag, mein Name ist {agent_name}, {agent_role} von CallShield. "
            "Ich rufe an, weil es um den Schutz Ihrer Rufnummer vor unerwünschten "
            "Anrufen und möglichen Betrugsversuchen geht."
        ),
        "safe_link": "Der Link führt ausschließlich zum offiziellen Apple App Store oder Google Play Store.",
        "value_pitch": (
            "Mit der App können Sie prüfen, ob Ihre Rufnummer in Risiko-, Werbe- oder "
            "Beschwerdedatenbanken auftaucht. Zusätzlich werden bekannte Risikonummern "
            "automatisch blockiert."
        ),
        "website_start": (
            "Wenn Sie keinen Link öffnen möchten, können Sie die App auch selbst öffnen und "
            f"Ihre Nummer eingeben — oder den Schutz direkt auf unserer Webseite {website} "
            "starten. Ich bleibe dabei an Ihrer Seite."
        ),
    }
    sales_script.update(product_facts.build_sales_script(product))
    return {
        "customer": {"name_placeholder": customer_name, "phone_number": "die angerufene Rufnummer"},
        "product": product,
        "sales_script": sales_script,
        "objection_handling": product_facts.build_objection_handling(product),
        "agent": {"name": agent_name, "role": agent_role},
    }


def build_user_payload(customer_text: str, state: dict[str, Any], recent_turns: list) -> dict:
    from app.core import content_store

    return {
        "task": _build_task(state),
        "customer": {"risk": "low"},
        "known_customer_data": _build_known_customer_data(state),
        "company_policy": {
            "price_fixed": True,
            "no_personal_data_on_call": True,
            "rules": list(content_store.pdf_rules()),
        },
        "last_agent_message": _last_agent_message(recent_turns),
        "customer_message": customer_text,
    }
