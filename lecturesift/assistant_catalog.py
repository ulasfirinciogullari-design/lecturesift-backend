"""Versioned assistant offers; currency is independent of interface language."""

import os

MODEL = "gpt-5.6-luna"
VERSION = "2026-09-08-assistant-v1"
# This capability stays closed until the three-table recovery contract and
# provider access have been verified in the release environment.
SCHEMA_RECOVERY_RELEASE_READY = False
INCLUDED = {"free": 50, "test": 0, "credit": 0, "lite": 500, "plus": 1500, "pro": 4000, "max": 10000, "business": 0}
PACKS = {"ai_1000": 1000, "ai_3000": 3000, "ai_10000": 10000}
# Deliberate regional price points, not a claim about today's exchange rate.
# JPY/KRW amounts already use their zero-decimal minor units.
PRICES = {
    "TRY": (14900, 34900, 99900), "USD": (399, 999, 2999),
    "EUR": (399, 999, 2999), "GBP": (349, 849, 2499),
    "CAD": (549, 1399, 3999), "AUD": (649, 1599, 4499),
    "NZD": (699, 1799, 4999), "JPY": (600, 1500, 4500),
    "KRW": (5900, 14900, 44900), "CNY": (2900, 6900, 19900),
    "INR": (34900, 89900, 249900), "BRL": (2290, 5490, 15990),
    "MXN": (7900, 19900, 59900), "CHF": (399, 999, 2999),
    "SEK": (4500, 10900, 32900), "NOK": (4500, 10900, 32900),
    "DKK": (2900, 7490, 21900), "PLN": (1699, 4299, 12999),
    "AED": (1499, 3699, 10999), "SAR": (1499, 3799, 11299),
    "SGD": (549, 1399, 3999), "HKD": (3199, 7999, 23999),
}


def enabled() -> bool:
    return SCHEMA_RECOVERY_RELEASE_READY and os.getenv("ASSISTANT_ENABLED", "").lower() == "true"


def offers(currency: str) -> dict:
    selected = currency if currency in PRICES else "USD"
    return {
        "available": enabled(), "version": VERSION, "currency": selected,
        "included": INCLUDED, "input_tokens_per_credit": 1000,
        "output_token_weight": 6, "topup_valid_days": 365,
        "packs": [
            {"code": code, "credits": credits, "amount_minor": PRICES[selected][i], "currency": selected}
            for i, (code, credits) in enumerate(PACKS.items())
        ],
    }
