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


# Conversation history injected per turn (WP-3). Char-budgeted so a very long
# call cannot blow the context window; oldest turns drop first.
_HISTORY_CHAR_BUDGET = 2800


def _build_history(recent_turns: list) -> list[dict]:
    """(kunde, agent) pairs of the recent turns, newest-last, budget-trimmed."""
    items: list[dict] = []
    used = 0
    for turn in reversed(recent_turns or []):
        customer = (getattr(turn, "customer_text", None) or "").strip()
        if getattr(turn, "was_interrupted", False):
            agent = (getattr(turn, "spoken_response", None) or "").strip()
        else:
            agent = (getattr(turn, "agent_response", None) or "").strip()
        if not customer and not agent:
            continue
        cost = len(customer) + len(agent)
        if used + cost > _HISTORY_CHAR_BUDGET:
            break
        entry: dict = {}
        if customer:
            entry["kunde"] = customer
        if agent:
            entry["agent"] = agent
        items.append(entry)
        used += cost
    return list(reversed(items))


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


# Words too common in German small talk to signal which objection is meant.
_STOPWORDS = frozenset(
    "ich sie das ist ein eine der die und nicht mir mich was wie zu es den dem "
    "ja nein auch doch noch schon bitte haben habe sind mit für auf im in an am "
    "um aber dann wenn oder wir ihr ihre ihren sich da so nur mal ganz kein "
    "keine meine mein wird werden kann können möchte will".split()
)


def _tokens(text: str) -> set[str]:
    import re

    return {
        token
        for token in re.findall(r"[a-zäöüß0-9]+", (text or "").lower())
        if len(token) > 2 and token not in _STOPWORDS
    }


def _select_objections(customer_text: str, limit: int = 4) -> list[dict]:
    """Panel-editable objection library entries relevant to this turn.

    The library can grow to dozens of entries, so instead of stuffing all of
    them into every prompt, score each entry by token overlap with the current
    customer message and inject only the best matches. An opening turn (empty
    text) or small talk with no overlap injects nothing.
    """
    from app.core import content_store

    text_tokens = _tokens(customer_text)
    if not text_tokens:
        return []
    scored: list[tuple[int, dict]] = []
    for item in content_store.objection_faq():
        trigger_overlap = len(text_tokens & _tokens(item.get("trigger", "")))
        answer_overlap = len(text_tokens & _tokens(item.get("answer", "")))
        score = trigger_overlap * 2 + answer_overlap
        if trigger_overlap or answer_overlap >= 2:
            scored.append((score, {"trigger": item["trigger"], "answer": item["answer"]}))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _, item in scored[:limit]]


def near_duplicate(a: str, b: str, threshold: float = 0.8) -> bool:
    """True when two utterances say practically the same thing (token jaccard)."""
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return False
    return len(ta & tb) / len(ta | tb) >= threshold


def opening_line(state: dict[str, Any]) -> str:
    """Deterministic call opening — also spoken directly on turn 0 (no LLM)."""
    agent_name = (state.get("agent_name") or "Anna Weber").strip()
    agent_role = (state.get("agent_role") or "").strip() or "Sicherheitsberaterin"
    return (
        f"Guten Tag, mein Name ist {agent_name}, {agent_role} von CallShield. "
        "Ich rufe an, weil es um den Schutz Ihrer Rufnummer vor unerwünschten "
        "Anrufen und möglichen Betrugsversuchen geht."
    )


def _build_known_customer_data(state: dict[str, Any], customer_text: str = "") -> dict:
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
        "opening": opening_line(state),
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
    data = {
        "customer": {"name_placeholder": customer_name, "phone_number": "die angerufene Rufnummer"},
        "product": product,
        "sales_script": sales_script,
        "objection_handling": product_facts.build_objection_handling(product),
        "agent": {"name": agent_name, "role": agent_role},
    }
    # Panel-editable objection library, narrowed to this turn (WP-4).
    objections = _select_objections(customer_text)
    if objections:
        data["objection_library"] = objections
    return data


def build_user_payload(customer_text: str, state: dict[str, Any], recent_turns: list) -> dict:
    from app.core import content_store

    payload = {
        "task": _build_task(state),
        "customer": {"risk": "low"},
        "known_customer_data": _build_known_customer_data(state, customer_text),
        "company_policy": {
            "price_fixed": True,
            "no_personal_data_on_call": True,
            "rules": list(content_store.pdf_rules()),
        },
        "last_agent_message": _last_agent_message(recent_turns),
        "customer_message": customer_text,
    }
    history = _build_history(recent_turns)
    if history:
        payload["conversation_history"] = history
    return payload
