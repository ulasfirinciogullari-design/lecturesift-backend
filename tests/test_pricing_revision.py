import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select, update

import lecturesift.billing as billing
import lecturesift.billing_service as billing_service
import lecturesift.rollout_service as rollout_service
from lecturesift.billing import LEGACY_PLAN_BY_CODE, PLAN_BY_CODE, REGIONAL_PRICES


def new_user() -> str:
    created = billing_service.register_user(
        f"pricing-{uuid.uuid4()}@example.com",
        "Strong-pricing-password1",
        "Pricing",
        "Test",
    )
    return created["user"]["id"]


def purchase_terms(reference: str) -> dict:
    with billing_service.ENGINE.connect() as connection:
        row = connection.execute(
            select(billing_service.PURCHASE_TERMS).where(
                billing_service.PURCHASE_TERMS.c.reference == reference
            )
        ).one()
    return {"row": row, "payload": json.loads(row.plan_json)}


def test_revised_catalog_and_regional_prices_preserve_legacy_catalog() -> None:
    expected = {
        "lite": (400, 29900, 120, 10, 20, 30, "standard"),
        "plus": (900, 59900, 240, 20, 40, 90, "standard"),
        "pro": (2000, 119900, 360, 30, 60, 365, "priority"),
        "max": (4000, 229900, 600, 30, 60, 730, "priority"),
    }
    for code, values in expected.items():
        plan = PLAN_BY_CODE[code]
        assert (
            plan.minutes,
            plan.try_amount_minor,
            plan.max_minutes_per_job,
            plan.quiz_questions,
            plan.flashcards,
            plan.history_days,
            plan.priority,
        ) == values

    assert (PLAN_BY_CODE["credit"].minutes, PLAN_BY_CODE["credit"].try_amount_minor) == (180, 19900)
    assert (LEGACY_PLAN_BY_CODE["plus"].minutes, LEGACY_PLAN_BY_CODE["plus"].try_amount_minor) == (1800, 44900)
    assert (LEGACY_PLAN_BY_CODE["max"].minutes, LEGACY_PLAN_BY_CODE["max"].max_minutes_per_job) == (12000, 900)
    assert [REGIONAL_PRICES[code]["USD"] for code in ("lite", "plus", "pro", "max")] == [899, 1699, 3299, 5999]
    assert [REGIONAL_PRICES[code]["EUR"] for code in ("lite", "plus", "pro", "max")] == [849, 1599, 3099, 5699]


def test_new_monthly_payment_snapshots_full_terms_and_drives_usage() -> None:
    user_id = new_user()
    order = billing_service.create_payment_order(user_id, "test-provider", "plus", "monthly", "TRY")
    assert order["amount_minor"] == 59900

    terms = purchase_terms(order["reference"])
    assert terms["row"].version == billing_service.PURCHASE_TERMS_VERSION
    assert terms["payload"]["purchase"] == {
        "amount_minor": 59900,
        "currency": "TRY",
        "interval": "monthly",
        "plan_code": "plus",
    }
    assert terms["payload"]["plan"]["minutes"] == 900
    assert terms["payload"]["entitlements"]["limits"]["max_minutes_per_job"] == 240
    assert terms["payload"]["entitlements"]["quiz_questions"] == 20
    assert terms["payload"]["entitlements"]["flashcards"] == 40

    billing_service.complete_payment_order(
        order["reference"], succeeded=True, provider_amount_minor=order["amount_minor"]
    )
    account = billing_service.account_status(user_id)
    assert account["remaining_minutes"] == 900
    assert account["plan"]["history_days"] == 90
    assert account["subscription"]["terms_version"] == billing_service.PURCHASE_TERMS_VERSION

    billing_service.record_usage(user_id, f"pricing-job-{uuid.uuid4()}", 60 * 60)
    assert billing_service.account_status(user_id)["remaining_minutes"] == 840


