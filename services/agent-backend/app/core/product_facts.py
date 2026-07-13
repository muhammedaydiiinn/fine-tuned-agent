"""PDF-aligned product facts and approved templates — single source of truth.

Source: docs/rules/anrufblocker_gold_paket_training.pdf (v1.0, Mai 2026)
Runtime prompt, guardrails, and training-worker policy txt derive from here.
"""

PRODUCT_FACTS: dict[str, str] = {
    "trial_period": "14 Tage kostenlos",
    "monthly_price": "29,99 Euro monatlich",
    "check_price_normal": "18 Euro",
    "check_price_today": "heute einmalig kostenfrei",
    "app_stores": "Apple App Store oder Google Play Store",
    "blocked_numbers": "über 7.000 bekannte Risikonummern",
    "risk_entries_example": "22 auffällige Einträge",
    "risk_entries_range": "18 bis 25",
    "legal_support": "Unterstützung bei Anwalts- und Gerichtskosten bis zu 2.500 Euro",
    "support_channel": "Support über die App",
}

# PDF section 04 (WICHTIGE REGELN) + the two code-enforced data-safety rules
PDF_RULES: tuple[str, ...] = (
    "Script möglichst originalgetreu verwenden.",
    "Die Zahl der auffälligen Einträge (22) gelegentlich variieren (18–25).",
    "Keine Adressen, Bankdaten oder persönlichen Informationen vom Kunden verlangen.",
    "Keine Tonaufnahme notwendig.",
    "Immer bestimmt, aber kontrolliert sprechen — klar, selbstbewusst, mit ruhiger Autorität.",
    "Rufnummer nur in der App eintragen, nicht am Telefon abfragen.",
    "SMS-Bestätigungscode nur in der App eingeben, nicht vorlesen lassen.",
)

FORBIDDEN_RESPONSE_PATTERNS: tuple[str, ...] = (
    "vollständige telefonnummer",
    "telefonnummer, damit ich",
    "ihre telefonnummer ab",
    "vollständige iban",
    "kontonummer",
    "kundennummer",
    "geburtsdatum",
    "ihre adresse",
    "bankdaten",
)

RAW_ACTION_TO_SALES: dict[str, str] = {
    "explain_product_value": "pitch_product",
    "create_problem_awareness": "pitch_product",
    "open_store_page": "guide_customer_step",
    "send_app_link": "guide_customer_step",
    "provide_app_link": "guide_customer_step",
    "explain_safe_app_link": "handle_objection",
    "collect_on_screen_confirmation": "collect_step_input",
    "guide_phone_entry": "guide_customer_step",
    "guide_activation_button": "guide_customer_step",
    "guide_app_download": "guide_customer_step",
    "collect_sms_verification": "collect_step_input",
    "explain_trial_and_price": "explain_offer_terms",
    "explain_price": "explain_offer_terms",
    "explain_trial": "explain_offer_terms",
    "ask_for_activation_commitment": "ask_for_commitment",
    "record_customer_decision": "close_call",
    "close_successful_sale": "close_call",
    "confirm_customer_identity": "qualify_lead",
    "answer_offer_question": "explain_offer_terms",
    "answer_question_then_resume": "handle_objection",
    "address_security": "handle_objection",
    "request_identity": "qualify_lead",
    "greeting_and_introduction": "qualify_lead",
}

CLOSING_NEXT_ACTIONS: frozenset[str] = frozenset({
    "close_call",
    "respect_decline_and_end_call",
})

IDENTITY_NEXT_ACTIONS: frozenset[str] = frozenset({
    "qualify_lead",
    "confirm_identity",
    "request_identity",
})

VOICE_TONE_ALIASES: dict[str, str] = {
    "calm": "warm",
    "empathetic": "warm",
    "reassuring": "warm",
    "professional": "formal",
    "friendly": "warm",
    "sachlich": "formal",
}

# PDF objection handling — verbatim approved wording
PRICE_TEMPLATE = (
    "Das Gold Paket ist 14 Tage kostenlos. "
    "Danach kostet es 29,99 Euro monatlich."
)

CHECK_PRICE_TEMPLATE = (
    "Normalerweise kostet dieser Check 18 Euro, "
    "für Sie ist er heute einmalig kostenfrei."
)

SECURITY_TEMPLATE = (
    "Das kann ich absolut verstehen. "
    "Der Link führt ausschließlich zum offiziellen Apple App Store oder Google Play Store. "
    "Nur dort verifizierte und sichere Applikationen sind verfügbar. "
    "Ich habe keinen Zugriff auf Ihre Daten."
)

DELAY_DEFERRAL_TEMPLATE = (
    "Natürlich — nehmen Sie sich die Zeit, die Sie brauchen. "
    "Die Rufnummer geben Sie später nur in der App ein, nicht am Telefon."
)

