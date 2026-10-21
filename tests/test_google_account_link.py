"""Linking a Google login to the signed-in account from the account page.

Synthetic data only: no Google or email request leaves the test.
"""
import time
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.pool import StaticPool

import lecturesift.app as app_module
from lecturesift import billing_service as billing
from lecturesift import config, google_signin, rollout_routes, security
from lecturesift import rollout_service as rollout
from lecturesift import workspace_state as workspace
from main import app

PASSWORD = "Strong-link-password1"
CLIENT_ID = "1234567890-synthetic.apps.googleusercontent.com"
LINKED_SUBJECT = "linked-google-subject"
client = TestClient(app)


@pytest.fixture
def notices(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    billing.METADATA.create_all(engine)
    workspace.METADATA.create_all(engine)
    monkeypatch.setattr(billing, "ENGINE", engine)
    monkeypatch.setattr(rollout, "ENGINE", engine)
    monkeypatch.setattr(billing, "_INITIALIZED", True)
    monkeypatch.setattr(config, "GOOGLE_CLIENT_ID", CLIENT_ID)
    limiter = security.RateLimiter()
    limiter._redis = None
    monkeypatch.setattr(app_module, "RATE_LIMITER", limiter)
    monkeypatch.setattr(rollout_routes, "RATE_LIMITER", limiter)
    sent = []
    monkeypatch.setattr(app_module, "email_delivery_configured", lambda: True)
    monkeypatch.setattr(app_module, "send_transactional_email", lambda to, subject, html, text: sent.append((to, subject)))
    yield sent
    engine.dispose()


def use_google(monkeypatch, *, email: str, sub: str = LINKED_SUBJECT, **overrides) -> None:
    claims = {
        "aud": CLIENT_ID, "iss": "https://accounts.google.com", "exp": str(int(time.time()) + 600),
        "email": email, "email_verified": "true", "sub": sub,
    }
    claims.update(overrides)
    monkeypatch.setattr(google_signin, "_fetch_tokeninfo", lambda credential: dict(claims))


def verified_account() -> tuple[str, str, str]:
    email = f"account-{uuid.uuid4().hex}@example.com"
    created = billing.register_user(email, PASSWORD)
    verified = billing.verify_email(created["verification_token"])
    return email, verified["token"], verified["user"]["id"]


def link(token: str, password: str = PASSWORD, **headers):
    return client.post(
        "/billing/me/google/link",
        json={"credential": "header.payload.signature", "current_password": password},
        headers={"Authorization": f"Bearer {token}", **headers},
    )


def unlink(token: str, password: str = PASSWORD):
    return client.post(
        "/billing/me/google/unlink",
        json={"current_password": password},
        headers={"Authorization": f"Bearer {token}"},
    )


def google_links(user_id: str) -> list:
    with billing.ENGINE.connect() as connection:
        return connection.execute(select(billing.AUTH_TOKENS).where(
            billing.AUTH_TOKENS.c.user_id == user_id,
            billing.AUTH_TOKENS.c.purpose == billing.GOOGLE_LINK_PURPOSE,
        )).all()


def linked_flag(token: str) -> bool:
    response = client.get("/billing/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200, response.text
    return response.json()["account"]["google_linked"]


LINKED = "LectureSift hesabına Google ile giriş eklendi"
UNLINKED = "LectureSift hesabından Google ile giriş kaldırıldı"
MOVED_AWAY = "LectureSift hesabındaki Google ile giriş başka bir hesaba taşındı"


def test_a_google_login_with_another_address_links_to_the_current_account(notices, monkeypatch):
    email, token, account_id = verified_account()
    assert linked_flag(token) is False
    use_google(monkeypatch, email=f"someone-else-{uuid.uuid4().hex}@gmail.com")

    response = link(token)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["google_link"] == "linked"
    assert body["message"] == app_module.GOOGLE_LINK_MESSAGES["linked"]
    assert body["account"]["google_linked"] is True
    assert [row.token_hash for row in google_links(account_id)] == [billing._google_link_hash(LINKED_SUBJECT)]
    assert linked_flag(token) is True
    assert notices == [(email, LINKED)]

    # Linking the same Google account again changes nothing and sends nothing.
    before = google_links(account_id)
    again = link(token)
    assert again.status_code == 200, again.text
    assert again.json()["google_link"] == "existing"
    assert again.json()["message"] == app_module.GOOGLE_LINK_MESSAGES["existing"]
    assert google_links(account_id) == before
    assert notices == [(email, LINKED)]


def test_a_link_moves_from_another_account_and_both_owners_are_told(notices, monkeypatch):
    # Google sign-in made a separate account for the Google address first.
    google_email = f"owner-{uuid.uuid4().hex}@gmail.com"
    first = billing.sign_in_with_google(google_email, sub=LINKED_SUBJECT)
    other_id = first["user"]["id"]
    assert first["google_link"] == "created" and len(google_links(other_id)) == 1

    email, token, account_id = verified_account()
    use_google(monkeypatch, email=google_email)
    response = link(token)
    assert response.status_code == 200, response.text
    assert response.json()["google_link"] == "moved"
    assert response.json()["message"] == app_module.GOOGLE_LINK_MESSAGES["moved"]
    assert google_links(other_id) == []
    assert [row.token_hash for row in google_links(account_id)] == [billing._google_link_hash(LINKED_SUBJECT)]
    assert sorted(notices) == sorted([(email, LINKED), (google_email, MOVED_AWAY)])

    # Signing in with that Google account now opens the current account,
    # although another account carries the Google address.
    signed_in = client.post("/billing/google", json={"credential": "header.payload.signature"})
    assert signed_in.status_code == 200, signed_in.text
    assert signed_in.json()["user"]["id"] == account_id
    assert signed_in.json()["google_link"] == "existing"


def test_a_stale_link_to_an_unverified_account_is_replaced_without_a_notice_to_it(notices, monkeypatch):
    stale = billing.register_user(f"never-verified-{uuid.uuid4().hex}@example.com", PASSWORD)
    now = billing.utcnow()
    with billing.ENGINE.begin() as connection:
        connection.execute(billing.AUTH_TOKENS.insert().values(
            token_hash=billing._google_link_hash(LINKED_SUBJECT), user_id=stale["user"]["id"],
            purpose=billing.GOOGLE_LINK_PURPOSE, expires_at=now + billing._GOOGLE_LINK_LIFETIME,
            consumed_at=now, created_at=now,
        ))
    email, token, account_id = verified_account()
    use_google(monkeypatch, email=f"student-{uuid.uuid4().hex}@gmail.com")
    response = link(token)
    assert response.status_code == 200, response.text
    assert response.json()["google_link"] == "linked"
    assert google_links(stale["user"]["id"]) == [] and len(google_links(account_id)) == 1
    assert notices == [(email, LINKED)]


@pytest.mark.parametrize("password", ["Wrong-link-password1", ""])
def test_a_wrong_or_empty_password_is_refused(notices, monkeypatch, password):
    _email, token, account_id = verified_account()
    use_google(monkeypatch, email="student@gmail.com")
    response = link(token, password)
    assert response.status_code == 400, response.text
    assert response.json()["detail"] == {"code": "LS-GOOGLE-05", "message": "Mevcut parola hatalı."}
    assert google_links(account_id) == [] and notices == []


def test_a_missing_password_is_refused_before_google_is_asked(notices, monkeypatch):
    _email, token, account_id = verified_account()
    monkeypatch.setattr(google_signin, "_fetch_tokeninfo", lambda credential: pytest.fail("must not contact Google"))
    response = client.post(
        "/billing/me/google/link",
        json={"credential": "header.payload.signature"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 422
    assert client.post("/billing/me/google/unlink", json={}, headers={"Authorization": f"Bearer {token}"}).status_code == 422
    assert google_links(account_id) == []


def test_a_session_is_required(notices, monkeypatch):
    monkeypatch.setattr(google_signin, "_fetch_tokeninfo", lambda credential: pytest.fail("must not contact Google"))
    response = client.post(
        "/billing/me/google/link", json={"credential": "header.payload.signature", "current_password": PASSWORD},
    )
    assert response.status_code == 401
    assert client.post("/billing/me/google/unlink", json={"current_password": PASSWORD}).status_code == 401


@pytest.mark.parametrize("bad", [
    {"email_verified": "false"},
    {"aud": "other-client.apps.googleusercontent.com"},
    {"exp": str(int(time.time()) - 5)},
])
def test_an_unverified_or_foreign_google_token_is_refused(notices, monkeypatch, bad):
    _email, token, account_id = verified_account()
    use_google(monkeypatch, email="student@gmail.com", **bad)
    response = link(token)
    assert response.status_code == 400, response.text
    assert response.json()["detail"]["code"] == "LS-GOOGLE-05"
    assert response.json()["detail"]["message"] == "Google ile giriş doğrulanamadı. Tekrar dene."
    assert google_links(account_id) == [] and notices == []


def test_linking_is_off_without_a_google_client_id(notices, monkeypatch):
    _email, token, _account_id = verified_account()
    monkeypatch.setattr(config, "GOOGLE_CLIENT_ID", "")
    monkeypatch.setattr(google_signin, "_fetch_tokeninfo", lambda credential: pytest.fail("must not contact Google"))
    response = link(token)
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "LS-GOOGLE-00"


def test_unlinking_needs_the_password_and_tells_the_owner(notices, monkeypatch):
    email, token, account_id = verified_account()
    use_google(monkeypatch, email="student@gmail.com")
    assert link(token).status_code == 200
    notices.clear()

    refused = unlink(token, "Wrong-link-password1")
    assert refused.status_code == 400, refused.text
    assert refused.json()["detail"]["code"] == "LS-GOOGLE-05"
    assert len(google_links(account_id)) == 1 and notices == []

    response = unlink(token)
    assert response.status_code == 200, response.text
    assert response.json()["removed"] is True
    assert response.json()["message"] == "Google ile giriş bu hesaptan kaldırıldı."
    assert response.json()["account"]["google_linked"] is False
    assert google_links(account_id) == [] and linked_flag(token) is False
    assert notices == [(email, UNLINKED)]

    again = unlink(token)
    assert again.status_code == 200, again.text
    assert again.json()["removed"] is False
    assert again.json()["message"] == "Bu hesaba bağlı bir Google girişi yok."
    assert notices == [(email, UNLINKED)]


def test_an_account_made_with_google_links_again_after_a_password_reset(notices, monkeypatch):
    google_email = f"made-with-google-{uuid.uuid4().hex}@gmail.com"
    created = billing.sign_in_with_google(google_email, sub=LINKED_SUBJECT)
    account_id, token = created["user"]["id"], created["token"]
    use_google(monkeypatch, email=google_email, sub="second-google-subject")
    # The account's password is random, so its session alone cannot link.
    assert link(token).status_code == 400
    assert unlink(token).status_code == 400
    assert len(google_links(account_id)) == 1

    reset = billing.create_password_reset_token(google_email)
    billing.reset_password(reset["token"], PASSWORD)
    # The emailed reset removed the old link and ended the old session.
    assert google_links(account_id) == []
    token = billing.login_user(google_email, PASSWORD)["token"]
    response = link(token)
    assert response.status_code == 200, response.text
    assert [row.token_hash for row in google_links(account_id)] == [billing._google_link_hash("second-google-subject")]


def test_a_guest_session_cannot_link(notices, monkeypatch):
    guest = client.post("/billing/guest-session", json={"device_id": f"device-{uuid.uuid4()}"})
    assert guest.status_code == 200, guest.text
    use_google(monkeypatch, email="student@gmail.com")
    response = link(guest.json()["token"])
    assert response.status_code == 400, response.text
    assert response.json()["detail"] == {
        "code": "LS-GOOGLE-06", "message": "Google hesabını bağlamak için ücretsiz hesap oluştur.",
    }
    assert google_links(guest.json()["account"]["user"]["id"]) == []


def test_closing_an_account_removes_a_link_made_here(notices, monkeypatch):
    email, token, account_id = verified_account()
    use_google(monkeypatch, email="student@gmail.com")
    assert link(token).status_code == 200
    rollout.close_user_account(account_id, PASSWORD, email)
    assert google_links(account_id) == []


def test_link_attempts_are_limited_per_account_and_per_network(notices, monkeypatch):
    monkeypatch.setattr(security, "time", SimpleNamespace(time=lambda: 1_800_000_000.0))
    monkeypatch.setitem(app_module.AUTH_ABUSE_LIMITS, "google-link-account", (2, 15 * 60))
    use_google(monkeypatch, email="student@gmail.com")
    _email, token, account_id = verified_account()
    # Wrong passwords from two networks use up one account budget.
    assert link(token, "Wrong-link-password1", **{"X-Forwarded-For": "8.8.8.8"}).status_code == 400
    assert unlink(token, "Wrong-link-password1").status_code == 400
    limited = link(token)
    assert limited.status_code == 429
    assert limited.json()["detail"]["code"] == "LS-SEC-01"
    assert int(limited.headers["Retry-After"]) >= 1
    assert unlink(token).status_code == 429
    assert google_links(account_id) == []

    monkeypatch.setitem(app_module.AUTH_ABUSE_LIMITS, "google-link-ip", (2, 15 * 60))
    tokens = [verified_account()[1] for _ in range(3)]
    assert link(tokens[0], **{"X-Forwarded-For": "1.1.1.1"}).status_code == 200
    assert link(tokens[1], "Wrong-link-password1", **{"X-Forwarded-For": "1.1.1.1"}).status_code == 400
    assert link(tokens[2], **{"X-Forwarded-For": "1.1.1.1"}).status_code == 429
    # The same account from another network is unaffected.
    assert link(tokens[2], **{"X-Forwarded-For": "9.9.9.9"}).status_code == 200
