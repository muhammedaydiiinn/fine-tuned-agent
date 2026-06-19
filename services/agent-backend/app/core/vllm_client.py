"""vLLM istemcisi — mock ve gerçek modlar aynı arayüzden çağrılır.

VLLM_MODE=mock  → Deterministik JSON; GPU gerektirmez, tüm akışı test eder.
VLLM_MODE=real  → OpenAI-compatible /v1/chat/completions endpoint'i çağırır.
"""
import json
import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


def chat(messages: list[dict]) -> str:
    """Verilen mesaj listesiyle LLM'i çağırır, ham metin yanıtı döndürür."""
    if settings.vllm_mode == "mock":
        return _mock_chat(messages)
    return _real_chat(messages)


# ── Mock ────────────────────────────────────────────────────────────────────

def _mock_chat(messages: list[dict]) -> str:
    """Anahtar kelime tabanlı deterministik JSON üretir.

    Guardrail'ler bu çıktıyı override edeceğinden intentler doğru olduğu sürece
    agent_response içeriğinin doğruluğu kritik değil.
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

    # Fiyat sorusu
    if any(w in text for w in ["kostet", "preis", "kosten", "wie viel", "wieviel", "euro"]):
        return {**base, "intent": "price_question", "next_action": "explain_price",
                "agent_response": "Das kostet 29,99 Euro monatlich nach der Testphase."}

    # Güvenlik itirazı
    if any(w in text for w in ["virus", "link", "phishing", "gefährlich", "sicher"]):
        return {**base, "intent": "security_objection", "next_action": "address_security",
                "agent_response": "Der Link ist sicher."}

    # Sert ret
    if any(w in text for w in ["nein", "kein interesse", "will nichts", "nicht kaufen",
                                "aufhören", "legen sie auf"]):
        return {**base, "intent": "hard_decline", "next_action": "acknowledge_objection",
                "risk": "high", "agent_response": "Ich verstehe Ihre Bedenken."}

    # Ücretsiz mi sorusu
    if any(w in text for w in ["kostenlos", "gratis", "umsonst", "frei"]):
        return {**base, "intent": "free_question", "next_action": "explain_trial",
                "agent_response": "Ja, die ersten 14 Tage sind komplett kostenlos."}

    # 14 gün sonra sorusu
    if any(w in text for w in ["nach 14", "danach", "nach der testphase", "was passiert"]):
        return {**base, "intent": "price_question", "next_action": "explain_price",
                "agent_response": "Nach den 14 Tagen kostet es 29,99 Euro monatlich."}

    # Zaman itirazı
    if any(w in text for w in ["keine zeit", "nicht jetzt", "später", "spater", "busy"]):
        return {**base, "intent": "time_objection", "next_action": "handle_time_objection",
                "agent_response": "Das dauert nur eine Minute."}

    # SMS talebi
    if "sms" in text:
        return {**base, "intent": "sms_request", "next_action": "redirect_to_app",
                "agent_response": "Wir senden den Link direkt in die App."}

    # Zaten bloklama yapıyor
    if any(w in text for w in ["blockiere", "blocke", "schon", "bereits"]):
        return {**base, "intent": "already_blocking", "next_action": "differentiate_product",
                "agent_response": "Unser System kennt über 7.000 Risikonummern."}

    # Neden aradınız
    if any(w in text for w in ["warum", "wieso", "weshalb", "nummer"]):
        return {**base, "intent": "why_calling", "next_action": "explain_service",
                "agent_response": "Wir informieren Sie über unseren Anrufschutz-Service."}

    # Varsayılan
    return {**base, "intent": "general_inquiry", "next_action": "present_offer",
            "agent_response": "Möchten Sie mehr über unseren Anrufschutz erfahren?"}


# ── Gerçek vLLM ─────────────────────────────────────────────────────────────

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
        logger.error("vLLM HTTP hatası: %s", exc)
        raise
    except (KeyError, IndexError) as exc:
        logger.error("vLLM yanıt formatı beklenmedik: %s", exc)
        raise
