"""Provider-neutral subscription catalog and regional display prices."""

from __future__ import annotations

from dataclasses import asdict, dataclass


SUPPORTED_CURRENCIES = ("TRY", "USD", "EUR", "GBP")


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
        return len(self.export_formats) > 1

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
REGIONAL_PRICES = {
    "free": {"TRY": 0, "USD": 0, "EUR": 0, "GBP": 0},
    "credit": {"TRY": 19900, "USD": 500, "EUR": 500, "GBP": 400},
    "lite": {"TRY": 34900, "USD": 900, "EUR": 900, "GBP": 800},
    "plus": {"TRY": 69900, "USD": 1800, "EUR": 1700, "GBP": 1500},
    "pro": {"TRY": 129900, "USD": 3300, "EUR": 3100, "GBP": 2700},
    "max": {"TRY": 249900, "USD": 6300, "EUR": 5900, "GBP": 5200},
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
        "status": "fallback",
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
