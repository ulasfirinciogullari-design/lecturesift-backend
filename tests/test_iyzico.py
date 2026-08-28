import base64
import hashlib
import hmac
import json
import uuid

from fastapi.testclient import TestClient

from lecturesift import config, payments
from lecturesift.billing_service import register_user, verify_email
from main import app


def _configure(monkeypatch) -> None:
    monkeypatch.setattr(config, "IYZICO_API_KEY", "live-api-key")
    monkeypatch.setattr(config, "IYZICO_SECRET_KEY", "live-secret-key")
    monkeypatch.setattr(config, "IYZICO_BASE_URL", "https://api.iyzipay.com")
    monkeypatch.setattr(config, "PUBLIC_BASE_URL", "https://lecturesift-backend.onrender.com")
    monkeypatch.setattr(config, "LEGAL_OPERATOR_NAME", "LectureSift Test")
    monkeypatch.setattr(config, "LEGAL_OPERATOR_ADDRESS", "Test Address 1")
    monkeypatch.setattr(config, "LEGAL_OPERATOR_COUNTRY", "TR")
    monkeypatch.setattr(config, "LEGAL_OPERATOR_PHONE", "+905551112233")
    monkeypatch.setattr(config, "LEGAL_OPERATOR_EMAIL", "billing@example.com")


def _account() -> tuple[str, str]:
    email = f"iyzico-{uuid.uuid4()}@example.com"
    created = register_user(
        email,
        "Strong-test-password1",
        "Ada",
        "Lovelace",
        phone="+905551112233",
        country_code="TR",
    )
    verified = verify_email(created["verification_token"])
    return email, verified["token"]


def _response_signature(values: list[object]) -> str:
    return hmac.new(
        config.IYZICO_SECRET_KEY.encode(),
        ":".join(str(value) for value in values).encode(),
        hashlib.sha256,
    ).hexdigest()


def _webhook_signature(payload: dict) -> str:
    message = (
        f"{config.IYZICO_SECRET_KEY}{payload['iyziEventType']}"
        f"{payload['iyziPaymentId']}{payload['token']}"
        f"{payload['paymentConversationId']}{payload['status']}"
    )
    return hmac.new(
        config.IYZICO_SECRET_KEY.encode(),
        message.encode(),
        hashlib.sha256,
    ).hexdigest()


