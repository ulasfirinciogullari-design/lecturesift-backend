from pathlib import Path


path = Path("lecturesift/email_auth.py")
text = path.read_text(encoding="utf-8")

old_import = '''from .billing_service import (
    ENGINE,
'''
new_import = '''from .resend_diagnostics import classify_resend_error
from .billing_service import (
    ENGINE,
'''
if old_import not in text:
    raise RuntimeError("Billing service import block not found")
text = text.replace(old_import, new_import, 1)

old_code = '''        except ResendError as exc:
            provider_status = _resend_status(exc.code)
            provider_code = str(exc.error_type or "resend_error").strip()[:80]
            last_error = EmailDeliveryError(
                "Resend isteği kabul etmedi.",
                provider_status=provider_status,
                provider_code=provider_code or "resend_error",
            )
'''
new_code = '''        except ResendError as exc:
            provider_status = _resend_status(exc.code)
            provider_code = classify_resend_error(
                status=provider_status,
                error_type=exc.error_type,
                message=exc.message,
            )
            last_error = EmailDeliveryError(
                "Resend isteği kabul etmedi.",
                provider_status=provider_status,
                provider_code=provider_code,
            )
'''
if old_code not in text:
    raise RuntimeError("Resend error adapter block not found")
text = text.replace(old_code, new_code, 1)

path.write_text(text, encoding="utf-8")
