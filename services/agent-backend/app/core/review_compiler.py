"""Natural-language supervisor instruction compiler (LLM editor + deterministic fallback)."""
from __future__ import annotations

import json
import logging
import re
import unicodedata
from dataclasses import asdict, dataclass

from app.config import settings
from app.core.product_facts import PRICE_TEMPLATE, SECURITY_TEMPLATE

logger = logging.getLogger(__name__)

_ALLOWED_TYPES = {
    "response_correction",
    "product_fact_correction",
    "wrong_next_action",
    "missing_step",
    "tone_correction",
}
_MIN_CONFIDENCE = 0.4
_EDITOR_SYSTEM = (
    "Du bist ein Redakteur, der EINEN Turn eines deutschen Telefon-Verkaufsagenten "
    '("Anna Weber", Anrufblocker) gemaess einer Supervisor-Anweisung korrigiert. '
    "Aendere nur, was die Anweisung verlangt; bleib bei den Produktfakten (14 Tage "
    "kostenlos, danach 29,99 Euro; keine Bank-/SMS-Code-/Nummer-Abfrage am Telefon). "
    "Gib NUR EIN JSON-Objekt zurueck:\n"
    '{"correction_type":"response_correction|product_fact_correction|wrong_next_action|missing_step|tone_correction",'
    '"corrected_agent_response":"<deutsch>","corrected_next_action":"<oder leer>",'
    '"suggestion":"<kurze Begruendung>","confidence":<0..1>}'
)


@dataclass(frozen=True)
class CompiledCorrection:
    matched: bool
    correction_type: str
    corrected_agent_response: str
    corrected_next_action: str
    matched_rule: str
    explanation: str
    suggestion: str = ""
    source: str = "deterministic"

    def as_dict(self) -> dict:
        return asdict(self)


def _normalise(value: str) -> str:
    value = unicodedata.normalize("NFKD", value.casefold())
    value = "".join(char for char in value if not unicodedata.combining(char))
    value = value.translate(str.maketrans({"ı": "i", "đ": "d"}))
    return " ".join(value.split())


def _contains_any(text: str, phrases: tuple[str, ...]) -> bool:
    return any(phrase in text for phrase in phrases)


def _infer_next_action(text: str) -> str | None:
    action_rules = (
        (("kimlik", "dogrula", "identity", "richtige person"), "confirm_identity"),
        (("fiyat", "ucret", "preis", "kosten", "14 gun", "deneme"), "explain_price"),
        (("guven", "virus", "link", "app store", "play store"), "address_security"),
        (("kapat", "bitir", "reddetti", "close"), "close_call"),
        (("aktivasyon", "activation", "link gonder"), "send_activation_link"),
    )
    for phrases, action in action_rules:
        if _contains_any(text, phrases):
            return action
    return None


