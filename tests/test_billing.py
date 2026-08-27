from fastapi.testclient import TestClient
import uuid

import lecturesift.app as app_module
from lecturesift import config
from lecturesift.app import app
from lecturesift.billing_service import (
    create_password_reset_token,
    login_user,
    register_user,
    reset_password,
    verify_email,
    verify_email_code,
)
from lecturesift.jobs import JOBS


def test_billing_catalog_has_hybrid_plans_and_translation_keys():
    response = TestClient(app).get("/billing/plans")
    assert response.status_code == 200
    catalog = response.json()
    plans = {plan["code"]: plan for plan in catalog["plans"]}
    assert {"free", "credit", "lite", "plus", "pro", "max", "business"} <= plans.keys()
    assert plans["free"]["export_enabled"] is False
    assert plans["credit"]["kind"] == "one_time"
    assert plans["plus"]["featured"] is True
    assert plans["pro"]["name_key"] == "billing.plan.pro.name"
    assert plans["plus"]["entitlements"]["quiz_questions"] == 30
    assert plans["plus"]["entitlements"]["flashcards"] == 60
    assert plans["plus"]["entitlements"]["export_formats"] == ["pdf", "docx", "txt"]

    usd = TestClient(app).get("/billing/plans?currency=USD").json()
    usd_plans = {plan["code"]: plan for plan in usd["plans"]}
    assert usd["selected_currency"] == "USD"
    assert usd_plans["plus"]["display_price"] == {"currency": "USD", "amount_minor": 1800}


def test_billing_providers_distinguish_ready_state():
    response = TestClient(app).get("/billing/providers")
    assert response.status_code == 200
    providers = {provider["code"]: provider for provider in response.json()["providers"]}
    assert providers["paytr"]["status"] == "pending_credentials"
    assert "global" in providers["paddle"]["regions"]


def test_billing_health_reports_local_database_and_fails_closed_on_render(monkeypatch):
    client = TestClient(app)
    response = client.get("/billing/health")
    assert response.status_code == 200
    assert response.json()["database"]["backend"] == "sqlite"
    assert response.json()["database"]["persistent"] is False

    monkeypatch.setenv("RENDER", "true")
    response = client.get("/billing/health")
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "LS-BILL-00"


def _new_account(client: TestClient) -> tuple[str, str]:
    email = f"billing-{uuid.uuid4()}@example.com"
    registration = register_user(email, "Strong-test-password1", "Test", "User", country_code="TR")
    result = verify_email(registration["verification_token"])
    return email, result["token"]


def test_registration_requires_email_verification(monkeypatch):
    client = TestClient(app)
    sent = {}
    monkeypatch.setattr(app_module, "email_delivery_configured", lambda: True)
    monkeypatch.setattr(app_module, "_send_verification_email", lambda email, token, code: sent.update(email=email, token=token, code=code))
    email = f"verify-{uuid.uuid4()}@example.com"
    response = client.post(
        "/billing/register",
        json={
            "email": email,
            "password": "Strong-test-password1",
            "first_name": "Ada",
            "last_name": "Lovelace",
            "phone": "+905551112233",
            "country_code": "TR",
        },
    )
    assert response.status_code == 200
    assert response.json()["verification_required"] is True
    assert "token" not in response.json()
    assert client.post("/billing/login", json={"email": email, "password": "Strong-test-password1"}).status_code == 401

    verified = client.post("/billing/verify-email", json={"token": sent["token"]})
    assert verified.status_code == 200
    assert verified.json()["account"]["user"]["name"] == "Ada Lovelace"
    assert verified.json()["account"]["user"]["email_verified"] is True


def test_registration_can_be_verified_with_email_code(monkeypatch):
    client = TestClient(app)
    sent = {}
    monkeypatch.setattr(app_module, "email_delivery_configured", lambda: True)
    monkeypatch.setattr(app_module, "_send_verification_email", lambda email, token, code: sent.update(email=email, token=token, code=code))
    email = f"code-{uuid.uuid4()}@example.com"
    response = client.post(
        "/billing/register",
        json={
            "email": email,
            "password": "Strong-test-password1",
            "first_name": "Grace",
            "last_name": "Hopper",
            "country_code": "US",
        },
    )
    assert response.status_code == 200
    assert len(sent["code"]) == 6 and sent["code"].isdigit()
    verified = client.post("/billing/verify-email-code", json={"email": email, "code": sent["code"]})
    assert verified.status_code == 200
    assert verified.json()["account"]["user"]["email_verified"] is True
    assert client.post("/billing/verify-email", json={"token": sent["token"]}).status_code == 400


