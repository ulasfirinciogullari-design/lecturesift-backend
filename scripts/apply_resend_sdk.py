from pathlib import Path


path = Path("lecturesift/email_auth.py")
text = path.read_text(encoding="utf-8")

old_import = "import httpx\nfrom fastapi import APIRouter, FastAPI, HTTPException\n"
new_import = (
    "import resend\n"
    "from resend.exceptions import ResendError\n"
    "from fastapi import APIRouter, FastAPI, HTTPException\n"
)
if old_import not in text:
    raise RuntimeError("Expected httpx import block was not found")
text = text.replace(old_import, new_import, 1)

send_start = text.index("def _send_verification_email")
send_end = text.index("\n\ndef _public_verification", send_start)
send_block = '''def _resend_status(value: object) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _send_verification_email(recipient: str, code: str, language: str, idempotency_key: str) -> str:
    if not email_delivery_available():
        raise EmailDeliveryError(
            "Resend e-posta servisi yapılandırılmamış.",
            provider_code="not_configured",
        )
    subject, text_body, html_body = _email_copy(code, language)
    params: resend.Emails.SendParams = {
        "from": config.RESEND_FROM_EMAIL.strip(),
        "to": [recipient],
        "subject": subject,
        "text": text_body,
        "html": html_body,
    }
    if config.BILLING_SUPPORT_EMAIL.strip():
        params["reply_to"] = config.BILLING_SUPPORT_EMAIL.strip()

    resend.api_key = config.RESEND_API_KEY.strip()
    last_error: EmailDeliveryError | None = None
    for attempt in range(2):
        try:
            response = resend.Emails.send(
                params,
                {"idempotency_key": idempotency_key[:256]},
            )
        except ResendError as exc:
            provider_status = _resend_status(exc.code)
            provider_code = str(exc.error_type or "resend_error").strip()[:80]
            last_error = EmailDeliveryError(
                "Resend isteği kabul etmedi.",
                provider_status=provider_status,
                provider_code=provider_code or "resend_error",
            )
            if (
                attempt == 0
                and provider_status is not None
                and (provider_status == 429 or provider_status >= 500)
            ):
                time.sleep(0.4)
                continue
            break
        except Exception:
            last_error = EmailDeliveryError(
                "Resend SDK isteği tamamlanamadı.",
                provider_code="sdk_client_error",
            )
            if attempt == 0:
                time.sleep(0.4)
                continue
            break
        return str(response.get("id") or "accepted")

    raise last_error or EmailDeliveryError(
        "Doğrulama e-postası gönderilemedi.",
        provider_code="unknown_delivery_error",
    )
'''
text = text[:send_start] + send_block + text[send_end:]
path.write_text(text, encoding="utf-8")
