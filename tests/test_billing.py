from fastapi.testclient import TestClient

from lecturesift.app import app


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