FORBIDDEN_DATA_TEMPLATE = (
    "Das brauchen wir am Telefon nicht — alle sensiblen Angaben geben Sie "
    "später sicher direkt in der App ein."
)

CLOSING_BRIEF_TEMPLATE = "Auf Wiederhören."

# Model sometimes invents capabilities not listed in the PDF.
FORBIDDEN_FACT_PATTERNS: tuple[str, ...] = (
    "im ausland",
    "akku",
    "batterie",
    "manuell wieder freigeb",
    "manuell freigeb",
    "unbegrenzt block",
    "unlimited",
)

PRICE_INTENT_ALIASES: frozenset[str] = frozenset({
    "price_question",
    "free_question",
    "price_inquiry",
    "contract_terms_inquiry",
})

CHECK_EXPLAIN_TEMPLATE = (
    "Dabei prüfen wir über verschiedene Schnittstellen und API-Verbindungen, wie "
    "häufig Ihre Nummer auftaucht, in welchen Werbe-, Callcenter- oder "
    "Beschwerdedatenbanken sie registriert wurde und ob bereits Meldungen zu "
    "verdächtigen Aktivitäten vorliegen. "
    f"Normalerweise kostet dieser Check {PRODUCT_FACTS['check_price_normal']}, "
    f"für Sie ist er {PRODUCT_FACTS['check_price_today']}."
)

PROBLEM_AWARENESS_TEMPLATE = (
    "Wir haben festgestellt, dass Ihre Rufnummer für Betrugsversuche und "
    "Datenmissbrauch verwendet und weitergegeben wird. Wir können Ihnen jetzt "
    "live auf Ihrem Handy zeigen, wo Ihre Nummer überall eingetragen ist und "
    "welche Betrugsversuche aktuell gegen Sie laufen."
)

# Objection handling / FAQ (argümanlar + sss) — PDF script wording, adapt naturally.
# Structured so the owner can add/edit/remove entries from the panel; rendered
# into the prompt as `- "<trigger>" → <answer>` lines by format_for_prompt().
OBJECTION_FAQ: tuple[dict[str, str], ...] = (
    {
        "trigger": "Ich möchte nicht",
        "answer": (
            "In Ordnung. Dann stoppe ich den Vorgang zur Schutz- und Sperranfrage bei "
            "den 22 erkannten Firmen vorerst. Ich möchte nur sichergehen, dass Ihnen die "
            "vollen Konsequenzen Ihrer Entscheidung wirklich bewusst sind. Wenn Sie die "
            "Absicherung wirklich nicht wünschen, sagen Sie mir bitte jetzt deutlich NEIN. "
            "Nur mit einem klaren NEIN kann ich den Vorgang stoppen."
        ),
    },
    {
        "trigger": "Wie überprüfen Sie das?",
        "answer": (
            "Dabei prüfen wir über verschiedene Schnittstellen und API-Verbindungen, wie "
            "häufig Ihre Nummer auftaucht, in welchen Werbe-, Callcenter- oder "
            "Beschwerdedatenbanken sie registriert wurde und ob bereits Meldungen zu "
            "verdächtigen Aktivitäten vorliegen. Normalerweise kostet dieser Check 18 Euro, "
            "für Sie ist er heute einmalig kostenfrei."
        ),
    },
    {
        "trigger": "Ich habe kein Schreiben bekommen",
        "answer": (
            "Verstehe ich. Aus organisatorischen und kostenbedingten Gründen versenden wir "
            "die Schreiben in der Regel nicht erneut. Genau deshalb rufe ich Sie an und "
            "führe Sie jetzt direkt durch die App."
        ),
    },
    {
        "trigger": "Nach 14 Tagen kann ich kündigen",
        "answer": (
            "Da haben Sie vollkommen Recht, viele denken am Anfang genauso. Jedoch bleibt "
            "es nicht bei den 22 Datenbanken, Ihre Rufnummer wird weiterverkauft. Nach der "
            "Kündigung wird der Schutz wieder inaktiv. Nur mit unserem aktiven Schutz können "
            "wir die drohenden Schäden noch rechtzeitig stoppen."
        ),
    },
    {
        "trigger": "Woher haben Sie meine Nummer?",
        "answer": (
            "Gute Frage. Ihre Nummer wurde im Rahmen einer allgemeinen Prüfung über externe "
            "Datenquellen erfasst. Wir sehen keine persönlichen Daten, nur dass eine Nummer "
            "vorhanden ist."
        ),
    },
    {
        "trigger": "Ist das ein Virus-Link?",
        "answer": (
            "Das kann ich absolut verstehen. Der Link führt ausschließlich zum offiziellen "
            "Apple App Store oder Google Play Store. Nur dort verifizierte und sichere "
            "Applikationen sind verfügbar. Ich habe keinen Zugriff auf Ihre Daten."
        ),
    },
    {
        "trigger": "Ich blockiere schon alles",
        "answer": (
            "Das ist gut. Die Frage ist nur: Blockieren Sie die Nummern oder wissen Sie auch, "
            "wo Ihre Nummer überall gespeichert ist? Genau das zeigt Ihnen der Check."
        ),
    },
)

