import base64
import hashlib
import hmac
import uuid

from fastapi.testclient import TestClient

from lecturesift import config, payments
from lecturesift.billing_service import register_user, verify_email
from main import app


def _account() -> tuple[str, str]:
    email = f"paytr-{uuid.uuid4()}@example.com"
    created = register_user(
        email,
        "Strong-test-password1",
        "Test",
        "Customer",
        phone="+905551112233",
        country_code="TR",
    )
    verified = verify_email(created["verification_token"])
    return email, verified["token"]


def _configure(monkeypatch) -> None:
    monkeypatch.setattr(config, "PAYTR_MERCHANT_ID", "123456")
    monkeypatch.setattr(config, "PAYTR_MERCHANT_KEY", "merchant-key")
    monkeypatch.setattr(config, "PAYTR_MERCHANT_SALT", "merchant-salt")
    monkeypatch.setattr(config, "PAYTR_TEST_MODE", True)
    monkeypatch.setattr(config, "PAYTR_DEBUG", False)
    monkeypatch.setattr(config, "LEGAL_OPERATOR_NAME", "LectureSift Test")
    monkeypatch.setattr(config, "LEGAL_OPERATOR_ADDRESS", "Test Address 1")
    monkeypatch.setattr(config, "LEGAL_OPERATOR_COUNTRY", "TR")
    monkeypatch.setattr(config, "LEGAL_OPERATOR_PHONE", "+905551112233")
    monkeypatch.setattr(config, "LEGAL_OPERATOR_EMAIL", "billing@example.com")


def _callback_hash(reference: str, status: str, total_amount: str) -> str:
    message = f"{reference}{config.PAYTR_MERCHANT_SALT}{status}{total_amount}"
    digest = hmac.new(
        config.PAYTR_MERCHANT_KEY.encode(), message.encode(), hashlib.sha256
    ).digest()
    return base64.b64encode(digest).decode()


def test_paytr_checkout_and_callback_are_signed_and_idempotent(monkeypatch):
    _configure(monkeypatch)
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"status": "success", "token": "safe-iframe-token"}

    def fake_post(url, *, data, timeout):
        captured.update(url=url, data=data, timeout=timeout)
        return FakeResponse()

    monkeypatch.setattr(payments.httpx, "post", fake_post)
    email, token = _account()
    client = TestClient(app)
    checkout = client.post(
        "/billing/checkout",
        headers={
            "Authorization": f"Bearer {token}",
            "X-Forwarded-For": "203.0.113.24",
        },
        json={
            "plan_code": "plus",
            "interval": "monthly",
            "currency": "TRY",
            "billing_address": "Örnek Mahallesi 1, İstanbul",
            "phone": "+905551112233",
            "language": "tr",
            "terms_accepted": True,
            "early_performance_requested": True,
        },
    )
    assert checkout.status_code == 200
    body = checkout.json()
    reference = body["order"]["reference"]
    assert reference.isalnum() and len(reference) <= 64
    assert body["checkout_url"].endswith("/safe-iframe-token")
    assert body["mode"] == "test"
    assert captured["url"] == payments.PAYTR_TOKEN_URL
    assert captured["data"]["merchant_oid"] == reference
    assert captured["data"]["email"] == email
    assert captured["data"]["payment_amount"] == "44900"
    assert captured["data"]["user_ip"] == "203.0.113.24"
    assert captured["data"]["test_mode"] == "1"
    assert "merchant-key" not in str(captured["data"])
    assert "merchant-salt" not in str(captured["data"])

    total_amount = "46300"
    callback = {
        "merchant_oid": reference,
        "status": "success",
        "total_amount": total_amount,
        "payment_amount": "44900",
        "hash": _callback_hash(reference, "success", total_amount),
    }
    first = client.post("/billing/paytr/callback", data=callback)
    second = client.post("/billing/paytr/callback", data=callback)
    assert first.status_code == 200 and first.text == "OK"
    assert second.status_code == 200 and second.text == "OK"

    account = client.get(
        "/billing/me", headers={"Authorization": f"Bearer {token}"}
    ).json()["account"]
    assert account["plan"]["code"] == "plus"
    assert account["payment_orders"][0]["status"] == "paid"
    assert account["payment_orders"][0]["provider_amount_minor"] == 46300
    export_response = client.get(
        "/billing/me/export", headers={"Authorization": f"Bearer {token}"}
    )
    assert export_response.status_code == 200, export_response.text
    exported = export_response.json()["export"]
    assert exported["payment_consents"][0]["order_reference"] == reference
    assert "ip_hash" not in exported["payment_consents"][0]


def test_paytr_callback_rejects_bad_hash_and_amount(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(
        payments.httpx,
        "post",
        lambda *args, **kwargs: type(
            "FakeResponse",
            (),
            {"raise_for_status": lambda self: None, "json": lambda self: {"status": "success", "token": "token"}},
        )(),
    )
    _, token = _account()
    client = TestClient(app)
    checkout = client.post(
        "/billing/checkout",
        headers={"Authorization": f"Bearer {token}", "X-Forwarded-For": "203.0.113.25"},
        json={
            "plan_code": "credit",
            "interval": "one_time",
            "currency": "TRY",
            "billing_address": "Örnek Mahallesi 2, Ankara",
            "phone": "+905551112233",
            "language": "en",
            "terms_accepted": True,
            "early_performance_requested": True,
        },
    ).json()
    reference = checkout["order"]["reference"]
    bad_hash = client.post(
        "/billing/paytr/callback",
        data={
            "merchant_oid": reference,
            "status": "success",
            "total_amount": "19900",
            "payment_amount": "19900",
            "hash": "invalid",
        },
    )
    assert bad_hash.status_code == 400

    bad_amount = client.post(
        "/billing/paytr/callback",
        data={
            "merchant_oid": reference,
            "status": "success",
            "total_amount": "1",
            "payment_amount": "1",
            "hash": _callback_hash(reference, "success", "1"),
        },
    )
    assert bad_amount.status_code == 400
    account = client.get(
        "/billing/me", headers={"Authorization": f"Bearer {token}"}
    ).json()["account"]
    assert account["credit_minutes"] == 0
