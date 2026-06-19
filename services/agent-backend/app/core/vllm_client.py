"""vLLM client — mock and real modes share the same interface.

VLLM_MODE=mock  -> Deterministic JSON; no GPU required, exercises the full pipeline.
VLLM_MODE=real  -> Calls the OpenAI-compatible /v1/chat/completions endpoint.
"""
import json
import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


def chat(messages: list[dict]) -> str:
    """Call the LLM with the given message list and return the raw text response."""
    if settings.vllm_mode == "mock":
        return _mock_chat(messages)
    return _real_chat(messages)


# ── Mock ────────────────────────────────────────────────────────────────────

def _mock_chat(messages: list[dict]) -> str:
    """Produce deterministic JSON based on keyword matching.

    Guardrails will override this output, so only intents need to be correct.
    """
    customer_text = ""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            customer_text = msg.get("content", "").lower()
            break

    policy = _classify_mock(customer_text)
    return json.dumps(policy, ensure_ascii=False)


def _classify_mock(text: str) -> dict:
    base = {
        "emotion": "neutral",
        "risk": "low",
        "behavior_strategy": "standard",
        "allowed_to_continue": True,
        "voice_style": {"tone": "clear", "pace": "normal", "confidence": "high"},
    }

    # Activation link request. The identity-before-link guardrail decides
    # whether this action is allowed for the current session.
    if any(phrase in text for phrase in [
        "schicken sie mir den sicheren link",
        "schicken sie mir den link",
        "ich öffne den link",
    ]):
        return {**base, "intent": "activation_link_request", "next_action": "send_activation_link",
                "agent_response": "Ich sende Ihnen jetzt den sicheren Aktivierungslink."}

    # Security objection
    if any(w in text for w in ["virus", "phishing", "gefährlich", "ist der link sicher"]):
        return {**base, "intent": "security_objection", "next_action": "address_security",
                "agent_response": "Der Link ist sicher."}

    # Hard decline
    if any(w in text for w in ["nein", "kein interesse", "will nichts", "nicht kaufen",
                                "aufhören", "legen sie auf"]):
        return {**base, "intent": "hard_decline", "next_action": "acknowledge_objection",
                "risk": "high", "agent_response": "Ich verstehe Ihre Bedenken."}

    # Free trial question
    if any(w in text for w in ["kostenlos", "gratis", "umsonst", "frei"]):
        return {**base, "intent": "free_question", "next_action": "explain_trial",
                "agent_response": "Ja, die ersten 14 Tage sind komplett kostenlos."}

    # Price question
    if any(w in text for w in ["kostet", "preis", "kosten", "wie viel", "wieviel", "euro"]):
        return {**base, "intent": "price_question", "next_action": "explain_price",
                "agent_response": "Das kostet 29,99 Euro monatlich nach der Testphase."}

    # After 14 days question
    if any(w in text for w in ["nach 14", "danach", "nach der testphase", "was passiert"]):
        return {**base, "intent": "price_question", "next_action": "explain_price",
                "agent_response": "Nach den 14 Tagen kostet es 29,99 Euro monatlich."}

    # Time objection
    if any(w in text for w in ["keine zeit", "nicht jetzt", "später", "spater", "busy"]):
        return {**base, "intent": "time_objection", "next_action": "handle_time_objection",
                "agent_response": "Das dauert nur eine Minute."}

    # SMS request
    if "sms" in text:
        return {**base, "intent": "sms_request", "next_action": "redirect_to_app",
                "agent_response": "Wir senden den Link direkt in die App."}

    # Already blocking
    if any(w in text for w in ["blockiere", "blocke", "schon", "bereits"]):
        return {**base, "intent": "already_blocking", "next_action": "differentiate_product",
                "agent_response": "Unser System kennt über 7.000 Risikonummern."}

    # Why are you calling
    if any(w in text for w in ["warum", "wieso", "weshalb", "nummer"]):
        return {**base, "intent": "why_calling", "next_action": "explain_service",
                "agent_response": "Wir informieren Sie über unseren Anrufschutz-Service."}

    # Default
    return {**base, "intent": "general_inquiry", "next_action": "present_offer",
            "agent_response": "Möchten Sie mehr über unseren Anrufschutz erfahren?"}


# ── Real vLLM ────────────────────────────────────────────────────────────────

def _real_chat(messages: list[dict]) -> str:
    payload = {
        "model": settings.vllm_model_name,
        "messages": messages,
        "temperature": 0.1,
        "max_tokens": 512,
    }
    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.post(
                f"{settings.vllm_base_url}/chat/completions",
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
            return data["choices"][0]["message"]["content"]
    except httpx.HTTPError as exc:
        logger.error("vLLM HTTP error: %s", exc)
        raise
    except (KeyError, IndexError) as exc:
        logger.error("vLLM unexpected response format: %s", exc)
        raise
