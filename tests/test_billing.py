from fastapi.testclient import TestClient
import uuid

import lecturesift.app as app_module
from lecturesift import config
from lecturesift.app import app
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


def test_billing_providers_distinguish_ready_state():
    response = TestClient(app).get("/billing/providers")
    assert response.status_code == 200
    providers = {provider["code"]: provider for provider in response.json()["providers"]}
    assert providers["paytr"]["status"] == "pending_credentials"
    assert "global" in providers["paddle"]["regions"]


def _new_account(client: TestClient) -> tuple[str, str]:
    email = f"billing-{uuid.uuid4()}@example.com"
    response = client.post("/billing/register", json={"email": email, "password": "strong-test-password"})
    assert response.status_code == 200
    return email, response.json()["token"]


def test_account_session_starts_on_free_plan():
    client = TestClient(app)
    email, token = _new_account(client)
    response = client.get("/billing/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    account = response.json()["account"]
    assert account["user"]["email"] == email
    assert account["plan"]["code"] == "free"
    assert account["remaining_minutes"] == 60


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
