"""Critical product facts — not left to model memory; this is the single source of truth.

Product facts and approved response templates are read from here.
Guardrails and prompt_builder import this module.
"""

PRODUCT_FACTS: dict[str, str] = {
    "trial_period":     "14 Tage kostenlos",
    "monthly_price":    "29,99 Euro monatlich",
    "app_stores":       "Apple App Store oder Google Play Store",
    "blocked_numbers":  "über 7.000 bekannte Risikonummern",
    "legal_support":    "Unterstützung bei Anwalts- und Gerichtskosten bis zu 2.500 Euro",
    "support_channel":  "Support über die App",
}

# Normalises raw LLM action names to platform action names
RAW_ACTION_TO_SALES: dict[str, str] = {
    "explain_product_value":        "pitch_product",
    "create_problem_awareness":     "pitch_product",
    "open_store_page":              "guide_customer_step",
    "send_app_link":                "pitch_product",
    "provide_app_link":             "pitch_product",
    "explain_safe_app_link":        "handle_objection",
    "collect_on_screen_confirmation": "collect_step_input",
    "guide_phone_entry":            "guide_customer_step",
    "guide_activation_button":      "guide_customer_step",
    "guide_app_download":           "guide_customer_step",
    "collect_sms_verification":     "collect_step_input",
    "explain_trial_and_price":      "explain_offer_terms",
    "ask_for_activation_commitment": "ask_for_commitment",
    "record_customer_decision":     "close_call",
    "close_successful_sale":        "close_call",
    "confirm_customer_identity":    "qualify_lead",
    "answer_offer_question":        "explain_offer_terms",
    "answer_question_then_resume":  "handle_objection",
}

# Normalises LLM voice tone names
VOICE_TONE_ALIASES: dict[str, str] = {
    "calm":          "warm",
    "empathetic":    "warm",
    "reassuring":    "warm",
    "professional":  "formal",
    "friendly":      "warm",
    "sachlich":      "formal",
}

# Approved price response — always used for price_question intent
PRICE_TEMPLATE = (
    "Das Gold Paket ist 14 Tage kostenlos. "
    "Danach kostet es 29,99 Euro monatlich."
)

# Approved security response — always used for security_objection intent
SECURITY_TEMPLATE = (
    "Nein, das ist kein Virus-Link. "
    "Der Link führt nur zum offiziellen Apple App Store oder Google Play Store."
)

# Summary text injected into the prompt (used by prompt_builder)
PRODUCT_FACTS_TEXT = """
Product facts (fixed, non-negotiable):
- Trial period: 14 Tage kostenlos
- Monthly price: 29,99 Euro monatlich (after day 14)
- Download: Apple App Store oder Google Play Store
- Blocked numbers: über 7.000 bekannte Risikonummern
- Legal support: Unterstützung bei Anwalts- und Gerichtskosten bis zu 2.500 Euro
- Support: Support über die App
""".strip()


def format_for_prompt() -> str:
    return PRODUCT_FACTS_TEXT


def normalize_action(raw_action: str) -> str:
    """Map a raw LLM action name to the platform action name. Returns the input unchanged if unknown."""
    return RAW_ACTION_TO_SALES.get(raw_action, raw_action)


def normalize_voice_tone(tone: str) -> str:
    """Normalise an LLM-generated voice tone name."""
    return VOICE_TONE_ALIASES.get((tone or "").lower(), tone)


def build_sales_script(product: dict, base_script: dict | None = None) -> dict:
    base_script = base_script or {}
    trial = product.get("trial_period", "14 Tage kostenlos")
    price = product.get("monthly_price", "")
    return {
        **base_script,
        "activation": f"Sie können den Schutz {trial} starten; danach gilt {price}.",
        "summary": (
            f"Das Anrufblocker Gold Paket bietet {trial} testen, "
            "Risikoprüfung Ihrer Rufnummer und Schutz vor bekannten Betrugsnummern."
        ),
    }


def build_objection_handling(product: dict, base: dict | None = None) -> dict:
    base = dict(base or {})
    trial = product.get("trial_period", "14 Tage kostenlos")
    price = product.get("monthly_price", "")
    return {
        **base,
        "price": (
            f"Der Schutz ist {trial}; danach gilt {price}. "
            "Bitte öffnen Sie jetzt die App, damit wir die Prüfung Ihrer Rufnummer starten können."
        ),
    }