def test_iyzico_checkout_and_callback_verify_signatures_amount_and_order(monkeypatch):
    _configure(monkeypatch)
    captured = []
    token = "safe-checkout-token"

    class FakeResponse:
        def __init__(self, body):
            self._body = body

        def raise_for_status(self):
            return None

        def json(self):
            return self._body

    def fake_post(url, *, content, headers, timeout):
        raw = content.decode("utf-8")
        payload = json.loads(raw)
        captured.append({"url": url, "raw": raw, "payload": payload, "headers": headers})
        if url.endswith(payments.IYZICO_INITIALIZE_PATH):
            reference = payload["conversationId"]
            return FakeResponse({
                "status": "success",
                "conversationId": reference,
                "token": token,
                "paymentPageUrl": f"https://api.iyzipay.com/checkoutform/{token}",
                "signature": _response_signature([reference, token]),
            })
        reference = payload["conversationId"]
        values = ["SUCCESS", "payment-123", "TRY", reference, reference, "699", "699", token]
        return FakeResponse({
            "status": "success",
            "paymentStatus": "SUCCESS",
            "paymentId": "payment-123",
            "currency": "TRY",
            "basketId": reference,
            "conversationId": reference,
            "paidPrice": "699.00",
            "price": "699.0",
            "token": token,
            "signature": _response_signature(values),
        })

    monkeypatch.setattr(payments.httpx, "post", fake_post)
    email, session = _account()
    client = TestClient(app)
    checkout = client.post(
        "/billing/checkout",
        headers={"Authorization": f"Bearer {session}", "X-Forwarded-For": "203.0.113.40"},
        json={
            "plan_code": "plus",
            "interval": "monthly",
            "currency": "TRY",
            "billing_address": "Örnek Mahallesi No 1",
            "billing_city": "İstanbul",
            "billing_zip_code": "34000",
            "phone": "+905551112233",
            "language": "tr",
            "terms_accepted": True,
            "early_performance_requested": True,
        },
    )
    assert checkout.status_code == 200, checkout.text
    body = checkout.json()
    reference = body["order"]["reference"]
    assert body["provider"] == "iyzico"
    assert body["display_mode"] == "redirect"
    assert body["mode"] == "live"
    assert body["checkout_url"] == f"https://api.iyzipay.com/checkoutform/{token}"

    initialize = captured[0]
    assert initialize["url"] == f"https://api.iyzipay.com{payments.IYZICO_INITIALIZE_PATH}"
    assert initialize["payload"]["buyer"]["email"] == email
    assert initialize["payload"]["buyer"]["identityNumber"] == "11111111111"
    assert initialize["payload"]["basketItems"][0]["itemType"] == "VIRTUAL"
    assert "shippingAddress" not in initialize["payload"]
    assert initialize["payload"]["price"] == "699.00"
    assert initialize["payload"]["callbackUrl"].endswith(f"?order={reference}")
    assert "live-secret-key" not in initialize["raw"]
    assert "live-api-key" not in initialize["raw"]
    random_key = initialize["headers"]["x-iyzi-rnd"]
    request_signature = hmac.new(
        config.IYZICO_SECRET_KEY.encode(),
        f"{random_key}{payments.IYZICO_INITIALIZE_PATH}{initialize['raw']}".encode(),
        hashlib.sha256,
    ).hexdigest()
    decoded_auth = base64.b64decode(
        initialize["headers"]["Authorization"].removeprefix("IYZWSv2 ")
    ).decode()
    assert decoded_auth == (
        f"apiKey:{config.IYZICO_API_KEY}&randomKey:{random_key}&signature:{request_signature}"
    )

    callback = client.post(
        f"/billing/iyzico/callback?order={reference}",
        data={"token": token},
        follow_redirects=False,
    )
    repeated_callback = client.post(
        f"/billing/iyzico/callback?order={reference}",
        data={"token": token},
        follow_redirects=False,
    )
    assert callback.status_code == 303
    assert repeated_callback.status_code == 303
    assert callback.headers["location"].endswith(
        f"/account.html?payment=success&order={reference}"
    )
    account = client.get(
        "/billing/me", headers={"Authorization": f"Bearer {session}"}
    ).json()["account"]
    assert account["plan"]["code"] == "plus"
    assert account["payment_orders"][0]["provider"] == "iyzico"
    assert account["payment_orders"][0]["status"] == "paid"


def test_iyzico_one_lira_test_pack_uses_exact_live_amount(monkeypatch):
    _configure(monkeypatch)
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "status": "success",
                "conversationId": captured["payload"]["conversationId"],
                "token": "one-lira-token",
                "paymentPageUrl": "https://api.iyzipay.com/checkoutform/one-lira-token",
                "signature": _response_signature([
                    captured["payload"]["conversationId"],
                    "one-lira-token",
                ]),
            }

    def fake_post(url, *, content, headers, timeout):
        captured["payload"] = json.loads(content)
        return FakeResponse()

    monkeypatch.setattr(payments.httpx, "post", fake_post)
    _, session = _account()
    response = TestClient(app).post(
        "/billing/checkout",
        headers={"Authorization": f"Bearer {session}", "X-Forwarded-For": "203.0.113.42"},
        json={
            "plan_code": "test", "interval": "one_time", "currency": "TRY",
            "billing_address": "Örnek Mahallesi No 3", "billing_city": "Hatay",
            "billing_zip_code": "31800", "phone": "+905551112233", "language": "tr",
            "terms_accepted": True, "early_performance_requested": True,
        },
    )
    assert response.status_code == 200, response.text
    assert captured["payload"]["price"] == "1.00"
    assert captured["payload"]["paidPrice"] == "1.00"
    assert captured["payload"]["currency"] == "TRY"
    assert captured["payload"]["basketItems"][0]["name"] == "LectureSift test"


