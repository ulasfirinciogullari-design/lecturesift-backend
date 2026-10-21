"""Versioned assistant offers; currency is independent of interface language."""

import os

MODEL = "gpt-5.6-luna"
IMAGE_MODEL = "gpt-image-1.5"
IMAGE_PRICED_MODEL = "gpt-image-1.5"
IMAGE_CREDITS = 200
VERSION = "2026-10-20-assistant-v2"
# PG18 v4 migration/recovery, runtime roles and both real provider requests
# were verified for the September 9 release. Runtime flags remain opt-in.
SCHEMA_RECOVERY_RELEASE_READY = True
INCLUDED = {"free": 100, "test": 0, "credit": 0, "lite": 1000, "plus": 1500, "pro": 4000, "max": 10000, "business": 0}
PACKS = {"ai_1000": 1000, "ai_3000": 3000, "ai_10000": 10000}
# Deliberate regional price points, not a claim about today's exchange rate.
# JPY/KRW amounts already use their zero-decimal minor units.
# October 2026 revision: TRY, USD, EUR and GBP were set directly; every other
# currency scales its previous packs by 199/399, 399/999 and 999/2999 and is
# rounded to the pattern that row already used (x99 or x49 cents, whole
# hundreds for JPY/SEK/NOK, x90 for BRL, x900 for KRW/CNY/INR/MXN).
PRICES = {
    "TRY": (4900, 11900, 29900), "USD": (199, 399, 999),
    "EUR": (199, 399, 999), "GBP": (179, 349, 849),
    "CAD": (249, 599, 1299), "AUD": (349, 599, 1499),
    "NZD": (299, 699, 1699), "JPY": (300, 600, 1500),
    "KRW": (2900, 5900, 14900), "CNY": (1900, 2900, 6900),
    "INR": (17900, 35900, 82900), "BRL": (1190, 2190, 5290),
    "MXN": (3900, 7900, 19900), "CHF": (199, 399, 999),
    "SEK": (2200, 4400, 11000), "NOK": (2200, 4400, 11000),
    "DKK": (1400, 2990, 7300), "PLN": (799, 1699, 4299),
    "AED": (699, 1499, 3699), "SAR": (699, 1499, 3799),
    "SGD": (249, 599, 1299), "HKD": (1599, 3199, 7999),
}


def enabled() -> bool:
    return SCHEMA_RECOVERY_RELEASE_READY and os.getenv("ASSISTANT_ENABLED", "").lower() == "true"


def images_enabled() -> bool:
    return (
        enabled()
        and IMAGE_MODEL == IMAGE_PRICED_MODEL
        and os.getenv("ASSISTANT_IMAGES_ENABLED", "").lower() == "true"
    )


def offers(currency: str) -> dict:
    selected = currency if currency in PRICES else "USD"
    return {
        "available": enabled(), "version": VERSION, "currency": selected,
        "included": INCLUDED, "input_tokens_per_credit": 1000,
        "output_token_weight": 6, "topup_valid_days": 365,
        "image": {"available": images_enabled(), "credits": IMAGE_CREDITS, "size": "1024x1024"},
        "packs": [
            {"code": code, "credits": credits, "amount_minor": PRICES[selected][i], "currency": selected}
            for i, (code, credits) in enumerate(PACKS.items())
        ],
    }
