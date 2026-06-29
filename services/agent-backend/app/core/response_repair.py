"""Response repair layer.

Deterministically fixes errors in LLM-generated agent_response strings.
Runs after guardrails, before the final response is returned.
"""
from __future__ import annotations

import re

from app.core.guardrails import customer_wants_delay
from app.core.product_facts import (
    DELAY_DEFERRAL_TEMPLATE,
    FORBIDDEN_DATA_TEMPLATE,
    FORBIDDEN_RESPONSE_PATTERNS,
    PRODUCT_FACTS,
)


def repair_all(response: str, state: dict, customer_message: str = "") -> tuple[str, list[str]]:
    """Apply all repair rules in sequence.

    Returns (fixed_response, applied_repairs) where applied_repairs lists
    which rules were triggered.
    """
    applied: list[str] = []
    filled = (state or {}).get("filled_slots") or {}

    r, hit = repair_trial_in_response(response)
    if hit:
        applied.append("trial_in_response")
        response = r

    r, hit = repair_stale_vague_price(response, filled)
    if hit:
        applied.append("stale_vague_price")
        response = r

    r, hit = repair_price_deflection(response, filled)
    if hit:
        applied.append("price_deflection")
        response = r

    r, hit = repair_sms_code_request(response)
    if hit:
        applied.append("sms_code_request")
        response = r

    r, hit = repair_premature_link(response, filled)
    if hit:
        applied.append("premature_link")
        response = r

    r, hit = repair_forbidden_data_request(response)
    if hit:
        applied.append("forbidden_data_request")
        response = r

    r, hit = repair_delay_phone_request(response, customer_message)
    if hit:
        applied.append("delay_phone_request")
        response = r

    return response, applied


def repair_trial_in_response(response: str) -> tuple[str, bool]:
    """Fix outdated trial period references if the configured trial differs from 14 days."""
    trial = PRODUCT_FACTS.get("trial_period", "")
    if not trial or not response:
        return response, False

    m = re.search(r"(\d+)", trial)
    days = m.group(1) if m else "14"
    if days == "14":
        return response, False

    out = response
    out = re.sub(r"14\s*Tage\s*kostenlos", trial, out, flags=re.I)
    out = re.sub(r"14\s*kostenlosen\s*Tagen", f"{days} kostenlosen Tagen", out, flags=re.I)
    out = re.sub(r"14-tägige", f"{days}-tägige", out, flags=re.I)
    out = re.sub(r"nach\s*14\s*Tagen", f"nach {days} Tagen", out, flags=re.I)
    changed = out != response
    return out, changed


def repair_stale_vague_price(response: str, filled_slots: dict) -> tuple[str, bool]:
    """Replace vague price phrases like 'aus unserem Angebot' with the real price."""
    msg = (response or "").lower()
    if "aus unserem angebot" in msg or "preis direkt in der app" in msg:
        return _format_price_answer(filled_slots), True
    return response, False


def repair_price_deflection(response: str, filled_slots: dict) -> tuple[str, bool]:
    """Fix responses that deflect price questions back to the customer."""
    msg = (response or "").lower()
    _deflection_triggers = [
        "welche rate angezeigt", "welchen betrag angezeigt", "welchen preis angezeigt",
        r"lesen sie mir.*rate", r"lesen sie mir.*preis",
        r"lesen sie mir.*betrag", r"lesen sie mir.*kosten",
        r"prüfen sie.*rate", r"prüfen sie.*preis",
        r"schauen sie.*rate", r"schauen sie.*preis",
        "in der app angezeigt",
    ]
    for t in _deflection_triggers:
        if re.search(t, msg):
            return _format_price_answer(filled_slots), True
    return response, False


def repair_sms_code_request(response: str) -> tuple[str, bool]:
    """Block responses that ask the customer to read out an SMS code — security rule."""
    msg = (response or "").lower()
    _triggers = [
        "lesen sie mir den code", "nennen sie mir den code", "code vor",
        "sagen sie mir den code", "code durch", "welchen code",
        "code vorlesen", "code nennen", "code kurz vor",
        "code direkt eingeben", "ich ihn direkt eingeben",
        "code notiert", "lesen sie mir bitte den code",
    ]
    if any(t in msg for t in _triggers):
        return (
            "Bitte geben Sie den Code direkt in der App ein — "
            "Sie müssen ihn mir nicht nennen. "
            "Sagen Sie mir Bescheid, sobald die Bestätigung erscheint.",
            True,
        )
    return response, False


def repair_forbidden_data_request(response: str) -> tuple[str, bool]:
    """Block IBAN, full phone number, address, birth date collection on the call."""
    msg = (response or "").lower()
    if not any(p in msg for p in FORBIDDEN_RESPONSE_PATTERNS):
        return response, False
    return (FORBIDDEN_DATA_TEMPLATE, True)


def repair_delay_phone_request(response: str, customer_message: str) -> tuple[str, bool]:
    """When customer wants time, replace phone-collection phrasing with deferral."""
    if not customer_wants_delay(customer_message):
        return response, False
    msg = (response or "").lower()
    if not any(p in msg for p in ("telefonnummer", "vollständige nummer", "ihre nummer")):
        return response, False
    return (DELAY_DEFERRAL_TEMPLATE + " Einen schönen Tag!", True)


def repair_premature_link(response: str, filled_slots: dict) -> tuple[str, bool]:
    """Replace premature store/link pushes with a value-first sentence."""
    msg = (response or "").lower()
    if "product_value_explained" in filled_slots:
        return response, False

    _link_tokens = [
        "app store", "google play", "download-link", "link per sms",
        "öffnen sie den link", "play store", "sicheren link",
        "download link", "app-store-link",
    ]
    if any(t in msg for t in _link_tokens):
        return (
            "Bevor wir zur Installation kommen: Anrufblocker erkennt und blockiert "
            "bekannte Betrugsnummern automatisch. "
            "Möchten Sie, dass ich Sie gleich Schritt für Schritt durch die sichere Einrichtung führe?",
            True,
        )
    return response, False


def _format_price_answer(filled_slots: dict) -> str:
    trial = PRODUCT_FACTS.get("trial_period", "14 Tage kostenlos")
    price = PRODUCT_FACTS.get("monthly_price", "")
    filled = filled_slots or {}
    link_ready = "product_value_explained" in filled and "safe_link_explained" in filled
    if link_ready:
        return (
            f"Der Schutz ist {trial}; danach gilt {price}. "
            "Bitte öffnen Sie jetzt die App — möchten Sie fortfahren?"
        )
    return (
        f"Der Schutz ist {trial}; danach gilt {price}. "
        "Zuerst zeige ich Ihnen kurz, wie der Schutz funktioniert — darf ich?"
    )
