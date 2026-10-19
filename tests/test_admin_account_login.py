"""Admin sign-in with a listed account, an emailed code and a bounded session.

Uses an isolated SQLite engine, a fixed clock, captured mail and a fresh rate
limiter, so no real account, mailbox or shared limiter state is involved.
"""

from datetime import datetime, timedelta, timezone
import re
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.pool import StaticPool

import lecturesift.app as app_module
from lecturesift import billing_service as billing, config, rollout_service as rollout, security
from lecturesift.app import app
from lecturesift.rollout_routes import install_rollout_routes

ADMIN_EMAIL = "owner@example.com"
PASSWORD = "Strong-admin-password1"
NEW_PASSWORD = "Updated-admin-password2"
EMERGENCY_KEY = "synthetic-emergency-admin-key"


@pytest.fixture
def state(monkeypatch):
    install_rollout_routes(app)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    billing.METADATA.create_all(engine)
    monkeypatch.setattr(billing, "ENGINE", engine)
    monkeypatch.setattr(billing, "_INITIALIZED", True)
    monkeypatch.setattr(rollout, "ENGINE", engine)
    monkeypatch.setattr(config, "BILLING_SESSION_SECRET", "synthetic-admin-login-test-session-secret")
    monkeypatch.setattr(config, "BILLING_LEGACY_SESSION_SECRET_HEX", "")
    monkeypatch.setattr(config, "ADMIN_ADMIN", EMERGENCY_KEY)
    monkeypatch.setattr(config, "ADMIN_EMAILS", {ADMIN_EMAIL})
    # A fresh process-local limiter with a frozen bucket keeps limits deterministic.
    limiter = security.RateLimiter()
    limiter._redis = None
    monkeypatch.setattr(app_module, "RATE_LIMITER", limiter)
    monkeypatch.setattr(security, "time", SimpleNamespace(time=lambda: 1_800_000_000.0))
    mail = []
    monkeypatch.setattr(app_module, "email_delivery_configured", lambda: True)
    monkeypatch.setattr(
        app_module,
        "send_transactional_email",
        lambda to, subject, html, text: mail.append({"to": to, "subject": subject, "text": text}),
    )
    clock = {"now": datetime(2026, 10, 1, 12, tzinfo=timezone.utc)}
    monkeypatch.setattr(billing, "utcnow", lambda: clock["now"])
    monkeypatch.setattr(rollout, "utcnow", lambda: clock["now"])
    yield {"engine": engine, "mail": mail, "clock": clock, "client": TestClient(app)}
    engine.dispose()


def _account(email=ADMIN_EMAIL, *, verified=True):
    created = billing.register_user(email, PASSWORD, "Site", "Owner")
    if verified:
        billing.verify_email_code(email, created["verification_code"])
    return created["user"]["id"]


def _start(state, email=ADMIN_EMAIL, password=PASSWORD, ip="192.0.2.10"):
    return state["client"].post(
        "/billing/admin/session/start",
        json={"email": email, "password": password},
        headers={"X-Forwarded-For": ip},
    )


def _verify(state, challenge, code, email=ADMIN_EMAIL):
    return state["client"].post(
        "/billing/admin/session/verify",
        json={"email": email, "challenge": challenge, "code": code},
    )


def _code(state):
    return re.search(r"\b\d{6}\b", state["mail"][-1]["text"]).group(0)


def _wrong(code):
    return f"{(int(code) + 1) % 1_000_000:06d}"


