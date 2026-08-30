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
    max_files_per_job: int = 3
    max_media_upload_mb: int = 100
    max_document_upload_mb: int = 25
    max_minutes_per_job: int = 30
    max_document_pages: int = 50
    max_ocr_pages: int = 20
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
                "limits": {
                    "max_files_per_job": self.max_files_per_job,
                    "max_media_upload_mb": self.max_media_upload_mb,
                    "max_document_upload_mb": self.max_document_upload_mb,
                    "max_minutes_per_job": self.max_minutes_per_job,
                    "max_document_pages": self.max_document_pages,
                    "max_ocr_pages": self.max_ocr_pages,
                },
                "team_seats": self.team_seats,
                "priority": self.priority,
                "ad_free": self.ad_free,
                "rewarded_minutes_eligible": not self.ad_free,
                "download_enabled": self.download_enabled,
            },
        }


ALL_SUMMARY_PROFILES = ("short", "standard", "detailed", "exam", "five_minute")

PLANS = (
    Plan("free", "free", 60, ("pdf",), "standard", 1, 10, 20, ("short", "standard"), 7, 3, 100, 25, 30, 50, 20),
    Plan("test", "one_time", 1, ("pdf",), "standard", 1, 1, 1, ("short",), 1, 1, 25, 10, 1, 10, 5),
    Plan("credit", "one_time", 180, ("pdf", "docx", "txt"), "standard", 1, 20, 40, ALL_SUMMARY_PROFILES, 30, 8, 500, 50, 180, 150, 50, 19900),
    Plan("lite", "subscription", 600, ("pdf", "docx", "txt"), "standard", 1, 20, 40, ALL_SUMMARY_PROFILES, 90, 12, 750, 50, 180, 250, 75, 27900),
    Plan("plus", "subscription", 1800, ("pdf", "docx", "txt"), "standard", 1, 30, 60, ALL_SUMMARY_PROFILES, 180, 16, 1024, 50, 300, 350, 100, 44900, featured=True),
    Plan("pro", "subscription", 5000, ("pdf", "docx", "txt"), "priority", 1, 30, 60, ALL_SUMMARY_PROFILES, 365, 24, 1024, 50, 600, 500, 150, 99900),
    Plan("max", "subscription", 12000, ("pdf", "docx", "txt"), "priority", 1, 30, 60, ALL_SUMMARY_PROFILES, 730, 24, 1024, 50, 900, 500, 150, 199900),
    Plan("business", "quote", None, ("pdf", "docx", "txt"), "priority", 10, None, None, ALL_SUMMARY_PROFILES, 730, 24, 1024, 50, 1440, 500, 150),
)

PLAN_BY_CODE = {plan.code: plan for plan in PLANS}

# Intentional regional product prices, not volatile exchange-rate conversions.
# The connected checkout provider remains the source of truth for tax and the
# final amount charged.
_PRICE_PLAN_CODES = ("free", "test", "credit", "lite", "plus", "pro", "max")
_REGIONAL_PRICE_POINTS = {
    "TRY": (0, 100, 19900, 27900, 44900, 99900, 199900),
    "USD": (0, None, 499, 699, 999, 2499, 4999),
    "EUR": (0, None, 499, 649, 949, 2399, 4799),
    "GBP": (0, None, 399, 599, 849, 2099, 4199),
    "CAD": (0, None, 699, 949, 1349, 3399, 6799),
    "AUD": (0, None, 799, 1099, 1549, 3799, 7599),
    "NZD": (0, None, 899, 1199, 1699, 4199, 8399),
    # JPY and KRW have zero-decimal minor units; the other values use cents.
    "JPY": (0, None, 750, 1050, 1500, 3750, 7500),
    "KRW": (0, None, 6900, 9500, 13900, 34900, 69900),
    "CNY": (0, None, 3500, 4900, 6900, 17500, 34900),
    "INR": (0, None, 39900, 54900, 79900, 199900, 399900),
    "BRL": (0, None, 2499, 3499, 4999, 12499, 24999),
    "MXN": (0, None, 9900, 13900, 19900, 49900, 99900),
    "CHF": (0, None, 449, 599, 849, 2199, 4399),
    "SEK": (0, None, 5299, 7299, 10499, 25999, 51999),
    "NOK": (0, None, 5499, 7699, 10999, 27499, 54999),
    "DKK": (0, None, 3499, 4499, 6699, 16999, 33999),
    "PLN": (0, None, 1999, 2699, 3999, 9999, 19999),
    "AED": (0, None, 1899, 2599, 3699, 9199, 18399),
    "SAR": (0, None, 1899, 2599, 3799, 9399, 18799),
    "SGD": (0, None, 699, 949, 1349, 3399, 6799),
    "HKD": (0, None, 3899, 5499, 7799, 19499, 38999),
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
