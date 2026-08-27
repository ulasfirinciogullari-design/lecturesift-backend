"""Provider-neutral subscription catalog for LectureSift.

Prices live at the payment provider so currency, tax and regional payment
methods can be managed without redeploying the application.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Plan:
    code: str
    kind: str
    minutes: int | None
    export_enabled: bool
    priority: str
    team_seats: int
    try_amount_minor: int | None = None
    featured: bool = False

    def public(self) -> dict:
        return {
            **asdict(self),
            "name_key": f"billing.plan.{self.code}.name",
            "description_key": f"billing.plan.{self.code}.description",
            "price_source": "manual_bank_transfer" if self.try_amount_minor is not None else "payment_provider",
            "manual_price": (
                {"currency": "TRY", "amount_minor": self.try_amount_minor}
                if self.try_amount_minor is not None
                else None
            ),
        }


PLANS = (
    Plan("free", "free", 60, False, "standard", 1),
    Plan("credit", "one_time", 180, True, "standard", 1, 19900),
    Plan("lite", "subscription", 600, True, "standard", 1, 34900),
    Plan("plus", "subscription", 2400, True, "standard", 1, 69900, featured=True),
    Plan("pro", "subscription", 6000, True, "priority", 1, 129900),
    Plan("max", "subscription", 15000, True, "priority", 1, 249900),
    Plan("business", "quote", None, True, "priority", 10),
)

PLAN_BY_CODE = {plan.code: plan for plan in PLANS}

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


def public_catalog() -> dict:
    return {
        "plans": [plan.public() for plan in PLANS],
        "billing_intervals": ["one_time", "monthly", "annual"],
        "localization": "client_translation_keys",
        "prices": "payment_provider",
    }


def public_providers() -> dict:
    return {"providers": list(PROVIDERS)}
