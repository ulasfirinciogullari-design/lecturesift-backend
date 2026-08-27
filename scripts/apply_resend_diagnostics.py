from pathlib import Path


path = Path("lecturesift/email_auth.py")
text = path.read_text(encoding="utf-8")

class_start = text.index("class EmailDeliveryError")
class_end = text.index("\n\nEMAIL_VERIFICATIONS = Table", class_start)
class_block = '''class EmailDeliveryError(RuntimeError):
    """Raised when Resend did not accept a transactional email."""

    def __init__(
        self,
        message: str,
        *,
        provider_status: int | None = None,
        provider_code: str | None = None,
    ):
        super().__init__(message)
        self.provider_status = provider_status
        self.provider_code = provider_code


class EmailVerificationError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str,
        status_code: int = 400,
        retry_after: int | None = None,
        provider_status: int | None = None,
        provider_code: str | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.retry_after = retry_after
        self.provider_status = provider_status
        self.provider_code = provider_code
'''
text = text[:class_start] + class_block + text[class_end:]

send_start = text.index("def _send_verification_email")
send_end = text.index("\n\ndef _public_verification", send_start)
send_block = '''def _send_verification_email(recipient: str, code: str, language: str, idempotency_key: str) -> str:
    if not email_delivery_available():
        raise EmailDeliveryError(
            "Resend e-posta servisi yapılandırılmamış.",
            provider_code="not_configured",
        )
    subject, text_body, html_body = _email_copy(code, language)
    payload: dict = {
        "from": config.RESEND_FROM_EMAIL.strip(),
        "to": [recipient],
        "subject": subject,
        "text": text_body,
        "html": html_body,
    }
    if config.BILLING_SUPPORT_EMAIL.strip():
        payload["reply_to"] = config.BILLING_SUPPORT_EMAIL.strip()
    headers = {
        "Authorization": f"Bearer {config.RESEND_API_KEY.strip()}",
        "Content-Type": "application/json",
        "User-Agent": f"LectureSift/{config.APP_VERSION}",
        "Idempotency-Key": idempotency_key[:256],
    }
    last_error: EmailDeliveryError | None = None
    for attempt in range(2):
        try:
            response = httpx.post("https://api.resend.com/emails", json=payload, headers=headers, timeout=12.0)
        except httpx.RequestError:
            last_error = EmailDeliveryError(
                "Resend ağına ulaşılamadı.",
                provider_code="network_error",
            )
            if attempt == 0:
                time.sleep(0.4)
                continue
            break
        if response.is_success:
            try:
                return str(response.json().get("id") or "accepted")
            except (AttributeError, ValueError):
                return "accepted"

        provider_code: str | None = None
        try:
            error_body = response.json()
            if isinstance(error_body, dict):
                raw_code = error_body.get("name") or error_body.get("code")
                if raw_code is not None:
                    provider_code = str(raw_code).strip()[:80] or None
        except ValueError:
            pass
        last_error = EmailDeliveryError(
            "Resend isteği kabul etmedi.",
            provider_status=int(response.status_code),
            provider_code=provider_code or f"http_{response.status_code}",
        )
        if response.status_code == 429 or response.status_code >= 500:
            if attempt == 0:
                time.sleep(0.4)
                continue
        break
    raise last_error or EmailDeliveryError(
        "Doğrulama e-postası gönderilemedi.",
        provider_code="unknown_delivery_error",
    )
'''
text = text[:send_start] + send_block + text[send_end:]

old_delivery_wrap = '''        raise EmailVerificationError(
            "Doğrulama e-postası gönderilemedi. Biraz sonra tekrar dene.",
            code="LS-AUTH-04",
            status_code=503,
        ) from exc'''
new_delivery_wrap = '''        raise EmailVerificationError(
            "Doğrulama e-postası gönderilemedi. Biraz sonra tekrar dene.",
            code="LS-AUTH-04",
            status_code=503,
            provider_status=exc.provider_status,
            provider_code=exc.provider_code,
        ) from exc'''
if text.count(old_delivery_wrap) != 2:
    raise RuntimeError("Expected exactly two delivery wrappers")
text = text.replace(old_delivery_wrap, new_delivery_wrap)

old_raise = '''def _raise_http(exc: EmailVerificationError) -> None:
    detail: dict = {"code": exc.code, "message": str(exc)}
    if exc.retry_after is not None:
        detail["retry_after"] = exc.retry_after
    raise HTTPException(exc.status_code, detail=detail) from exc'''
new_raise = '''def _raise_http(exc: EmailVerificationError) -> None:
    detail: dict = {"code": exc.code, "message": str(exc)}
    if exc.retry_after is not None:
        detail["retry_after"] = exc.retry_after
    if exc.provider_status is not None:
        detail["provider_status"] = exc.provider_status
    if exc.provider_code:
        detail["provider_code"] = exc.provider_code
    raise HTTPException(exc.status_code, detail=detail) from exc'''
if old_raise not in text:
    raise RuntimeError("HTTP error adapter block not found")
text = text.replace(old_raise, new_raise, 1)

path.write_text(text, encoding="utf-8")
