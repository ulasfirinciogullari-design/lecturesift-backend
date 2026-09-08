"""Account protection must survive missing configuration and all close paths."""

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.pool import StaticPool

from lecturesift import billing_service as billing, config, rollout_service as rollout


PASSWORD = "Synthetic-owner-password1"
OWNER_EMAIL = "ulasfirinciogullari@gmail.com"


@pytest.fixture
def state(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    billing.METADATA.create_all(engine)
    monkeypatch.setattr(billing, "ENGINE", engine)
    monkeypatch.setattr(rollout, "ENGINE", engine)
    monkeypatch.setattr(billing, "_INITIALIZED", True)
    monkeypatch.setattr(config, "BILLING_SESSION_SECRET", "synthetic-protection-session-secret")
    monkeypatch.setattr(config, "BILLING_PROTECTED_EMAILS", set())
    monkeypatch.setattr(config, "LEGAL_OPERATOR_EMAIL", "")
    monkeypatch.setenv("LECTURESIFT_REFERRALS_ENABLED", "false")
    yield engine
    engine.dispose()


def account(email):
    result = billing.register_user(email, PASSWORD, "Synthetic", "Owner", country_code="TR")
    billing.verify_email(result["verification_token"])
    return result["user"]["id"]


def identity(engine, user_id):
    with engine.connect() as connection:
        user = connection.execute(select(billing.USERS).where(billing.USERS.c.id == user_id)).one()
        profile = connection.execute(
            select(billing.USER_PROFILES).where(billing.USER_PROFILES.c.user_id == user_id)
        ).one()
    return tuple(user), tuple(profile)


@pytest.mark.parametrize("source", ["owner", "configured", "legal"])
@pytest.mark.parametrize("path", ["self", "admin"])
def test_protected_accounts_cannot_be_closed(state, monkeypatch, source, path):
    email = OWNER_EMAIL if source == "owner" else "protected@example.test"
    if source == "configured":
        monkeypatch.setattr(config, "BILLING_PROTECTED_EMAILS", {f"  {email.upper()}  "})
    elif source == "legal":
        monkeypatch.setattr(config, "LEGAL_OPERATOR_EMAIL", f"  {email.upper()}  ")
    user_id = account(email)
    before = identity(state, user_id)

    assert rollout.admin_user_identity(user_id)["is_protected"] is True
    overview_user = next(
        user for user in billing.admin_billing_overview()["users"] if user["id"] == user_id
    )
    assert overview_user["is_protected"] is True
    with pytest.raises(billing.BillingError, match="kapatılamaz"):
        if path == "self":
            rollout.close_user_account(user_id, PASSWORD, email)
        else:
            rollout.admin_close_user_account(
                user_id, confirmation_email=email, reason="Synthetic test", actor="test"
            )
    assert identity(state, user_id) == before


def test_admin_cannot_remove_protection_by_renaming_owner(state):
    user_id = account(OWNER_EMAIL)
    before = identity(state, user_id)
    values = dict(
        first_name="Synthetic", last_name="Owner", phone="", country_code="TR",
        preferred_language="tr", email_verified=True, actor="test",
    )
    with pytest.raises(billing.BillingError, match="e-posta adresi değiştirilemez"):
        rollout.admin_update_user(user_id, email="renamed@example.test", **values)
    assert identity(state, user_id) == before

    updated = rollout.admin_update_user(user_id, email=OWNER_EMAIL, **values)
    assert updated["user"]["email"] == OWNER_EMAIL
    assert updated["is_admin"] is False


@pytest.mark.parametrize("path", ["self", "admin"])
def test_ordinary_account_closure_remains_available(state, path):
    email = "ordinary@example.test"
    user_id = account(email)
    assert rollout.admin_user_identity(user_id)["is_protected"] is False
    if path == "self":
        result = rollout.close_user_account(user_id, PASSWORD, email)
    else:
        result = rollout.admin_close_user_account(
            user_id, confirmation_email=email, reason="Synthetic test", actor="test"
        )
    assert result["status"] == "closed"
    with pytest.raises(billing.BillingAuthenticationError):
        billing.login_user(email, PASSWORD)
