"""Kritik ürün bilgileri — model belleğine bırakılmaz, burada tek kaynak.

Dokümandaki product facts ve onaylı yanıt şablonları buradan okunur.
Guardrail ve prompt_builder bu modülü import eder.
"""

PRODUCT_FACTS: dict[str, str] = {
    "trial_period":     "14 Tage kostenlos",
    "monthly_price":    "29,99 Euro monatlich",
    "app_stores":       "Apple App Store oder Google Play Store",
    "blocked_numbers":  "über 7.000 bekannte Risikonummern",
    "legal_support":    "Unterstützung bei Anwalts- und Gerichtskosten bis zu 2.500 Euro",
    "support_channel":  "Support über die App",
}

# Onaylı fiyat yanıtı — price_question intent'inde her zaman bu kullanılır
PRICE_TEMPLATE = (
    "Das Gold Paket ist 14 Tage kostenlos. "
    "Danach kostet es 29,99 Euro monatlich."
)

# Onaylı güvenlik yanıtı — security_objection intent'inde her zaman bu kullanılır
SECURITY_TEMPLATE = (
    "Nein, das ist kein Virus-Link. "
    "Der Link führt nur zum offiziellen Apple App Store oder Google Play Store."
)

# Prompt'a eklenecek özet metin (prompt_builder tarafından kullanılır)
PRODUCT_FACTS_TEXT = """
Ürün gerçekleri (bunlar kesin, değiştirilemez):
- Deneme süresi: 14 Tage kostenlos
- Aylık fiyat: 29,99 Euro monatlich (14. günden sonra)
- İndirme: Apple App Store oder Google Play Store
- Engellenen numara: über 7.000 bekannte Risikonummern
- Hukuki destek: Unterstützung bei Anwalts- und Gerichtskosten bis zu 2.500 Euro
- Destek: Support über die App
""".strip()


def format_for_prompt() -> str:
    return PRODUCT_FACTS_TEXT
