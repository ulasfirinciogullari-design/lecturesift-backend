"""Hosted payment adapters with signed, idempotent callbacks.

Provider credentials are read only from runtime environment variables. Card
details never pass through LectureSift servers.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from decimal import Decimal
from urllib.parse import urlparse

import httpx

from . import config
from .billing_service import (
    adopt_payment_order_token_digest_from_verified_notification,
    BillingConfigurationError,
    BillingError,
    bind_payment_order_token_digest,
    complete_payment_order,
    commerce_identity,
    create_payment_order,
    mark_payment_order_pending,
    mark_payment_order_token_failed,
    payment_order,
    payment_order_token_digest,
    record_payment_consent,
)


PAYTR_TOKEN_URL = "https://www.paytr.com/odeme/api/get-token"
PAYTR_CHECKOUT_BASE_URL = "https://www.paytr.com/odeme/guvenli"
PAYTR_CURRENCIES = {"TRY": "TL", "USD": "USD", "EUR": "EUR", "GBP": "GBP"}
IYZICO_INITIALIZE_PATH = "/payment/iyzipos/checkoutform/initialize/auth/ecom"
IYZICO_RETRIEVE_PATH = "/payment/iyzipos/checkoutform/auth/ecom/detail"
IYZICO_CURRENCIES = ("TRY", "USD", "EUR", "GBP", "NOK", "CHF")
IYZICO_ASYNC_PAYMENT_STATUSES = {
    "INIT_BANK_TRANSFER",
    "INIT_CREDIT",
    "PENDING_CREDIT",
    "INIT_APM",
    "INIT_THREEDS",
    "CALLBACK_THREEDS",
    "BKM_POS_SELECTED",
    "INIT_CONTACTLESS",
}
IYZICO_HPP_WEBHOOK_EVENTS = {
    "CHECKOUT_FORM_AUTH",
    "BANK_TRANSFER_AUTH",
    "BKM_AUTH",
    "BALANCE",
    "CONTACTLESS_AUTH",
    "CONTACTLESS_REFUND",
    "CREDIT_PAYMENT_AUTH",
    "CREDIT_PAYMENT_PENDING",
    "CREDIT_PAYMENT_INIT",
    "PWI_TKN_FUND",
    "PWI_TKN_AUTH",
    "PWI_TKN_THREEDS_AUTH",
}
IYZICO_BASE_URLS = {
    "https://api.iyzipay.com",
    "https://sandbox-api.iyzipay.com",
}
IYZICO_PUBLIC_FAILURE_MESSAGES = {
    # iyzico documents 10208 as INVALID_MERCHANT_OR_SP: the live merchant's
    # category/MCC assignment was rejected.  It is not a signal that the
    # buyer entered a bad card number, so give the buyer an actionable and
    # non-blaming message while retaining the provider code on the order for
    # merchant support.
    "10208": (
        "iyzico, mağazanın kategori kodunu doğrulayamadı. Bu sipariş için plan etkinleşmedi; "
        "sipariş numarasıyla LectureSift desteğine başvurabilirsin."
    ),
}


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


def _iyzico_base_url() -> str:
    selected = config.IYZICO_BASE_URL.rstrip("/")
    if selected not in IYZICO_BASE_URLS:
        raise BillingConfigurationError("Geçersiz iyzico API adresi yapılandırması.")
    return selected


def iyzico_configured() -> bool:
    return bool(
        config.IYZICO_API_KEY
        and config.IYZICO_SECRET_KEY
        and len(config.PAYMENT_TOKEN_BINDING_SECRET) >= 32
    )


def iyzico_public_status() -> dict:
    configured = iyzico_configured()
    sandbox = config.IYZICO_BASE_URL.rstrip("/") == "https://sandbox-api.iyzipay.com"
    capabilities = [
        "cards",
        "foreign_cards",
        "one_time",
        "monthly",
        "annual",
        "3ds",
        "signed_webhook",
    ]
    # A configured iyzico account does not imply that protected bank transfer
    # was enabled for the merchant. Expose it only after iyzico confirms that
    # separate activation and the runtime flag is deliberately enabled.
    if configured and config.IYZICO_BANK_TRANSFER_ENABLED:
        capabilities.append("bank_transfer")
    return {
        "code": "iyzico",
        "configured": configured,
        "status": "test_mode" if configured and sandbox else ("active" if configured else "pending_credentials"),
        "currencies": list(IYZICO_CURRENCIES),
        "capabilities": capabilities,
        "checkout": "hosted_redirect",
        "recurring": False,
        "webhook_signature": {
            "required": True,
            "version": "v3",
            "header": "X-IYZ-SIGNATURE-V3",
            "algorithm": "HMAC-SHA256",
        },
    }


def preferred_card_provider() -> str:
    if iyzico_configured():
        return "iyzico"
    if paytr_configured():
        return "paytr"
    raise BillingConfigurationError("Kartlı ödeme sağlayıcısı henüz etkinleştirilmemiş.")


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


def _clean_address(value: str) -> str:
    selected = " ".join(value.strip().split())
    if len(selected) < 5 or len(selected) > 400:
        raise BillingError("Kartlı ödeme için 5 ile 400 karakter arasında bir fatura adresi gir.")
    return selected


def _clean_location(value: str, label: str, *, maximum: int) -> str:
    selected = " ".join(value.strip().split())
    if len(selected) < 2 or len(selected) > maximum:
        raise BillingError(f"Kartlı ödeme için geçerli bir {label} gir.")
    return selected


def _clean_name(value: str, label: str) -> str:
    selected = " ".join(value.strip().split())
    if len(selected) < 2 or len(selected) > 80:
        raise BillingError(f"Kartlı ödeme için {label} 2 ile 80 karakter arasında olmalı.")
    return selected


def _amount_string(amount_minor: int) -> str:
    return f"{Decimal(int(amount_minor)) / Decimal(100):.2f}"


def _strip_price_zeros(value: object) -> str:
    try:
        normalized = format(Decimal(str(value)), "f")
    except Exception as exc:
        raise PaymentProviderError("iyzico geçersiz ödeme tutarı döndürdü.") from exc
    if "." in normalized:
        normalized = normalized.rstrip("0").rstrip(".")
    return normalized or "0"


def _iyzico_response_signature(values: list[object]) -> str:
    message = ":".join(str(value) for value in values)
    return hmac.new(
        config.IYZICO_SECRET_KEY.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _iyzico_token_digest_with_key(order_reference: str, token: str, key: str) -> str:
    """Create a non-replayable, order-scoped digest of a checkout token."""
    message = f"lecturesift:iyzico:checkout-token:v1:{order_reference}:{token}"
    return hmac.new(
        key.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _iyzico_token_binding_keys() -> tuple[str, ...]:
    active = config.PAYMENT_TOKEN_BINDING_SECRET
    legacy = config.PAYMENT_TOKEN_BINDING_LEGACY_SECRET
    if len(active) < 32:
        raise BillingConfigurationError(
            "Ödeme oturumu bağlama anahtarı en az 32 karakter olmalı."
        )
    if legacy and len(legacy) < 32:
        raise BillingConfigurationError(
            "Eski ödeme oturumu bağlama anahtarı en az 32 karakter olmalı."
        )
    return (active,) if not legacy or hmac.compare_digest(active, legacy) else (active, legacy)


def _iyzico_token_digest(order_reference: str, token: str) -> str:
    return _iyzico_token_digest_with_key(
        order_reference,
        token,
        _iyzico_token_binding_keys()[0],
    )


def _require_iyzico_token_match(order_reference: str, token: str) -> None:
    """Fail before contacting iyzico unless this token initialized the order."""
    stored_digest = payment_order_token_digest(order_reference, "iyzico")
    # Check every configured key without short-circuiting. During a deliberate
    # rotation the previous key remains accepted only for the maximum pending
    # transfer window, while all new bindings use the active key.
    matched = 0
    for key in _iyzico_token_binding_keys():
        candidate_digest = _iyzico_token_digest_with_key(order_reference, token, key)
        matched |= int(hmac.compare_digest(stored_digest, candidate_digest))
    if matched == 0:
        raise PaymentProviderError("Ödeme oturumu siparişle eşleşmiyor.")


def _verify_iyzico_response_signature(body: dict, values: list[object]) -> None:
    signature = str(body.get("signature") or "")
    expected = _iyzico_response_signature(values)
    if not signature or not hmac.compare_digest(expected, signature):
        raise PaymentProviderError("Geçersiz iyzico yanıt imzası.")


def _verify_iyzico_hpp_webhook_signature(payload: dict, signature: str) -> None:
    """Verify iyzico's X-IYZ-SIGNATURE-V3 hosted-page notification."""
    event_type = str(payload.get("iyziEventType") or "")
    payment_id = str(payload.get("iyziPaymentId") or "")
    token = str(payload.get("token") or "")
    conversation_id = str(payload.get("paymentConversationId") or "")
    raw_status = str(payload.get("status") or "")
    if (
        event_type not in IYZICO_HPP_WEBHOOK_EVENTS
        or not payment_id
        or not token
        or not conversation_id
        or not raw_status
        or len(token) > 200
        or len(conversation_id) > 64
    ):
        raise PaymentProviderError("Geçersiz iyzico webhook bildirimi.")
    message = (
        f"{config.IYZICO_SECRET_KEY}{event_type}{payment_id}"
        f"{token}{conversation_id}{raw_status}"
    )
    expected = hmac.new(
        config.IYZICO_SECRET_KEY.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not signature or not hmac.compare_digest(expected, signature.strip().lower()):
        raise PaymentProviderError("Geçersiz iyzico webhook imzası.")


def _iyzico_headers(path: str, raw_body: str) -> dict[str, str]:
    random_key = f"{int(time.time() * 1000)}{secrets.randbelow(1_000_000_000):09d}"
    signature = hmac.new(
        config.IYZICO_SECRET_KEY.encode("utf-8"),
        f"{random_key}{path}{raw_body}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    authorization = base64.b64encode(
        (
            f"apiKey:{config.IYZICO_API_KEY}&randomKey:{random_key}"
            f"&signature:{signature}"
        ).encode("utf-8")
    ).decode("ascii")
    return {
        "Authorization": f"IYZWSv2 {authorization}",
        "x-iyzi-rnd": random_key,
        "Content-Type": "application/json",
    }


def _iyzico_post(path: str, payload: dict) -> dict:
    raw_body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    try:
        response = httpx.post(
            f"{_iyzico_base_url()}{path}",
            content=raw_body.encode("utf-8"),
            headers=_iyzico_headers(path, raw_body),
            timeout=20.0,
        )
        response.raise_for_status()
        body = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise PaymentProviderError("iyzico ödeme hizmetine şu anda ulaşılamıyor.") from exc
    if not isinstance(body, dict):
        raise PaymentProviderError("iyzico geçersiz bir yanıt döndürdü.")
    return body


def _iyzico_failure(body: dict) -> tuple[str, str]:
    """Return bounded, public-safe provider failure details for an order."""
    code = str(
        body.get("errorCode")
        or body.get("errorGroup")
        or "iyzico_failure"
    ).strip()[:32]
    provider_message = str(
        body.get("errorMessage")
        or "Ödeme banka veya iyzico tarafından onaylanmadı."
    ).strip()
    public_message = IYZICO_PUBLIC_FAILURE_MESSAGES.get(code, provider_message)
    return code or "iyzico_failure", public_message[:240]


def create_iyzico_checkout(
    user: dict,
    *,
    plan_code: str,
    interval: str,
    currency: str,
    user_ip: str,
    first_name: str,
    last_name: str,
    billing_address: str,
    billing_city: str,
    billing_zip_code: str,
    phone: str,
    language: str,
    terms_accepted: bool,
    early_performance_requested: bool,
    user_agent: str,
) -> dict:
    if not iyzico_configured():
        raise BillingConfigurationError("iyzico canlı ödeme anahtarları henüz etkinleştirilmemiş.")
    if not commerce_identity()["configured"]:
        raise BillingConfigurationError("Satıcı/sağlayıcı kimliği ve iletişim bilgileri tamamlanmadan ödeme açılamaz.")
    if not config.PUBLIC_BASE_URL.startswith("https://"):
        raise BillingConfigurationError("iyzico geri dönüş adresi güvenli HTTPS olarak yapılandırılmamış.")
    if not terms_accepted or not early_performance_requested:
        raise BillingError("Ödeme öncesi bilgilendirmeyi ve hizmetin hemen başlamasını açıkça onaylamalısın.")

    selected_currency = currency.strip().upper()
    if selected_currency not in IYZICO_CURRENCIES:
        raise BillingError("iyzico için TRY, USD, EUR, GBP, NOK veya CHF seç.")
    selected_address = _clean_address(billing_address)
    selected_city = _clean_location(billing_city, "şehir", maximum=80)
    selected_zip = _clean_location(billing_zip_code, "posta kodu", maximum=20)
    selected_phone = _phone(phone or user.get("phone") or "")
    selected_first_name = _clean_name(first_name or user.get("first_name") or "", "ad")
    selected_last_name = _clean_name(last_name or user.get("last_name") or "", "soyad")
    selected_ip = user_ip.strip()[:39]
    if not selected_ip:
        raise BillingError("Ödeme isteği için kullanıcı IP adresi alınamadı.")
    order = create_payment_order(user["id"], "iyzico", plan_code, interval, selected_currency)
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
    price = _amount_string(order["amount_minor"])
    contact_name = f"{selected_first_name} {selected_last_name}"[:160]
    country = (user.get("country_code") or "TR").strip().upper()[:2]
    callback_url = f"{config.PUBLIC_BASE_URL}/billing/iyzico/callback?order={reference}"
    payload = {
        "locale": "tr" if language == "tr" else "en",
        "conversationId": reference,
        "price": price,
        "paidPrice": price,
        "currency": selected_currency,
        "basketId": reference,
        "paymentGroup": "PRODUCT",
        "callbackUrl": callback_url,
        "enabledInstallments": [1],
        "buyer": {
            "id": user["id"],
            "name": selected_first_name,
            "surname": selected_last_name,
            # iyzico's own official integrations use this non-identifying value
            # when a merchant does not collect a Turkish national ID.
            "identityNumber": "11111111111",
            "email": user["email"][:320],
            "gsmNumber": selected_phone,
            "registrationAddress": selected_address,
            "ip": selected_ip,
            "city": selected_city,
            "country": country,
            "zipCode": selected_zip,
        },
        "billingAddress": {
            "address": selected_address,
            "zipCode": selected_zip,
            "contactName": contact_name,
            "city": selected_city,
            "country": country,
        },
        "basketItems": [{
            "id": reference,
            "price": price,
            "name": f"LectureSift {plan_code}"[:120],
            "category1": "Digital Education",
            "itemType": "VIRTUAL",
        }],
    }
    try:
        body = _iyzico_post(IYZICO_INITIALIZE_PATH, payload)
        if body.get("status") != "success":
            raise PaymentProviderError("iyzico ödeme formunu başlatamadı. Bilgilerini kontrol edip tekrar dene.")
        token = str(body.get("token") or "").strip()
        conversation_id = str(body.get("conversationId") or "")
        checkout_url = str(body.get("paymentPageUrl") or "")
        _verify_iyzico_response_signature(body, [conversation_id, token])
        parsed = urlparse(checkout_url)
        if (
            conversation_id != reference
            or not token
            or len(token) > 200
            or parsed.scheme != "https"
            or not (parsed.hostname == "iyzipay.com" or (parsed.hostname or "").endswith(".iyzipay.com"))
        ):
            raise PaymentProviderError("iyzico ödeme oturumu doğrulanamadı.")
        bind_payment_order_token_digest(
            reference,
            "iyzico",
            _iyzico_token_digest(reference, token),
        )
    except BillingError:
        mark_payment_order_token_failed(reference)
        raise
    return {
        "provider": "iyzico",
        "order": order,
        "checkout_url": checkout_url,
        "display_mode": "redirect",
        "mode": "test" if _iyzico_base_url().startswith("https://sandbox-") else "live",
    }


def process_iyzico_callback(
    *,
    order_reference: str,
    token: str,
    _verified_webhook: bool = False,
) -> dict:
    if not iyzico_configured():
        raise BillingConfigurationError("iyzico canlı ödeme anahtarları henüz etkinleştirilmemiş.")
    order = payment_order(order_reference)
    if order["provider"] != "iyzico":
        raise PaymentProviderError("Ödeme sağlayıcısı siparişle eşleşmiyor.")
    selected_token = token.strip()
    if not selected_token or len(selected_token) > 200:
        raise PaymentProviderError("Geçersiz iyzico ödeme belirteci.")
    # The browser callback is not itself signed. A signed webhook can also be
    # replayed with a valid signature, so both paths must prove that the token
    # is the exact one returned by our initialize call before any retrieve or
    # local state transition occurs.
    _require_iyzico_token_match(order_reference, selected_token)
    body = _iyzico_post(
        IYZICO_RETRIEVE_PATH,
        {"locale": "tr", "conversationId": order_reference, "token": selected_token},
    )
    if body.get("status") != "success":
        # A declined Checkout Form payment is returned as a valid, authenticated
        # retrieve response whose status is ``failure``.  Treat it as a terminal
        # payment result instead of leaving the local order indefinitely in the
        # ``created`` state.  The request itself is signed and sent server-side;
        # when iyzico echoes a conversation id it must still match our order.
        conversation_id = str(body.get("conversationId") or "")
        if (
            (conversation_id and conversation_id != order_reference)
            or (not conversation_id and not _verified_webhook)
        ):
            raise PaymentProviderError("iyzico ödeme sonucu siparişle eşleşmiyor.")
        failure_code, failure_message = _iyzico_failure(body)
        return complete_payment_order(
            order_reference,
            succeeded=False,
            provider_amount_minor=0,
            failure_code=failure_code,
            failure_message=failure_message,
        )
    signature_values = [
        body.get("paymentStatus"),
        body.get("paymentId"),
        body.get("currency"),
        body.get("basketId"),
        body.get("conversationId"),
        _strip_price_zeros(body.get("paidPrice")),
        _strip_price_zeros(body.get("price")),
        body.get("token"),
    ]
    _verify_iyzico_response_signature(body, signature_values)
    expected_price = Decimal(order["amount_minor"]) / Decimal(100)
    try:
        paid_price = Decimal(str(body.get("paidPrice")))
        basket_price = Decimal(str(body.get("price")))
    except Exception as exc:
        raise PaymentProviderError("iyzico geçersiz ödeme tutarı döndürdü.") from exc
    if (
        str(body.get("conversationId") or "") != order_reference
        or str(body.get("basketId") or "") != order_reference
        or str(body.get("token") or "") != selected_token
        or str(body.get("currency") or "").upper() != order["currency"]
        or paid_price != expected_price
        or basket_price != expected_price
    ):
        raise PaymentProviderError("iyzico ödeme sonucu siparişle eşleşmiyor.")
    payment_status = str(body.get("paymentStatus") or "").upper()
    if payment_status in IYZICO_ASYNC_PAYMENT_STATUSES:
        return mark_payment_order_pending(order_reference)
    succeeded = payment_status == "SUCCESS"
    failure_code, failure_message = ("", "") if succeeded else _iyzico_failure(body)
    return complete_payment_order(
        order_reference,
        succeeded=succeeded,
        provider_amount_minor=int(paid_price * 100),
        failure_code=failure_code,
        failure_message=failure_message,
    )


def process_iyzico_webhook(*, payload: dict, signature: str) -> dict:
    """Confirm a signed iyzico HPP event with a server-side retrieve call."""
    if not iyzico_configured():
        raise BillingConfigurationError("iyzico canlı ödeme anahtarları henüz etkinleştirilmemiş.")
    if not isinstance(payload, dict):
        raise PaymentProviderError("Geçersiz iyzico webhook bildirimi.")
    _verify_iyzico_hpp_webhook_signature(payload, signature)
    order_reference = str(payload.get("paymentConversationId") or "")
    selected_token = str(payload.get("token") or "").strip()
    # Compatibility bridge for checkouts initialized before token-binding was
    # deployed. Only the signed provider notification may create this legacy
    # binding; an unsigned browser callback remains strictly fail-closed.
    adopt_payment_order_token_digest_from_verified_notification(
        order_reference,
        "iyzico",
        _iyzico_token_digest(order_reference, selected_token),
    )
    return process_iyzico_callback(
        order_reference=order_reference,
        token=selected_token,
        _verified_webhook=True,
    )


def create_paytr_checkout(
    user: dict,
    *,
    plan_code: str,
    interval: str,
    currency: str,
    user_ip: str,
    first_name: str,
    last_name: str,
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
    selected_address = _clean_address(billing_address)
    selected_phone = _phone(phone or user.get("phone") or "")
    selected_first_name = _clean_name(first_name or user.get("first_name") or "", "ad")
    selected_last_name = _clean_name(last_name or user.get("last_name") or "", "soyad")
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
        "user_name": f"{selected_first_name} {selected_last_name}"[:60],
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
