from fastapi.testclient import TestClient
import uuid

import lecturesift.app as app_module
import lecturesift.billing_service as billing_service_module
from lecturesift import config
from lecturesift.app import app
from lecturesift.billing_service import (
    approve_manual_order,
    create_manual_order,
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
    assert {"free", "test", "credit", "lite", "plus", "pro", "max", "business"} <= plans.keys()
    assert plans["free"]["export_enabled"] is False
    assert plans["credit"]["kind"] == "one_time"
    assert plans["test"]["kind"] == "one_time"
    assert plans["test"]["minutes"] == 1
    assert plans["test"]["display_price"] == {"currency": "TRY", "amount_minor": 100}
    assert plans["test"]["manual_price"] is None
    assert plans["plus"]["featured"] is True
    assert plans["pro"]["name_key"] == "billing.plan.pro.name"
    assert plans["plus"]["entitlements"]["quiz_questions"] == 30
    assert plans["plus"]["entitlements"]["flashcards"] == 60
    assert plans["plus"]["entitlements"]["export_formats"] == ["pdf", "docx", "txt"]
    assert plans["free"]["entitlements"]["ad_free"] is False
    assert plans["free"]["entitlements"]["rewarded_minutes_eligible"] is True
    assert plans["free"]["entitlements"]["download_enabled"] is False
    assert plans["credit"]["entitlements"]["download_enabled"] is True
    assert plans["plus"]["entitlements"]["ad_free"] is True
    assert plans["plus"]["entitlements"]["rewarded_minutes_eligible"] is False

    usd = TestClient(app).get("/billing/plans?currency=USD").json()
    usd_plans = {plan["code"]: plan for plan in usd["plans"]}
    assert usd["selected_currency"] == "USD"
    assert usd_plans["test"]["display_price"] is None
    assert usd_plans["plus"]["display_price"] == {"currency": "USD", "amount_minor": 1800}

    jpy = TestClient(app).get("/billing/plans?currency=JPY").json()
    jpy_plans = {plan["code"]: plan for plan in jpy["plans"]}
    assert jpy["selected_currency"] == "JPY"
    assert jpy_plans["plus"]["display_price"] == {"currency": "JPY", "amount_minor": 2800}
    assert {"CAD", "AUD", "INR", "BRL", "AED", "SGD"} <= set(jpy["supported_currencies"])


def test_billing_providers_distinguish_ready_state():
    response = TestClient(app).get("/billing/providers")
    assert response.status_code == 200
    providers = {provider["code"]: provider for provider in response.json()["providers"]}
    assert providers["paytr"]["status"] == "pending_credentials"
    assert providers["iyzico"]["status"] == "pending_credentials"
    assert providers["iyzico"]["checkout"] == "hosted_redirect"
    assert providers["iyzico"]["webhook_signature"] == {
        "required": True,
        "version": "v3",
        "header": "X-IYZ-SIGNATURE-V3",
        "algorithm": "HMAC-SHA256",
    }
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


def test_same_six_digit_verification_code_can_be_issued_to_different_users(monkeypatch):
    monkeypatch.setattr(billing_service_module.secrets, "randbelow", lambda _: 123456)
    first = register_user(
        f"same-code-a-{uuid.uuid4()}@example.com",
        "Strong-test-password1",
        "Ada",
        "Lovelace",
    )
    second = register_user(
        f"same-code-b-{uuid.uuid4()}@example.com",
        "Strong-test-password1",
        "Grace",
        "Hopper",
    )
    assert first["verification_code"] == second["verification_code"] == "123456"
    assert verify_email_code(first["user"]["email"], "123456")["user"]["email_verified"] is True
    assert verify_email_code(second["user"]["email"], "123456")["user"]["email_verified"] is True


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


def test_verification_email_code_fits_narrow_mobile_clients(monkeypatch):
    sent = {}
    monkeypatch.setattr(
        app_module,
        "send_transactional_email",
        lambda email, subject, html, text: sent.update(
            email=email, subject=subject, html=html, text=text
        ),
    )

    app_module._send_verification_email("learner@example.com", "safe-token", "123456")

    html = sent["html"]
    assert "table-layout:fixed" in html
    assert "max-width:276px" in html
    assert html.count("width:16.66%") == 6
    assert "letter-spacing" not in html
    assert all(f">{digit}</td>" in html for digit in "123456")
    assert "safe-token" in html


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


def test_account_profile_and_password_can_be_updated():
    client = TestClient(app)
    email, token = _new_account(client)
    profile = client.patch(
        "/billing/me/profile",
        headers={"Authorization": f"Bearer {token}"},
        json={"first_name": "Ada", "last_name": "Lovelace", "phone": "+905551112233"},
    )
    assert profile.status_code == 200
    assert profile.json()["account"]["user"]["name"] == "Ada Lovelace"
    assert profile.json()["account"]["user"]["phone"] == "+905551112233"

    changed = client.post(
        "/billing/me/change-password",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "current_password": "Strong-test-password1",
            "new_password": "Updated-strong-password2",
        },
    )
    assert changed.status_code == 200
    new_token = changed.json()["token"]
    assert new_token and new_token != token
    assert client.get("/billing/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401
    assert client.get("/billing/me", headers={"Authorization": f"Bearer {new_token}"}).status_code == 200
    assert client.post(
        "/billing/login", json={"email": email, "password": "Updated-strong-password2"}
    ).status_code == 200


def test_manual_transfer_order_and_admin_approval(monkeypatch):
    client = TestClient(app)
    _, token = _new_account(client)
    monkeypatch.setattr(config, "BILLING_BANK_IBAN", "TR000000000000000000000000")
    monkeypatch.setattr(config, "BILLING_BANK_ACCOUNT_HOLDER", "LectureSift Test")
    monkeypatch.setattr(config, "BILLING_SUPPORT_EMAIL", "billing@example.com")
    monkeypatch.setattr(config, "LEGAL_OPERATOR_NAME", "LectureSift Test")
    monkeypatch.setattr(config, "LEGAL_OPERATOR_ADDRESS", "Test Address 1")
    monkeypatch.setattr(config, "LEGAL_OPERATOR_COUNTRY", "TR")
    monkeypatch.setattr(config, "LEGAL_OPERATOR_PHONE", "+905551112233")
    monkeypatch.setattr(config, "LEGAL_OPERATOR_EMAIL", "billing@example.com")
    monkeypatch.setattr(config, "ADMIN_ADMIN", "test-admin-token")
    monkeypatch.setattr(config, "INSTAGRAM_ADMIN_TOKEN", "instagram-only-token")

    instagram_token_attempt = client.get(
        "/billing/admin/overview",
        headers={"Authorization": "Bearer instagram-only-token"},
    )
    assert instagram_token_attempt.status_code == 401

    response = client.post(
        "/billing/manual-transfer/orders",
        json={"plan_code": "plus", "interval": "monthly", "terms_accepted": True, "early_performance_requested": True},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    order = response.json()["order"]
    assert order["order_number"] == order["reference"]
    assert order["reference"].startswith("LS-20")
    assert order["amount_minor"] == 69900
    assert order["bank"]["iban"].startswith("TR")
    assert order["bank"]["account_holder"] == "LectureSift Test"

    public_details = client.get("/billing/manual-transfer")
    assert public_details.status_code == 200
    assert public_details.json()["bank"]["account_holder"] == "LectureSift Test"

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

    overview = client.get(
        "/billing/admin/overview",
        headers={"Authorization": "Bearer test-admin-token"},
    )
    assert overview.status_code == 200
    assert overview.json()["counts"]["users"] >= 1
    assert overview.json()["counts"]["users_24h"] >= 1
    assert overview.json()["counts"]["users_7d"] >= 1
    assert overview.json()["counts"]["paid_orders"] >= 1
    assert overview.json()["verification_rate"] >= 0
    assert overview.json()["revenue_by_currency"]["TRY"] >= order["amount_minor"]
    assert any(item["order_number"] == order["order_number"] for item in overview.json()["orders"])

    cancelled = client.post(
        "/billing/me/subscription/cancel",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert cancelled.status_code == 200
    cancelled_account = cancelled.json()["account"]
    assert cancelled_account["plan"]["code"] == "plus"
    assert cancelled_account["subscription"]["status"] == "cancel_at_end"
    assert cancelled_account["subscription"]["cancel_at_period_end"] is True
    assert cancelled_account["remaining_minutes"] > 0

    repeated_cancel = client.post(
        "/billing/me/subscription/cancel",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert repeated_cancel.status_code == 200
    assert repeated_cancel.json()["account"]["plan"]["code"] == "plus"


def test_payment_orders_fail_closed_without_identity_or_explicit_consent(monkeypatch):
    client = TestClient(app)
    _, token = _new_account(client)
    headers = {"Authorization": f"Bearer {token}"}
    monkeypatch.setattr(config, "BILLING_BANK_IBAN", "TR000000000000000000000000")
    monkeypatch.setattr(config, "BILLING_BANK_ACCOUNT_HOLDER", "LectureSift Test")
    monkeypatch.setattr(config, "BILLING_SUPPORT_EMAIL", "billing@example.com")
    monkeypatch.setattr(config, "LEGAL_OPERATOR_NAME", "")
    missing_identity = client.post(
        "/billing/manual-transfer/orders",
        headers=headers,
        json={"plan_code": "plus", "interval": "monthly", "terms_accepted": True, "early_performance_requested": True},
    )
    assert missing_identity.status_code == 503

    monkeypatch.setattr(config, "LEGAL_OPERATOR_NAME", "LectureSift Test")
    monkeypatch.setattr(config, "LEGAL_OPERATOR_ADDRESS", "Test Address 1")
    monkeypatch.setattr(config, "LEGAL_OPERATOR_COUNTRY", "TR")
    monkeypatch.setattr(config, "LEGAL_OPERATOR_PHONE", "+905551112233")
    monkeypatch.setattr(config, "LEGAL_OPERATOR_EMAIL", "billing@example.com")
    missing_consent = client.post(
        "/billing/manual-transfer/orders",
        headers=headers,
        json={"plan_code": "plus", "interval": "monthly"},
    )
    assert missing_consent.status_code == 400

def test_job_creation_requires_account():
    response = TestClient(app).post("/jobs", files={"file": ("notes.txt", b"not a video", "text/plain")})
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "LS-BILL-01"


def test_free_results_are_preview_only_until_a_paid_credit_purchase(tmp_path, monkeypatch):
    client = TestClient(app)
    _, token = _new_account(client)
    headers = {"Authorization": f"Bearer {token}"}
    user_id = client.get("/billing/me", headers=headers).json()["account"]["user"]["id"]
    job_id = f"preview-{uuid.uuid4()}"
    package = tmp_path / "package"
    package.mkdir()
    (package / "notes.pdf").write_bytes(b"pdf-preview")
    (tmp_path / "result.json").write_text(
        '{"title":"Preview","artifacts":[{"file":"notes.pdf","format":"pdf","label":"Notes","size_bytes":11}]}',
        encoding="utf-8",
    )
    archive = tmp_path / "LectureSift_Paketi.zip"
    archive.write_bytes(b"zip-preview")
    JOBS.create(job_id, tmp_path, {"billing_user_id": user_id, "download_entitled": False})
    JOBS.update(job_id, status="done", result_path=str(archive))

    result = client.get(f"/jobs/{job_id}/result", headers=headers)
    assert result.status_code == 200
    assert result.json()["download_enabled"] is False
    assert client.get(f"/jobs/{job_id}/artifact/notes.pdf", headers=headers).status_code == 402
    assert client.get(f"/jobs/{job_id}/download", headers=headers).status_code == 402

    monkeypatch.setattr(config, "BILLING_BANK_IBAN", "TR000000000000000000000000")
    monkeypatch.setattr(config, "BILLING_BANK_ACCOUNT_HOLDER", "LectureSift Test")
    monkeypatch.setattr(config, "BILLING_SUPPORT_EMAIL", "billing@example.com")
    monkeypatch.setattr(config, "LEGAL_OPERATOR_NAME", "LectureSift Test")
    monkeypatch.setattr(config, "LEGAL_OPERATOR_ADDRESS", "Test Address 1")
    monkeypatch.setattr(config, "LEGAL_OPERATOR_COUNTRY", "TR")
    monkeypatch.setattr(config, "LEGAL_OPERATOR_PHONE", "+905551112233")
    monkeypatch.setattr(config, "LEGAL_OPERATOR_EMAIL", "billing@example.com")
    order = create_manual_order(user_id, "credit", "one_time")
    approve_manual_order(order["reference"])

    unlocked = client.get(f"/jobs/{job_id}/result", headers=headers)
    assert unlocked.json()["download_enabled"] is True
    assert client.get(f"/jobs/{job_id}/artifact/notes.pdf", headers=headers).status_code == 200
    assert client.get(f"/jobs/{job_id}/download", headers=headers).status_code == 200


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


def test_lesson_question_is_scoped_to_completed_job_owner(tmp_path, monkeypatch):
    client = TestClient(app)
    _, owner_token = _new_account(client)
    _, other_token = _new_account(client)
    owner_headers = {"Authorization": f"Bearer {owner_token}"}
    owner = client.get("/billing/me", headers=owner_headers).json()["account"]["user"]
    job_id = f"ask-{uuid.uuid4()}"
    (tmp_path / "result.json").write_text(
        '{"options":{"output_language":"tr"},"summary":"Test"}',
        encoding="utf-8",
    )
    JOBS.create(job_id, tmp_path, {"billing_user_id": owner["id"], "output_language": "tr"})
    JOBS.update(job_id, status="done")
    monkeypatch.setattr(
        app_module,
        "answer_lesson_question",
        lambda result, question, language: {
            "answer": "Yalnızca ders içeriğinden yanıt.",
            "citations": [{"timestamp": "00:01:00", "excerpt": "Dayanak"}],
            "insufficient": False,
        },
    )

    response = client.post(
        f"/jobs/{job_id}/ask",
        headers=owner_headers,
        json={"question": "Bu konu nedir?"},
    )
    assert response.status_code == 200
    assert response.json()["citations"][0]["timestamp"] == "00:01:00"
    assert client.post(
        f"/jobs/{job_id}/ask",
        headers={"Authorization": f"Bearer {other_token}"},
        json={"question": "Bu konu nedir?"},
    ).status_code == 404