# Canned answers (hazır cevaplar) — approved verbatim wording the code enforces on
# the model output (guardrails / response_repair / review_compiler). Keyed so each
# one is editable from the panel; the module constants above are the seed defaults.
CANNED_ANSWERS: dict[str, str] = {
    "price": PRICE_TEMPLATE,
    "check_price": CHECK_PRICE_TEMPLATE,
    "security": SECURITY_TEMPLATE,
    "delay_deferral": DELAY_DEFERRAL_TEMPLATE,
    "forbidden_data": FORBIDDEN_DATA_TEMPLATE,
    "closing_brief": CLOSING_BRIEF_TEMPLATE,
    "check_explain": CHECK_EXPLAIN_TEMPLATE,
    "problem_awareness": PROBLEM_AWARENESS_TEMPLATE,
}

# Technical output contract — appended in code, never shown in the panel. Keeps
# the human-editable sales script free of JSON/schema jargon while still telling
# the model exactly how to shape its output.
SYSTEM_OUTPUT_CONTRACT: str = (
    "Respond with a single JSON object only — no text before or after it — "
    "using exactly these fields:\n"
    "{\n"
    '  "intent": "<customer intent>",\n'
    '  "emotion": "<customer emotion>",\n'
    '  "risk": "<low|medium|high>",\n'
    '  "next_action": "<next sales step>",\n'
    '  "behavior_strategy": "<approach strategy>",\n'
    '  "allowed_to_continue": <true|false>,\n'
    '  "agent_response": "<the German sentence you say to the customer>",\n'
    '  "voice_style": {"tone": "clear", "pace": "normal", "confidence": "high"}\n'
    "}\n"
    "agent_response is always in German and never in ALL CAPS. "
    "Set allowed_to_continue to false when the call should end."
)

_LINK_PUSH_TOKENS: tuple[str, ...] = (
    "app store",
    "google play",
    "download-link",
    "link per sms",
    "öffnen sie den link",
    "play store",
    "sicheren link",
    "download link",
    "app-store-link",
)


def normalize_action(raw_action: str) -> str:
    return RAW_ACTION_TO_SALES.get((raw_action or "").strip(), (raw_action or "").strip())


def normalize_next_action(raw_action: str) -> str:
    return normalize_action(raw_action)


def normalize_voice_tone(tone: str) -> str:
    return VOICE_TONE_ALIASES.get((tone or "").lower(), tone)


def format_for_prompt() -> str:
    """Prompt block injected after system_instruction.txt.

    Reads the live (panel-editable) product facts / rules / objection FAQ from
    the DB-backed content store, falling back to the module defaults above.
    """
    from app.core import content_store

    facts = content_store.product_facts()
    rules = content_store.pdf_rules()
    faq = content_store.objection_faq()
    lines = [
        "Product facts (PDF v1.0 — fixed, do not invent other values):",
        f"- Trial: {facts['trial_period']}",
        f"- Monthly price after trial: {facts['monthly_price']}",
        f"- One-time check (comparison): {facts['check_price_normal']} — {facts['check_price_today']}",
        f"- Download: {facts['app_stores']}",
        f"- Blocked numbers: {facts['blocked_numbers']}",
        f"- Scan result example: {facts['risk_entries_example']} (vary {facts['risk_entries_range']})",
        f"- Legal support: {facts['legal_support']}",
        f"- Support: {facts['support_channel']}",
        "",
        "PDF rules (section 04 — enforced in code):",
    ]
    lines.extend(f"- {rule}" for rule in rules)
    lines.extend([
        "",
        "Objection themes (use PDF script wording, adapt naturally):",
    ])
    lines.extend(
        f'- "{item["trigger"]}" → {item["answer"]}'
        for item in faq
        if item.get("trigger")
    )
    return "\n".join(lines)


def build_sales_script(product: dict, base_script: dict | None = None) -> dict:
    base_script = base_script or {}
    trial = product.get("trial_period", PRODUCT_FACTS["trial_period"])
    price = product.get("monthly_price", PRODUCT_FACTS["monthly_price"])
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
    return {
        **base,
        "price": PRICE_TEMPLATE,
        "check_price": CHECK_PRICE_TEMPLATE,
        "security": SECURITY_TEMPLATE,
    }
