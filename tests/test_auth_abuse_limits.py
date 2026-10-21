"""IP-wide and per-e-mail abuse limits on the public account endpoints.

Account work is stubbed and the rate limiter is a fresh process-local one with
a frozen clock, so only the limits themselves are exercised.
"""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import lecturesift.app as app_module
from lecturesift import security
from lecturesift.app import AUTH_ABUSE_LIMITS, app
from lecturesift.billing_service import BillingAuthenticationError, BillingError

PASSWORD = "Strong-limit-password1"


def _refuse_registration(*_args, **_kwargs):
    raise BillingError("Bu e-posta adresi zaten kayıtlı.")


def _refuse_credentials(*_args, **_kwargs):
    raise BillingAuthenticationError("E-posta veya şifre hatalı.")


# action: (path, payload for an address, IP-wide scope that action checks first)
ENDPOINTS = {
    "register": ("/billing/register", lambda email: {"email": email, "password": PASSWORD}, "register-ip"),
    "login": ("/billing/login", lambda email: {"email": email, "password": PASSWORD}, "login-ip"),
    "resend-verification": ("/billing/resend-verification", lambda email: {"email": email}, "resend-verification-ip"),
    "forgot-password": ("/billing/forgot-password", lambda email: {"email": email}, "forgot-password-ip"),
    "verify-code": ("/billing/verify-email-code", lambda email: {"email": email, "code": "000000"}, "verify-code-ip"),
}


@pytest.fixture
def client(monkeypatch):
    limiter = security.RateLimiter()
    limiter._redis = None
    monkeypatch.setattr(app_module, "RATE_LIMITER", limiter)
    monkeypatch.setattr(security, "time", SimpleNamespace(time=lambda: 1_800_000_000.0))
    monkeypatch.setattr(app_module, "email_delivery_configured", lambda: True)
    monkeypatch.setattr(app_module, "register_user", _refuse_registration)
    monkeypatch.setattr(app_module, "login_user", _refuse_credentials)
    monkeypatch.setattr(app_module, "verify_email_code", _refuse_credentials)
    monkeypatch.setattr(app_module, "create_verification_token", lambda _email: None)
    monkeypatch.setattr(app_module, "create_password_reset_token", lambda _email: None)
    return TestClient(app)


def _post(client, action, email, ip):
    path, payload, _scope = ENDPOINTS[action]
    return client.post(path, json=payload(email), headers={"X-Forwarded-For": ip})


def _limited(response) -> bool:
    if response.status_code != 429:
        return False
    assert response.json()["detail"]["code"] == "LS-SEC-01"
    assert int(response.headers["Retry-After"]) >= 1
    return True


@pytest.mark.parametrize("action", sorted(ENDPOINTS))
def test_one_public_ip_cannot_spray_many_addresses(client, monkeypatch, action):
    scope = ENDPOINTS[action][2]
    monkeypatch.setitem(AUTH_ABUSE_LIMITS, scope, (3, 60 * 60))
    for number in range(3):
        assert not _limited(_post(client, action, f"student{number}@example.com", "8.8.8.8"))
    assert _limited(_post(client, action, "student3@example.com", "8.8.8.8"))
    # Another address, and the same student from another network, are unaffected.
    assert not _limited(_post(client, action, "student3@example.com", "1.1.1.1"))


@pytest.mark.parametrize("action", sorted(ENDPOINTS))
def test_one_address_is_limited_from_every_ip(client, monkeypatch, action):
    monkeypatch.setitem(AUTH_ABUSE_LIMITS, f"{action}-email", (2, 60 * 60))
    assert not _limited(_post(client, action, "Target@Example.com", "8.8.4.1"))
    assert not _limited(_post(client, action, "target@example.com", "8.8.4.2"))
    assert _limited(_post(client, action, " TARGET@example.com ", "8.8.4.3"))
    assert not _limited(_post(client, action, "other@example.com", "8.8.4.3"))


def test_private_or_unknown_client_addresses_skip_only_the_ip_wide_limit(client, monkeypatch):
    # A gateway or proxy address must not become one bucket for every user.
    monkeypatch.setitem(AUTH_ABUSE_LIMITS, "login-ip", (1, 15 * 60))
    monkeypatch.setitem(AUTH_ABUSE_LIMITS, "login-email", (2, 15 * 60))
    for number in range(4):
        assert not _limited(_post(client, "login", f"student{number}@example.com", "172.18.0.1"))
    response = client.post("/billing/login", json={"email": "student9@example.com", "password": PASSWORD})
    assert not _limited(response)
    # The per-e-mail limit still applies.
    assert not _limited(_post(client, "login", "student0@example.com", "172.18.0.1"))
    assert _limited(_post(client, "login", "student0@example.com", "10.0.0.7"))


def test_ipv6_clients_are_limited_by_their_64_prefix(client, monkeypatch):
    monkeypatch.setitem(AUTH_ABUSE_LIMITS, "login-ip", (2, 15 * 60))
    assert not _limited(_post(client, "login", "a@example.com", "2001:4860:4860::8888"))
    assert not _limited(_post(client, "login", "b@example.com", "2001:4860:4860::1:2"))
    assert _limited(_post(client, "login", "c@example.com", "2001:4860:4860::99"))
    assert not _limited(_post(client, "login", "c@example.com", "2001:4860:4861::99"))


def test_verification_code_guesses_are_capped_per_address_per_day(client):
    limit, window = AUTH_ABUSE_LIMITS["verify-code-email"]
    assert window == 24 * 60 * 60
    assert app_module.VERIFY_CODE_EMAIL_DAILY_LIMIT == limit <= 20
    for attempt in range(limit):
        response = _post(client, "verify-code", "student@example.com", f"8.8.{attempt // 200}.{attempt % 200 + 1}")
        assert response.status_code == 400, response.text
    assert _limited(_post(client, "verify-code", "student@example.com", "9.9.9.9"))


def test_ip_wide_limits_leave_room_for_a_shared_campus_address():
    def per_hour(scope):
        limit, window = AUTH_ABUSE_LIMITS[scope]
        return limit * 3600 / window

    for action in ENDPOINTS:
        ip_scope = ENDPOINTS[action][2]
        # A classroom behind one NAT can sign up, log in and verify together.
        assert per_hour(ip_scope) >= 20, ip_scope
        assert per_hour(ip_scope) >= 3 * per_hour(f"{action}-email"), ip_scope
    assert AUTH_ABUSE_LIMITS["register-ip-day"][0] >= 4 * AUTH_ABUSE_LIMITS["register-ip"][0]