def compile_instruction(
    instruction: str,
    *,
    customer_text: str = "",
    agent_response: str = "",
    current_next_action: str = "",
) -> CompiledCorrection:
    """Compile a supervisor note into a structured correction preview."""
    text = _normalise(instruction)
    original_response = " ".join((agent_response or "").split())

    if not text:
        return CompiledCorrection(
            matched=False,
            correction_type="",
            corrected_agent_response=original_response,
            corrected_next_action=current_next_action,
            matched_rule="empty_instruction",
            explanation="Supervisor instruction is empty.",
        )

    price_terms = (
        "fiyat", "ucret", "ne kadar", "kac para", "14 gun", "ucretsiz",
        "deneme", "preis", "kosten", "kostenlos", "trial",
    )
    if _contains_any(text, price_terms):
        return CompiledCorrection(
            matched=True,
            correction_type="product_fact_correction",
            corrected_agent_response=PRICE_TEMPLATE,
            corrected_next_action="explain_price",
            matched_rule="product_fact_price",
            explanation="Matched price/trial language and applied the approved product-fact template.",
        )

    security_terms = (
        "virus", "guvenli", "guvenlik", "resmi magaza", "app store",
        "play store", "virus-link",
    )
    if _contains_any(text, security_terms):
        return CompiledCorrection(
            matched=True,
            correction_type="product_fact_correction",
            corrected_agent_response=SECURITY_TEMPLATE,
            corrected_next_action="address_security",
            matched_rule="product_fact_security",
            explanation="Matched link/security language and applied the approved security template.",
        )

    wrong_action_terms = (
        "yanlis aksiyon", "aksiyon yanlis", "next action",
        "sonraki adim", "yanlis adim",
    )
    inferred_action = _infer_next_action(text)
    if _contains_any(text, wrong_action_terms) and inferred_action:
        return CompiledCorrection(
            matched=True,
            correction_type="wrong_next_action",
            corrected_agent_response=original_response,
            corrected_next_action=inferred_action,
            matched_rule="wrong_next_action",
            explanation=f"Mapped the requested next step to `{inferred_action}`.",
        )

    missing_step_terms = (
        "once", "eksik", "unut", "atladi", "atlamis",
        "kimlik", "dogrula", "teyit",
    )
    if _contains_any(text, missing_step_terms):
        action = inferred_action or "confirm_identity"
        response = (
            "Bevor wir fortfahren, bestätigen Sie bitte kurz, "
            "dass ich mit der richtigen Person spreche."
            if action == "confirm_identity"
            else original_response
        )
        return CompiledCorrection(
            matched=True,
            correction_type="missing_step",
            corrected_agent_response=response,
            corrected_next_action=action,
            matched_rule="missing_step",
            explanation=f"Detected a missing prerequisite step and selected `{action}`.",
        )

    tone_terms = (
        "kibar", "nazik", "daha kisa", "kisa cevap", "ton",
        "sert", "freundlich", "hoflich",
    )
    if _contains_any(text, tone_terms):
        response = original_response
        if _contains_any(text, ("daha kisa", "kisa cevap")) and response:
            response = re.split(r"(?<=[.!?])\s+", response, maxsplit=1)[0]
        if _contains_any(text, ("kibar", "nazik", "freundlich", "hoflich")):
            response = f"Gern. {response}" if response else "Gern, ich helfe Ihnen weiter."
        return CompiledCorrection(
            matched=True,
            correction_type="tone_correction",
            corrected_agent_response=response,
            corrected_next_action=current_next_action,
            matched_rule="tone_correction",
            explanation="Applied a deterministic tone/length adjustment to the existing response.",
        )

    return CompiledCorrection(
        matched=False,
        correction_type="",
        corrected_agent_response=original_response,
        corrected_next_action=current_next_action,
        matched_rule="no_rule",
        explanation="No safe compiler rule matched. Edit the correction manually.",
    )


def compile_instruction_llm(
    instruction: str,
    *,
    customer_text: str = "",
    agent_response: str = "",
    current_next_action: str = "",
) -> CompiledCorrection | None:
    """LLM editor path. Returns None (→ deterministic fallback) in mock mode, on
    empty input, parse failure, low confidence, or any exception."""
    if settings.vllm_mode == "mock" or not (instruction or "").strip():
        return None
    # Lazy imports keep this module importable without the vLLM/runtime stack.
    from app.core import json_repair, model_runtime, vllm_client

    user = json.dumps(
        {
            "instruction": instruction,
            "customer_text": customer_text,
            "agent_response": agent_response,
            "current_next_action": current_next_action,
        },
        ensure_ascii=False,
    )
    messages = [
        {"role": "system", "content": _EDITOR_SYSTEM},
        {"role": "user", "content": user},
    ]
    try:
        raw = vllm_client.chat(
            messages,
            target=model_runtime.production_serving_target(),
            temperature=0.1,
            max_tokens=400,
        )
        parsed = json_repair.extract_json(raw)
    except Exception:
        logger.exception("review LLM editor call failed")
        return None
    if not isinstance(parsed, dict):
        return None
    corrected = str(parsed.get("corrected_agent_response") or "").strip()
    if not corrected:
        return None
    try:
        confidence = float(parsed.get("confidence", 0))
    except (TypeError, ValueError):
        confidence = 0.0
    if confidence < _MIN_CONFIDENCE:
        return None
    ctype = str(parsed.get("correction_type") or "").strip()
    if ctype not in _ALLOWED_TYPES:
        ctype = "response_correction"
    return CompiledCorrection(
        matched=True,
        correction_type=ctype,
        corrected_agent_response=corrected,
        corrected_next_action=str(parsed.get("corrected_next_action") or current_next_action or "").strip(),
        matched_rule="llm_editor",
        explanation="LLM editor compiled the supervisor instruction.",
        suggestion=str(parsed.get("suggestion") or "").strip(),
        source="llm",
    )


def compile_review(
    instruction: str,
    *,
    customer_text: str = "",
    agent_response: str = "",
    current_next_action: str = "",
) -> CompiledCorrection:
    """Dispatch by settings.review_compiler_mode: 'auto'/'llm' try the LLM editor
    first, then fall back to the deterministic compiler; 'deterministic' skips the LLM."""
    if settings.review_compiler_mode != "deterministic":
        result = compile_instruction_llm(
            instruction,
            customer_text=customer_text,
            agent_response=agent_response,
            current_next_action=current_next_action,
        )
        if result is not None:
            return result
    return compile_instruction(
        instruction,
        customer_text=customer_text,
        agent_response=agent_response,
        current_next_action=current_next_action,
    )
