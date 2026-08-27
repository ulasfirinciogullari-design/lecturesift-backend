"""Provider-neutral subscription catalog and regional display prices."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass


SUPPORTED_CURRENCIES = (
    "TRY", "USD", "EUR", "GBP", "CAD", "AUD", "NZD", "JPY", "KRW",
    "CNY", "INR", "BRL", "MXN", "CHF", "SEK", "NOK", "DKK", "PLN",
    "AED", "SAR", "SGD", "HKD",
)
ALL_SUMMARY_PROFILES = ("short", "standard", "detailed", "exam", "five_minute")


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
    download_enabled: bool = True
    visual_translation: bool = False
    output_retention_days: int = 30

    @property
    def export_enabled(self) -> bool:
        """Whether the user may download generated files under this plan."""
        return self.download_enabled and bool(self.export_formats)

    def interval_amount(self, interval: str) -> int | None:
        if self.try_amount_minor is None:
            return None
        if self.kind != "subscription":
            return self.try_amount_minor if interval == "one_time" else None
        if interval == "monthly":
            return self.try_amount_minor
        if interval == "annual":
            return self.try_amount_minor * 10
        return None

    def public(self, currency: str = "TRY") -> dict:
        selected_currency = currency if currency in SUPPORTED_CURRENCIES else "TRY"
        regional = REGIONAL_PRICES.get(self.code, {})
        monthly = regional.get(selected_currency)
        interval_prices: dict[str, dict] = {}
        if monthly is not None:
            if self.kind == "subscription":
                interval_prices = {
                    "monthly": {"currency": selected_currency, "amount_minor": monthly},
                    "annual": {"currency": selected_currency, "amount_minor": monthly * 10},
                }
            elif self.kind == "one_time":
                interval_prices = {
                    "one_time": {"currency": selected_currency, "amount_minor": monthly},
                }
        display_price = interval_prices.get("monthly") or interval_prices.get("one_time")
        manual_monthly = self.try_amount_minor
        manual_prices = None
        if manual_monthly is not None:
            manual_prices = (
                {
                    "monthly": {"currency": "TRY", "amount_minor": manual_monthly},
                    "annual": {"currency": "TRY", "amount_minor": manual_monthly * 10},
                }
                if self.kind == "subscription"
                else {"one_time": {"currency": "TRY", "amount_minor": manual_monthly}}
            )
        return {
            **asdict(self),
            "export_enabled": self.export_enabled,
            "name_key": f"billing.plan.{self.code}.name",
            "description_key": f"billing.plan.{self.code}.description",
            "price_source": "payment_provider_or_manual_transfer",
            "manual_price": (
                {"currency": "TRY", "amount_minor": manual_monthly}
                if manual_monthly is not None
                else None
            ),
            "manual_prices": manual_prices,
            "display_price": display_price,
            "interval_prices": interval_prices,
            "annual_savings_months": 2 if self.kind == "subscription" else 0,
            "entitlements": {
                "minutes": self.minutes,
                "quiz_questions": self.quiz_questions,
                "flashcards": self.flashcards,
                "export_formats": list(self.export_formats),
                "summary_profiles": list(self.summary_profiles),
                "history_days": self.history_days,
                "team_seats": self.team_seats,
                "priority": self.priority,
                "download_enabled": self.download_enabled,
                "visual_translation": self.visual_translation,
                "output_retention_days": self.output_retention_days,
            },
        }


PLANS = (
    Plan(
        "free", "free", 60, ("pdf",), "standard", 1, 10, 20,
        ("short", "standard"), 7, download_enabled=False,
        visual_translation=False, output_retention_days=0,
    ),
    Plan(
        "mini", "one_time", 60, ("pdf", "docx", "txt"), "standard", 1, 20, 40,
        ALL_SUMMARY_PROFILES, 30, 4900, download_enabled=True,
        visual_translation=True, output_retention_days=30,
    ),
    Plan(
        "credit", "one_time", 180, ("pdf", "docx", "txt"), "standard", 1, 20, 40,
        ALL_SUMMARY_PROFILES, 90, 14900, download_enabled=True,
        visual_translation=True, output_retention_days=90,
    ),
    Plan(
        "lite", "subscription", 600, ("pdf", "docx", "txt"), "standard", 1, 20, 40,
        ALL_SUMMARY_PROFILES, 90, 34900, download_enabled=True,
        visual_translation=True, output_retention_days=90,
    ),
    Plan(
        "plus", "subscription", 2400, ("pdf", "docx", "txt"), "standard", 1, 30, 60,
        ALL_SUMMARY_PROFILES, 180, 69900, featured=True, download_enabled=True,
        visual_translation=True, output_retention_days=180,
    ),
    Plan(
        "pro", "subscription", 6000, ("pdf", "docx", "txt"), "priority", 1, 30, 60,
        ALL_SUMMARY_PROFILES, 365, 129900, download_enabled=True,
        visual_translation=True, output_retention_days=365,
    ),
    Plan(
        "max", "subscription", 15000, ("pdf", "docx", "txt"), "priority", 1, 30, 60,
        ALL_SUMMARY_PROFILES, 730, 249900, download_enabled=True,
        visual_translation=True, output_retention_days=730,
    ),
    Plan(
        "business", "quote", None, ("pdf", "docx", "txt"), "priority", 10, None, None,
        ALL_SUMMARY_PROFILES, 730, download_enabled=True,
        visual_translation=True, output_retention_days=730,
    ),
)
PLAN_BY_CODE = {plan.code: plan for plan in PLANS}


_PRICE_PLAN_CODES = ("free", "mini", "credit", "lite", "plus", "pro", "max")
_REGIONAL_PRICE_POINTS = {
    "TRY": (0, 4900, 14900, 34900, 69900, 129900, 249900),
    "USD": (0, 200, 500, 900, 1800, 3300, 6300),
    "EUR": (0, 200, 500, 900, 1700, 3100, 5900),
    "GBP": (0, 150, 400, 800, 1500, 2700, 5200),
    "CAD": (0, 300, 700, 1200, 2500, 4500, 8500),
    "AUD": (0, 300, 800, 1400, 2800, 5200, 9900),
    "NZD": (0, 350, 900, 1600, 3100, 5700, 10900),
    "JPY": (0, 300, 800, 1400, 2800, 5000, 9500),
    "KRW": (0, 3000, 7000, 13000, 25000, 47000, 89000),
    "CNY": (0, 1500, 3600, 6500, 12900, 23900, 45900),
    "INR": (0, 14900, 39900, 74900, 149900, 279900, 529900),
    "BRL": (0, 1000, 2500, 4500, 8900, 16900, 31900),
    "MXN": (0, 3900, 9900, 17900, 34900, 64900, 124900),
    "CHF": (0, 200, 500, 800, 1600, 3000, 5700),
    "SEK": (0, 2200, 5500, 9900, 19900, 36900, 69900),
    "NOK": (0, 2400, 5900, 10900, 21900, 39900, 76900),
    "DKK": (0, 1500, 3500, 6500, 12900, 22900, 44900),
    "PLN": (0, 800, 2000, 3600, 7200, 13200, 25200),
    "AED": (0, 800, 1900, 3300, 6600, 12100, 23100),
    "SAR": (0, 800, 1900, 3400, 6800, 12400, 23600),
    "SGD": (0, 300, 700, 1200, 2400, 4500, 8500),
    "HKD": (0, 1600, 3900, 7000, 14000, 26000, 49000),
}
REGIONAL_PRICES = {
    plan_code: {
        currency: amounts[index]
        for currency, amounts in _REGIONAL_PRICE_POINTS.items()
    }
    for index, plan_code in enumerate(_PRICE_PLAN_CODES)
}

try:
    _overrides = json.loads(os.getenv("LECTURESIFT_PLAN_PRICE_OVERRIDES", "{}") or "{}")
except json.JSONDecodeError:
    _overrides = {}
if isinstance(_overrides, dict):
    for _plan_code, _prices in _overrides.items():
        if _plan_code not in REGIONAL_PRICES or not isinstance(_prices, dict):
            continue
        for _currency, _amount in _prices.items():
            if _currency in SUPPORTED_CURRENCIES:
                try:
                    REGIONAL_PRICES[_plan_code][_currency] = max(0, int(_amount))
                except (TypeError, ValueError):
                    continue


PROVIDERS = (
    {
        "code": "paytr",
        "regions": ["TR", "global_cards"],
        "currencies": ["TRY", "USD", "EUR"],
        "capabilities": [
            "cards", "foreign_cards", "one_time", "monthly", "annual",
            "iframe", "callback", "refund",
        ],
        "status": "configuration_required",
        "automatic_renewal": "requires_paytr_card_storage_permission",
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
    requested = str(currency or "TRY").upper()
    selected_currency = requested if requested in SUPPORTED_CURRENCIES else "TRY"
    return {
        "plans": [plan.public(selected_currency) for plan in PLANS],
        "billing_intervals": ["one_time", "monthly", "annual"],
        "supported_currencies": list(SUPPORTED_CURRENCIES),
        "selected_currency": selected_currency,
        "localization": "client_translation_keys",
        "prices": "regional_display_provider_checkout",
        "annual_discount": {"months_charged": 10, "months_of_access": 12},
        "free_downloads": False,
        "lowest_download_plan": "mini",
    }


def public_providers() -> dict:
    return {"providers": list(PROVIDERS)}
