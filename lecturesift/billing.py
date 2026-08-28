"""Provider-neutral subscription catalog and regional display prices."""

from __future__ import annotations

from dataclasses import asdict, dataclass


SUPPORTED_CURRENCIES = (
    "TRY", "USD", "EUR", "GBP", "CAD", "AUD", "NZD", "JPY", "KRW",
    "CNY", "INR", "BRL", "MXN", "CHF", "SEK", "NOK", "DKK", "PLN",
    "AED", "SAR", "SGD", "HKD",
)


@dataclass(frozen=True)
class Plan:
    code: str
    kind: str
    minutes: int | None
    export_formats: tuple[str, ...]
    priority: str
    team_seats: int
    quiz_questions: int | None
    flashcards: int | None
    summary_profiles: tuple[str, ...]
    history_days: int
    try_amount_minor: int | None = None
    featured: bool = False

    @property
    def export_enabled(self) -> bool:
        return self.download_enabled

    @property
    def download_enabled(self) -> bool:
        return self.code != "free"

    @property
    def ad_free(self) -> bool:
        return self.code in {"lite", "plus", "pro", "max", "business"}

    def public(self, currency: str = "TRY") -> dict:
        selected_currency = currency if currency in SUPPORTED_CURRENCIES else "TRY"
        regional = REGIONAL_PRICES.get(self.code, {})
        display_amount = regional.get(selected_currency)
        return {
            **asdict(self),
            "export_enabled": self.export_enabled,
            "name_key": f"billing.plan.{self.code}.name",
            "description_key": f"billing.plan.{self.code}.description",
            "price_source": "manual_bank_transfer" if self.try_amount_minor is not None else "payment_provider",
            "manual_price": (
                {"currency": "TRY", "amount_minor": self.try_amount_minor}
                if self.try_amount_minor is not None
                else None
            ),
            "display_price": (
                {"currency": selected_currency, "amount_minor": display_amount}
                if display_amount is not None
                else None
            ),
            "entitlements": {
                "minutes": self.minutes,
                "quiz_questions": self.quiz_questions,
                "flashcards": self.flashcards,
                "export_formats": list(self.export_formats),
                "summary_profiles": list(self.summary_profiles),
                "history_days": self.history_days,
                "team_seats": self.team_seats,
                "priority": self.priority,
                "ad_free": self.ad_free,
                "rewarded_minutes_eligible": not self.ad_free,
                "download_enabled": self.download_enabled,
            },
        }


ALL_SUMMARY_PROFILES = ("short", "standard", "detailed", "exam", "five_minute")

PLANS = (
    Plan("free", "free", 60, ("pdf",), "standard", 1, 10, 20, ("short", "standard"), 7),
    Plan("credit", "one_time", 180, ("pdf", "docx", "txt"), "standard", 1, 20, 40, ALL_SUMMARY_PROFILES, 30, 19900),
    Plan("lite", "subscription", 600, ("pdf", "docx", "txt"), "standard", 1, 20, 40, ALL_SUMMARY_PROFILES, 90, 34900),
    Plan("plus", "subscription", 2400, ("pdf", "docx", "txt"), "standard", 1, 30, 60, ALL_SUMMARY_PROFILES, 180, 69900, featured=True),
    Plan("pro", "subscription", 6000, ("pdf", "docx", "txt"), "priority", 1, 30, 60, ALL_SUMMARY_PROFILES, 365, 129900),
    Plan("max", "subscription", 15000, ("pdf", "docx", "txt"), "priority", 1, 30, 60, ALL_SUMMARY_PROFILES, 730, 249900),
    Plan("business", "quote", None, ("pdf", "docx", "txt"), "priority", 10, None, None, ALL_SUMMARY_PROFILES, 730),
)

PLAN_BY_CODE = {plan.code: plan for plan in PLANS}

# Intentional regional product prices, not volatile exchange-rate conversions.
# The connected checkout provider remains the source of truth for tax and the
# final amount charged.
_PRICE_PLAN_CODES = ("free", "credit", "lite", "plus", "pro", "max")
_REGIONAL_PRICE_POINTS = {
    "TRY": (0, 19900, 34900, 69900, 129900, 249900),
    "USD": (0, 500, 900, 1800, 3300, 6300),
    "EUR": (0, 500, 900, 1700, 3100, 5900),
    "GBP": (0, 400, 800, 1500, 2700, 5200),
    "CAD": (0, 700, 1200, 2500, 4500, 8500),
    "AUD": (0, 800, 1400, 2800, 5200, 9900),
    "NZD": (0, 900, 1600, 3100, 5700, 10900),
    # JPY and KRW have zero-decimal minor units; the other values use cents.
    "JPY": (0, 800, 1400, 2800, 5000, 9500),
    "KRW": (0, 7000, 13000, 25000, 47000, 89000),
    "CNY": (0, 3600, 6500, 12900, 23900, 45900),
    "INR": (0, 39900, 74900, 149900, 279900, 529900),
    "BRL": (0, 2500, 4500, 8900, 16900, 31900),
    "MXN": (0, 9900, 17900, 34900, 64900, 124900),
    "CHF": (0, 500, 800, 1600, 3000, 5700),
    "SEK": (0, 5500, 9900, 19900, 36900, 69900),
    "NOK": (0, 5900, 10900, 21900, 39900, 76900),
    "DKK": (0, 3500, 6500, 12900, 22900, 44900),
    "PLN": (0, 2000, 3600, 7200, 13200, 25200),
    "AED": (0, 1900, 3300, 6600, 12100, 23100),
    "SAR": (0, 1900, 3400, 6800, 12400, 23600),
    "SGD": (0, 700, 1200, 2400, 4500, 8500),
    "HKD": (0, 3900, 7000, 14000, 26000, 49000),
}
REGIONAL_PRICES = {
    plan_code: {
        currency: amounts[index]
        for currency, amounts in _REGIONAL_PRICE_POINTS.items()
    }
    for index, plan_code in enumerate(_PRICE_PLAN_CODES)
}

PROVIDERS = (
    {
        "code": "paytr",
        "regions": ["TR", "global_cards"],
        "currencies": ["TRY", "USD", "EUR"],
        "capabilities": ["cards", "foreign_cards", "one_time", "monthly", "annual", "saved_card"],
        "status": "pending_credentials",
    },
    {
        "code": "paddle",
        "regions": ["global"],
        "currencies": ["provider_managed"],
        "capabilities": ["cards", "wallets", "local_methods", "monthly", "annual", "tax"],
        "status": "planned",
    },
    {
        "code": "iyzico",
        "regions": ["TR"],
        "currencies": ["TRY", "USD", "EUR"],
        "capabilities": ["cards", "one_time", "monthly", "annual", "saved_card"],
        "status": "application_review",
    },
)


def public_catalog(currency: str = "TRY") -> dict:
    selected_currency = currency.upper() if currency.upper() in SUPPORTED_CURRENCIES else "TRY"
    return {
        "plans": [plan.public(selected_currency) for plan in PLANS],
        "billing_intervals": ["one_time", "monthly", "annual"],
        "supported_currencies": list(SUPPORTED_CURRENCIES),
        "selected_currency": selected_currency,
        "localization": "client_translation_keys",
        "prices": "regional_display_provider_checkout",
    }


def public_providers() -> dict:
    return {"providers": list(PROVIDERS)}
