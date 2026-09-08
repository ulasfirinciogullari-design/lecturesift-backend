"""Pure referral policy checks; no accounts, database or provider calls."""

from dataclasses import FrozenInstanceError, asdict

import pytest

from lecturesift.referral_policy import (
    FIRST_PURCHASE_TERMS,
    POLICY_TERMS,
    REGIONAL_COUPON_CAPS_MINOR,
    REGIONAL_FIRST_PURCHASE_TERMS,
    RENEWAL_TERMS,
    coupon_terms_for_policy,
    policy_for_purchase,
)


def purchase(**overrides):
    values = dict(
        plan_code="lite", interval="monthly", payment_source="payment_order",
        amount_minor=27900, mode="first",
    )
    return policy_for_purchase(**(values | overrides))


def test_existing_first_purchase_terms_are_preserved_by_version():
    assert asdict(POLICY_TERMS["referral-2026-09-v1"]) == {
        "version": "referral-2026-09-v1", "inviter_minutes": 60, "invitee_minutes": 30,
        "coupon_percent": 10, "coupon_max_minor": 5000, "currency": "TRY",
        "monthly_reward_limit": 5, "hold_days": 14, "coupon_valid_days": 90,
    }


def test_renewal_has_reduced_alternatives_and_no_second_invitee_bonus():
    assert asdict(RENEWAL_TERMS) == {
        "version": "referral-2026-09-renewal-v1", "inviter_minutes": 30,
        "invitee_minutes": 0, "coupon_percent": 5, "coupon_max_minor": 2500,
        "currency": "TRY", "monthly_reward_limit": 5, "hold_days": 14,
        "coupon_valid_days": 90,
    }
    assert RENEWAL_TERMS.version != FIRST_PURCHASE_TERMS.version
    assert len(RENEWAL_TERMS.version) <= 32


def test_versioned_terms_and_registry_cannot_be_mutated():
    with pytest.raises(FrozenInstanceError):
        FIRST_PURCHASE_TERMS.inviter_minutes = 999
    with pytest.raises(TypeError):
        POLICY_TERMS[FIRST_PURCHASE_TERMS.version] = RENEWAL_TERMS
    with pytest.raises(TypeError):
        del POLICY_TERMS[RENEWAL_TERMS.version]
    with pytest.raises(TypeError):
        REGIONAL_COUPON_CAPS_MINOR["TRY"] = (999, 999)


@pytest.mark.parametrize("plan", ["lite", "plus", "pro", "max"])
@pytest.mark.parametrize("interval", ["monthly", "annual"])
@pytest.mark.parametrize("source", ["payment_order", "manual_order"])
@pytest.mark.parametrize("mode,terms", [("first", REGIONAL_FIRST_PURCHASE_TERMS), ("renewal", RENEWAL_TERMS)])
def test_subscription_payment_selects_its_mode_terms(plan, interval, source, mode, terms):
    assert purchase(plan_code=plan, interval=interval, payment_source=source, mode=mode) is terms


@pytest.mark.parametrize("plan", ["free", "guest", "test", "credit", "business", "unknown"])
@pytest.mark.parametrize("mode", ["first", "renewal"])
def test_non_subscription_plans_never_qualify_even_with_positive_amounts(plan, mode):
    assert purchase(plan_code=plan, mode=mode) is None


@pytest.mark.parametrize("source", ["admin_grant", "quota_reset", "trial", "", "unknown"])
def test_non_payment_events_never_qualify(source):
    assert purchase(payment_source=source) is None
    assert purchase(payment_source=source, mode="renewal") is None


@pytest.mark.parametrize("amount", [0, -1, True, False, 50.0, "5000", None])
def test_amount_requires_positive_integer_minor_units(amount):
    assert purchase(amount_minor=amount) is None
    assert purchase(amount_minor=1) is REGIONAL_FIRST_PURCHASE_TERMS


@pytest.mark.parametrize("overrides", [
    {"interval": "one_time"}, {"interval": "trial"}, {"interval": ""},
    {"mode": "quota_reset"}, {"mode": ""}, {"mode": "unknown"},
    {"plan_code": None}, {"interval": []}, {"payment_source": None}, {"mode": []},
])
def test_unsupported_or_malformed_classification_fails_closed(overrides):
    assert purchase(**overrides) is None


def test_annual_payment_uses_one_reward_and_quota_reset_is_ineligible():
    assert purchase(interval="annual", amount_minor=334800, mode="renewal") is RENEWAL_TERMS
    assert purchase(interval="monthly", amount_minor=27900, mode="renewal") is RENEWAL_TERMS
    assert purchase(interval="annual", payment_source="quota_reset", mode="renewal") is None


def test_new_attribution_uses_regional_version_without_changing_legacy_rewards():
    assert purchase().version == "referral-2026-09-v2"
    assert coupon_terms_for_policy("referral-2026-09-v1", "TRY") is FIRST_PURCHASE_TERMS
    assert coupon_terms_for_policy("referral-2026-09-v1", "USD") is None
    assert coupon_terms_for_policy("referral-2026-09-v2", "USD").coupon_percent == 10


# Snapshot input values, not runtime prices: future catalog edits must not
# silently reprice existing referral versions. JPY/KRW use whole minor units.
LITE_PRICE_SNAPSHOT = {
    "TRY": 29900, "USD": 899, "EUR": 849, "GBP": 642, "CAD": 1017, "AUD": 1178,
    "NZD": 1285, "JPY": 1125, "KRW": 10181, "CNY": 5251, "INR": 58835, "BRL": 3750,
    "MXN": 14896, "CHF": 642, "SEK": 7822, "NOK": 8251, "DKK": 4822, "PLN": 2892,
    "AED": 2785, "SAR": 2785, "SGD": 1017, "HKD": 5893,
}


@pytest.mark.parametrize("currency,price", LITE_PRICE_SNAPSHOT.items())
def test_regional_coupon_caps_preserve_frozen_catalog_ratios(currency, price):
    assert len(REGIONAL_COUPON_CAPS_MINOR) == 22
    for base, try_cap, percent in ((REGIONAL_FIRST_PURCHASE_TERMS, 5000, 10), (RENEWAL_TERMS, 2500, 5)):
        terms = coupon_terms_for_policy(base.version, currency)
        assert terms.currency == currency
        assert terms.coupon_max_minor == price * try_cap // 29900
        assert terms.coupon_percent == percent
        assert terms.inviter_minutes == base.inviter_minutes
        assert terms.invitee_minutes == base.invitee_minutes
        assert terms.coupon_valid_days == 90


def test_resolving_currency_cannot_mutate_base_or_returned_terms():
    selected = coupon_terms_for_policy(REGIONAL_FIRST_PURCHASE_TERMS.version, "JPY")
    assert selected.coupon_max_minor == 188
    with pytest.raises(FrozenInstanceError):
        selected.currency = "TRY"
    assert REGIONAL_FIRST_PURCHASE_TERMS.currency == "TRY"
    assert REGIONAL_FIRST_PURCHASE_TERMS.coupon_max_minor == 5000


@pytest.mark.parametrize("version,currency", [
    ("unknown-version", "TRY"), (None, "TRY"), ([], "TRY"),
    ("referral-2026-09-v2", "BTC"), ("referral-2026-09-v2", "usd"),
    ("referral-2026-09-v2", ""), ("referral-2026-09-v2", None),
])
def test_unknown_versions_or_currencies_do_not_fall_back_to_another_policy(version, currency):
    assert coupon_terms_for_policy(version, currency) is None