def _sign_in(state, password=PASSWORD):
    started = _start(state, password=password)
    assert started.status_code == 200, started.text
    verified = _verify(state, started.json()["challenge"], _code(state))
    assert verified.status_code == 200, verified.text
    return verified.json()["token"]


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def test_listed_account_signs_in_with_emailed_code_and_audits_its_email(state):
    user_id = _account()
    started = _start(state, email="  Owner@Example.com ")
    assert started.status_code == 200
    assert state["mail"][-1]["to"] == ADMIN_EMAIL
    code = _code(state)
    assert code not in started.text
    with state["engine"].connect() as connection:
        stored = connection.execute(
            select(billing.AUTH_TOKENS).where(billing.AUTH_TOKENS.c.user_id == user_id,
                                              billing.AUTH_TOKENS.c.purpose.like("admin_login:%"))
        ).one()
    # Only a digest bound to the browser's challenge is stored, never the code.
    assert stored.token_hash == billing._admin_code_hash(started.json()["challenge"], code)
    assert stored.token_hash != billing._token_hash(code)
    assert stored.expires_at.replace(tzinfo=timezone.utc) == state["clock"]["now"] + timedelta(minutes=10)

    verified = _verify(state, started.json()["challenge"], code)
    assert verified.status_code == 200
    assert verified.json()["email"] == ADMIN_EMAIL
    token = verified.json()["token"]
    client = state["client"]
    assert client.get("/billing/admin/overview", headers=_bearer(token)).status_code == 200
    assert client.get("/billing/admin/credit-events", headers=_bearer(token)).status_code == 200
    # Admin and site sessions stay separate in both directions.
    assert client.get("/billing/me", headers=_bearer(token)).status_code == 401
    site_token = billing.login_user(ADMIN_EMAIL, PASSWORD)["token"]
    assert client.get("/billing/admin/overview", headers=_bearer(site_token)).status_code == 401

    revoked = client.post(f"/billing/admin/users/{user_id}/revoke-sessions", headers=_bearer(token))
    assert revoked.status_code == 200
    with state["engine"].connect() as connection:
        actors = connection.execute(select(rollout.ADMIN_ACCOUNT_EVENTS.c.actor)).scalars().all()
    assert actors == [ADMIN_EMAIL]
    # Revoking the account's sessions also ends the admin session.
    assert client.get("/billing/admin/overview", headers=_bearer(token)).status_code == 401


def test_unlisted_unverified_or_wrong_password_never_receive_a_code(state, monkeypatch):
    _account()
    _account("other@example.com")
    _account("pending@example.com", verified=False)
    monkeypatch.setattr(config, "ADMIN_EMAILS", {ADMIN_EMAIL, "pending@example.com"})

    unlisted = _start(state, email="other@example.com")
    wrong_password = _start(state, password="Wrong-admin-password3")
    unknown = _start(state, email="nobody@example.com")
    unverified = _start(state, email="pending@example.com")
    for response in (unlisted, wrong_password, unknown, unverified):
        assert response.status_code == 401
        assert response.json()["detail"]["code"] == "LS-ADMIN-AUTH-02"
    assert "doğrulanmış" in unverified.json()["detail"]["message"]
    assert state["mail"] == []

    monkeypatch.setattr(config, "ADMIN_EMAILS", set())
    disabled = _start(state)
    assert disabled.status_code == 503
    assert state["mail"] == []


def test_code_is_bound_to_its_challenge_single_use_and_expires(state):
    _account()
    started = _start(state)
    challenge, code = started.json()["challenge"], _code(state)

    other_challenge = _verify(state, "x" * 43, code)
    assert other_challenge.status_code == 400
    assert other_challenge.json()["detail"]["code"] == "LS-ADMIN-AUTH-04"
    assert _verify(state, challenge, code).status_code == 200
    reused = _verify(state, challenge, code)
    assert reused.status_code == 400
    assert reused.json()["detail"]["code"] == "LS-ADMIN-AUTH-03"

    started = _start(state)
    state["clock"]["now"] += timedelta(minutes=10, seconds=1)
    expired = _verify(state, started.json()["challenge"], _code(state))
    assert expired.status_code == 400
    assert expired.json()["detail"]["code"] == "LS-ADMIN-AUTH-03"


def test_five_wrong_codes_burn_the_challenge(state):
    _account()
    started = _start(state)
    challenge, code = started.json()["challenge"], _code(state)
    for remaining in (4, 3, 2, 1):
        response = _verify(state, challenge, _wrong(code))
        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "LS-ADMIN-AUTH-04"
        assert f"Kalan deneme: {remaining}" in response.json()["detail"]["message"]
    fifth = _verify(state, challenge, _wrong(code))
    assert fifth.status_code == 400
    assert fifth.json()["detail"]["code"] == "LS-ADMIN-AUTH-03"
    assert _verify(state, challenge, code).json()["detail"]["code"] == "LS-ADMIN-AUTH-03"


def test_admin_session_expires_after_twelve_hours(state):
    _account()
    token = _sign_in(state)
    state["clock"]["now"] += timedelta(hours=11, minutes=59)
    assert state["client"].get("/billing/admin/overview", headers=_bearer(token)).status_code == 200
    state["clock"]["now"] += timedelta(minutes=1, seconds=1)
    assert state["client"].get("/billing/admin/overview", headers=_bearer(token)).status_code == 401


