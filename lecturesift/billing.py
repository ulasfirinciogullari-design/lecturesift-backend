"""Provider-neutral subscription catalog and regional display prices."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace

from .config import MAX_DOCUMENT_BYTES, MAX_DOCUMENT_CHARACTERS


SUPPORTED_CURRENCIES = (
    "TRY", "USD", "EUR", "GBP", "CAD", "AUD", "NZD", "JPY", "KRW",
    "CNY", "INR", "BRL", "MXN", "CHF", "SEK", "NOK", "DKK", "PLN",
    "AED", "SAR", "SGD", "HKD",
)

_MEBIBYTE = 1024 * 1024


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
    assistant_credits: int = 0

    @property
    def export_enabled(self) -> bool:
        return self.download_enabled

    @property
    def download_enabled(self) -> bool:
        return self.code != "free" and not self.code.startswith("ai_")

    @property
    def ad_free(self) -> bool:
        return self.code in {"lite", "plus", "pro", "max", "business"}

    def public(self, currency: str = "TRY") -> dict:
        selected_currency = currency if currency in SUPPORTED_CURRENCIES else "TRY"
        regional = REGIONAL_PRICES.get(self.code, {})
        display_amount = regional.get(selected_currency)
        # A plan must never advertise a document size that the active runtime
        # rejects. Keep this final safety boundary here as environment-specific
        # deployments can lower the global cap without changing the catalog.
        effective_document_upload_mb = min(
            self.max_document_upload_mb,
            max(0, MAX_DOCUMENT_BYTES // _MEBIBYTE),
        )
        public_plan = asdict(self)
        public_plan["max_document_upload_mb"] = effective_document_upload_mb
        return {
            **public_plan,
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
                "assistant_credits": self.assistant_credits,
                "quiz_questions": self.quiz_questions,
                "flashcards": self.flashcards,
                "export_formats": list(self.export_formats),
                "summary_profiles": list(self.summary_profiles),
                "history_days": self.history_days,
                "limits": {
                    "max_files_per_job": self.max_files_per_job,
                    "max_media_upload_mb": self.max_media_upload_mb,
                    "max_document_upload_mb": effective_document_upload_mb,
                    "max_minutes_per_job": self.max_minutes_per_job,
                    "max_document_pages": self.max_document_pages,
                    "max_ocr_pages": self.max_ocr_pages,
                    "max_document_characters": MAX_DOCUMENT_CHARACTERS,
                },
                "team_seats": self.team_seats,
                "priority": self.priority,
                "ad_free": self.ad_free,
                "rewarded_minutes_eligible": not self.ad_free,
                "download_enabled": self.download_enabled,
            },
        }


DETAILED_SUMMARY_PROFILE = ("detailed",)
# Backwards-compatible export for code that imports the former catalog
# constant.  Public plans intentionally advertise only the single profile the
# product now generates.
ALL_SUMMARY_PROFILES = DETAILED_SUMMARY_PROFILE

LEGACY_PLANS = (
    Plan("free", "free", 60, ("pdf",), "standard", 1, 10, 20, DETAILED_SUMMARY_PROFILE, 7, 3, 100, 25, 30, 50, 20),
    Plan("test", "one_time", 1, ("pdf",), "standard", 1, 1, 1, DETAILED_SUMMARY_PROFILE, 1, 1, 25, 10, 1, 10, 5),
    Plan("credit", "one_time", 180, ("pdf", "docx", "txt"), "standard", 1, 20, 40, ALL_SUMMARY_PROFILES, 30, 8, 500, 50, 180, 150, 50, 19900),
    Plan("lite", "subscription", 600, ("pdf", "docx", "txt"), "standard", 1, 20, 40, ALL_SUMMARY_PROFILES, 90, 12, 750, 75, 180, 250, 75, 27900),
    Plan("plus", "subscription", 1800, ("pdf", "docx", "txt"), "standard", 1, 30, 60, ALL_SUMMARY_PROFILES, 180, 16, 1024, 100, 300, 350, 100, 44900, featured=True),
    Plan("pro", "subscription", 5000, ("pdf", "docx", "txt"), "priority", 1, 30, 60, ALL_SUMMARY_PROFILES, 365, 24, 1024, 100, 600, 500, 150, 99900),
    Plan("max", "subscription", 12000, ("pdf", "docx", "txt"), "priority", 1, 30, 60, ALL_SUMMARY_PROFILES, 730, 24, 1024, 100, 900, 500, 150, 199900),
    Plan("business", "quote", None, ("pdf", "docx", "txt"), "priority", 10, None, None, ALL_SUMMARY_PROFILES, 730, 24, 1024, 100, 1440, 500, 150),
)
LEGACY_PLAN_BY_CODE = {plan.code: plan for plan in LEGACY_PLANS}

# New purchases use this catalog. Existing subscriptions and orders without a
# durable purchase-terms snapshot continue to resolve through
# LEGACY_PLAN_BY_CODE in billing_service.py.
PLANS = (
    Plan("free", "free", 60, ("pdf",), "standard", 1, 10, 20, DETAILED_SUMMARY_PROFILE, 7, 3, 100, 25, 30, 50, 20),
    Plan("test", "one_time", 1, ("pdf",), "standard", 1, 1, 1, DETAILED_SUMMARY_PROFILE, 1, 1, 25, 10, 1, 10, 5),
    Plan("credit", "one_time", 180, ("pdf", "docx", "txt"), "standard", 1, 20, 40, ALL_SUMMARY_PROFILES, 30, 8, 500, 50, 180, 150, 50, 19900),
    Plan("lite", "subscription", 400, ("pdf", "docx", "txt"), "standard", 1, 10, 20, ALL_SUMMARY_PROFILES, 30, 12, 750, 75, 120, 250, 75, 29900),
    Plan("plus", "subscription", 900, ("pdf", "docx", "txt"), "standard", 1, 20, 40, ALL_SUMMARY_PROFILES, 90, 16, 1024, 100, 240, 350, 100, 59900, featured=True),
    Plan("pro", "subscription", 2000, ("pdf", "docx", "txt"), "priority", 1, 30, 60, ALL_SUMMARY_PROFILES, 365, 24, 1024, 100, 360, 500, 150, 119900),
    Plan("max", "subscription", 4000, ("pdf", "docx", "txt"), "priority", 1, 30, 60, ALL_SUMMARY_PROFILES, 730, 24, 1024, 100, 600, 500, 150, 229900),
    Plan("business", "quote", None, ("pdf", "docx", "txt"), "priority", 10, None, None, ALL_SUMMARY_PROFILES, 730, 24, 1024, 100, 1440, 500, 150),
)

from . import assistant_catalog

PLANS = tuple(replace(plan, assistant_credits=assistant_catalog.INCLUDED.get(plan.code, 0)) for plan in PLANS)
ASSISTANT_PLANS = tuple(
    replace(PLANS[1], code=code, minutes=0, assistant_credits=credits,
            try_amount_minor=assistant_catalog.PRICES["TRY"][index])
    for index, (code, credits) in enumerate(assistant_catalog.PACKS.items())
)
PLAN_BY_CODE = {plan.code: plan for plan in (*PLANS, *ASSISTANT_PLANS)}

# Intentional regional product prices, not volatile exchange-rate conversions.
# The connected checkout provider remains the source of truth for tax and the
# final amount charged.
_PRICE_PLAN_CODES = ("free", "test", "credit", "lite", "plus", "pro", "max")
_REGIONAL_PRICE_POINTS = {
    "TRY": (0, 100, 19900, 29900, 59900, 119900, 229900),
    "USD": (0, None, 499, 899, 1699, 3299, 5999),
    "EUR": (0, None, 499, 849, 1599, 3099, 5699),
    "GBP": (0, None, 399, 642, 1133, 2519, 4829),
    "CAD": (0, None, 699, 1017, 1800, 4079, 7819),
    "AUD": (0, None, 799, 1178, 2066, 4560, 8739),
    "NZD": (0, None, 899, 1285, 2267, 5040, 9659),
    # JPY and KRW have zero-decimal minor units; the other values use cents.
    "JPY": (0, None, 750, 1125, 2001, 4501, 8626),
    "KRW": (0, None, 6900, 10181, 18544, 41887, 80390),
    "CNY": (0, None, 3500, 5251, 9205, 21004, 40138),
    "INR": (0, None, 39900, 58835, 106593, 239920, 459915),
    "BRL": (0, None, 2499, 3750, 6669, 15001, 28751),
    "MXN": (0, None, 9900, 14896, 26548, 59890, 114892),
    "CHF": (0, None, 449, 642, 1133, 2639, 5059),
    "SEK": (0, None, 5299, 7822, 14006, 31204, 59803),
    "NOK": (0, None, 5499, 8251, 14673, 33004, 63253),
    "DKK": (0, None, 3499, 4822, 8937, 20402, 39101),
    "PLN": (0, None, 1999, 2892, 5335, 12001, 23000),
    "AED": (0, None, 1899, 2785, 4935, 11041, 21160),
    "SAR": (0, None, 1899, 2785, 5068, 11281, 21620),
    "SGD": (0, None, 699, 1017, 1800, 4079, 7819),
    "HKD": (0, None, 3899, 5893, 10404, 23403, 44852),
}
REGIONAL_PRICES = {
    plan_code: {
        currency: amounts[index]
        for currency, amounts in _REGIONAL_PRICE_POINTS.items()
    }
    for index, plan_code in enumerate(_PRICE_PLAN_CODES)
}
REGIONAL_PRICES.update({
    code: {currency: amounts[index] for currency, amounts in assistant_catalog.PRICES.items()}
    for index, code in enumerate(assistant_catalog.PACKS)
})

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
        "assistant": assistant_catalog.offers(selected_currency),
    }


def public_providers() -> dict:
    return {"providers": list(PROVIDERS)}
