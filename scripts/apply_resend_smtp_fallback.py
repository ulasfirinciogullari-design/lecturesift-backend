from pathlib import Path


path = Path("lecturesift/email_auth.py")
text = path.read_text(encoding="utf-8")

old_import = '''from .resend_diagnostics import classify_resend_error
from .billing_service import (
'''
new_import = '''from .resend_diagnostics import classify_resend_error
from .resend_smtp import ResendSMTPError, send_resend_smtp
from .billing_service import (
'''
if old_import not in text:
    raise RuntimeError("Resend diagnostics import block not found")
text = text.replace(old_import, new_import, 1)

old_block = '''            last_error = EmailDeliveryError(
                "Resend isteği kabul etmedi.",
                provider_status=provider_status,
                provider_code=provider_code,
            )
            if (
                attempt == 0
                and provider_status is not None
                and (provider_status == 429 or provider_status >= 500)
            ):
                time.sleep(0.4)
                continue
            break
'''
new_block = '''            last_error = EmailDeliveryError(
                "Resend isteği kabul etmedi.",
                provider_status=provider_status,
                provider_code=provider_code,
            )
            if provider_status == 403:
                try:
                    return send_resend_smtp(
                        api_key=config.RESEND_API_KEY.strip(),
                        sender=config.RESEND_FROM_EMAIL.strip(),
                        recipient=recipient,
                        subject=subject,
                        text_body=text_body,
                        html_body=html_body,
                        idempotency_key=idempotency_key,
                        reply_to=config.BILLING_SUPPORT_EMAIL.strip(),
                    )
                except ResendSMTPError as smtp_exc:
                    last_error = EmailDeliveryError(
                        "Resend SMTP isteği kabul etmedi.",
                        provider_status=smtp_exc.status,
                        provider_code=smtp_exc.code,
                    )
            if (
                attempt == 0
                and provider_status is not None
                and (provider_status == 429 or provider_status >= 500)
            ):
                time.sleep(0.4)
                continue
            break
'''
if old_block not in text:
    raise RuntimeError("Resend exception block not found")
text = text.replace(old_block, new_block, 1)

path.write_text(text, encoding="utf-8")
