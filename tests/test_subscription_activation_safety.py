"""Purchase replacement regressions using only synthetic in-memory records.

These functional cases do not prove PostgreSQL row-lock serialization. That
requires concurrent verification in a separately authorized test environment.
"""

from datetime import datetime, timedelta, timezone
import uuid

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.pool import StaticPool

from lecturesift import billing_service as billing, rollout_service


PASSWORD = "Synthetic-subscription-password1"


@pytest.fixture
def state(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    billing.METADATA.create_all(engine)
    monkeypatch.setattr(billing, "ENGINE", engine)
    monkeypatch.setattr(rollout_service, "ENGINE", engine)
    monkeypatch.setattr(billing, "_INITIALIZED", True)
    monkeypatch.setenv("LECTURESIFT_REFERRALS_ENABLED", "false")
    monkeypatch.setattr(billing, "bank_transfer_available", lambda: True)
    monkeypatch.setattr(billing.config, "BILLING_BANK_IBAN", "synthetic-bank-reference")
    monkeypatch.setattr(billing.config, "BILLING_BANK_ACCOUNT_HOLDER", "Synthetic Test")
    monkeypatch.setattr(billing.config, "BILLING_BANK_NAME", "Synthetic Bank")
    monkeypatch.setattr(billing.config, "BILLING_SUPPORT_EMAIL", "billing@example.com")
    clock = {"now": datetime(2026, 9, 8, 12, tzinfo=timezone.utc)}
    monkeypatch.setattr(billing, "utcnow", lambda: clock["now"])
    monkeypatch.setattr(rollout_service, "utcnow", lambda: clock["now"])
    yield clock
    engine.dispose()


def new_user():
    return billing.register_user(
        f"subscription-{uuid.uuid4().hex}@example.com", PASSWORD, "Synthetic", "Test"
    )["user"]


def settle(order):
    return billing.complete_payment_order(
        order["reference"], succeeded=True, provider_amount_minor=order["amount_minor"]
    )


def subscription_rows(user_id):
    with billing.ENGINE.connect() as connection:
        return connection.execute(
            select(billing.SUBSCRIPTIONS).where(billing.SUBSCRIPTIONS.c.user_id == user_id)
        ).all()


@pytest.mark.parametrize("previous_status", ["active", "cancel_at_end"])
@pytest.mark.parametrize("purchase_method", ["payment", "manual"])
def test_new_monthly_purchase_replaces_longer_old_term_and_survives_retries(
    state, previous_status, purchase_method
):
    owner = new_user()
    annual = billing.create_payment_order(
        owner["id"], "synthetic-provider", "lite", "annual", "TRY"
    )
    settle(annual)
    if previous_status == "cancel_at_end":
        billing.cancel_active_subscription(owner["id"])
    state["now"] += timedelta(days=1)

    if purchase_method == "manual":
        monthly = billing.create_manual_order(owner["id"], "pro", "monthly")
        billing.approve_manual_order(monthly["reference"])
    else:
        monthly = billing.create_payment_order(
            owner["id"], "synthetic-provider", "pro", "monthly", "TRY"
        )
        settle(monthly)

    # A retry from the older annual order must not restore the earlier plan.
    state["now"] += timedelta(minutes=1)
    settle(annual)
    if purchase_method == "manual":
        billing.approve_manual_order(monthly["reference"])
    else:
        settle(monthly)

    account = billing.account_status(owner["id"])
    assert account["plan"]["code"] == "pro"
    assert account["remaining_minutes"] == 2000
    assert account["subscription"]["interval"] == "monthly"
    assert account["subscription"]["cancel_at_period_end"] is False
    rows = subscription_rows(owner["id"])
    assert {row.source_reference: row.status for row in rows} == {
        annual["reference"]: "replaced", monthly["reference"]: "active"
    }

    # Expiring the new term must not expose the replaced annual entitlement.
    state["now"] += timedelta(days=31)
    expired = billing.account_status(owner["id"])
    assert expired["plan"]["code"] == "free"
    assert expired["subscription"] is None


@pytest.mark.parametrize("closure_method", ["self", "admin"])
@pytest.mark.parametrize("plan_code, interval", [("pro", "monthly"), ("credit", "one_time")])
def test_account_closure_cancels_pending_transfer_before_late_completion(
    state, closure_method, plan_code, interval
):
    owner = new_user()
    order = billing.create_payment_order(
        owner["id"], billing.IYZICO_BANK_TRANSFER_PROVIDER, plan_code, interval, "TRY"
    )
    assert billing.mark_payment_order_pending(order["reference"])["status"] == "pending"

    if closure_method == "self":
        rollout_service.close_user_account(owner["id"], PASSWORD, owner["email"])
    else:
        rollout_service.admin_close_user_account(
            owner["id"], confirmation_email=owner["email"],
            reason="Synthetic closure test", actor="synthetic-admin",
        )

    assert billing.payment_order(order["reference"])["status"] == "cancelled"
    # Exercise the settlement boundary; provider callbacks still require
    # their own authentication and reconciliation of money received late.
    assert settle(order)["status"] == "cancelled"
    assert settle(order)["status"] == "cancelled"
    account = billing.account_status(owner["id"])
    assert account["plan"]["code"] == "free"
    assert account["credit_minutes"] == 0
    assert subscription_rows(owner["id"]) == []
