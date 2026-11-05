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
    advertising: str = "legacy"
    # Unlisted plans keep their code, terms and prices so existing orders,
    # purchase snapshots and deep links keep working, but public_catalog()
    # and the plans page no longer offer them as cards.
    listed: bool = True

    @property
    def export_enabled(self) -> bool:
        return self.download_enabled

    @property
    def download_enabled(self) -> bool:
        # Verified free accounts download their own results, like guest
        # trials. Add-ons without study minutes grant no downloads by
        # themselves; ownership and minute limits are enforced elsewhere.
        return self.code != "ad_free" and not self.code.startswith("ai_")

    @property
    def ad_free(self) -> bool:
        return self.ad_mode == "none"

    @property
    def ad_mode(self) -> str:
        if self.advertising != "legacy":
            return self.advertising
        return "none" if self.code in {"lite", "plus", "pro", "max", "business", "ad_free"} else "standard"

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
                "ad_mode": self.ad_mode,
                "rewarded_minutes_eligible": self.code not in {"lite", "plus", "pro", "max", "business", "ad_free"},
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
    Plan("free", "free", 120, ("pdf",), "standard", 1, 10, 20, DETAILED_SUMMARY_PROFILE, 7, 3, 250, 50, 60, 100, 30),
    Plan("test", "one_time", 1, ("pdf",), "standard", 1, 1, 1, DETAILED_SUMMARY_PROFILE, 1, 1, 25, 10, 1, 10, 5),
    Plan("credit", "one_time", 200, ("pdf", "docx", "txt"), "standard", 1, 20, 40, ALL_SUMMARY_PROFILES, 30, 8, 500, 50, 180, 150, 50, 9900),
    # "lite" keeps its code; it is sold as the Student plan ("Öğrenci").
    Plan("lite", "subscription", 600, ("pdf", "docx", "txt"), "standard", 1, 20, 40, ALL_SUMMARY_PROFILES, 30, 12, 750, 75, 120, 250, 75, 14900, featured=True),
    # Plus and Max are hidden, not removed: still orderable by code, no longer listed.
    Plan("plus", "subscription", 900, ("pdf", "docx", "txt"), "standard", 1, 20, 40, ALL_SUMMARY_PROFILES, 90, 16, 1024, 100, 240, 350, 100, 59900, listed=False),
    Plan("pro", "subscription", 1500, ("pdf", "docx", "txt"), "priority", 1, 30, 60, ALL_SUMMARY_PROFILES, 365, 24, 1024, 100, 360, 500, 150, 44900),
    Plan("max", "subscription", 4000, ("pdf", "docx", "txt"), "priority", 1, 30, 60, ALL_SUMMARY_PROFILES, 730, 24, 1024, 100, 600, 500, 150, 229900, listed=False),
    Plan("business", "quote", None, ("pdf", "docx", "txt"), "priority", 10, None, None, ALL_SUMMARY_PROFILES, 730, 24, 1024, 100, 1440, 500, 150),
)

from . import assistant_catalog

# Every paid plan is ad-free ("legacy" resolves to ad_mode "none" for them);
# the hidden Plus keeps the limited home-page ads its subscribers bought.
PLANS = tuple(replace(plan, assistant_credits=assistant_catalog.INCLUDED.get(plan.code, 0),
                      advertising={"plus": "limited"}.get(plan.code, "legacy"))
              for plan in PLANS)
LISTED_PLAN_CODES = tuple(plan.code for plan in PLANS if plan.listed)
# Precise transcript timestamps and speaker detection run the diarization
# model, which costs roughly four times plain transcription. Only these plans
# may request them; their jobs count PRECISE_TRANSCRIPT_USAGE_MULTIPLIER times
# the media minutes against the quota.
PRECISE_TRANSCRIPT_PLAN_CODES = frozenset({"plus", "pro", "max", "business"})
PRECISE_TRANSCRIPT_USAGE_MULTIPLIER = 4
ASSISTANT_PLANS = tuple(
    replace(PLANS[1], code=code, minutes=0, assistant_credits=credits,
            try_amount_minor=assistant_catalog.PRICES["TRY"][index])
    for index, (code, credits) in enumerate(assistant_catalog.PACKS.items())
)
AD_FREE_PLAN = replace(
    PLANS[1], code="ad_free", minutes=0, export_formats=(), quiz_questions=0,
    flashcards=0, summary_profiles=(), history_days=0, max_files_per_job=0,
    max_media_upload_mb=0, max_document_upload_mb=0, max_minutes_per_job=0,
    max_document_pages=0, max_ocr_pages=0, try_amount_minor=5990,
)
PLAN_BY_CODE = {plan.code: plan for plan in (*PLANS, *ASSISTANT_PLANS, AD_FREE_PLAN)}

