import base64
import hashlib
import hmac
import json
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from lecturesift import config, payments
from lecturesift.billing_service import (
    admin_billing_overview,
    authenticate_session,
    ENGINE,
    IYZICO_CARD_PROVIDER,
    IYZICO_CARD_INTENT_PROVIDER,
    IYZICO_BANK_TRANSFER_INTENT_PROVIDER,
    IYZICO_BANK_TRANSFER_PROVIDER,
    PAYMENT_ORDERS,
    PAYMENT_PROVIDER_SESSIONS,
    iyzico_provider_is_confirmed,
    register_user,
    verify_email,
)
from lecturesift.rollout_service import create_refund_request, list_admin_orders_page
from main import app


def _configure(monkeypatch) -> None:
    monkeypatch.setattr(config, "IYZICO_API_KEY", "live-api-key")
    monkeypatch.setattr(config, "IYZICO_SECRET_KEY", "live-secret-key")
    monkeypatch.setattr(config, "PAYMENT_TOKEN_BINDING_SECRET", "payment-binding-secret-at-least-32-chars")
    monkeypatch.setattr(config, "PAYMENT_TOKEN_BINDING_LEGACY_SECRET", "")
    monkeypatch.setattr(config, "IYZICO_BASE_URL", "https://api.iyzipay.com")
    monkeypatch.setattr(config, "IYZICO_BANK_TRANSFER_ENABLED", False)
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
        values = ["SUCCESS", "payment-123", "TRY", reference, reference, "449", "449", token]
        return FakeResponse({
            "status": "success",
            "paymentStatus": "SUCCESS",
            "paymentId": "payment-123",
            "currency": "TRY",
            "basketId": reference,
            "conversationId": reference,
            "paidPrice": "449.00",
            "price": "449.0",
            "token": token,
            "cardType": "CREDIT_CARD",
            "cardAssociation": "VISA",
            "lastFourDigits": "1234",
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
            "payment_method": "card",
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
    assert body["order"]["order_number"] == reference
    assert body["provider"] == "iyzico"
    assert body["display_mode"] == "redirect"
    assert body["mode"] == "live"
    assert body["checkout_url"] == f"https://api.iyzipay.com/checkoutform/{token}"
    with ENGINE.connect() as connection:
        provider_session = connection.execute(
            select(PAYMENT_PROVIDER_SESSIONS).where(
                PAYMENT_PROVIDER_SESSIONS.c.order_reference == reference
            )
        ).one()
    assert provider_session.provider == IYZICO_CARD_INTENT_PROVIDER
    assert len(provider_session.token_digest) == 64
    assert provider_session.token_digest != token

    initialize = captured[0]
    assert initialize["url"] == f"https://api.iyzipay.com{payments.IYZICO_INITIALIZE_PATH}"
    assert initialize["payload"]["buyer"]["email"] == email
    assert initialize["payload"]["buyer"]["identityNumber"] == "11111111111"
    assert initialize["payload"]["basketItems"][0]["itemType"] == "VIRTUAL"
    assert "shippingAddress" not in initialize["payload"]
    assert initialize["payload"]["price"] == "449.00"
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

    previous_binding_secret = config.PAYMENT_TOKEN_BINDING_SECRET
    monkeypatch.setattr(
        config,
        "PAYMENT_TOKEN_BINDING_SECRET",
        "rotated-payment-binding-secret-at-least-32-chars",
    )
    monkeypatch.setattr(
        config,
        "PAYMENT_TOKEN_BINDING_LEGACY_SECRET",
        previous_binding_secret,
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
            "payment_method": "card",
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


def test_iyzico_bank_transfer_checkout_fails_closed_without_activation_or_try(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(
        payments.httpx,
        "post",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("invalid protected-transfer request must not contact iyzico")
        ),
    )
    _, session = _account()
    client = TestClient(app)
    payload = {
        "plan_code": "plus",
        "interval": "monthly",
        "currency": "TRY",
        "payment_method": "bank_transfer",
        "billing_address": "Örnek Mahallesi No 11",
        "billing_city": "Hatay",
        "billing_zip_code": "31800",
        "phone": "+905551112233",
        "language": "tr",
        "terms_accepted": True,
        "early_performance_requested": True,
    }
    headers = {
        "Authorization": f"Bearer {session}",
        "X-Forwarded-For": "203.0.113.52",
    }

    missing_method = client.post(
        "/billing/checkout",
        headers=headers,
        json={key: value for key, value in payload.items() if key != "payment_method"},
    )
    assert missing_method.status_code == 422
    assert any(
        error.get("loc", [])[-1:] == ["payment_method"]
        for error in missing_method.json()["detail"]
    )

    disabled = client.post("/billing/checkout", headers=headers, json=payload)
    assert disabled.status_code == 503
    assert disabled.json()["detail"]["code"] == "LS-PAY-01"

    monkeypatch.setattr(config, "IYZICO_BANK_TRANSFER_ENABLED", True)
    non_try = client.post(
        "/billing/checkout", headers=headers, json={**payload, "currency": "EUR"}
    )
    assert non_try.status_code == 400
    assert non_try.json()["detail"]["code"] == "LS-PAY-02"

    invalid = client.post(
        "/billing/checkout",
        headers=headers,
        json={**payload, "payment_method": "provider_redirect"},
    )
    assert invalid.status_code == 422
    assert any(
        error.get("loc", [])[-1:] == ["payment_method"]
        for error in invalid.json()["detail"]
    )


@pytest.mark.parametrize(
    "event_type",
    sorted(payments.IYZICO_HPP_NON_TRANSFER_EVENTS),
)
def test_all_non_transfer_hpp_events_are_classified_as_card(event_type):
    assert payments._iyzico_webhook_payment_method(event_type) == "card"


def test_bank_transfer_and_refund_hpp_events_have_distinct_routing():
    assert payments._iyzico_webhook_payment_method("BANK_TRANSFER_AUTH") == "bank_transfer"
    assert payments._iyzico_webhook_payment_method("CONTACTLESS_REFUND") == ""


def test_legacy_iyzico_provider_stays_unconfirmed_until_signed_evidence():
    assert iyzico_provider_is_confirmed("iyzico", "paid") is False
    assert iyzico_provider_is_confirmed("iyzico", "created") is False
    assert iyzico_provider_is_confirmed("iyzico", "pending") is False
    assert iyzico_provider_is_confirmed("iyzico", "failed") is False


@pytest.mark.parametrize("requested_method", ["card", "bank_transfer"])
def test_success_without_provider_method_evidence_never_confirms_ui_intent(requested_method):
    assert payments._iyzico_result_payment_method(
        {"paymentStatus": "SUCCESS"},
        requested_method=requested_method,
    ) == ""


def test_iyzico_decline_is_recorded_instead_of_staying_created(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(config, "IYZICO_BANK_TRANSFER_ENABLED", True)
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
            "payment_method": "bank_transfer",
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
    assert order["payment_method_confirmed"] is False
    assert account["credit_minutes"] == 0

    webhook = {
        "paymentConversationId": state["reference"],
        "merchantId": 3404590,
        "status": "FAILURE",
        "token": token,
        "iyziReferenceCode": "callback-first-failure-event",
        "iyziEventType": "BKM_AUTH",
        "iyziEventTime": 1762239642003,
        "iyziPaymentId": 27553423,
    }
    reconciled = client.post(
        "/billing/iyzico/webhook",
        headers={"X-IYZ-SIGNATURE-V3": _webhook_signature(webhook)},
        json=webhook,
    )
    assert reconciled.status_code == 200
    corrected = client.get(
        "/billing/me", headers={"Authorization": f"Bearer {session}"}
    ).json()["account"]["payment_orders"][0]
    assert corrected["status"] == "failed"
    assert corrected["payment_method"] == "card"
    assert corrected["payment_method_confirmed"] is True


def test_signed_non_transfer_failure_reconciles_bank_transfer_intent(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(config, "IYZICO_BANK_TRANSFER_ENABLED", True)
    state = {"reference": ""}
    token = "failed-cross-selection-token"

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
        headers={"Authorization": f"Bearer {session}", "X-Forwarded-For": "203.0.113.55"},
        json={
            "plan_code": "plus", "interval": "monthly", "currency": "TRY",
            "payment_method": "bank_transfer",
            "billing_address": "Örnek Mahallesi No 14", "billing_city": "Hatay",
            "billing_zip_code": "31800", "phone": "+905551112233", "language": "tr",
            "terms_accepted": True, "early_performance_requested": True,
        },
    )
    assert checkout.status_code == 200, checkout.text
    reference = state["reference"]
    webhook = {
        "paymentConversationId": reference,
        "merchantId": 3404590,
        "status": "FAILURE",
        "token": token,
        "iyziReferenceCode": "failed-bkm-event",
        "iyziEventType": "BKM_AUTH",
        "iyziEventTime": 1762239642001,
        "iyziPaymentId": 27553421,
    }
    response = client.post(
        "/billing/iyzico/webhook",
        headers={"X-IYZ-SIGNATURE-V3": _webhook_signature(webhook)},
        json=webhook,
    )
    assert response.status_code == 200
    account = client.get(
        "/billing/me", headers={"Authorization": f"Bearer {session}"}
    ).json()["account"]
    order = account["payment_orders"][0]
    assert order["status"] == "failed"
    assert order["payment_method"] == "card"
    assert order["payment_method_confirmed"] is True
    assert account["plan"]["code"] == "free"
    with ENGINE.connect() as connection:
        provider_session = connection.execute(
            select(PAYMENT_PROVIDER_SESSIONS).where(
                PAYMENT_PROVIDER_SESSIONS.c.order_reference == reference
            )
        ).one()
    assert provider_session.provider == IYZICO_CARD_PROVIDER


def test_iyzico_merchant_category_failure_is_actionable_and_keeps_code(monkeypatch):
    _configure(monkeypatch)
    state = {"reference": ""}
    token = "merchant-category-checkout-token"

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
            "errorCode": "10208",
            "errorGroup": "INVALID_MERCHANT_OR_SP",
            "errorMessage": "Üye işyeri kategori kodu hatalı",
        })

    monkeypatch.setattr(payments.httpx, "post", fake_post)
    _, session = _account()
    client = TestClient(app)
    checkout = client.post(
        "/billing/checkout",
        headers={"Authorization": f"Bearer {session}", "X-Forwarded-For": "203.0.113.47"},
        json={
            "plan_code": "test", "interval": "one_time", "currency": "TRY",
            "payment_method": "card",
            "billing_address": "Örnek Mahallesi No 6", "billing_city": "Hatay",
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
    account = client.get(
        "/billing/me", headers={"Authorization": f"Bearer {session}"}
    ).json()["account"]
    order = account["payment_orders"][0]
    assert order["status"] == "failed"
    assert order["failure_code"] == "10208"
    assert "mağazanın kategori kodunu" in order["failure_message"]
    assert "plan etkinleşmedi" in order["failure_message"]
    assert "Karttan çekim yapılmadı" not in order["failure_message"]


def test_iyzico_uncorrelated_failure_cannot_terminally_fail_order(monkeypatch):
    _configure(monkeypatch)
    state = {"reference": ""}
    token = "uncorrelated-failure-token"

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
            "errorCode": "10208",
            "errorMessage": "Uncorrelated provider failure",
        })

    monkeypatch.setattr(payments.httpx, "post", fake_post)
    _, session = _account()
    client = TestClient(app)
    checkout = client.post(
        "/billing/checkout",
        headers={"Authorization": f"Bearer {session}", "X-Forwarded-For": "203.0.113.48"},
        json={
            "plan_code": "test", "interval": "one_time", "currency": "TRY",
            "payment_method": "card",
            "billing_address": "Örnek Mahallesi No 7", "billing_city": "Hatay",
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
    assert "payment=verification_failed" in callback.headers["location"]
    account = client.get(
        "/billing/me", headers={"Authorization": f"Bearer {session}"}
    ).json()["account"]
    order = account["payment_orders"][0]
    assert order["status"] == "created"
    assert order["failure_code"] is None


def test_iyzico_browser_callback_rejects_wrong_or_legacy_unbound_token_before_retrieve(monkeypatch):
    _configure(monkeypatch)
    state = {"reference": "", "requests": 0}
    token = "bound-checkout-token"

    class FakeResponse:
        def __init__(self, body):
            self._body = body

        def raise_for_status(self):
            return None

        def json(self):
            return self._body

    def fake_post(url, *, content, headers, timeout):
        state["requests"] += 1
        payload = json.loads(content)
        reference = payload["conversationId"]
        if not url.endswith(payments.IYZICO_INITIALIZE_PATH):
            raise AssertionError("wrong checkout token must fail before retrieve")
        state["reference"] = reference
        return FakeResponse({
            "status": "success",
            "conversationId": reference,
            "token": token,
            "paymentPageUrl": f"https://api.iyzipay.com/checkoutform/{token}",
            "signature": _response_signature([reference, token]),
        })

    monkeypatch.setattr(payments.httpx, "post", fake_post)
    _, session = _account()
    client = TestClient(app)
    checkout = client.post(
        "/billing/checkout",
        headers={"Authorization": f"Bearer {session}", "X-Forwarded-For": "203.0.113.49"},
        json={
            "plan_code": "test", "interval": "one_time", "currency": "TRY",
            "payment_method": "card",
            "billing_address": "Örnek Mahallesi No 8", "billing_city": "Hatay",
            "billing_zip_code": "31800", "phone": "+905551112233", "language": "tr",
            "terms_accepted": True, "early_performance_requested": True,
        },
    )
    assert checkout.status_code == 200, checkout.text
    callback = client.post(
        f"/billing/iyzico/callback?order={state['reference']}",
        data={"token": "different-checkout-token"},
        follow_redirects=False,
    )
    assert callback.status_code == 303
    assert "payment=verification_failed" in callback.headers["location"]
    assert state["requests"] == 1

    # A correct raw token still cannot self-adopt through the unsigned browser
    # callback when the order predates the binding table.
    with ENGINE.begin() as connection:
        connection.execute(
            delete(PAYMENT_PROVIDER_SESSIONS).where(
                PAYMENT_PROVIDER_SESSIONS.c.order_reference == state["reference"]
            )
        )
    legacy_callback = client.post(
        f"/billing/iyzico/callback?order={state['reference']}",
        data={"token": token},
        follow_redirects=False,
    )
    assert legacy_callback.status_code == 303
    assert "payment=verification_failed" in legacy_callback.headers["location"]
    assert state["requests"] == 1
    account = client.get(
        "/billing/me", headers={"Authorization": f"Bearer {session}"}
    ).json()["account"]
    assert account["payment_orders"][0]["status"] == "created"
    assert account["payment_orders"][0]["failure_code"] is None


def test_iyzico_signed_webhook_rejects_wrong_bound_token_before_retrieve(monkeypatch):
    _configure(monkeypatch)
    state = {"reference": "", "requests": 0}
    token = "signed-bound-checkout-token"

    class FakeResponse:
        def __init__(self, body):
            self._body = body

        def raise_for_status(self):
            return None

        def json(self):
            return self._body

    def fake_post(url, *, content, headers, timeout):
        state["requests"] += 1
        payload = json.loads(content)
        reference = payload["conversationId"]
        if not url.endswith(payments.IYZICO_INITIALIZE_PATH):
            raise AssertionError("wrong signed webhook token must fail before retrieve")
        state["reference"] = reference
        return FakeResponse({
            "status": "success",
            "conversationId": reference,
            "token": token,
            "paymentPageUrl": f"https://api.iyzipay.com/checkoutform/{token}",
            "signature": _response_signature([reference, token]),
        })

    monkeypatch.setattr(payments.httpx, "post", fake_post)
    _, session = _account()
    client = TestClient(app)
    checkout = client.post(
        "/billing/checkout",
        headers={"Authorization": f"Bearer {session}", "X-Forwarded-For": "203.0.113.50"},
        json={
            "plan_code": "test", "interval": "one_time", "currency": "TRY",
            "payment_method": "card",
            "billing_address": "Örnek Mahallesi No 9", "billing_city": "Hatay",
            "billing_zip_code": "31800", "phone": "+905551112233", "language": "tr",
            "terms_accepted": True, "early_performance_requested": True,
        },
    )
    assert checkout.status_code == 200, checkout.text
    webhook = {
        "paymentConversationId": state["reference"],
        "merchantId": 3404590,
        "status": "FAILURE",
        "token": "different-signed-token",
        "iyziReferenceCode": "wrong-token-reference",
        "iyziEventType": "CHECKOUT_FORM_AUTH",
        "iyziEventTime": 1762239641852,
        "iyziPaymentId": 27553417,
    }
    rejected = client.post(
        "/billing/iyzico/webhook",
        headers={"X-IYZ-SIGNATURE-V3": _webhook_signature(webhook)},
        json=webhook,
    )
    assert rejected.status_code == 400
    assert state["requests"] == 1
    account = client.get(
        "/billing/me", headers={"Authorization": f"Bearer {session}"}
    ).json()["account"]
    assert account["payment_orders"][0]["status"] == "created"
    assert account["payment_orders"][0]["failure_code"] is None


def test_iyzico_signed_webhook_adopts_legacy_token_and_terminalizes_correlated_10208(monkeypatch):
    _configure(monkeypatch)
    state = {"reference": ""}
    token = "signed-category-checkout-token"

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
        # Some authenticated failure retrieves do not echo conversationId.
        # The signed webhook plus the initialize-token binding still provides
        # the required correlation to terminalize this exact order safely.
        return FakeResponse({
            "status": "failure",
            "errorCode": "10208",
            "errorGroup": "INVALID_MERCHANT_OR_SP",
            "errorMessage": "Üye işyeri kategori kodu hatalı",
        })

    monkeypatch.setattr(payments.httpx, "post", fake_post)
    _, session = _account()
    client = TestClient(app)
    checkout = client.post(
        "/billing/checkout",
        headers={"Authorization": f"Bearer {session}", "X-Forwarded-For": "203.0.113.51"},
        json={
            "plan_code": "test", "interval": "one_time", "currency": "TRY",
            "payment_method": "card",
            "billing_address": "Örnek Mahallesi No 10", "billing_city": "Hatay",
            "billing_zip_code": "31800", "phone": "+905551112233", "language": "tr",
            "terms_accepted": True, "early_performance_requested": True,
        },
    )
    assert checkout.status_code == 200, checkout.text
    with ENGINE.begin() as connection:
        connection.execute(
            delete(PAYMENT_PROVIDER_SESSIONS).where(
                PAYMENT_PROVIDER_SESSIONS.c.order_reference == state["reference"]
            )
        )
    webhook = {
        "paymentConversationId": state["reference"],
        "merchantId": 3404590,
        "status": "FAILURE",
        "token": token,
        "iyziReferenceCode": "category-failure-reference",
        "iyziEventType": "CHECKOUT_FORM_AUTH",
        "iyziEventTime": 1762239641852,
        "iyziPaymentId": 27553418,
    }
    accepted = client.post(
        "/billing/iyzico/webhook",
        headers={"X-IYZ-SIGNATURE-V3": _webhook_signature(webhook)},
        json=webhook,
    )
    assert accepted.status_code == 200 and accepted.text == "OK"
    account = client.get(
        "/billing/me", headers={"Authorization": f"Bearer {session}"}
    ).json()["account"]
    order = account["payment_orders"][0]
    assert order["status"] == "failed"
    assert order["failure_code"] == "10208"
    assert "plan etkinleşmedi" in order["failure_message"]
    with ENGINE.connect() as connection:
        adopted = connection.execute(
            select(PAYMENT_PROVIDER_SESSIONS).where(
                PAYMENT_PROVIDER_SESSIONS.c.order_reference == state["reference"]
            )
        ).one()
    assert adopted.token_digest == payments._iyzico_token_digest(state["reference"], token)


def test_iyzico_bank_transfer_waits_for_signed_webhook_before_activation(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(config, "IYZICO_BANK_TRANSFER_ENABLED", True)
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
        values = [payment_status, "bank-payment-123", "TRY", reference, reference, "449", "449", token]
        return FakeResponse({
            "status": "success",
            "paymentStatus": payment_status,
            "paymentId": "bank-payment-123",
            "currency": "TRY",
            "basketId": reference,
            "conversationId": reference,
            "paidPrice": "449.00",
            "price": "449.00",
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
            "payment_method": "bank_transfer",
            "billing_address": "Örnek Mahallesi No 5", "billing_city": "Hatay",
            "billing_zip_code": "31800", "phone": "+905551112233", "language": "tr",
            "terms_accepted": True, "early_performance_requested": True,
        },
    )
    assert checkout.status_code == 200, checkout.text
    assert checkout.json()["provider"] == "iyzico"
    assert checkout.json()["payment_method"] == "bank_transfer"
    assert checkout.json()["order"]["provider"] == "iyzico"
    assert checkout.json()["order"]["payment_method"] == "bank_transfer"
    reference = state["reference"]
    with ENGINE.connect() as connection:
        provider_session = connection.execute(
            select(PAYMENT_PROVIDER_SESSIONS).where(
                PAYMENT_PROVIDER_SESSIONS.c.order_reference == reference
            )
        ).one()
    assert provider_session.provider == IYZICO_BANK_TRANSFER_INTENT_PROVIDER

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
    assert account["payment_orders"][0]["provider"] == "iyzico"
    assert account["payment_orders"][0]["payment_method"] == "bank_transfer"
    assert account["plan"]["code"] == "free"
    admin_orders = list_admin_orders_page(
        search=reference,
        provider="bank_transfer",
        page=1,
        page_size=10,
    )["items"]
    assert len(admin_orders) == 1
    assert admin_orders[0]["provider"] == "iyzico"
    assert admin_orders[0]["payment_method"] == "bank_transfer"
    assert admin_billing_overview()["counts"]["pending_orders"] >= 1
    with ENGINE.begin() as connection:
        connection.execute(
            delete(PAYMENT_PROVIDER_SESSIONS).where(
                PAYMENT_PROVIDER_SESSIONS.c.order_reference == reference
            )
        )

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
    missing_signature = client.post("/billing/iyzico/webhook", json=webhook)
    assert missing_signature.status_code == 400
    state["matched"] = True
    # Disabling new transfer checkouts must not strand an already-created
    # transfer. Signed provider notifications remain processable in flight.
    monkeypatch.setattr(config, "IYZICO_BANK_TRANSFER_ENABLED", False)
    accepted = client.post(
        "/billing/iyzico/webhook",
        headers={"X-IYZ-SIGNATURE-V3": _webhook_signature(webhook).upper()},
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
    assert account["payment_orders"][0]["payment_method"] == "bank_transfer"
    assert account["plan"]["code"] == "plus"
    user = authenticate_session(session)
    refund = create_refund_request(
        user["id"],
        reference,
        "Korumalı havale siparişi için test iade talebi.",
    )
    assert refund["provider"] == "iyzico"


@pytest.mark.parametrize("legacy_status", ["created", "paid"])
def test_legacy_iyzico_order_accepts_signed_bank_transfer_evidence(
    monkeypatch,
    legacy_status,
):
    _configure(monkeypatch)
    state = {"reference": ""}
    token = "legacy-inflight-token"

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
        values = ["SUCCESS", "legacy-bank-payment", "TRY", reference, reference, "449", "449", token]
        return FakeResponse({
            "status": "success",
            "paymentStatus": "SUCCESS",
            "paymentId": "legacy-bank-payment",
            "currency": "TRY",
            "basketId": reference,
            "conversationId": reference,
            "paidPrice": "449.00",
            "price": "449.00",
            "token": token,
            "signature": _response_signature(values),
        })

    monkeypatch.setattr(payments.httpx, "post", fake_post)
    _, session = _account()
    client = TestClient(app)
    checkout = client.post(
        "/billing/checkout",
        headers={"Authorization": f"Bearer {session}", "X-Forwarded-For": "203.0.113.56"},
        json={
            "plan_code": "plus", "interval": "monthly", "currency": "TRY",
            "payment_method": "card",
            "billing_address": "Örnek Mahallesi No 15", "billing_city": "Hatay",
            "billing_zip_code": "31800", "phone": "+905551112233", "language": "tr",
            "terms_accepted": True, "early_performance_requested": True,
        },
    )
    assert checkout.status_code == 200, checkout.text
    reference = state["reference"]
    # Simulate an old row that stored every iyzico method under one generic
    # provider value, including already-paid protected transfers.
    with ENGINE.begin() as connection:
        connection.execute(
            PAYMENT_ORDERS.update()
            .where(PAYMENT_ORDERS.c.reference == reference)
            .values(
                provider="iyzico",
                status=legacy_status,
                provider_amount_minor=44900 if legacy_status == "paid" else None,
            )
        )
        connection.execute(
            PAYMENT_PROVIDER_SESSIONS.update()
            .where(PAYMENT_PROVIDER_SESSIONS.c.order_reference == reference)
            .values(provider="iyzico")
        )
    before = client.get(
        "/billing/me", headers={"Authorization": f"Bearer {session}"}
    ).json()["account"]["payment_orders"][0]
    assert before["payment_method"] == "unknown"
    assert before["payment_method_confirmed"] is False

    webhook = {
        "paymentConversationId": reference,
        "merchantId": 3404590,
        "status": "SUCCESS",
        "token": token,
        "iyziReferenceCode": "legacy-bank-confirmation",
        "iyziEventType": "BANK_TRANSFER_AUTH",
        "iyziEventTime": 1762239642004,
        "iyziPaymentId": 27553424,
    }
    response = client.post(
        "/billing/iyzico/webhook",
        headers={"X-IYZ-SIGNATURE-V3": _webhook_signature(webhook)},
        json=webhook,
    )
    assert response.status_code == 200
    account = client.get(
        "/billing/me", headers={"Authorization": f"Bearer {session}"}
    ).json()["account"]
    order = account["payment_orders"][0]
    assert order["status"] == "paid"
    assert order["provider"] == "iyzico"
    assert order["payment_method"] == "bank_transfer"
    assert order["payment_method_confirmed"] is True
    with ENGINE.connect() as connection:
        provider_session = connection.execute(
            select(PAYMENT_PROVIDER_SESSIONS).where(
                PAYMENT_PROVIDER_SESSIONS.c.order_reference == reference
            )
        ).one()
    assert provider_session.provider == IYZICO_BANK_TRANSFER_PROVIDER


def test_iyzico_ambiguous_success_waits_for_signed_method_notification(monkeypatch):
    _configure(monkeypatch)
    state = {"reference": ""}
    token = "ambiguous-success-token"

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
        values = ["SUCCESS", "ambiguous-payment-1", "TRY", reference, reference, "449", "449", token]
        return FakeResponse({
            "status": "success",
            "paymentStatus": "SUCCESS",
            "paymentId": "ambiguous-payment-1",
            "currency": "TRY",
            "basketId": reference,
            "conversationId": reference,
            "paidPrice": "449.00",
            "price": "449.00",
            "token": token,
            "signature": _response_signature(values),
        })

    monkeypatch.setattr(payments.httpx, "post", fake_post)
    _, session = _account()
    client = TestClient(app)
    checkout = client.post(
        "/billing/checkout",
        headers={"Authorization": f"Bearer {session}", "X-Forwarded-For": "203.0.113.54"},
        json={
            "plan_code": "plus", "interval": "monthly", "currency": "TRY",
            "payment_method": "card",
            "billing_address": "Örnek Mahallesi No 13", "billing_city": "Hatay",
            "billing_zip_code": "31800", "phone": "+905551112233", "language": "tr",
            "terms_accepted": True, "early_performance_requested": True,
        },
    )
    assert checkout.status_code == 200, checkout.text
    reference = state["reference"]

    browser_callback = client.post(
        f"/billing/iyzico/callback?order={reference}",
        data={"token": token},
        follow_redirects=False,
    )
    assert browser_callback.status_code == 303
    assert browser_callback.headers["location"].endswith(
        f"/account.html?payment=pending&order={reference}"
    )
    pending_account = client.get(
        "/billing/me", headers={"Authorization": f"Bearer {session}"}
    ).json()["account"]
    pending_order = pending_account["payment_orders"][0]
    assert pending_order["status"] == "pending"
    assert pending_order["payment_method_confirmed"] is False
    assert pending_account["plan"]["code"] == "free"

    webhook = {
        "paymentConversationId": reference,
        "merchantId": 3404590,
        "status": "SUCCESS",
        "token": token,
        "iyziReferenceCode": "ambiguous-method-confirmed",
        "iyziEventType": "CHECKOUT_FORM_AUTH",
        "iyziEventTime": 1762239642000,
        "iyziPaymentId": 27553420,
    }
    accepted = client.post(
        "/billing/iyzico/webhook",
        headers={"X-IYZ-SIGNATURE-V3": _webhook_signature(webhook)},
        json=webhook,
    )
    assert accepted.status_code == 200
    paid_account = client.get(
        "/billing/me", headers={"Authorization": f"Bearer {session}"}
    ).json()["account"]
    paid_order = paid_account["payment_orders"][0]
    assert paid_order["status"] == "paid"
    assert paid_order["payment_method"] == "card"
    assert paid_order["payment_method_confirmed"] is True
    assert paid_account["plan"]["code"] == "plus"

    refund_notice = {
        "paymentConversationId": reference,
        "merchantId": 3404590,
        "status": "SUCCESS",
        "token": token,
        "iyziReferenceCode": "contactless-refund-notice",
        "iyziEventType": "CONTACTLESS_REFUND",
        "iyziEventTime": 1762239642002,
        "iyziPaymentId": 27553422,
    }
    refund_response = client.post(
        "/billing/iyzico/webhook",
        headers={"X-IYZ-SIGNATURE-V3": _webhook_signature(refund_notice)},
        json=refund_notice,
    )
    assert refund_response.status_code == 200
    unchanged = client.get(
        "/billing/me", headers={"Authorization": f"Bearer {session}"}
    ).json()["account"]["payment_orders"][0]
    assert unchanged["status"] == "paid"
    assert unchanged["payment_method"] == "card"


def test_iyzico_transfer_intent_is_reconciled_to_actual_card_payment(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(config, "IYZICO_BANK_TRANSFER_ENABLED", True)
    state = {"reference": ""}
    token = "cross-selected-card-token"

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
        values = ["SUCCESS", "card-payment-456", "TRY", reference, reference, "449", "449", token]
        return FakeResponse({
            "status": "success",
            "paymentStatus": "SUCCESS",
            "paymentId": "card-payment-456",
            "currency": "TRY",
            "basketId": reference,
            "conversationId": reference,
            "paidPrice": "449.00",
            "price": "449.00",
            "token": token,
            "cardType": "CREDIT_CARD",
            "cardAssociation": "VISA",
            "binNumber": "45436000",
            "lastFourDigits": "1234",
            "signature": _response_signature(values),
        })

    monkeypatch.setattr(payments.httpx, "post", fake_post)
    _, session = _account()
    client = TestClient(app)
    checkout = client.post(
        "/billing/checkout",
        headers={"Authorization": f"Bearer {session}", "X-Forwarded-For": "203.0.113.53"},
        json={
            "plan_code": "plus", "interval": "monthly", "currency": "TRY",
            "payment_method": "bank_transfer",
            "billing_address": "Örnek Mahallesi No 12", "billing_city": "Hatay",
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
    account = client.get(
        "/billing/me", headers={"Authorization": f"Bearer {session}"}
    ).json()["account"]
    order = account["payment_orders"][0]
    assert order["status"] == "paid"
    assert order["provider"] == "iyzico"
    assert order["payment_method"] == "card"
    assert order["payment_method_confirmed"] is True
    with ENGINE.connect() as connection:
        provider_session = connection.execute(
            select(PAYMENT_PROVIDER_SESSIONS).where(
                PAYMENT_PROVIDER_SESSIONS.c.order_reference == reference
            )
        ).one()
    assert provider_session.provider == IYZICO_CARD_PROVIDER

    contradictory = {
        "paymentConversationId": reference,
        "merchantId": 3404590,
        "status": "SUCCESS",
        "token": token,
        "iyziReferenceCode": "contradictory-bank-event",
        "iyziEventType": "BANK_TRANSFER_AUTH",
        "iyziEventTime": 1762239641999,
        "iyziPaymentId": 27553419,
    }
    rejected = client.post(
        "/billing/iyzico/webhook",
        headers={"X-IYZ-SIGNATURE-V3": _webhook_signature(contradictory)},
        json=contradictory,
    )
    assert rejected.status_code == 400
    unchanged = client.get(
        "/billing/me", headers={"Authorization": f"Bearer {session}"}
    ).json()["account"]["payment_orders"][0]
    assert unchanged["status"] == "paid"
    assert unchanged["payment_method"] == "card"


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
            "payment_method": "card",
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
