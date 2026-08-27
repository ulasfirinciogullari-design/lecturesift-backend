"""SMTP fallback for Resend transactional delivery.

Resend supports SMTP with the same API key used by its HTTPS API. The fallback
tries the provider's supported TLS transports in a bounded order because cloud
hosts can block individual outbound SMTP ports. Provider responses are reduced
to safe operational codes; raw SMTP text is never exposed to clients.
"""

from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage
from typing import Literal

from .resend_diagnostics import classify_resend_error


SMTPMode = Literal["ssl", "starttls"]
DEFAULT_TRANSPORTS: tuple[tuple[int, SMTPMode], ...] = (
    (587, "starttls"),
    (2587, "starttls"),
    (465, "ssl"),
    (2465, "ssl"),
)


class ResendSMTPError(RuntimeError):
    def __init__(self, code: str, *, status: int | None = None):
        super().__init__("Resend SMTP delivery failed.")
        self.code = code
        self.status = status


def _smtp_text(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value or "")


def _smtp_status(value: object) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _classify_smtp_failure(status: int | None, message: object, fallback: str) -> str:
    text = _smtp_text(message)
    normalized = " ".join(text.casefold().split())
    if status in {534, 535} or "authentication" in normalized or "credentials" in normalized:
        return "invalid_api_key"
    if "sender" in normalized and "not verified" in normalized:
        return "domain_not_verified"
    if "domain" in normalized and "not verified" in normalized:
        return "domain_not_verified"
    if "permission" in normalized and "domain" in normalized:
        return "api_key_domain_mismatch"
    classified = classify_resend_error(
        status=status,
        error_type=fallback,
        message=text,
    )
    return classified if classified != fallback else fallback


def _message(
    *,
    sender: str,
    recipient: str,
    subject: str,
    text_body: str,
    html_body: str,
    idempotency_key: str,
    reply_to: str,
) -> EmailMessage:
    message = EmailMessage()
    message["From"] = sender
    message["To"] = recipient
    message["Subject"] = subject
    message["Resend-Idempotency-Key"] = idempotency_key[:256]
    if reply_to:
        message["Reply-To"] = reply_to
    message.set_content(text_body)
    message.add_alternative(html_body, subtype="html")
    return message


def _deliver(
    *,
    host: str,
    port: int,
    mode: SMTPMode,
    timeout: float,
    context: ssl.SSLContext,
    api_key: str,
    message: EmailMessage,
) -> None:
    if mode == "ssl":
        client_context = smtplib.SMTP_SSL(
            host,
            int(port),
            timeout=timeout,
            context=context,
        )
    else:
        client_context = smtplib.SMTP(host, int(port), timeout=timeout)

    with client_context as client:
        if mode == "starttls":
            client.ehlo()
            client.starttls(context=context)
            client.ehlo()
        client.login("resend", api_key)
        refused = client.send_message(message)
    if refused:
        raise ResendSMTPError("recipient_refused")


def send_resend_smtp(
    *,
    api_key: str,
    sender: str,
    recipient: str,
    subject: str,
    text_body: str,
    html_body: str,
    idempotency_key: str,
    reply_to: str = "",
    host: str = "smtp.resend.com",
    port: int | None = None,
    timeout: float = 6.0,
    transports: tuple[tuple[int, SMTPMode], ...] | None = None,
) -> str:
    message = _message(
        sender=sender,
        recipient=recipient,
        subject=subject,
        text_body=text_body,
        html_body=html_body,
        idempotency_key=idempotency_key,
        reply_to=reply_to,
    )
    candidates = (
        ((int(port), "ssl"),)
        if port is not None
        else (transports or DEFAULT_TRANSPORTS)
    )
    context = ssl.create_default_context()
    last_network_error: BaseException | None = None

    for candidate_port, mode in candidates:
        try:
            _deliver(
                host=host,
                port=int(candidate_port),
                mode=mode,
                timeout=timeout,
                context=context,
                api_key=api_key,
                message=message,
            )
            return "smtp-accepted"
        except smtplib.SMTPAuthenticationError as exc:
            status = _smtp_status(getattr(exc, "smtp_code", None))
            raise ResendSMTPError("invalid_api_key", status=status) from exc
        except smtplib.SMTPSenderRefused as exc:
            status = _smtp_status(getattr(exc, "smtp_code", None))
            code = _classify_smtp_failure(
                status,
                getattr(exc, "smtp_error", ""),
                "sender_refused",
            )
            raise ResendSMTPError(code, status=status) from exc
        except smtplib.SMTPRecipientsRefused as exc:
            status: int | None = None
            response: object = ""
            recipients = getattr(exc, "recipients", {}) or {}
            if recipients:
                status, response = next(iter(recipients.values()))
                status = _smtp_status(status)
            code = _classify_smtp_failure(status, response, "recipient_refused")
            raise ResendSMTPError(code, status=status) from exc
        except smtplib.SMTPResponseException as exc:
            status = _smtp_status(getattr(exc, "smtp_code", None))
            code = _classify_smtp_failure(
                status,
                getattr(exc, "smtp_error", ""),
                "smtp_rejected",
            )
            raise ResendSMTPError(code, status=status) from exc
        except ResendSMTPError:
            raise
        except (smtplib.SMTPException, OSError, TimeoutError) as exc:
            last_network_error = exc
            continue

    raise ResendSMTPError("smtp_unavailable") from last_network_error
