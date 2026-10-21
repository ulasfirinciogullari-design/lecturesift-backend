"""Hourly reconciliation settles paid iyzico orders whose callback never came.

Every provider call here goes to a fake: nothing reaches a real payment API.
"""

import hashlib
import hmac
import json
import logging
import uuid
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from lecturesift import config, payments
from lecturesift.billing_service import (
    ENGINE,
    PAYMENT_ORDERS,
    SUBSCRIPTIONS,
    register_user,
    unsettled_iyzico_order_references,
    utcnow,
    verify_email,
)
from main import app

TOKEN = "reconcile-checkout-token"
CARD_DIGITS = "4242"
BIN = "552879"


@pytest.fixture(autouse=True)
def no_real_provider(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("tests must never call a real payment API")

    monkeypatch.setattr(payments.httpx, "post", refuse)
    monkeypatch.setattr(payments.time, "sleep", lambda delay: None)


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


def _signature(values: list[object]) -> str:
    return hmac.new(
        config.IYZICO_SECRET_KEY.encode(),
        ":".join(str(value) for value in values).encode(),
        hashlib.sha256,
    ).hexdigest()


class _Response:
    def __init__(self, body):
        self._body = body

    def raise_for_status(self):
        return None

    def json(self):
        return self._body


class FakeIyzico:
    """Answers initialize, Checkout Form retrieve and Retrieve Payment calls."""

    def __init__(self, payment_status="SUCCESS", *, tamper=False, card=True, fraud_status=1):
        self.payment_status = payment_status
        self.tamper = tamper
        self.card = card
        self.fraud_status = fraud_status
        self.paid = set()
        self.detail_calls = []

    def detail(self, reference: str) -> dict:
        if reference not in self.paid:
            return {"status": "failure", "errorCode": "5115", "errorMessage": "Ödeme bulunamadı"}
        values = ["payment-777", "TRY", reference, reference, "599", "599"]
        card = {
            "cardType": "CREDIT_CARD",
            "cardAssociation": "VISA",
            "binNumber": BIN,
            "lastFourDigits": CARD_DIGITS,
        } if self.card else {}
        return {
            **card,
            "status": "success",
            "paymentStatus": self.payment_status,
            "fraudStatus": self.fraud_status,
            "paymentId": "payment-777",
            "currency": "TRY",
            "basketId": reference,
            "conversationId": reference,
            "paidPrice": "599.00" if not self.tamper else "1.00",
            "price": "599.0",
            "signature": _signature(values),
        }

    def __call__(self, url, *, content, headers, timeout):
        payload = json.loads(content)
        reference = payload["conversationId"]
        if url.endswith(payments.IYZICO_INITIALIZE_PATH):
            return _Response({
                "status": "success",
                "conversationId": reference,
                "token": TOKEN,
                "paymentPageUrl": f"https://api.iyzipay.com/checkoutform/{TOKEN}",
                "signature": _signature([reference, TOKEN]),
            })
        if url.endswith(payments.IYZICO_PAYMENT_DETAIL_PATH):
            assert payload["paymentConversationId"] == reference
            self.detail_calls.append(reference)
            return _Response(self.detail(reference))
        assert url.endswith(payments.IYZICO_RETRIEVE_PATH)
        values = ["SUCCESS", "payment-777", "TRY", reference, reference, "599", "599", TOKEN]
        return _Response({
            "status": "success", "paymentStatus": "SUCCESS", "paymentId": "payment-777",
            "currency": "TRY", "basketId": reference, "conversationId": reference,
            "paidPrice": "599.00", "price": "599.0", "token": TOKEN,
            "cardType": "CREDIT_CARD", "cardAssociation": "VISA", "lastFourDigits": CARD_DIGITS,
            "signature": _signature(values),
        })


def _paid_checkout_without_callback(monkeypatch, fake: FakeIyzico) -> tuple[TestClient, str, str]:
    """A buyer starts checkout, pays, and closes the tab: no callback, no webhook."""
    _configure(monkeypatch)
    monkeypatch.setattr(payments.httpx, "post", fake)
    created = register_user(
        f"reconcile-{uuid.uuid4()}@example.com", "Strong-test-password1", "Ada", "Lovelace",
        phone="+905551112233", country_code="TR",
    )
    session = verify_email(created["verification_token"])["token"]
    client = TestClient(app)
    checkout = client.post(
        "/billing/checkout",
        headers={"Authorization": f"Bearer {session}", "X-Forwarded-For": "203.0.113.70"},
        json={
            "plan_code": "plus", "interval": "monthly", "currency": "TRY", "payment_method": "card",
            "billing_address": "Örnek Mahallesi No 7", "billing_city": "İzmir",
            "billing_zip_code": "35000", "phone": "+905551112233", "language": "tr",
            "terms_accepted": True, "early_performance_requested": True,
        },
    )
    assert checkout.status_code == 200, checkout.text
    reference = checkout.json()["order"]["reference"]
    fake.paid.add(reference)
    return client, session, reference


def _order(reference: str):
    with ENGINE.connect() as connection:
        return connection.execute(select(PAYMENT_ORDERS).where(PAYMENT_ORDERS.c.reference == reference)).one()


def _subscription_count(user_id: str) -> int:
    with ENGINE.connect() as connection:
        return connection.execute(
            select(func.count()).select_from(SUBSCRIPTIONS).where(SUBSCRIPTIONS.c.user_id == user_id)
        ).scalar_one()


def test_paid_order_without_callback_is_settled_once(monkeypatch, caplog):
    fake = FakeIyzico()
    client, session, reference = _paid_checkout_without_callback(monkeypatch, fake)
    user_id = _order(reference).user_id
    later = utcnow() + timedelta(minutes=20)

    with caplog.at_level(logging.INFO, logger="lecturesift.payments"):
        first = payments.reconcile_iyzico_orders(later)
    assert reference in fake.detail_calls and first["settled"] >= 1
    assert _order(reference).status == "paid"
    assert _order(reference).provider_amount_minor == 59900
    account = client.get("/billing/me", headers={"Authorization": f"Bearer {session}"}).json()["account"]
    assert account["plan"]["code"] == "plus"
    assert _subscription_count(user_id) == 1

    # A second run, a late browser callback and a late webhook never activate again.
    fake.detail_calls.clear()
    payments.reconcile_iyzico_orders(later)
    assert reference not in fake.detail_calls
    late = client.post(f"/billing/iyzico/callback?order={reference}", data={"token": TOKEN}, follow_redirects=False)
    assert late.status_code == 303
    assert _order(reference).status == "paid"
    assert _subscription_count(user_id) == 1

    for secret in (TOKEN, CARD_DIGITS, BIN, "live-secret-key", "live-api-key"):
        assert secret not in caplog.text
    assert f"reference={reference}" in caplog.text


@pytest.mark.parametrize("payment_status", ["FAILURE", "INIT_THREEDS", "CALLBACK_THREEDS"])
def test_unpaid_or_unfinished_payment_is_left_for_the_callback(monkeypatch, payment_status):
    fake = FakeIyzico(payment_status)
    _, _, reference = _paid_checkout_without_callback(monkeypatch, fake)
    summary = payments.reconcile_iyzico_orders(utcnow() + timedelta(minutes=20))
    assert reference in fake.detail_calls and summary["unpaid"] >= 1
    order = _order(reference)
    assert order.status == "created" and order.failure_code is None
    assert _subscription_count(order.user_id) == 0


def test_paid_order_without_card_evidence_is_reported_not_settled(monkeypatch, caplog):
    # A protected bank transfer has no card fields, and Retrieve Payment does
    # not otherwise prove the method: only the signed webhook may settle it.
    fake = FakeIyzico(card=False)
    _, _, reference = _paid_checkout_without_callback(monkeypatch, fake)
    intent = _order(reference).provider
    with caplog.at_level(logging.INFO, logger="lecturesift.payments"):
        summary = payments.reconcile_iyzico_orders(utcnow() + timedelta(minutes=20))
    assert reference in fake.detail_calls and summary["unverified"] >= 1
    order = _order(reference)
    assert order.status == "created" and order.provider == intent
    assert _subscription_count(order.user_id) == 0
    assert f"without card evidence; it needs manual review reference={reference}" in caplog.text


@pytest.mark.parametrize("fraud_status", [0, -1, None])
def test_payment_under_fraud_review_stays_pending_until_approved(monkeypatch, fraud_status):
    fake = FakeIyzico(fraud_status=fraud_status)
    _, _, reference = _paid_checkout_without_callback(monkeypatch, fake)
    later = utcnow() + timedelta(minutes=20)
    summary = payments.reconcile_iyzico_orders(later)
    assert reference in fake.detail_calls and summary["pending"] >= 1
    order = _order(reference)
    assert order.status == "pending" and _subscription_count(order.user_id) == 0

    # Still in the reconciliation set: once iyzico approves it, the next run settles it.
    fake.fraud_status = 1
    fake.detail_calls.clear()
    assert payments.reconcile_iyzico_orders(later)["settled"] >= 1
    assert reference in fake.detail_calls
    assert _order(reference).status == "paid" and _subscription_count(order.user_id) == 1


def test_order_without_any_payment_is_left_untouched(monkeypatch):
    fake = FakeIyzico()
    _, _, reference = _paid_checkout_without_callback(monkeypatch, fake)
    fake.paid.discard(reference)
    payments.reconcile_iyzico_orders(utcnow() + timedelta(minutes=20))
    assert reference in fake.detail_calls
    assert _order(reference).status == "created"


def test_tampered_provider_answer_never_settles_and_logs_no_card_data(monkeypatch, caplog):
    fake = FakeIyzico(tamper=True)
    _, _, reference = _paid_checkout_without_callback(monkeypatch, fake)
    with caplog.at_level(logging.INFO, logger="lecturesift.payments"):
        summary = payments.reconcile_iyzico_orders(utcnow() + timedelta(minutes=20))
    assert summary["errors"] >= 1
    order = _order(reference)
    assert order.status == "created" and _subscription_count(order.user_id) == 0
    assert f"reconciliation failed reference={reference}" in caplog.text
    for secret in (TOKEN, CARD_DIGITS, BIN, "live-secret-key", "1.00"):
        assert secret not in caplog.text


def test_lookback_window_is_bounded(monkeypatch):
    fake = FakeIyzico()
    _, _, reference = _paid_checkout_without_callback(monkeypatch, fake)
    created_at = _order(reference).created_at

    def window(now):
        return unsettled_iyzico_order_references(
            created_after=now - payments.IYZICO_RECONCILE_LOOKBACK,
            created_before=now - payments.IYZICO_RECONCILE_MIN_AGE,
            limit=10_000,
        )

    assert reference in window(utcnow() + timedelta(minutes=20))
    # Too fresh: the browser callback and webhook still have their chance.
    assert reference not in window(utcnow() + timedelta(minutes=5))
    # Older than the 48-hour look-back.
    assert reference not in window(utcnow() + timedelta(hours=49))
    assert created_at is not None


def test_pending_fraud_review_stays_eligible_past_the_created_lookback(monkeypatch):
    fake = FakeIyzico(fraud_status=0)
    _, _, reference = _paid_checkout_without_callback(monkeypatch, fake)
    assert payments.reconcile_iyzico_orders(utcnow() + timedelta(minutes=20))["pending"] >= 1
    assert _order(reference).status == "pending"

    # Approved only after three days: a pending order is still asked about.
    fake.fraud_status = 1
    fake.detail_calls.clear()
    assert payments.reconcile_iyzico_orders(utcnow() + timedelta(days=3))["settled"] >= 1
    assert reference in fake.detail_calls
    assert _order(reference).status == "paid" and _subscription_count(_order(reference).user_id) == 1


def test_pending_lookback_is_bounded_too(monkeypatch):
    fake = FakeIyzico(fraud_status=0)
    _, _, reference = _paid_checkout_without_callback(monkeypatch, fake)
    payments.reconcile_iyzico_orders(utcnow() + timedelta(minutes=20))
    assert _order(reference).status == "pending"
    fake.detail_calls.clear()
    payments.reconcile_iyzico_orders(utcnow() + timedelta(days=15))
    assert reference not in fake.detail_calls


def test_reconciliation_is_off_without_iyzico_or_during_maintenance(monkeypatch):
    fake = FakeIyzico()
    _, _, reference = _paid_checkout_without_callback(monkeypatch, fake)
    later = utcnow() + timedelta(minutes=20)
    monkeypatch.setattr(config, "current_maintenance_mode", lambda: "drain")
    assert payments.reconcile_iyzico_orders(later)["disabled"] == "maintenance"
    monkeypatch.setattr(config, "current_maintenance_mode", lambda: "off")
    monkeypatch.setattr(config, "IYZICO_SECRET_KEY", "")
    assert payments.reconcile_iyzico_orders(later)["disabled"] == "unconfigured"
    assert fake.detail_calls == [] and _order(reference).status == "created"
