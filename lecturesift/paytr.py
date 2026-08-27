"""PayTR iFrame checkout, callback validation, and refund adapter."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Any

import httpx

from . import config


class PayTRConfigurationError(RuntimeError):
    pass


class PayTRRequestError(RuntimeError):
    def __init__(self, message: str, *, code: str = "provider_error"):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class PayTRCheckout:
    token: str
    iframe_url: str
    merchant_oid: str


def configured() -> bool:
    return all((config.PAYTR_MERCHANT_ID, config.PAYTR_MERCHANT_KEY, config.PAYTR_MERCHANT_SALT))


def _require_configuration() -> None:
    if not configured():
        raise PayTRConfigurationError("PayTR mağaza bilgileri henüz yapılandırılmamış.")


def _token(message: str) -> str:
    digest = hmac.new(config.PAYTR_MERCHANT_KEY.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).digest()
    return base64.b64encode(digest).decode("ascii")


def _basket(name: str, amount_minor: int) -> str:
    amount = f"{max(0, int(amount_minor)) / 100:.2f}"
    raw = json.dumps([[name[:120], amount, 1]], ensure_ascii=False, separators=(",", ":"))
    return base64.b64encode(raw.encode("utf-8")).decode("ascii")


def create_iframe_checkout(*, merchant_oid: str, user_ip: str, email: str, payment_amount: int, currency: str, product_name: str, user_name: str, user_address: str, user_phone: str, language: str = "tr") -> PayTRCheckout:
    _require_configuration()
    safe_oid = "".join(char for char in merchant_oid if char.isalnum())[:64]
    if not safe_oid:
        raise PayTRRequestError("Geçerli ödeme referansı üretilemedi.", code="invalid_order")
    safe_email = str(email or "").strip()[:100]
    safe_ip = str(user_ip or "").strip()[:39] or "127.0.0.1"
    selected_currency = str(currency or "TRY").upper()
    if selected_currency not in {"TRY", "TL", "USD", "EUR", "GBP", "RUB"}:
        raise PayTRRequestError("PayTR bu para birimini desteklemiyor.", code="currency")
    user_basket = _basket(product_name, payment_amount)
    no_installment = "1"
    max_installment = "0"
    test_mode = "1" if config.PAYTR_TEST_MODE else "0"
    hash_str = config.PAYTR_MERCHANT_ID + safe_ip + safe_oid + safe_email + str(int(payment_amount)) + user_basket + no_installment + max_installment + selected_currency + test_mode + config.PAYTR_MERCHANT_SALT
    payload = {
        "merchant_id": config.PAYTR_MERCHANT_ID,
        "user_ip": safe_ip,
        "merchant_oid": safe_oid,
        "email": safe_email,
        "payment_amount": str(int(payment_amount)),
        "paytr_token": _token(hash_str),
        "user_basket": user_basket,
        "debug_on": "1" if config.PAYTR_DEBUG else "0",
        "no_installment": no_installment,
        "max_installment": max_installment,
        "user_name": (user_name or "LectureSift Kullanıcısı")[:60],
        "user_address": (user_address or "Dijital hizmet")[:400],
        "user_phone": (user_phone or "0000000000")[:20],
        "merchant_ok_url": f"{config.FRONTEND_BASE_URL}/payment-return.html?status=success&order={safe_oid}",
        "merchant_fail_url": f"{config.FRONTEND_BASE_URL}/payment-return.html?status=failed&order={safe_oid}",
        "timeout_limit": str(max(5, min(int(config.PAYTR_TIMEOUT_MINUTES), 60))),
        "currency": selected_currency,
        "test_mode": test_mode,
        "lang": "tr" if str(language).casefold().startswith("tr") else "en",
    }
    try:
        with httpx.Client(timeout=25.0, follow_redirects=False) as client:
            response = client.post(config.PAYTR_TOKEN_URL, data=payload)
            response.raise_for_status()
            body = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise PayTRRequestError("PayTR ödeme ekranı şu anda başlatılamadı.", code="network_or_invalid_response") from exc
    if body.get("status") != "success" or not body.get("token"):
        reason = str(body.get("reason") or body.get("err_no") or "token_failed")
        safe_reason = "".join(char for char in reason if char.isalnum() or char in "_-")[:50]
        raise PayTRRequestError("PayTR ödeme ekranı başlatılamadı.", code="token_" + safe_reason)
    token = str(body["token"])
    return PayTRCheckout(token=token, iframe_url=f"{config.PAYTR_IFRAME_BASE_URL}/{token}", merchant_oid=safe_oid)


def validate_callback(values: dict[str, Any]) -> dict[str, Any]:
    _require_configuration()
    merchant_oid = str(values.get("merchant_oid") or "")
    status = str(values.get("status") or "")
    total_amount = str(values.get("total_amount") or "")
    received_hash = str(values.get("hash") or "")
    expected = _token(merchant_oid + config.PAYTR_MERCHANT_SALT + status + total_amount)
    if not merchant_oid or not status or not total_amount or not hmac.compare_digest(received_hash, expected):
        raise PayTRRequestError("PayTR callback doğrulaması başarısız.", code="callback_hash")
    try:
        amount_minor = int(total_amount)
    except ValueError as exc:
        raise PayTRRequestError("PayTR callback tutarı geçersiz.", code="callback_amount") from exc
    return {
        "merchant_oid": merchant_oid,
        "status": status,
        "amount_minor": amount_minor,
        "provider_reference": str(values.get("payment_type") or values.get("payment_amount") or values.get("failed_code") or "")[:120],
        "failure_code": str(values.get("failed_code") or "")[:80],
        "failure_message": str(values.get("failed_message") or "")[:240],
        "event_identity": "|".join((merchant_oid, status, total_amount, received_hash)),
    }


def request_refund(*, merchant_oid: str, amount_minor: int, reference_no: str = "") -> dict[str, Any]:
    _require_configuration()
    safe_oid = "".join(char for char in merchant_oid if char.isalnum())[:64]
    return_amount = f"{max(0, int(amount_minor)) / 100:.2f}"
    hash_str = config.PAYTR_MERCHANT_ID + safe_oid + return_amount + config.PAYTR_MERCHANT_SALT
    payload = {
        "merchant_id": config.PAYTR_MERCHANT_ID,
        "merchant_oid": safe_oid,
        "return_amount": return_amount,
        "paytr_token": _token(hash_str),
    }
    if reference_no:
        payload["reference_no"] = "".join(char for char in reference_no if char.isalnum())[:64]
    try:
        with httpx.Client(timeout=95.0, follow_redirects=False) as client:
            response = client.post(config.PAYTR_REFUND_URL, data=payload)
            response.raise_for_status()
            body = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise PayTRRequestError("PayTR iade servisine ulaşılamadı.", code="refund_network") from exc
    if body.get("status") != "success":
        code = str(body.get("err_no") or "refund_failed")
        raise PayTRRequestError("PayTR iade talebini kabul etmedi.", code="refund_" + "".join(char for char in code if char.isalnum())[:30])
    return {
        "status": "success",
        "merchant_oid": str(body.get("merchant_oid") or safe_oid),
        "return_amount": str(body.get("return_amount") or return_amount),
        "reference_no": str(body.get("reference_no") or reference_no),
        "is_test": str(body.get("is_test") or ""),
    }


def public_status() -> dict[str, Any]:
    return {
        "provider": "paytr",
        "configured": configured(),
        "test_mode": bool(config.PAYTR_TEST_MODE),
        "iframe_checkout": True,
        "callback": True,
        "refund": True,
        "automatic_renewal": bool(config.PAYTR_RECURRING_ENABLED),
        "automatic_renewal_note": "enabled" if config.PAYTR_RECURRING_ENABLED else "PayTR kart saklama/tekrarlayan ödeme yetkisi gerekli",
    }
