"""Order confirmation email: once per paid order, after commit, never blocking payment."""

import base64
import hashlib
import hmac
import uuid
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from lecturesift import billing_service as billing, config, order_notifications as notifications, rollout_service
from lecturesift.app import app
from lecturesift.order_notification_templates import (
    SUPPORTED_LANGUAGES,
    _COPY,
    render_order_confirmation_email,
)


@pytest.fixture
def mail(monkeypatch):
    for name, value in {
        "EMAIL_PROVIDER": "resend",
        "FRONTEND_BASE_URL": "https://lecturesift.com",
        "BILLING_SUPPORT_EMAIL": "billing@example.com",
        "LEGAL_OPERATOR_NAME": "LectureSift Test Satıcı",
        "LEGAL_OPERATOR_ADDRESS": "Test Address 1",
        "LEGAL_OPERATOR_COUNTRY": "TR",
        "LEGAL_OPERATOR_PHONE": "+905551112233",
        "LEGAL_OPERATOR_EMAIL": "billing@example.com",
    }.items():
        monkeypatch.setattr(config, name, value)
    monkeypatch.setattr(notifications, "email_delivery_configured", lambda: True)
    sent = []
    monkeypatch.setattr(notifications, "send_transactional_email",
                        lambda *args, **kwargs: sent.append((args, kwargs)) or "synthetic-mail")
    return sent


def _customer(language: str = "") -> tuple[str, str]:
    email = f"order-{uuid.uuid4()}@example.com"
    created = billing.register_user(email, "Strong-test-password1", "Ayşe", "Customer", country_code="TR")
    billing.verify_email(created["verification_token"])
    user_id = created["user"]["id"]
    if language:
        billing.update_account_preferences(user_id, "TR", language)
    return user_id, email


def _paid(reference: str, amount_minor: int) -> dict:
    return billing.complete_payment_order(reference, succeeded=True, provider_amount_minor=amount_minor)


def _event(reference: str):
    events = rollout_service.ADMIN_ACCOUNT_EVENTS
    with billing.ENGINE.connect() as connection:
        return connection.execute(
            select(events).where(events.c.id == notifications.event_id(reference))
        ).first()


def test_paid_order_is_confirmed_once_and_only_after_the_payment_commits(mail, monkeypatch):
    user_id, email = _customer()
    order = billing.create_payment_order(user_id, "iyzico", "credit", "one_time", "TRY")
    reference = order["reference"]
    observed = []

    def deliver(*args, **kwargs):
        # A separate connection only sees committed rows.
        observed.append((billing.payment_order(reference)["status"],
                         billing.account_status(user_id)["credit_minutes"]))
        mail.append((args, kwargs))
        return "synthetic-mail"

    monkeypatch.setattr(notifications, "send_transactional_email", deliver)
    first = _paid(reference, 9900)
    duplicate = _paid(reference, 9900)

    assert first["status"] == duplicate["status"] == "paid"
    assert observed == [("paid", 200)]
    assert len(mail) == 1
    (recipient, subject, html, text), kwargs = mail[0]
    assert recipient == email
    assert subject == f"LectureSift · Siparişin onaylandı · {reference}"
    for expected in (
        reference, "Merhaba Ayşe,", "Dakika Paketi", "99,00 TRY", "200 dakika",
        "tek seferlik bir satın almadır", "otomatik olarak yenilenmez",
        "Hizmet sağlayıcı bilgileri", "Unvan / ad: LectureSift Test Satıcı", "Açık adres: Test Address 1",
        "https://lecturesift.com/account.html", "https://lecturesift.com/distance-sales",
        "https://lecturesift.com/refund",
    ):
        assert expected in text
        assert expected.split(": ")[-1] in html
    assert kwargs["reply_to"] == "billing@example.com"
    assert kwargs["idempotency_key"] == "order-confirmation/" + hashlib.sha256(reference.encode()).hexdigest()
    event = _event(reference)
    assert event.action == notifications.SENT
    assert event.actor == "system" and event.subject_user_id == user_id
    assert reference in event.summary
    assert billing.account_status(user_id)["credit_minutes"] == 200


def test_fixed_term_package_states_thirty_days_without_renewal_in_english(mail):
    user_id, _ = _customer("en")
    order = billing.create_payment_order(user_id, "paytr", "plus", "monthly", "TRY")
    _paid(order["reference"], order["amount_minor"])

    assert len(mail) == 1
    (_, subject, _, text), _ = mail[0]
    assert subject.startswith("LectureSift · Your order is confirmed · ")
    for expected in (
        "Package: Plus", "Amount: 599.00 TRY", "900 minutes for the 30-day period", "30 days, until ",
        "fixed 30-day package bought with a one-time payment", "does not renew automatically",
        "Service provider details", "Legal name: LectureSift Test Satıcı",
        "https://lecturesift.com/en/account.html", "Distance Sales Agreement",
        "https://lecturesift.com/en/distance-sales", "https://lecturesift.com/en/refund",
    ):
        assert expected in text
    assert "Siparişin" not in text


