"""Deterministic natural-language supervisor instruction compiler."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass

from app.core.product_facts import PRICE_TEMPLATE, SECURITY_TEMPLATE


@dataclass(frozen=True)
class CompiledCorrection:
    matched: bool
    correction_type: str
    corrected_agent_response: str
    corrected_next_action: str
    matched_rule: str
    explanation: str

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