def test_annual_order_snapshots_discounted_amount_but_resets_same_quota_monthly(monkeypatch) -> None:
    clock = {"now": datetime(2026, 1, 15, 12, tzinfo=timezone.utc)}
    monkeypatch.setattr(billing_service, "utcnow", lambda: clock["now"])
    user_id = new_user()
    order = billing_service.create_payment_order(user_id, "test-provider", "pro", "annual", "USD")
    assert order["amount_minor"] == 32990
    terms = purchase_terms(order["reference"])["payload"]
    assert terms["purchase"]["amount_minor"] == 32990
    assert terms["purchase"]["interval"] == "annual"
    assert terms["plan"]["minutes"] == 2000

    billing_service.complete_payment_order(
        order["reference"], succeeded=True, provider_amount_minor=order["amount_minor"]
    )
    billing_service.record_usage(user_id, f"annual-job-{uuid.uuid4()}", 120 * 60)
    assert billing_service.account_status(user_id)["remaining_minutes"] == 1880

    clock["now"] = datetime(2026, 2, 15, 12, tzinfo=timezone.utc)
    renewed_month = billing_service.account_status(user_id)
    assert renewed_month["subscription"]["interval"] == "annual"
    assert renewed_month["remaining_minutes"] == 2000


def test_legacy_subscription_and_pending_order_keep_old_entitlements() -> None:
    now = billing_service.utcnow()
    existing_user = new_user()
    existing_reference = f"LEG{uuid.uuid4().hex}"
    pending_user = new_user()
    pending_reference = f"LEGACY-PAY-{uuid.uuid4()}"
    with billing_service.ENGINE.begin() as connection:
        connection.execute(
            billing_service.PAYMENT_ORDERS.insert().values(
                reference=existing_reference,
                user_id=existing_user,
                provider="legacy-provider",
                plan_code="plus",
                interval="monthly",
                amount_minor=44900,
                currency="TRY",
                status="paid",
                provider_amount_minor=44900,
                failure_code=None,
                failure_message=None,
                created_at=now,
                updated_at=now,
            )
        )
        connection.execute(
            billing_service.SUBSCRIPTIONS.insert().values(
                id=str(uuid.uuid4()),
                user_id=existing_user,
                plan_code="plus",
                interval="monthly",
                status="active",
                starts_at=now,
                ends_at=now + timedelta(days=30),
                source_reference=existing_reference,
                created_at=now,
            )
        )
        connection.execute(
            billing_service.PAYMENT_ORDERS.insert().values(
                reference=pending_reference,
                user_id=pending_user,
                provider="legacy-provider",
                plan_code="plus",
                interval="monthly",
                amount_minor=44900,
                currency="TRY",
                status="created",
                provider_amount_minor=None,
                failure_code=None,
                failure_message=None,
                created_at=now,
                updated_at=now,
            )
        )

    existing = billing_service.account_status(existing_user)
    assert existing["remaining_minutes"] == 1800
    assert existing["plan"]["history_days"] == 180
    assert existing["plan"]["display_price"] is None
    assert existing["plan"]["manual_price"] == {"currency": "TRY", "amount_minor": 44900}
    assert existing["job_entitlements"]["limits"]["max_minutes_per_job"] == 300
    assert existing["subscription"]["terms_version"] == billing_service.LEGACY_TERMS_VERSION

    billing_service.complete_payment_order(
        pending_reference, succeeded=True, provider_amount_minor=44900
    )
    activated = billing_service.account_status(pending_user)
    assert activated["remaining_minutes"] == 1800
    assert activated["plan"]["entitlements"]["quiz_questions"] == 30
    assert activated["subscription"]["terms_version"] == billing_service.LEGACY_TERMS_VERSION


def test_manual_order_snapshot_default_free_and_cross_user_activation_are_safe(monkeypatch) -> None:
    monkeypatch.setattr(billing_service, "bank_transfer_available", lambda: True)
    owner_id = new_user()
    other_id = new_user()
    assert billing_service.account_status(other_id)["plan"]["code"] == "free"
    assert billing_service.account_status(other_id)["remaining_minutes"] == 60

    manual = billing_service.create_manual_order(owner_id, "lite", "annual")
    assert manual["amount_minor"] == 299000
    terms = purchase_terms(manual["reference"])["payload"]
    assert terms["purchase"]["amount_minor"] == 299000
    assert terms["plan"]["minutes"] == 400

    with pytest.raises(billing_service.BillingAuthenticationError):
        with billing_service.ENGINE.begin() as connection:
            billing_service._activate_purchase(
                connection,
                user_id=other_id,
                plan_code="lite",
                interval="annual",
                reference=manual["reference"],
                now=billing_service.utcnow(),
            )
    assert billing_service.account_status(other_id)["plan"]["code"] == "free"

    first = billing_service.approve_manual_order(manual["reference"])
    second = billing_service.approve_manual_order(manual["reference"])
    assert first["remaining_minutes"] == second["remaining_minutes"] == 400
    with billing_service.ENGINE.connect() as connection:
        count = connection.execute(
            select(func.count()).select_from(billing_service.SUBSCRIPTIONS).where(
                billing_service.SUBSCRIPTIONS.c.source_reference == manual["reference"]
            )
        ).scalar_one()
    assert count == 1