def test_admin_password_change_ends_the_admin_session(state):
    _account()
    token = _sign_in(state)
    client = state["client"]
    change = "/billing/admin/session/change-password"

    wrong = client.post(change, headers=_bearer(token),
                        json={"current_password": "Wrong-admin-password3", "new_password": NEW_PASSWORD})
    weak = client.post(change, headers=_bearer(token),
                       json={"current_password": PASSWORD, "new_password": "short"})
    assert wrong.status_code == 400 and weak.status_code == 400
    assert client.get("/billing/admin/overview", headers=_bearer(token)).status_code == 200
    emergency = client.post(change, headers=_bearer(EMERGENCY_KEY),
                            json={"current_password": PASSWORD, "new_password": NEW_PASSWORD})
    assert emergency.status_code == 400
    assert emergency.json()["detail"]["code"] == "LS-ADMIN-AUTH-05"

    changed = client.post(change, headers=_bearer(token),
                          json={"current_password": PASSWORD, "new_password": NEW_PASSWORD})
    assert changed.status_code == 200
    assert "token" not in changed.json()
    assert client.get("/billing/admin/overview", headers=_bearer(token)).status_code == 401
    assert _start(state).status_code == 401
    assert _start(state, password=NEW_PASSWORD).status_code == 200


def test_site_password_change_also_ends_the_admin_session(state):
    _account()
    token = _sign_in(state)
    site_token = billing.login_user(ADMIN_EMAIL, PASSWORD)["token"]
    changed = state["client"].post(
        "/billing/me/change-password",
        headers=_bearer(site_token),
        json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
    )
    assert changed.status_code == 200
    assert state["client"].get("/billing/admin/overview", headers=_bearer(token)).status_code == 401


def test_sign_out_ends_the_admin_session(state):
    _account()
    token = _sign_in(state)
    client = state["client"]
    assert client.post("/billing/admin/session/logout", headers=_bearer(token)).status_code == 200
    assert client.get("/billing/admin/overview", headers=_bearer(token)).status_code == 401
    assert client.post("/billing/admin/session/logout", headers=_bearer(token)).status_code == 401


def test_emergency_key_still_works_and_other_requests_are_rejected(state, monkeypatch):
    user_id = _account()
    client = state["client"]
    for path in ("/billing/admin/overview", "/billing/admin/credit-events"):
        assert client.get(path, headers=_bearer(EMERGENCY_KEY)).status_code == 200
        assert client.get(path).status_code == 401
        assert client.get(path, headers=_bearer("not-the-key")).status_code == 401
        assert client.get(path, headers={"Authorization": f"Basic {EMERGENCY_KEY}"}).status_code == 401
    assert client.post(f"/billing/admin/users/{user_id}/revoke-sessions",
                       headers=_bearer(EMERGENCY_KEY)).status_code == 200
    with state["engine"].connect() as connection:
        actors = connection.execute(select(rollout.ADMIN_ACCOUNT_EVENTS.c.actor)).scalars().all()
    assert actors == ["admin_token"]

    token = _sign_in(state)
    monkeypatch.setattr(config, "ADMIN_ADMIN", "")
    assert client.get("/billing/admin/overview", headers=_bearer(token)).status_code == 200
    assert client.get("/billing/admin/overview", headers=_bearer(EMERGENCY_KEY)).status_code == 401
    # Removing the email from the list revokes its existing admin session.
    monkeypatch.setattr(config, "ADMIN_EMAILS", {"someone-else@example.com"})
    assert client.get("/billing/admin/overview", headers=_bearer(token)).status_code == 401
    monkeypatch.setattr(config, "ADMIN_EMAILS", set())
    assert client.get("/billing/admin/overview", headers=_bearer(token)).status_code == 503


def test_admin_sign_in_is_rate_limited_per_email_and_per_ip(state):
    _account()
    for index in range(5):
        assert _start(state, password="Wrong-admin-password3", ip=f"198.51.100.{index + 1}").status_code == 401
    limited = _start(state, ip="198.51.100.99")
    assert limited.status_code == 429
    assert limited.json()["detail"]["code"] == "LS-SEC-01"
    assert state["mail"] == []

    for index in range(10):
        assert _start(state, email=f"guess-{index}@example.com", ip="203.0.113.9").status_code == 401
    assert _start(state, email="guess-last@example.com", ip="203.0.113.9").status_code == 429
