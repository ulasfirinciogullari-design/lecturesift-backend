"""Permanent account add-on, separate from study and assistant allowances."""
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import update

from lecturesift import billing, billing_service as service, rollout_service


def user():
    return service.register_user(f"adfree-{uuid.uuid4()}@example.com", "Strong-password123!", "Ad", "Free")["user"]["id"]


def paid(user_id, code="ad_free", interval="one_time"):
    order = service.create_payment_order(user_id, "test-provider", code, interval, "TRY")
    service.complete_payment_order(order["reference"], succeeded=True, provider_amount_minor=order["amount_minor"])
    return order


def test_pending_failed_and_another_users_orders_cannot_remove_ads():
    owner, other = user(), user()
    order = service.create_payment_order(owner, "test-provider", "ad_free", "one_time", "TRY")
    assert order["amount_minor"] == 5990
    assert not service.account_status(owner)["permanent_ad_free"]
    service.complete_payment_order(order["reference"], succeeded=False, provider_amount_minor=0)
    assert not service.account_status(owner)["plan"]["entitlements"]["ad_free"]
    paid(owner)
    assert not service.account_status(other)["permanent_ad_free"]


def test_permanent_access_preserves_plan_balances_and_survives_subscription_expiry(monkeypatch):
    owner = user()
    paid(owner, "plus", "monthly")
    before = service.account_status(owner)
    order = paid(owner)
    service.complete_payment_order(order["reference"], succeeded=True, provider_amount_minor=5990)
    after = service.account_status(owner)
    for key in ("remaining_minutes", "credit_minutes", "subscription"):
        assert after[key] == before[key]
    assert after["plan"]["code"] == "plus"
    assert after["permanent_ad_free"] is True
    assert after["plan"]["entitlements"]["assistant_credits"] == before["plan"]["entitlements"]["assistant_credits"]
    future = service.utcnow() + timedelta(days=800)
    monkeypatch.setattr(service, "utcnow", lambda: future)
    expired = service.account_status(owner)
    assert expired["plan"]["code"] == "free"
    assert expired["permanent_ad_free"] is True
    assert expired["plan"]["entitlements"]["ad_free"] is True
    assert expired["job_entitlements"]["ad_free"] is True
    assert expired["download_enabled"] is False
    assert expired["credit_minutes"] == 0
    assert not rollout_service.rewarded_ads_for_user(owner)["enabled"]


def test_repurchase_is_rejected_and_refunded_order_no_longer_grants_access():
    owner = user()
    order = paid(owner)
    with pytest.raises(service.BillingError):
        service.create_payment_order(owner, "test-provider", "ad_free", "one_time", "TRY")
    with service.ENGINE.begin() as connection:
        connection.execute(update(service.PAYMENT_ORDERS).where(service.PAYMENT_ORDERS.c.reference == order["reference"]).values(status="refunded"))
    assert not service.account_status(owner)["permanent_ad_free"]
    assert not service.account_status(owner)["plan"]["entitlements"]["ad_free"]


def test_manual_purchase_requires_approval_and_grants_only_ad_free(monkeypatch):
    monkeypatch.setattr(service, "bank_transfer_available", lambda: True)
    owner = user()
    order = service.create_manual_order(owner, "ad_free", "one_time")
    assert order["amount_minor"] == 5990
    assert not service.account_status(owner)["permanent_ad_free"]
    service.approve_manual_order(order["reference"])
    after = service.account_status(owner)
    assert after["permanent_ad_free"] is True
    assert after["plan"]["code"] == "free"
    assert after["credit_minutes"] == 0
    assert after["download_enabled"] is False
    with pytest.raises(service.BillingError):
        service.create_manual_order(owner, "ad_free", "one_time")


def test_catalog_retires_test_offer_and_prices_addon_in_all_supported_currencies():
    owner = user()
    for currency in billing.SUPPORTED_CURRENCIES:
        catalog = billing.public_catalog(currency)
        assert "test" not in {plan["code"] for plan in catalog["plans"]}
        addon = catalog["ad_free"]
        assert addon["display_price"]["currency"] == currency
        assert addon["display_price"]["amount_minor"] > 0
        assert addon["kind"] == "one_time"
        assert addon["minutes"] == addon["assistant_credits"] == 0
        assert not addon["export_enabled"]
    with pytest.raises(service.BillingError):
        service.create_payment_order(owner, "test-provider", "test", "one_time", "TRY")
    with pytest.raises(service.BillingError):
        service.create_payment_order(owner, "test-provider", "ad_free", "annual", "TRY")


def test_preexisting_test_order_can_still_complete_without_becoming_ad_free():
    owner = user()
    now = service.utcnow()
    reference = "legacy-test-" + uuid.uuid4().hex
    with service.ENGINE.begin() as connection:
        connection.execute(service.PAYMENT_ORDERS.insert().values(
            reference=reference, user_id=owner, provider="legacy-test-provider",
            plan_code="test", interval="one_time", amount_minor=100, currency="TRY",
            status="created", created_at=now, updated_at=now,
        ))
    service.complete_payment_order(reference, succeeded=True, provider_amount_minor=100)
    account = service.account_status(owner)
    assert account["credit_minutes"] == 1
    assert not account["permanent_ad_free"]
