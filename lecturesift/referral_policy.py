"""Versioned referral terms without database, payment or activation effects.

Renewal terms are a pre-release draft. Selecting terms does not authorize a
reward: the ledger must verify a unique settled payment, attribution, account
eligibility, the shared monthly limit and reconciliation after the hold.
"""

from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Mapping


@dataclass(frozen=True, slots=True)
class ReferralPolicyTerms:
    version: str
    inviter_minutes: int
    invitee_minutes: int
    coupon_percent: int
    coupon_max_minor: int
    currency: str = "TRY"
    monthly_reward_limit: int = 5
    hold_days: int = 14
    coupon_valid_days: int = 90


# Never revise stored version values. Existing rewards retain their terms.
# The inviter chooses minutes OR a coupon; these alternatives do not stack.
FIRST_PURCHASE_TERMS = ReferralPolicyTerms(
    version="referral-2026-09-v1",
    inviter_minutes=60,
    invitee_minutes=30,
    coupon_percent=10,
    coupon_max_minor=5000,
)
REGIONAL_FIRST_PURCHASE_TERMS = replace(FIRST_PURCHASE_TERMS, version="referral-2026-09-v2")
RENEWAL_TERMS = ReferralPolicyTerms(
    version="referral-2026-09-renewal-v1",
    inviter_minutes=30,
    invitee_minutes=0,
    coupon_percent=5,
    coupon_max_minor=2500,
)
POLICY_TERMS: Mapping[str, ReferralPolicyTerms] = MappingProxyType({
    FIRST_PURCHASE_TERMS.version: FIRST_PURCHASE_TERMS,
    REGIONAL_FIRST_PURCHASE_TERMS.version: REGIONAL_FIRST_PURCHASE_TERMS,
    RENEWAL_TERMS.version: RENEWAL_TERMS,
})
_MODE_TERMS: Mapping[str, ReferralPolicyTerms] = MappingProxyType({
    "first": REGIONAL_FIRST_PURCHASE_TERMS,
    "renewal": RENEWAL_TERMS,
})
ELIGIBLE_PLANS = frozenset({"lite", "plus", "pro", "max"})
ELIGIBLE_INTERVALS = frozenset({"monthly", "annual"})
PAYMENT_SOURCES = frozenset({"payment_order", "manual_order"})


# Frozen from 2026-09 regional Lite prices: price * (5000 or 2500) // 29900.
# Catalog minor units (JPY/KRW whole units); regional price ratios, not live FX.
# This policy catalog does not imply that a provider accepts these currencies.
REGIONAL_COUPON_CAPS_MINOR: Mapping[str, tuple[int, int]] = MappingProxyType({
    "TRY": (5000, 2500), "USD": (150, 75), "EUR": (141, 70), "GBP": (107, 53),
    "CAD": (170, 85), "AUD": (196, 98), "NZD": (214, 107), "JPY": (188, 94),
    "KRW": (1702, 851), "CNY": (878, 439), "INR": (9838, 4919), "BRL": (627, 313),
    "MXN": (2490, 1245), "CHF": (107, 53), "SEK": (1308, 654), "NOK": (1379, 689),
    "DKK": (806, 403), "PLN": (483, 241), "AED": (465, 232), "SAR": (465, 232),
    "SGD": (170, 85), "HKD": (985, 492),
})


def coupon_terms_for_policy(version: str, currency: str) -> ReferralPolicyTerms | None:
    """Resolve frozen coupon terms without extending old TRY-only rewards."""
    if not isinstance(version, str) or not isinstance(currency, str):
        return None
    terms = POLICY_TERMS.get(version)
    if terms is None:
        return None
    if version == FIRST_PURCHASE_TERMS.version:
        return terms if currency == "TRY" else None
    caps = REGIONAL_COUPON_CAPS_MINOR.get(currency)
    if caps is None:
        return None
    cap = caps[0 if version == REGIONAL_FIRST_PURCHASE_TERMS.version else 1]
    return replace(terms, currency=currency, coupon_max_minor=cap)


def policy_for_purchase(
    *,
    plan_code: str,
    interval: str,
    payment_source: str,
    amount_minor: int,
    mode: str,
) -> ReferralPolicyTerms | None:
    """Classify an already-validated payment; return None for other events.

    Sources identify billing order types, not provider names or authentication.
    The caller must validate paid status and provider/manual reconciliation and
    derive first/renewal from payment history. Positive amounts alone are not
    proof of settlement. Annual terms apply once per actual new payment; monthly
    quota resets have no eligible source. First and renewal rewards share one
    monthly limit per inviter, enforced by the ledger, not separately per mode.
    """
    if not all(isinstance(value, str) for value in (plan_code, interval, payment_source, mode)):
        return None
    if not isinstance(amount_minor, int) or isinstance(amount_minor, bool) or amount_minor <= 0:
        return None
    if (
        plan_code not in ELIGIBLE_PLANS
        or interval not in ELIGIBLE_INTERVALS
        or payment_source not in PAYMENT_SOURCES
    ):
        return None
    return _MODE_TERMS.get(mode)