def test_iyzico_decline_is_recorded_instead_of_staying_created(monkeypatch):
    _configure(monkeypatch)
    state = {"reference": ""}
    token = "declined-checkout-token"

    class FakeResponse:
        def __init__(self, body):
            self._body = body

        def raise_for_status(self):
            return None

        def json(self):
            return self._body

    def fake_post(url, *, content, headers, timeout):
        payload = json.loads(content)
        reference = payload["conversationId"]
        if url.endswith(payments.IYZICO_INITIALIZE_PATH):
            state["reference"] = reference
            return FakeResponse({
                "status": "success",
                "conversationId": reference,
                "token": token,
                "paymentPageUrl": f"https://api.iyzipay.com/checkoutform/{token}",
                "signature": _response_signature([reference, token]),
            })
        return FakeResponse({
            "status": "failure",
            "conversationId": reference,
            "errorCode": "10220",
            "errorGroup": "DECLINED",
            "errorMessage": "Ödeme alınamadı",
        })

    monkeypatch.setattr(payments.httpx, "post", fake_post)
    _, session = _account()
    client = TestClient(app)
    checkout = client.post(
        "/billing/checkout",
        headers={"Authorization": f"Bearer {session}", "X-Forwarded-For": "203.0.113.45"},
        json={
            "plan_code": "test", "interval": "one_time", "currency": "TRY",
            "billing_address": "Örnek Mahallesi No 4", "billing_city": "Hatay",
            "billing_zip_code": "31800", "phone": "+905551112233", "language": "tr",
            "terms_accepted": True, "early_performance_requested": True,
        },
    )
    assert checkout.status_code == 200, checkout.text
    callback = client.post(
        f"/billing/iyzico/callback?order={state['reference']}",
        data={"token": token},
        follow_redirects=False,
    )
    assert callback.status_code == 303
    assert callback.headers["location"].endswith(
        f"/plans.html?payment=failed&order={state['reference']}"
    )
    account = client.get(
        "/billing/me", headers={"Authorization": f"Bearer {session}"}
    ).json()["account"]
    order = account["payment_orders"][0]
    assert order["status"] == "failed"
    assert order["provider_amount_minor"] == 0
    assert order["failure_code"] == "10220"
    assert order["failure_message"] == "Ödeme alınamadı"
    assert account["credit_minutes"] == 0


