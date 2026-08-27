import uuid

from fastapi.testclient import TestClient

import lecturesift.email_auth as email_auth
from lecturesift import config
from main import app


def _credentials() -> tuple[str, str]:
    return f"verify-{uuid.uuid4()}@example.com", "strong-test-password"


def _enable_verification(monkeypatch) -> None:
    monkeypatch.setattr(config, "EMAIL_VERIFICATION_REQUIRED", True)
    monkeypatch.setattr(config, "RESEND_API_KEY", "re_test_key")
    monkeypatch.setattr(config, "RESEND_FROM_EMAIL", "LectureSift <no-reply@mail.lecturesift.com>")
    monkeypatch.setattr(config, "EMAIL_VERIFICATION_SECRET", "test-verification-secret")
    monkeypatch.setattr(config, "EMAIL_VERIFICATION_TTL_SECONDS", 600)
    monkeypatch.setattr(config, "EMAIL_VERIFICATION_RESEND_SECONDS", 60)
    monkeypatch.setattr(config, "EMAIL_VERIFICATION_MAX_SENDS_PER_HOUR", 5)
    monkeypatch.setattr(config, "EMAIL_VERIFICATION_MAX_ATTEMPTS", 5)


def test_registration_requires_resend_code_and_verifies_account(monkeypatch):
    _enable_verification(monkeypatch)
    sent: dict[str, str] = {}

    def fake_send(recipient: str, code: str, language: str, idempotency_key: str) -> str:
        sent.update(recipient=recipient, code=code, language=language, key=idempotency_key)
        return "email_test_id"

    monkeypatch.setattr(email_auth, "_send_verification_email", fake_send)
    client = TestClient(app)
    email, password = _credentials()

    registration = client.post(
        "/billing/register",
        json={"email": email, "password": password, "language": "tr"},
    )
    assert registration.status_code == 200
    body = registration.json()
    assert body["verification_required"] is True
    assert body["email"] == email
    assert "token" not in body
    assert sent["recipient"] == email
    assert len(sent["code"]) == 6 and sent["code"].isdigit()

    login_before_verification = client.post(
        "/billing/login", json={"email": email, "password": password}
    )
    assert login_before_verification.status_code == 403
    assert login_before_verification.json()["detail"]["code"] == "LS-AUTH-06"

    wrong_code = "000000" if sent["code"] != "000000" else "000001"
    wrong = client.post("/billing/verify-email", json={"email": email, "code": wrong_code})
    assert wrong.status_code == 400
    assert wrong.json()["detail"]["code"] == "LS-AUTH-08"

    verified = client.post(
        "/billing/verify-email", json={"email": email, "code": sent["code"]}
    )
    assert verified.status_code == 200
    verified_body = verified.json()
    assert verified_body["account"]["user"]["email"] == email
    token = verified_body["token"]

    account = client.get("/billing/me", headers={"Authorization": f"Bearer {token}"})
    assert account.status_code == 200
    assert account.json()["account"]["plan"]["code"] == "free"

    reused = client.post(
        "/billing/verify-email", json={"email": email, "code": sent["code"]}
    )
    assert reused.status_code == 409
    assert reused.json()["detail"]["code"] == "LS-AUTH-11"


def test_resend_is_rate_limited(monkeypatch):
    _enable_verification(monkeypatch)
    sent: list[str] = []
    monkeypatch.setattr(
        email_auth,
        "_send_verification_email",
        lambda recipient, code, language, idempotency_key: sent.append(code) or "email_test_id",
    )
    client = TestClient(app)
    email, password = _credentials()
    assert client.post(
        "/billing/register", json={"email": email, "password": password}
    ).status_code == 200

    response = client.post("/billing/resend-verification", json={"email": email})
    assert response.status_code == 429
    detail = response.json()["detail"]
    assert detail["code"] == "LS-AUTH-09"
    assert detail["retry_after"] >= 1
    assert len(sent) == 1


def test_required_verification_fails_closed_without_resend_configuration(monkeypatch):
    _enable_verification(monkeypatch)
    monkeypatch.setattr(config, "RESEND_API_KEY", "")
    client = TestClient(app)
    email, password = _credentials()

    unavailable = client.post(
        "/billing/register", json={"email": email, "password": password}
    )
    assert unavailable.status_code == 503
    assert unavailable.json()["detail"]["code"] == "LS-AUTH-00"

    sent: dict[str, str] = {}
    monkeypatch.setattr(config, "RESEND_API_KEY", "re_test_key")
    monkeypatch.setattr(
        email_auth,
        "_send_verification_email",
        lambda recipient, code, language, idempotency_key: sent.setdefault("code", code) or "email_test_id",
    )
    retry = client.post("/billing/register", json={"email": email, "password": password})
    assert retry.status_code == 200
    assert sent["code"]


def test_existing_accounts_are_not_locked_when_verification_is_enabled(monkeypatch):
    client = TestClient(app)
    email, password = _credentials()
    monkeypatch.setattr(config, "EMAIL_VERIFICATION_REQUIRED", False)
    created = client.post("/billing/register", json={"email": email, "password": password})
    assert created.status_code == 200
    assert created.json()["token"]

    _enable_verification(monkeypatch)
    logged_in = client.post("/billing/login", json={"email": email, "password": password})
    assert logged_in.status_code == 200
    assert logged_in.json()["account"]["user"]["email"] == email


def test_email_health_exposes_configuration_without_secrets(monkeypatch):
    _enable_verification(monkeypatch)
    response = TestClient(app).get("/billing/email/health")
    assert response.status_code == 200
    body = response.json()
    assert body["provider"] == "resend"
    assert body["configured"] is True
    assert body["verification_required"] is True
    assert "re_test_key" not in response.text
