"""Response repair layer.

Surgical fixes only — preserve the model's good wording; strip or block real violations.
"""
from __future__ import annotations

import re

from app.core.guardrails import customer_wants_delay
from app.core import content_store
from app.core.product_facts import (
    FORBIDDEN_FACT_PATTERNS,
    FORBIDDEN_RESPONSE_PATTERNS,
)

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def _is_sanctioned_template(response: str) -> bool:
    """True if the text is an operator-approved canned answer already applied by
    a guardrail (price, security, …). These are approved verbatim and must pass
    through untouched — otherwise a later repair (e.g. premature_link seeing the
    App-Store wording in the security template) would mangle approved content."""
    text = (response or "").strip()
    if not text:
        return False
    try:
        from app.core import product_facts as pf
        return any(
            text == (content_store.canned(key) or "").strip()
            for key in pf.CANNED_ANSWERS
        )
    except Exception:  # noqa: BLE001 — repair must never crash on content lookup
        return False


def repair_all(response: str, state: dict, customer_message: str = "") -> tuple[str, list[str]]:
    applied: list[str] = []
    filled = (state or {}).get("filled_slots") or {}

    # Approved templates already enforced by guardrails are final — skip repairs.
    if _is_sanctioned_template(response):
        return response, applied

    # Legal/accuracy floor only. The app-funnel repairs (premature_link,
    # stale_vague_price, price_deflection) were removed so the model can offer
    # alternatives (self-entry, website) and phrase the offer in its own words.
    r, hit = repair_trial_in_response(response)
    if hit:
        applied.append("trial_in_response")
        response = r

    r, hit = repair_sms_code_request(response)
    if hit:
        applied.append("sms_code_request")
        response = r

    r, hit = repair_invented_facts(response)
    if hit:
        applied.append("invented_facts")
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
    trial = content_store.product_facts().get("trial_period", "")
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
    return out, out != response


def repair_sms_code_request(response: str) -> tuple[str, bool]:
    msg = (response or "").lower()
    triggers = [
        "lesen sie mir den code", "nennen sie mir den code", "code vor",
        "sagen sie mir den code", "code durch", "welchen code",
        "code vorlesen", "code nennen", "code kurz vor",
        "code direkt eingeben", "ich ihn direkt eingeben",
        "code notiert", "lesen sie mir bitte den code",
    ]
    if any(t in msg for t in triggers):
        return (
            "Bitte geben Sie den Code direkt in der App ein — "
            "Sie müssen ihn mir nicht nennen. "
            "Sagen Sie mir Bescheid, sobald die Bestätigung erscheint.",
            True,
        )
    return response, False


def repair_invented_facts(response: str) -> tuple[str, bool]:
    """Drop sentences that claim PDF-unlisted product capabilities."""
    if not response or not response.strip():
        return response, False

    sentences = _SENTENCE_SPLIT_RE.split(response.strip())
    kept = [
        s for s in sentences
        if s.strip() and not any(p in s.lower() for p in FORBIDDEN_FACT_PATTERNS)
    ]
    if len(kept) == len(sentences):
        return response, False
    if kept:
        return " ".join(kept).strip(), True
    return content_store.canned("problem_awareness"), True


def repair_forbidden_data_request(response: str) -> tuple[str, bool]:
    msg = (response or "").lower()
    if not any(p in msg for p in FORBIDDEN_RESPONSE_PATTERNS):
        return response, False
    return (content_store.canned("forbidden_data"), True)


def repair_delay_phone_request(response: str, customer_message: str) -> tuple[str, bool]:
    if not customer_wants_delay(customer_message):
        return response, False
    msg = (response or "").lower()
    if not any(p in msg for p in ("telefonnummer", "vollständige nummer", "ihre nummer")):
        return response, False
    return (content_store.canned("delay_deferral") + " Einen schönen Tag!", True)
