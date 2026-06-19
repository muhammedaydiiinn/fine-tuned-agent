"""Model çıktısından JSON çıkarma, eksik key tamamlama ve safe fallback."""
import json
import re
import logging

logger = logging.getLogger(__name__)

# Geçerli bir policy'de bulunması zorunlu alanlar
REQUIRED_KEYS = {
    "intent",
    "emotion",
    "risk",
    "next_action",
    "behavior_strategy",
    "allowed_to_continue",
    "agent_response",
    "voice_style",
}

SAFE_FALLBACK_POLICY: dict = {
    "intent": "unknown",
    "emotion": "neutral",
    "risk": "low",
    "next_action": "clarify",
    "behavior_strategy": "safe_fallback",
    "allowed_to_continue": True,
    "agent_response": (
        "Entschuldigung, ich habe Sie nicht ganz verstanden. "
        "Könnten Sie das bitte wiederholen?"
    ),
    "voice_style": {"tone": "calm", "pace": "slow", "confidence": "medium"},
}


def extract_json(raw_text: str) -> dict:
    """Model çıktısından JSON parse eder.

    Sırasıyla dener:
    1. ```json ... ``` bloğu
    2. { ... } içinde ilk geçerli JSON objesi
    3. Ham parse
    """
    if not raw_text or not raw_text.strip():
        logger.warning("json_repair: boş model çıktısı, fallback kullanılıyor")
        return dict(SAFE_FALLBACK_POLICY)

    # 1. Markdown kod bloğu
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw_text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # 2. İlk { ... } bloğu
    brace_match = re.search(r"\{.*\}", raw_text, re.DOTALL)
    if brace_match:
        try:
            return json.loads(brace_match.group(0))
        except json.JSONDecodeError:
            pass

    # 3. Ham parse
    try:
        return json.loads(raw_text.strip())
    except json.JSONDecodeError:
        logger.warning("json_repair: JSON parse başarısız, fallback kullanılıyor")
        return dict(SAFE_FALLBACK_POLICY)


def repair(policy: dict) -> dict:
    """Eksik zorunlu key'leri fallback değerleriyle tamamlar."""
    repaired = dict(policy)
    needs_repair = False

    for key in REQUIRED_KEYS:
        if key not in repaired:
            repaired[key] = SAFE_FALLBACK_POLICY[key]
            needs_repair = True
            logger.info("json_repair: eksik key tamamlandı — %s", key)

    # voice_style bir dict olmalı
    if not isinstance(repaired.get("voice_style"), dict):
        repaired["voice_style"] = SAFE_FALLBACK_POLICY["voice_style"]
        needs_repair = True

    # allowed_to_continue bool olmalı
    if not isinstance(repaired.get("allowed_to_continue"), bool):
        repaired["allowed_to_continue"] = True
        needs_repair = True

    # agent_response boş bırakılamaz
    if not repaired.get("agent_response", "").strip():
        repaired["agent_response"] = SAFE_FALLBACK_POLICY["agent_response"]
        needs_repair = True

    if needs_repair:
        logger.info("json_repair: policy onarıldı")

    return repaired
