"""Synthetic guest identities must not inflate registered-account metrics."""

from datetime import datetime, timedelta, timezone
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from lecturesift import billing_service as billing


NOW = datetime(2026, 10, 12, 12, tzinfo=timezone.utc)


@pytest.fixture
def metrics_database(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    billing.METADATA.create_all(engine)
    monkeypatch.setattr(billing, "ENGINE", engine)
    monkeypatch.setattr(billing, "_INITIALIZED", True)
    monkeypatch.setattr(billing, "utcnow", lambda: NOW)
    yield engine
    engine.dispose()


def add_user(engine, user_id, email, age, *, verified):
    created_at = NOW - age
    with engine.begin() as connection:
        connection.execute(billing.USERS.insert().values(
            id=user_id,
            email=email,
            password_salt="synthetic-salt",
            password_hash="synthetic-hash",
            credit_minutes=0,
            created_at=created_at,
        ))
        connection.execute(billing.USER_PROFILES.insert().values(
            user_id=user_id,
            first_name="Synthetic",
            last_name="Account",
            email_verified_at=created_at if verified else None,
            created_at=created_at,
            updated_at=created_at,
        ))


def add_subscription(engine, user_id, plan_code, *, status="active", days_remaining=10):
    subscription_id = str(uuid.uuid4())
    with engine.begin() as connection:
        connection.execute(billing.SUBSCRIPTIONS.insert().values(
            id=subscription_id,
            user_id=user_id,
            plan_code=plan_code,
            interval="monthly",
            status=status,
            starts_at=NOW - timedelta(days=20),
            ends_at=NOW + timedelta(days=days_remaining),
            source_reference=subscription_id,
            created_at=NOW - timedelta(days=20),
        ))


def test_overview_separates_registered_metrics_without_hiding_guests(metrics_database):
    engine = metrics_database
    for user_id, email, age, verified in (
        ("recent-verified", "verified@example.test", timedelta(hours=2), True),
        ("recent-unverified", "unverified@example.test", timedelta(hours=12), False),
        ("weekly", "weekly@example.test", timedelta(days=3), True),
        ("monthly", "monthly@example.test", timedelta(days=15), False),
        ("older", "older@example.test", timedelta(days=40), True),
        ("lookalike", "user@guest.lecturesift.invalid.example", timedelta(days=8), False),
        ("guest-lower", "guest-a@guest.lecturesift.invalid", timedelta(hours=1), True),
        ("guest-upper", "guest-b@GUEST.LECTURESIFT.INVALID", timedelta(days=2), True),
        ("deleted", "deleted+closed@users.invalid", timedelta(hours=1), True),
    ):
        add_user(engine, user_id, email, age, verified=verified)

    add_subscription(engine, "recent-verified", "plus")
    add_subscription(engine, "recent-verified", "plus", days_remaining=20)
    add_subscription(engine, "recent-unverified", "free")
    add_subscription(engine, "weekly", "guest")
    add_subscription(engine, "monthly", "plus", days_remaining=0)
    add_subscription(engine, "monthly", "pro", status="cancelled")
    add_subscription(engine, "older", "plus", status="cancel_at_end")
    add_subscription(engine, "guest-lower", "guest")
    add_subscription(engine, "guest-upper", "plus")
    add_subscription(engine, "deleted", "pro")

    overview = billing.admin_billing_overview()
    counts = overview["counts"]
    assert {key: counts[key] for key in (
        "registered_users", "guest_users", "verified_registered_users",
        "registered_users_24h", "registered_users_7d", "registered_users_30d",
        "active_registered_subscriptions",
    )} == {
        "registered_users": 6,
        "guest_users": 2,
        "verified_registered_users": 3,
        "registered_users_24h": 2,
        "registered_users_7d": 3,
        "registered_users_30d": 5,
        "active_registered_subscriptions": 2,
    }
    assert overview["registered_verification_rate"] == 50.0

    # Keep the existing API totals and plan distribution available to callers.
    assert {key: counts[key] for key in (
        "users", "verified_users", "users_24h", "users_7d", "users_30d",
        "active_subscriptions",
    )} == {
        "users": 8,
        "verified_users": 5,
        "users_24h": 3,
        "users_7d": 5,
        "users_30d": 7,
        "active_subscriptions": 6,
    }
    assert overview["verification_rate"] == 62.5
    assert overview["plan_distribution"] == {"free": 3, "guest": 2, "plus": 3}
    users = {user["id"]: user for user in overview["users"]}
    assert set(users) == {
        "recent-verified", "recent-unverified", "weekly", "monthly", "older",
        "lookalike", "guest-lower", "guest-upper",
    }
    assert users["guest-lower"]["email_verified"] is True
    assert users["guest-lower"]["plan_code"] == "guest"
    assert users["guest-upper"]["subscription"]["plan_code"] == "plus"

    limited_overview = billing.admin_billing_overview(limit=1)
    assert len(limited_overview["users"]) == 1
    assert limited_overview["counts"] == counts
    assert limited_overview["registered_verification_rate"] == 50.0
    assert limited_overview["plan_distribution"] == overview["plan_distribution"]


@pytest.mark.parametrize("include_guest", [False, True])
def test_registered_metrics_are_zero_without_registered_users(metrics_database, include_guest):
    if include_guest:
        add_user(
            metrics_database, "guest-only", "guest-only@guest.lecturesift.invalid",
            timedelta(hours=1), verified=True,
        )
        add_subscription(metrics_database, "guest-only", "guest")

    overview = billing.admin_billing_overview()
    counts = overview["counts"]
    assert counts["registered_users"] == 0
    assert counts["guest_users"] == int(include_guest)
    assert counts["verified_registered_users"] == 0
    assert counts["registered_users_24h"] == 0
    assert counts["registered_users_7d"] == 0
    assert counts["registered_users_30d"] == 0
    assert counts["active_registered_subscriptions"] == 0
    assert overview["registered_verification_rate"] == 0
    assert counts["users"] == int(include_guest)
    assert counts["verified_users"] == int(include_guest)
    assert counts["active_subscriptions"] == int(include_guest)
    assert len(overview["users"]) == int(include_guest)