def test_checkout_language_is_used_when_no_account_language_is_saved(mail):
    user_id, _ = _customer()
    order = billing.create_payment_order(user_id, "iyzico", "credit", "one_time", "EUR")
    billing.record_payment_consent(
        order["reference"], user_id, terms_accepted=True, early_performance_requested=True,
        language="de", client_ip="203.0.113.5", user_agent="pytest",
    )
    _paid(order["reference"], order["amount_minor"])

    (_, subject, _, text), _ = mail[0]
    assert "Deine Bestellung ist bestätigt" in subject
    assert "Minutenpaket" in text and "2,99 EUR" in text and "200 Minuten" in text


def test_failed_payment_sends_no_confirmation(mail):
    user_id, _ = _customer()
    order = billing.create_payment_order(user_id, "iyzico", "credit", "one_time", "TRY")
    result = billing.complete_payment_order(
        order["reference"], succeeded=False, provider_amount_minor=0,
        failure_code="10051", failure_message="Declined",
    )

    assert result["status"] == "failed"
    assert mail == []
    assert _event(order["reference"]) is None
    assert notifications.notify_paid_order(order["reference"]) == "not_paid"
    assert billing.account_status(user_id)["credit_minutes"] == 0


def test_duplicate_paytr_callbacks_send_one_confirmation_with_the_charged_amount(mail, monkeypatch):
    monkeypatch.setattr(config, "PAYTR_MERCHANT_ID", "123456")
    monkeypatch.setattr(config, "PAYTR_MERCHANT_KEY", "merchant-key")
    monkeypatch.setattr(config, "PAYTR_MERCHANT_SALT", "merchant-salt")
    user_id, _ = _customer()
    order = billing.create_payment_order(user_id, "paytr", "credit", "one_time", "TRY")
    # PayTR reports installment fees in total_amount; the card was charged that.
    reference, total = order["reference"], "10500"
    digest = hmac.new(b"merchant-key", f"{reference}merchant-saltsuccess{total}".encode(), hashlib.sha256).digest()
    callback = {"merchant_oid": reference, "status": "success", "total_amount": total,
                "payment_amount": "9900", "hash": base64.b64encode(digest).decode()}
    client = TestClient(app)

    responses = [client.post("/billing/paytr/callback", data=callback) for _ in range(3)]

    assert [(response.status_code, response.text) for response in responses] == [(200, "OK")] * 3
    assert len(mail) == 1
    (_, _, html, text), _ = mail[0]
    assert "Tutar: 105,00 TRY" in text and "105,00 TRY" in html
    assert "99,00" not in text
    assert billing.payment_order(reference)["provider_amount_minor"] == 10500
    assert billing.account_status(user_id)["credit_minutes"] == 200


def test_manual_transfer_approval_confirms_once(mail, monkeypatch):
    monkeypatch.setattr(config, "BILLING_BANK_IBAN", "TR000000000000000000000000")
    monkeypatch.setattr(config, "BILLING_BANK_ACCOUNT_HOLDER", "LectureSift Test")
    user_id, email = _customer()
    order = billing.create_manual_order(user_id, "credit", "one_time")

    billing.approve_manual_order(order["reference"])
    billing.approve_manual_order(order["reference"])

    assert len(mail) == 1
    (recipient, subject, _, text), _ = mail[0]
    assert recipient == email and order["reference"] in subject
    assert "99,00 TRY" in text and "200 dakika" in text
    assert billing.account_status(user_id)["credit_minutes"] == 200


def test_payment_survives_a_failing_provider_and_a_later_callback_retries_once(mail, monkeypatch):
    attempts = []

    def flaky(*args, **kwargs):
        attempts.append((args, kwargs))
        if len(attempts) == 1:
            raise RuntimeError("provider timeout for private@example.com")
        return "synthetic-mail"

    monkeypatch.setattr(notifications, "send_transactional_email", flaky)
    user_id, _ = _customer()
    order = billing.create_payment_order(user_id, "iyzico", "credit", "one_time", "TRY")
    reference = order["reference"]

    assert _paid(reference, 9900)["status"] == "paid"
    assert billing.account_status(user_id)["credit_minutes"] == 200
    failed = _event(reference)
    assert failed.action == notifications.RETRY
    assert "private@example.com" not in failed.summary

    _paid(reference, 9900)
    _paid(reference, 9900)

    assert len(attempts) == 2
    assert attempts[0] == attempts[1]
    assert _event(reference).action == notifications.SENT
    assert billing.account_status(user_id)["credit_minutes"] == 200