def test_password_reset_invalidates_existing_session():
    client = TestClient(app)
    email, old_token = _new_account(client)
    reset = create_password_reset_token(email)
    assert reset is not None
    reset_password(reset["token"], "Another-strong-password2")
    assert client.get("/billing/me", headers={"Authorization": f"Bearer {old_token}"}).status_code == 401
    assert login_user(email, "Another-strong-password2")["token"]


def test_logout_invalidates_existing_session():
    client = TestClient(app)
    _, token = _new_account(client)
    response = client.post("/billing/logout", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert client.get("/billing/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_account_session_starts_on_free_plan():
    client = TestClient(app)
    email, token = _new_account(client)
    response = client.get("/billing/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    account = response.json()["account"]
    assert account["user"]["email"] == email
    assert account["plan"]["code"] == "free"
    assert account["remaining_minutes"] == 60


def test_account_country_and_language_preferences_are_saved():
    client = TestClient(app)
    _, token = _new_account(client)
    response = client.patch(
        "/billing/me/preferences",
        headers={"Authorization": f"Bearer {token}"},
        json={"country_code": "DE", "preferred_language": "de"},
    )
    assert response.status_code == 200
    user = response.json()["account"]["user"]
    assert user["country_code"] == "DE"
    assert user["preferred_language"] == "de"
    refreshed = client.get("/billing/me", headers={"Authorization": f"Bearer {token}"})
    assert refreshed.json()["account"]["user"]["preferred_language"] == "de"


def test_manual_transfer_order_and_admin_approval(monkeypatch):
    client = TestClient(app)
    _, token = _new_account(client)
    monkeypatch.setattr(config, "BILLING_BANK_IBAN", "TR000000000000000000000000")
    monkeypatch.setattr(config, "BILLING_BANK_ACCOUNT_HOLDER", "LectureSift Test")
    monkeypatch.setattr(config, "BILLING_SUPPORT_EMAIL", "billing@example.com")
    monkeypatch.setattr(app_module, "BILLING_ADMIN_TOKEN", "test-admin-token")

    response = client.post(
        "/billing/manual-transfer/orders",
        json={"plan_code": "plus", "interval": "monthly"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    order = response.json()["order"]
    assert order["amount_minor"] == 69900
    assert order["bank"]["iban"].startswith("TR")

    approval = client.post(
        f"/billing/manual-transfer/orders/{order['reference']}/approve",
        headers={"Authorization": "Bearer test-admin-token"},
    )
    assert approval.status_code == 200
    assert approval.json()["account"]["plan"]["code"] == "plus"
    assert approval.json()["account"]["manual_orders"][0]["status"] == "paid"

    repeated = client.post(
        f"/billing/manual-transfer/orders/{order['reference']}/approve",
        headers={"Authorization": "Bearer test-admin-token"},
    )
    assert repeated.status_code == 200
    assert repeated.json()["account"]["plan"]["code"] == "plus"


def test_job_creation_requires_account():
    response = TestClient(app).post("/jobs", files={"file": ("notes.txt", b"not a video", "text/plain")})
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "LS-BILL-01"


def test_job_status_is_visible_only_to_its_owner(tmp_path):
    client = TestClient(app)
    _, owner_token = _new_account(client)
    _, other_token = _new_account(client)
    owner = client.get("/billing/me", headers={"Authorization": f"Bearer {owner_token}"}).json()["account"]["user"]
    job_id = f"private-{uuid.uuid4()}"
    JOBS.create(job_id, tmp_path, {"billing_user_id": owner["id"], "output_language": "tr"})

    owner_response = client.get(f"/jobs/{job_id}", headers={"Authorization": f"Bearer {owner_token}"})
    assert owner_response.status_code == 200
    assert "billing_user_id" not in owner_response.json()["options"]
    assert client.get(f"/jobs/{job_id}").status_code == 401
    assert client.get(
        f"/jobs/{job_id}", headers={"Authorization": f"Bearer {other_token}"}
    ).status_code == 404
