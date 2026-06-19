"""Tam akış test senaryoları kataloğu.

get_scenario_catalog() — tam konuşma dizileri (multi-turn eval için).
scenarios.jsonl — tek tur birim testleri (guardrail/intent doğruluğu için).
"""
from __future__ import annotations

import re


def _trial_days(trial_period: str) -> str:
    m = re.search(r"(\d+)", trial_period or "")
    return m.group(1) if m else "14"


def get_scenario_catalog(trial_period: str = "14 Tage kostenlos") -> dict[str, list[str]]:
    """Multi-turn test senaryolarını döndürür. Her senaryo müşteri mesajlarının listesidir."""
    days = _trial_days(trial_period)
    return {
        # Tam akış — kimlik → değer → link → sms → aktivasyon → fiyat → kabul
        "full_flow": [
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
        # Önce fiyat — müşteri baştan fiyat sorar, köprülenip akış tamamlanır
        "price_first": [
            "Ja, das bin ich.",
            "Was kostet das genau?",
            f"Was passiert nach {days} Tagen?",
            "Verstanden. Wie schützt mich das genau?",
            "Okay, schicken Sie mir den sicheren Link.",
            "Ja, die App ist offen.",
            "Schutz aktivieren steht da.",
            "Ja, machen wir das.",
        ],
        # Kısa fiyat testi — köprülenme olmaz, premature_link/stale_price ölçümü
        "price_probe": [
            "Ja, das bin ich.",
            "Was kostet das genau?",
            f"Was passiert nach {days} Tagen?",
            "Ja, machen wir das.",
        ],
        # Link itirazı — güvenlik objection → ajan açıklar → kabul
        "objection_link": [
            "Was möchten Sie von mir?",
            "Warum haben Sie meine Nummer?",
            "Ist das ein Virus-Link?",
            "Okay, ich öffne den Link.",
            "Ja, die App ist offen.",
        ],
        # Sert red — beklenen çıktı: respect_decline_and_end_call
        "decline": [
            "Ja, das bin ich.",
            "Was wollen Sie genau?",
            "Nein, kein Interesse. Bitte nicht mehr anrufen.",
        ],
    }