def test_iyzico_bank_transfer_waits_for_signed_webhook_before_activation(monkeypatch):
    _configure(monkeypatch)
    state = {"reference": "", "matched": False}
    token = "protected-bank-transfer-token"

    class FakeResponse:
        def __init__(self, body):
            self._body = body

        def raise_for_status(self):
            return None

        def json(self):
            return self._body

    def fake_post(url, *, content, headers, timeout):
        payload = json.loads(content)
        reference = payload["conversationId"]
        if url.endswith(payments.IYZICO_INITIALIZE_PATH):
            state["reference"] = reference
            return FakeResponse({
                "status": "success",
                "conversationId": reference,
                "token": token,
                "paymentPageUrl": f"https://api.iyzipay.com/checkoutform/{token}",
                "signature": _response_signature([reference, token]),
            })
        payment_status = "SUCCESS" if state["matched"] else "INIT_BANK_TRANSFER"
        values = [payment_status, "bank-payment-123", "TRY", reference, reference, "699", "699", token]
        return FakeResponse({
            "status": "success",
            "paymentStatus": payment_status,
            "paymentId": "bank-payment-123",
            "currency": "TRY",
            "basketId": reference,
            "conversationId": reference,
            "paidPrice": "699.00",
            "price": "699.00",
            "token": token,
            "signature": _response_signature(values),
        })

    monkeypatch.setattr(payments.httpx, "post", fake_post)
    _, session = _account()
    client = TestClient(app)
    checkout = client.post(
        "/billing/checkout",
        headers={"Authorization": f"Bearer {session}", "X-Forwarded-For": "203.0.113.46"},
        json={
            "plan_code": "plus", "interval": "monthly", "currency": "TRY",
            "billing_address": "Örnek Mahallesi No 5", "billing_city": "Hatay",
            "billing_zip_code": "31800", "phone": "+905551112233", "language": "tr",
            "terms_accepted": True, "early_performance_requested": True,
        },
    )
    assert checkout.status_code == 200, checkout.text
    reference = state["reference"]

    callback = client.post(
        f"/billing/iyzico/callback?order={reference}",
        data={"token": token},
        follow_redirects=False,
    )
    assert callback.status_code == 303
    assert callback.headers["location"].endswith(
        f"/account.html?payment=pending&order={reference}"
    )
    account = client.get(
        "/billing/me", headers={"Authorization": f"Bearer {session}"}
    ).json()["account"]
    assert account["payment_orders"][0]["status"] == "pending"
    assert account["plan"]["code"] == "free"

    webhook = {
        "paymentConversationId": reference,
        "merchantId": 3404590,
        "status": "SUCCESS",
        "token": token,
        "iyziReferenceCode": "bank-webhook-reference",
        "iyziEventType": "BANK_TRANSFER_AUTH",
        "iyziEventTime": 1762239641852,
        "iyziPaymentId": 27553416,
    }
    rejected = client.post(
        "/billing/iyzico/webhook",
        headers={"X-IYZ-SIGNATURE-V3": "tampered"},
        json=webhook,
    )
    assert rejected.status_code == 400
    state["matched"] = True
    accepted = client.post(
        "/billing/iyzico/webhook",
        headers={"X-IYZ-SIGNATURE-V3": _webhook_signature(webhook)},
        json=webhook,
    )
    repeated = client.post(
        "/billing/iyzico/webhook",
        headers={"X-IYZ-SIGNATURE-V3": _webhook_signature(webhook)},
        json=webhook,
    )
    assert accepted.status_code == 200 and accepted.text == "OK"
    assert repeated.status_code == 200 and repeated.text == "OK"
    account = client.get(
        "/billing/me", headers={"Authorization": f"Bearer {session}"}
    ).json()["account"]
    assert account["payment_orders"][0]["status"] == "paid"
    assert account["plan"]["code"] == "plus"


def test_iyzico_callback_rejects_tampered_response(monkeypatch):
    _configure(monkeypatch)
    token = "tamper-test-token"
    state = {"reference": ""}

    class FakeResponse:
        def __init__(self, body):
            self._body = body

        def raise_for_status(self):
            return None

        def json(self):
            return self._body

    def fake_post(url, *, content, headers, timeout):
        payload = json.loads(content)
        reference = payload["conversationId"]
        if url.endswith(payments.IYZICO_INITIALIZE_PATH):
            state["reference"] = reference
            return FakeResponse({
                "status": "success",
                "conversationId": reference,
                "token": token,
                "paymentPageUrl": f"https://api.iyzipay.com/checkoutform/{token}",
                "signature": _response_signature([reference, token]),
            })
        return FakeResponse({
            "status": "success",
            "paymentStatus": "SUCCESS",
            "paymentId": "payment-tampered",
            "currency": "TRY",
            "basketId": reference,
            "conversationId": reference,
            "paidPrice": "1.00",
            "price": "1.00",
            "token": token,
            "signature": "invalid",
        })

    monkeypatch.setattr(payments.httpx, "post", fake_post)
    _, session = _account()
    client = TestClient(app)
    checkout = client.post(
        "/billing/checkout",
        headers={"Authorization": f"Bearer {session}", "X-Forwarded-For": "203.0.113.41"},
        json={
            "plan_code": "credit", "interval": "one_time", "currency": "TRY",
            "billing_address": "Örnek Mahallesi No 2", "billing_city": "Ankara",
            "billing_zip_code": "06000", "phone": "+905551112233", "language": "tr",
            "terms_accepted": True, "early_performance_requested": True,
        },
    )
    assert checkout.status_code == 200
    callback = client.post(
        f"/billing/iyzico/callback?order={state['reference']}",
        data={"token": token},
        follow_redirects=False,
    )
    assert callback.status_code == 303
    assert "payment=verification_failed" in callback.headers["location"]
    account = client.get(
        "/billing/me", headers={"Authorization": f"Bearer {session}"}
    ).json()["account"]
    assert account["credit_minutes"] == 0
    assert account["payment_orders"][0]["status"] == "created"
