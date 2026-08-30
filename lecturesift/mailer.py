"""Small transactional-email adapter used by account verification flows.

Secrets are read only from runtime environment variables. Message bodies and
recipient addresses are never logged.
"""

from __future__ import annotations

import json
import smtplib
import ssl
import urllib.error
import urllib.request
from email.message import EmailMessage

from . import config


class EmailDeliveryError(RuntimeError):
    pass


def email_delivery_configured() -> bool:
    if not config.EMAIL_FROM:
        return False
    if config.EMAIL_PROVIDER == "resend":
        return bool(config.RESEND_API_KEY)
    if config.EMAIL_PROVIDER == "smtp":
        return bool(config.SMTP_HOST and config.SMTP_USERNAME and config.SMTP_PASSWORD)
    return False


def send_transactional_email(
    to: str,
    subject: str,
    html: str,
    text: str,
    *,
    reply_to: str = "",
) -> str:
    if not email_delivery_configured():
        raise EmailDeliveryError("E-posta doğrulama hizmeti henüz yapılandırılmamış.")
    if config.EMAIL_PROVIDER == "resend":
        return _send_resend(to, subject, html, text, reply_to=reply_to)
    return _send_smtp(to, subject, html, text, reply_to=reply_to)


def _send_resend(to: str, subject: str, html: str, text: str, *, reply_to: str = "") -> str:
    message = {"from": config.EMAIL_FROM, "to": [to], "subject": subject, "html": html, "text": text}
    if reply_to:
        message["reply_to"] = reply_to
    payload = json.dumps(message).encode("utf-8")
    request = urllib.request.Request(
        "https://api.resend.com/emails",
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {config.RESEND_API_KEY}",
            "Content-Type": "application/json",
            "User-Agent": "LectureSift/1.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            if not 200 <= response.status < 300:
                raise EmailDeliveryError("E-posta sağlayıcısı isteği kabul etmedi.")
            try:
                response_payload = json.loads(response.read().decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                response_payload = {}
            return str(response_payload.get("id") or "")
    except (OSError, urllib.error.URLError, urllib.error.HTTPError) as exc:
        raise EmailDeliveryError("Doğrulama e-postası gönderilemedi.") from exc


def _send_smtp(to: str, subject: str, html: str, text: str, *, reply_to: str = "") -> str:
    message = EmailMessage()
    message["From"] = config.EMAIL_FROM
    message["To"] = to
    message["Subject"] = subject
    if reply_to:
        message["Reply-To"] = reply_to
    message.set_content(text)
    message.add_alternative(html, subtype="html")
    try:
        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=15) as client:
            if config.SMTP_USE_TLS:
                client.starttls(context=ssl.create_default_context())
            client.login(config.SMTP_USERNAME, config.SMTP_PASSWORD)
            client.send_message(message)
        return str(message.get("Message-ID") or "")
    except (OSError, smtplib.SMTPException) as exc:
        raise EmailDeliveryError("Doğrulama e-postası gönderilemedi.") from exc