def test_ambiguous_smtp_failure_is_never_replayed(mail, monkeypatch):
    monkeypatch.setattr(config, "EMAIL_PROVIDER", "smtp")
    attempts = []

    def broken(*args, **kwargs):
        attempts.append(args)
        raise RuntimeError("connection reset after DATA")

    monkeypatch.setattr(notifications, "send_transactional_email", broken)
    user_id, _ = _customer()
    order = billing.create_payment_order(user_id, "iyzico", "credit", "one_time", "TRY")

    assert _paid(order["reference"], 9900)["status"] == "paid"
    assert _paid(order["reference"], 9900)["status"] == "paid"

    assert len(attempts) == 1
    assert _event(order["reference"]).action == notifications.UNKNOWN


@pytest.mark.parametrize("provider", ["resend", "smtp"])
def test_a_claim_left_pending_by_a_crashed_sender_is_recovered_only_when_stale(mail, monkeypatch, provider):
    monkeypatch.setattr(config, "EMAIL_PROVIDER", provider)
    user_id, email = _customer()
    order = billing.create_payment_order(user_id, "iyzico", "credit", "one_time", "TRY")
    reference = order["reference"]
    monkeypatch.setattr(notifications, "email_delivery_configured", lambda: False)
    _paid(reference, 9900)
    monkeypatch.setattr(notifications, "email_delivery_configured", lambda: True)
    # The process died after committing its claim and before sending.
    assert notifications._claim(rollout_service.ADMIN_ACCOUNT_EVENTS, reference, user_id, email)

    _paid(reference, 9900)
    assert mail == []
    assert notifications.notify_paid_order(reference) == "duplicate"

    events = rollout_service.ADMIN_ACCOUNT_EVENTS
    with billing.ENGINE.begin() as connection:
        connection.execute(update(events).where(events.c.id == notifications.event_id(reference)).values(
            created_at=billing.utcnow() - notifications.STALE_CLAIM - timedelta(minutes=1)))
    _paid(reference, 9900)
    _paid(reference, 9900)

    if provider == "smtp":
        # Without an idempotency key a stale claim may already have been sent.
        assert mail == []
        assert _event(reference).action == notifications.PENDING
        return
    assert len(mail) == 1
    assert mail[0][1]["idempotency_key"] == "order-confirmation/" + hashlib.sha256(reference.encode()).hexdigest()
    assert _event(reference).action == notifications.SENT


def test_a_crashing_notifier_never_fails_the_payment(mail, monkeypatch):
    def crash(*args, **kwargs):
        raise RuntimeError("unexpected")

    user_id, _ = _customer()
    monkeypatch.setattr(notifications, "_prepare", crash)
    first = billing.create_payment_order(user_id, "iyzico", "credit", "one_time", "TRY")
    assert _paid(first["reference"], 9900)["status"] == "paid"

    monkeypatch.setattr(notifications, "notify_paid_order", crash)
    second = billing.create_payment_order(user_id, "iyzico", "credit", "one_time", "TRY")
    assert _paid(second["reference"], 9900)["status"] == "paid"

    assert mail == []
    assert billing.account_status(user_id)["credit_minutes"] == 400


def test_orders_paid_before_the_retry_window_are_not_backfilled(mail, monkeypatch):
    user_id, _ = _customer()
    order = billing.create_payment_order(user_id, "iyzico", "credit", "one_time", "TRY")
    reference = order["reference"]
    monkeypatch.setattr(notifications, "email_delivery_configured", lambda: False)
    _paid(reference, 9900)
    assert _event(reference) is None
    with billing.ENGINE.begin() as connection:
        connection.execute(update(billing.PAYMENT_ORDERS).where(
            billing.PAYMENT_ORDERS.c.reference == reference,
        ).values(updated_at=billing.utcnow() - timedelta(days=2)))
    monkeypatch.setattr(notifications, "email_delivery_configured", lambda: True)

    _paid(reference, 9900)

    assert mail == []
    assert _event(reference) is None
    assert notifications.notify_paid_order(reference) == "historical"


