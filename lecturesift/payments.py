"""PayTR checkout adapter with signed, idempotent payment callbacks."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from decimal import Decimal

import httpx

from . import config
from .billing_service import (
    BillingConfigurationError,
    BillingError,
    complete_payment_order,
    commerce_identity,
    create_payment_order,
    mark_payment_order_token_failed,
    payment_order,
    record_payment_consent,
)


PAYTR_TOKEN_URL = "https://www.paytr.com/odeme/api/get-token"
PAYTR_CHECKOUT_BASE_URL = "https://www.paytr.com/odeme/guvenli"
PAYTR_CURRENCIES = {"TRY": "TL", "USD": "USD", "EUR": "EUR", "GBP": "GBP"}


class PaymentProviderError(BillingError):
    pass


def paytr_configured() -> bool:
    return all((config.PAYTR_MERCHANT_ID, config.PAYTR_MERCHANT_KEY, config.PAYTR_MERCHANT_SALT))


def paytr_public_status() -> dict:
    return {
        "code": "paytr",
        "configured": paytr_configured(),
        "status": "test_mode" if paytr_configured() and config.PAYTR_TEST_MODE else (
            "active" if paytr_configured() else "pending_credentials"
        ),
        "currencies": list(PAYTR_CURRENCIES),
        "capabilities": ["cards", "foreign_cards", "one_time", "monthly", "annual"],
        "checkout": "hosted_iframe",
        "recurring": False,
    }


def _token(message: str) -> str:
    digest = hmac.new(
        config.PAYTR_MERCHANT_KEY.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return base64.b64encode(digest).decode("ascii")


def _phone(value: str) -> str:
    selected = "".join(char for char in value if char.isdigit() or char == "+")[:20]
    if sum(char.isdigit() for char in selected) < 7:
        raise BillingError("Kartlı ödeme için geçerli bir telefon numarası gir.")
    return selected


def create_paytr_checkout(
    user: dict,
    *,
    plan_code: str,
    interval: str,
    currency: str,
    user_ip: str,
    billing_address: str,
    phone: str,
    language: str,
    terms_accepted: bool,
    early_performance_requested: bool,
    user_agent: str,
) -> dict:
    if not paytr_configured():
        raise BillingConfigurationError("PayTR mağaza bilgileri henüz etkinleştirilmemiş.")
    if not commerce_identity()["configured"]:
        raise BillingConfigurationError("Satıcı/sağlayıcı kimliği ve iletişim bilgileri tamamlanmadan ödeme açılamaz.")
    if not terms_accepted or not early_performance_requested:
        raise BillingError("Ödeme öncesi bilgilendirmeyi ve hizmetin hemen başlamasını açıkça onaylamalısın.")
    selected_currency = currency.strip().upper()
    paytr_currency = PAYTR_CURRENCIES.get(selected_currency)
    if not paytr_currency:
        raise BillingError("PayTR bu para birimini desteklemiyor. TRY, USD, EUR veya GBP seç.")
    selected_address = " ".join(billing_address.strip().split())
    if len(selected_address) < 5 or len(selected_address) > 400:
        raise BillingError("Kartlı ödeme için 5 ile 400 karakter arasında bir fatura adresi gir.")
    selected_phone = _phone(phone or user.get("phone") or "")
    selected_ip = user_ip.strip()[:39]
    if not selected_ip:
        raise BillingError("Ödeme isteği için kullanıcı IP adresi alınamadı.")

    order = create_payment_order(user["id"], "paytr", plan_code, interval, selected_currency)
    reference = order["reference"]
    record_payment_consent(
        reference,
        user["id"],
        terms_accepted=terms_accepted,
        early_performance_requested=early_performance_requested,
        language=language,
        client_ip=selected_ip,
        user_agent=user_agent,
    )
    amount = str(int(order["amount_minor"]))
    display_amount = f"{Decimal(order['amount_minor']) / Decimal(100):.2f}"
    basket = base64.b64encode(
        json.dumps(
            [[f"LectureSift {plan_code}", display_amount, 1]],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    ).decode("ascii")
    no_installment = "0"
    max_installment = "0"
    test_mode = "1" if config.PAYTR_TEST_MODE else "0"
    hash_source = (
        f"{config.PAYTR_MERCHANT_ID}{selected_ip}{reference}{user['email']}"
        f"{amount}{basket}{no_installment}{max_installment}{paytr_currency}{test_mode}"
    )
    paytr_token = _token(hash_source + config.PAYTR_MERCHANT_SALT)
    payload = {
        "merchant_id": config.PAYTR_MERCHANT_ID,
        "user_ip": selected_ip,
        "merchant_oid": reference,
        "email": user["email"][:100],
        "payment_amount": amount,
        "paytr_token": paytr_token,
        "user_basket": basket,
        "debug_on": "1" if config.PAYTR_DEBUG else "0",
        "no_installment": no_installment,
        "max_installment": max_installment,
        "user_name": (user.get("name") or user["email"])[:60],
        "user_address": selected_address,
        "user_phone": selected_phone,
        "merchant_ok_url": f"{config.FRONTEND_BASE_URL}/account.html?payment=success&order={reference}",
        "merchant_fail_url": f"{config.FRONTEND_BASE_URL}/plans.html?payment=failed&order={reference}",
        "timeout_limit": "30",
        "currency": paytr_currency,
        "test_mode": test_mode,
        "lang": "tr" if language == "tr" else "en",
    }
    try:
        response = httpx.post(PAYTR_TOKEN_URL, data=payload, timeout=20.0)
        response.raise_for_status()
        body = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        mark_payment_order_token_failed(reference)
        raise PaymentProviderError("Ödeme sağlayıcısına şu anda ulaşılamıyor.") from exc
    if body.get("status") != "success" or not body.get("token"):
        mark_payment_order_token_failed(reference)
        raise PaymentProviderError("Ödeme formu başlatılamadı. Bilgilerini kontrol edip tekrar dene.")
    return {
        "provider": "paytr",
        "order": order,
        "checkout_url": f"{PAYTR_CHECKOUT_BASE_URL}/{body['token']}",
        "mode": "test" if config.PAYTR_TEST_MODE else "live",
    }


def process_paytr_callback(
    *,
    merchant_oid: str,
    status: str,
    total_amount: str,
    payment_amount: str,
    callback_hash: str,
    failed_reason_code: str = "",
    failed_reason_msg: str = "",
) -> dict:
    if not paytr_configured():
        raise BillingConfigurationError("PayTR mağaza bilgileri henüz etkinleştirilmemiş.")
    expected = _token(
        f"{merchant_oid}{config.PAYTR_MERCHANT_SALT}{status}{total_amount}"
    )
    if not hmac.compare_digest(expected, callback_hash):
        raise PaymentProviderError("Geçersiz PayTR bildirim imzası.")
    order = payment_order(merchant_oid)
    try:
        requested_amount = int(payment_amount)
        charged_amount = int(total_amount)
    except (TypeError, ValueError) as exc:
        raise PaymentProviderError("Geçersiz PayTR ödeme tutarı.") from exc
    if requested_amount != int(order["amount_minor"]):
        raise PaymentProviderError("PayTR sipariş tutarı eşleşmiyor.")
    return complete_payment_order(
        merchant_oid,
        succeeded=status == "success",
        provider_amount_minor=charged_amount,
        failure_code=failed_reason_code,
        failure_message=failed_reason_msg,
    )