# Intentional regional product prices, not volatile exchange-rate conversions.
# The connected checkout provider remains the source of truth for tax and the
# final amount charged.
_PRICE_PLAN_CODES = ("free", "test", "credit", "lite", "plus", "pro", "max")
# October 2026 revision: lite (Student) and pro were repriced in TRY and USD;
# plus and max are unchanged. November 2026: the credit pack became
# 200 minutes for 9900 kuruş (it was 300 for 19900, dearer per minute than the
# Student plan); TRY, USD and EUR take the next x99 above 9900/19900 of the old
# value so card fees stay covered, every other currency scales by that ratio. The other currencies scale lite by
# 699/899 and pro by 1499/3299 from their previous value. Rounding follows the
# pattern each row already used: TRY, USD and EUR list prices end in 99 (the
# nearest x99), every other currency keeps plain minor units rounded to the
# nearest whole unit (JPY and KRW are zero-decimal, so whole yen/won).
_REGIONAL_PRICE_POINTS = {
    "TRY": (0, 100, 9900, 14900, 59900, 44900, 229900),
    "USD": (0, None, 299, 699, 1699, 1499, 5999),
    "EUR": (0, None, 299, 699, 1599, 1399, 5699),
    "GBP": (0, None, 198, 499, 1133, 1145, 4829),
    "CAD": (0, None, 348, 791, 1800, 1853, 7819),
    "AUD": (0, None, 397, 916, 2066, 2072, 8739),
    "NZD": (0, None, 447, 999, 2267, 2290, 9659),
    # JPY and KRW have zero-decimal minor units; the other values use cents.
    "JPY": (0, None, 373, 875, 2001, 2045, 8626),
    "KRW": (0, None, 3433, 7916, 18544, 19033, 80390),
    "CNY": (0, None, 1741, 4083, 9205, 9544, 40138),
    "INR": (0, None, 19850, 45746, 106593, 109015, 459915),
    "BRL": (0, None, 1243, 2916, 6669, 6816, 28751),
    "MXN": (0, None, 4925, 11582, 26548, 27213, 114892),
    "CHF": (0, None, 223, 499, 1133, 1199, 5059),
    "SEK": (0, None, 2636, 6082, 14006, 14178, 59803),
    "NOK": (0, None, 2736, 6415, 14673, 14996, 63253),
    "DKK": (0, None, 1741, 3749, 8937, 9270, 39101),
    "PLN": (0, None, 994, 2249, 5335, 5453, 23000),
    "AED": (0, None, 945, 2165, 4935, 5017, 21160),
    "SAR": (0, None, 945, 2165, 5068, 5126, 21620),
    "SGD": (0, None, 348, 791, 1800, 1853, 7819),
    "HKD": (0, None, 1940, 4582, 10404, 10634, 44852),
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
REGIONAL_PRICES["ad_free"] = {
    "TRY": 5990, "USD": 149, "EUR": 149, "GBP": 119, "CAD": 199,
    "AUD": 249, "NZD": 269, "JPY": 230, "KRW": 2100, "CNY": 1090,
    "INR": 11900, "BRL": 749, "MXN": 2990, "CHF": 139, "SEK": 1590,
    "NOK": 1690, "DKK": 1090, "PLN": 599, "AED": 599, "SAR": 599,
    "SGD": 199, "HKD": 1190,
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
        "plans": [plan.public(selected_currency) for plan in PLANS if plan.listed and plan.code != "test"],
        "ad_free": AD_FREE_PLAN.public(selected_currency),
        "billing_intervals": ["one_time", "monthly", "annual"],
        "supported_currencies": list(SUPPORTED_CURRENCIES),
        "selected_currency": selected_currency,
        "localization": "client_translation_keys",
        "prices": "regional_display_provider_checkout",
        "assistant": assistant_catalog.offers(selected_currency),
    }


def public_providers() -> dict:
    return {"providers": list(PROVIDERS)}
