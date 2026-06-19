"""vLLM için mesaj listesi oluşturur.

Sistem talimatı + ürün gerçekleri + mevcut state + son turn'ler +
correction ipuçları + müşteri metni → OpenAI chat messages formatı.
"""
import json
from typing import Any

from app.core.product_facts import format_for_prompt

# Kaç önceki turn prompt'a dahil edilir
HISTORY_WINDOW = 5

SYSTEM_INSTRUCTION = """Sen CallShield Gold Paket için bir satış politikası ajanısın.
Görevin her müşteri mesajına aşağıdaki JSON formatında SADECE bir JSON objesi döndürmek:

{
  "intent": "<müşteri niyeti>",
  "emotion": "<müşteri duygu durumu>",
  "risk": "<low|medium|high>",
  "next_action": "<yapılacak satış adımı>",
  "behavior_strategy": "<yaklaşım stratejisi>",
  "allowed_to_continue": <true|false>,
  "agent_response": "<Almanca müşteri yanıtı>",
  "voice_style": {"tone": "clear", "pace": "normal", "confidence": "high"}
}

Kurallar:
- Yalnızca JSON döndür, başka hiçbir şey yazma.
- agent_response her zaman Almanca olmalı.
- Fiyat bilgisi için ürün gerçeklerindeki değerleri kullan.
- Müşteri ikinci kez sert ret gösterirse next_action="close_call" yap.
""".strip()


def build(
    customer_text: str,
    state: dict[str, Any],
    recent_turns: list,
    correction_hints: list[dict],
) -> list[dict]:
    """OpenAI chat messages listesi döndürür."""
    messages: list[dict] = []

    # 1. Sistem talimatı
    system_content = SYSTEM_INSTRUCTION + "\n\n" + format_for_prompt()
    messages.append({"role": "system", "content": system_content})

    # 2. Mevcut state özeti (ayrı bir sistem mesajı)
    state_summary = _format_state(state)
    if state_summary:
        messages.append({
            "role": "system",
            "content": f"Mevcut durum:\n{state_summary}",
        })

    # 3. Correction ipuçları
    if correction_hints:
        hints_text = "Düzeltme hafızası (bu talimatlar önceliklidir):\n"
        for h in correction_hints:
            if h.get("correct_response"):
                hints_text += f"- Trigger '{h['trigger_key']}' → yanıt: {h['correct_response']}\n"
            if h.get("correct_next_action"):
                hints_text += f"  next_action: {h['correct_next_action']}\n"
        messages.append({"role": "system", "content": hints_text.strip()})

    # 4. Konuşma geçmişi (son N turn)
    for turn in recent_turns[-HISTORY_WINDOW:]:
        messages.append({"role": "user", "content": turn.customer_text})
        if turn.agent_response:
            messages.append({"role": "assistant", "content": turn.agent_response})

    # 5. Mevcut müşteri mesajı
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
        lines.append("- Kimlik doğrulandı")
    if state.get("offer_terms_explained"):
        lines.append("- Teklif koşulları açıklandı")
    if state.get("stage") != "initial":
        lines.append(f"- Aşama: {state.get('stage')}")
    return "\n".join(lines)


def _compact_state(state: dict[str, Any]) -> dict:
    """Prompt boyutunu azaltmak için yalnızca önemli state alanlarını döndürür."""
    return {
        "stage": state.get("stage"),
        "hard_decline_count": state.get("hard_decline_count", 0),
        "identity_confirmed": state.get("identity_confirmed", False),
        "offer_terms_explained": state.get("offer_terms_explained", False),
        "price_explained": state.get("price_explained", False),
    }
