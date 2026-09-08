"""Email-change regressions using only synthetic accounts and captured mail.

The isolated SQLite fixture checks transaction outcomes and deterministic
resend interleaving; PostgreSQL row-lock concurrency still needs remote tests.
"""

from datetime import datetime, timedelta, timezone
import re
import uuid

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.pool import StaticPool

from lecturesift import billing_service as billing, config, rollout_service as rollout


@pytest.fixture
def state(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    billing.METADATA.create_all(engine)
    monkeypatch.setattr(billing, "ENGINE", engine)
    monkeypatch.setattr(billing, "_INITIALIZED", True)
    monkeypatch.setattr(rollout, "ENGINE", engine)
    monkeypatch.setattr(config, "BILLING_SESSION_SECRET", "synthetic-email-change-test-session-secret")
    monkeypatch.setattr(config, "BILLING_LEGACY_SESSION_SECRET_HEX", "")
    monkeypatch.setattr(config, "BILLING_PROTECTED_EMAILS", ())
    monkeypatch.setattr(config, "LEGAL_OPERATOR_EMAIL", "")
    monkeypatch.setattr(rollout, "email_delivery_configured", lambda: True)
    captured = []
    monkeypatch.setattr(
        rollout,
        "send_transactional_email",
        lambda to, subject, html, text: captured.append({"to": to, "text": text}),
    )
    clock = {"now": datetime(2026, 9, 8, 12, tzinfo=timezone.utc)}
    monkeypatch.setattr(billing, "utcnow", lambda: clock["now"])
    monkeypatch.setattr(rollout, "utcnow", lambda: clock["now"])
    yield {"engine": engine, "mail": captured, "clock": clock}
    engine.dispose()


def _account(state, email="before@example.com"):
    user_id = str(uuid.uuid4())
    now = state["clock"]["now"]
    with state["engine"].begin() as connection:
        connection.execute(billing.USERS.insert().values(
            id=user_id, email=email, password_salt="00" * 16,
            password_hash="00" * 32, credit_minutes=0, created_at=now,
        ))
        connection.execute(billing.USER_PROFILES.insert().values(
            user_id=user_id, first_name="Synthetic", last_name="Account", country_code="TR",
            email_verified_at=now, session_version=1, created_at=now, updated_at=now,
        ))
    return {"id": user_id, "email": email, "token": billing.issue_session(user_id, email, 1)}


def _code(state):
    return re.search(r"\b\d{6}\b", state["mail"][-1]["text"]).group(0)


def _pending(state, user_id):
    with state["engine"].connect() as connection:
        return connection.execute(select(rollout.EMAIL_CHANGE_REQUESTS).where(
            rollout.EMAIL_CHANGE_REQUESTS.c.user_id == user_id,
        )).one_or_none()


def _wrong_code(code):
    return f"{(int(code) + 1) % 1_000_000:06d}"


def test_request_returns_only_metadata_and_sends_code_to_destination(state):
    account = _account(state)
    result = rollout.request_email_change(account["id"], " NEW@example.com ")

    assert set(result) == {"ok", "new_email", "expires_at"}
    assert result["new_email"] == "new@example.com"
    assert state["mail"][-1]["to"] == "new@example.com"
    assert len(_code(state)) == 6
    assert billing.account_status(account["id"])["user"]["email"] == account["email"]


def test_five_invalid_attempts_persist_and_invalidate_even_the_correct_code(state):
    account = _account(state)
    rollout.request_email_change(account["id"], "new@example.com")
    correct_code = _code(state)

    for attempt in range(1, 6):
        with pytest.raises(billing.BillingAuthenticationError, match="Doğrulama kodu geçersiz"):
            rollout.verify_email_change(account["id"], code=_wrong_code(correct_code))
        pending = _pending(state, account["id"])
        if attempt < 5:
            assert pending.attempt_count == attempt
        else:
            assert pending is None

    with pytest.raises(billing.BillingAuthenticationError):
        rollout.verify_email_change(account["id"], code=correct_code)
    assert billing.authenticate_session(account["token"])["email"] == account["email"]


def test_success_after_four_errors_rotates_sessions_and_cannot_be_replayed(state):
    account = _account(state)
    rollout.request_email_change(account["id"], "new@example.com")
    correct_code = _code(state)
    for _ in range(4):
        with pytest.raises(billing.BillingAuthenticationError):
            rollout.verify_email_change(account["id"], code=_wrong_code(correct_code))

    result = rollout.verify_email_change(account["id"], code=correct_code)

    assert result["account"]["user"]["email"] == "new@example.com"
    assert billing.authenticate_session(result["token"])["email"] == "new@example.com"
    with pytest.raises(billing.BillingAuthenticationError):
        billing.authenticate_session(account["token"])
    with pytest.raises(billing.BillingAuthenticationError):
        rollout.verify_email_change(account["id"], code=correct_code)
    assert _pending(state, account["id"]) is None


def test_expired_change_cannot_update_the_account(state):
    account = _account(state)
    rollout.request_email_change(account["id"], "new@example.com")
    state["clock"]["now"] += timedelta(minutes=15)

    with pytest.raises(billing.BillingAuthenticationError):
        rollout.verify_email_change(account["id"], code=_code(state))

    assert billing.authenticate_session(account["token"])["email"] == account["email"]


def test_failed_delivery_does_not_remove_a_later_successful_resend(state, monkeypatch):
    account = _account(state)

    def interleaved_delivery(to, subject, html, text):
        if to == "first@example.com":
            rollout.request_email_change(account["id"], "latest@example.com")
            raise rollout.EmailDeliveryError("Synthetic failed delivery")
        state["mail"].append({"to": to, "text": text})

    monkeypatch.setattr(rollout, "send_transactional_email", interleaved_delivery)
    with pytest.raises(rollout.EmailDeliveryError):
        rollout.request_email_change(account["id"], "first@example.com")

    assert _pending(state, account["id"]).new_email == "latest@example.com"
    result = rollout.verify_email_change(account["id"], code=_code(state))
    assert result["account"]["user"]["email"] == "latest@example.com"


def test_failed_delivery_removes_its_own_request(state, monkeypatch):
    account = _account(state)

    def failed_delivery(*args):
        raise rollout.EmailDeliveryError("Synthetic failed delivery")

    monkeypatch.setattr(rollout, "send_transactional_email", failed_delivery)
    with pytest.raises(rollout.EmailDeliveryError):
        rollout.request_email_change(account["id"], "new@example.com")

    assert _pending(state, account["id"]) is None
    assert billing.authenticate_session(account["token"])["email"] == account["email"]


def test_protected_owner_cannot_lose_protection_by_changing_email(state):
    account = _account(state, "ulasfirinciogullari@gmail.com")

    with pytest.raises(billing.BillingError, match="Korunan"):
        rollout.request_email_change(account["id"], "new@example.com")

    assert _pending(state, account["id"]) is None
    assert state["mail"] == []


def test_pending_change_cannot_rename_a_newly_protected_account(state, monkeypatch):
    account = _account(state)
    rollout.request_email_change(account["id"], "new@example.com")
    monkeypatch.setattr(config, "BILLING_PROTECTED_EMAILS", ("  BEFORE@EXAMPLE.COM  ",))

    with pytest.raises(billing.BillingError, match="Korunan"):
        rollout.verify_email_change(account["id"], code=_code(state))

    assert billing.authenticate_session(account["token"])["email"] == account["email"]
