"""Supervisor-driven browser voice test scenarios."""

VOICE_TEST_SCENARIOS: dict[str, dict[str, object]] = {
    "full_flow": {
        "label": "Full activation flow",
        "description": "Identity, value, link, installation, price, and activation.",
        "turns": [
            "Ja, das bin ich.",
            "Ja, bitte erklären Sie mir kurz den Schutz.",
            "Okay, schicken Sie mir den sicheren Link.",
            "Ja, ich habe den Link im App Store geöffnet.",
            "Die App wird gerade heruntergeladen.",
            "Ja, die App ist jetzt offen.",
            "Auf dem Bildschirm steht Telefonnummer bestätigen.",
            "Der SMS-Code ist da, ich habe ihn eingegeben.",
            "Jetzt steht da Schutz aktivieren.",
            "Was kostet das nach der Testphase?",
            "Ja, machen wir das.",
            "Der Schutz ist aktiv, danke.",
        ],
    },
    "price_first": {
        "label": "Price-first customer",
        "description": "The customer asks about price before accepting the flow.",
        "turns": [
            "Ja, das bin ich.",
            "Was kostet das genau?",
            "Was passiert nach 14 Tagen?",
            "Verstanden. Wie schützt mich das genau?",
            "Okay, schicken Sie mir den sicheren Link.",
            "Ja, die App ist offen.",
            "Schutz aktivieren steht da.",
            "Ja, machen wir das.",
        ],
    },
    "price_probe": {
        "label": "Short price probe",
        "description": "A compact price and trial-period consistency check.",
        "turns": [
            "Ja, das bin ich.",
            "Was kostet das genau?",
            "Was passiert nach 14 Tagen?",
            "Ja, machen wir das.",
        ],
    },
    "objection_link": {
        "label": "Link security objection",
        "description": "The customer questions the call origin and activation link.",
        "turns": [
            "Was möchten Sie von mir?",
            "Warum haben Sie meine Nummer?",
            "Ist das ein Virus-Link?",
            "Okay, ich öffne den Link.",
            "Ja, die App ist offen.",
        ],
    },
    "decline": {
        "label": "Hard decline",
        "description": "The agent must respect a clear opt-out and end the call.",
        "turns": [
            "Ja, das bin ich.",
            "Was wollen Sie genau?",
            "Nein, kein Interesse. Bitte nicht mehr anrufen.",
        ],
    },
    "free_conversation": {
        "label": "Free conversation",
        "description": "No scripted prompts; explore the agent manually.",
        "turns": [],
    },
}


def get_voice_test_scenario(scenario_id: str) -> dict[str, object]:
    return VOICE_TEST_SCENARIOS.get(
        scenario_id,
        VOICE_TEST_SCENARIOS["free_conversation"],
    )