class _EmailHTML(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.links = []
        self.root = {}
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "html":
            self.root = attributes
        if tag == "a":
            self.links.append(attributes["href"])


_PAID_AT = datetime(2026, 10, 19, 11, 32, tzinfo=timezone.utc)
_SELLER = {"configured": True, "operator_name": "Seller Ltd", "address": "Street 1", "country": "TR",
           "phone": "+905551112233", "email": "billing@example.com", "tax_id": None}


def test_every_language_has_the_same_copy_keys():
    keys = set(_COPY["tr"])
    assert set(SUPPORTED_LANGUAGES) == set(_COPY)
    assert all(set(copy) == keys for copy in _COPY.values())


@pytest.mark.parametrize("language", SUPPORTED_LANGUAGES)
def test_all_site_languages_render_order_facts_and_working_links(language):
    mail = render_order_confirmation_email(
        language, reference="LS20261019ABCDEF123456", plan_code="plus", plan_kind="subscription",
        interval="monthly", amount_minor=59900, currency="TRY", purchased_at=_PAID_AT, minutes=900,
        ends_at=_PAID_AT + timedelta(days=30), first_name="Ayşe", seller=_SELLER,
    )
    parsed = _EmailHTML(mail["html"])
    prefix = "" if language == "tr" else f"/{language}"
    assert parsed.root == {"lang": language, "dir": "rtl" if language == "ar" else "ltr"}
    assert parsed.links == [
        f"https://lecturesift.com{prefix}/account.html",
        f"https://lecturesift.com{prefix}/distance-sales",
        f"https://lecturesift.com{prefix}/refund",
        f"https://lecturesift.com{prefix}/contact",
    ]
    for value in ("LS20261019ABCDEF123456", "599", "TRY", "900", "30", "2026-10-19 14:32", "2026-11-18",
                  "Seller Ltd", "Street 1", "Ayşe"):
        assert value in mail["text"]
        assert value in mail["html"]
    assert not any(token in mail["text"] for token in ("{value}", "{days}", "{end}", "{name}", "None"))
    if language != "en":
        assert "does not renew automatically" not in mail["text"]


@pytest.mark.parametrize("locale, expected", [("en-US", "en"), ("PT_br", "pt"), ("zh-CN", "zh"), ("unknown", "tr"), ("", "tr")])
def test_regional_languages_and_safe_default(locale, expected):
    mail = render_order_confirmation_email(
        locale, reference="LS-20261019-ABC123", plan_code="credit", plan_kind="one_time",
        interval="one_time", amount_minor=19900, currency="TRY", purchased_at=_PAID_AT, minutes=180,
    )
    assert _EmailHTML(mail["html"]).root["lang"] == expected


def test_purchase_kinds_describe_what_was_added():
    def text(**values):
        base = {"reference": "LS-20261019-ABC123", "amount_minor": 1000, "currency": "TRY",
                "purchased_at": _PAID_AT}
        return render_order_confirmation_email("en", **base, **values)["text"]

    annual = text(plan_code="pro", plan_kind="subscription", interval="annual", minutes=2000,
                  ends_at=_PAID_AT + timedelta(days=365))
    assert "2000 minutes per month" in annual and "365 days, until 2027-10-19" in annual
    assert "fixed 365-day package" in annual
    pack = text(plan_code="ai_1000", plan_kind="one_time", interval="one_time", minutes=0, assistant_credits=1000)
    assert "Assistant credits (1000)" in pack and "1000 assistant credits" in pack
    assert "one-time purchase, not a subscription" in pack
    ad_free = text(plan_code="ad_free", plan_kind="one_time", interval="one_time", minutes=0)
    assert "Added to your account: Permanent ad-free access" in ad_free
    yen = render_order_confirmation_email(
        "ja", reference="LS-1", plan_code="credit", plan_kind="one_time", interval="one_time",
        amount_minor=750, currency="JPY", purchased_at=_PAID_AT, minutes=180,
    )["text"]
    assert "750 JPY" in yen
    # An early renewal of the same package is queued after the running term.
    starts = _PAID_AT + timedelta(days=3)
    queued = text(plan_code="pro", plan_kind="subscription", interval="monthly", minutes=1500,
                  starts_at=starts, ends_at=starts + timedelta(days=30))
    assert "Starts: 2026-10-22 14:32" in queued and "30 days, until 2026-11-21" in queued
    immediate = text(plan_code="pro", plan_kind="subscription", interval="monthly", minutes=1500,
                     starts_at=_PAID_AT, ends_at=_PAID_AT + timedelta(days=30))
    assert "Starts:" not in immediate


def test_names_and_seller_values_are_escaped_and_missing_identity_is_omitted():
    mail = render_order_confirmation_email(
        "tr", reference="LS-1", plan_code="credit", plan_kind="one_time", interval="one_time",
        amount_minor=19900, currency="TRY", purchased_at=_PAID_AT, minutes=180,
        first_name="<script>alert(1)</script>", seller={**_SELLER, "operator_name": "<b>Seller</b>"},
    )
    assert "<script>" not in mail["html"] and "<b>Seller" not in mail["html"]
    assert "&lt;script&gt;" in mail["html"] and "&lt;b&gt;Seller&lt;/b&gt;" in mail["html"]
    unconfigured = render_order_confirmation_email(
        "tr", reference="LS-1", plan_code="credit", plan_kind="one_time", interval="one_time",
        amount_minor=19900, currency="TRY", purchased_at=_PAID_AT, minutes=180,
        seller={"configured": False, "operator_name": "Hidden"},
    )
    assert "Hizmet sağlayıcı bilgileri" not in unconfigured["text"] and "Hidden" not in unconfigured["text"]