def test_runtime_safety_cap_change_does_not_invalidate_immutable_terms(monkeypatch) -> None:
    user_id = new_user()
    order = billing_service.create_payment_order(
        user_id, "test-provider", "plus", "monthly", "TRY"
    )
    terms = purchase_terms(order["reference"])["payload"]
    assert terms["plan"]["max_document_upload_mb"] == 100

    monkeypatch.setattr(billing, "MAX_DOCUMENT_BYTES", 10 * 1024 * 1024)
    monkeypatch.setattr(billing, "MAX_DOCUMENT_CHARACTERS", 12_345)
    billing_service.complete_payment_order(
        order["reference"], succeeded=True, provider_amount_minor=order["amount_minor"]
    )

    account = billing_service.account_status(user_id)
    limits = account["plan"]["entitlements"]["limits"]
    assert limits["max_document_upload_mb"] == 10
    assert limits["max_document_characters"] == 12_345
    assert account["remaining_minutes"] == 900


def test_snapshot_purchase_amount_is_bound_to_the_server_order() -> None:
    user_id = new_user()
    order = billing_service.create_payment_order(
        user_id, "test-provider", "lite", "monthly", "TRY"
    )
    terms = purchase_terms(order["reference"])
    terms["payload"]["purchase"]["amount_minor"] += 1
    with billing_service.ENGINE.begin() as connection:
        connection.execute(
            update(billing_service.PURCHASE_TERMS)
            .where(billing_service.PURCHASE_TERMS.c.reference == order["reference"])
            .values(plan_json=json.dumps(terms["payload"]))
        )

    with pytest.raises(billing_service.BillingConfigurationError):
        billing_service.complete_payment_order(
            order["reference"],
            succeeded=True,
            provider_amount_minor=order["amount_minor"],
        )
    assert billing_service.account_status(user_id)["plan"]["code"] == "free"


def test_new_admin_grant_uses_current_terms_while_old_admin_grant_stays_legacy() -> None:
    current_user = new_user()
    current = rollout_service.admin_set_user_subscription(
        current_user,
        plan_code="plus",
        interval="monthly",
        duration_days=45,
        actor="pricing-test-admin",
    )
    assert current["remaining_minutes"] == 900
    assert current["subscription"]["terms_version"] == billing_service.ADMIN_GRANT_TERMS_VERSION

    with billing_service.ENGINE.connect() as connection:
        subscription = connection.execute(
            select(billing_service.SUBSCRIPTIONS).where(
                billing_service.SUBSCRIPTIONS.c.user_id == current_user,
                billing_service.SUBSCRIPTIONS.c.status == "active",
            )
        ).one()
    terms = purchase_terms(subscription.source_reference)
    assert terms["row"].version == billing_service.ADMIN_GRANT_TERMS_VERSION
    assert "purchase" not in terms["payload"]
    assert terms["payload"]["admin_grant"] == {
        "amount_minor": 0,
        "currency": None,
        "interval": "monthly",
        "plan_code": "plus",
        "source": "admin",
    }

    legacy_user = new_user()
    now = billing_service.utcnow()
    with billing_service.ENGINE.begin() as connection:
        connection.execute(
            billing_service.SUBSCRIPTIONS.insert().values(
                id=str(uuid.uuid4()),
                user_id=legacy_user,
                plan_code="plus",
                interval="monthly",
                status="active",
                starts_at=now,
                ends_at=now + timedelta(days=45),
                source_reference=f"ADMIN-OLD-{uuid.uuid4().hex[:20].upper()}",
                created_at=now,
            )
        )
    legacy = billing_service.account_status(legacy_user)
    assert legacy["remaining_minutes"] == 1800
    assert legacy["subscription"]["terms_version"] == billing_service.LEGACY_TERMS_VERSION


def test_purchase_terms_reference_is_standalone_64_character_key() -> None:
    table = billing_service.PURCHASE_TERMS
    assert list(table.c.keys()) == ["reference", "plan_json", "version", "created_at"]
    assert table.c.reference.type.length == 64
    assert list(table.primary_key.columns) == [table.c.reference]
    assert not table.foreign_keys
