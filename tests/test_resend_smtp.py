import smtplib

import pytest
from resend.exceptions import ResendError

import lecturesift.email_auth as email_auth
import lecturesift.resend_smtp as resend_smtp
from lecturesift import config


class FakeSMTPSSL:
    instance = None

    def __init__(self, host, port, timeout, context):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.context = context
        self.login_args = None
        self.message = None
        FakeSMTPSSL.instance = self

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def login(self, username, password):
        self.login_args = (username, password)

    def send_message(self, message):
        self.message = message
        return {}


def test_smtp_fallback_builds_secure_transactional_message(monkeypatch):
    monkeypatch.setattr(resend_smtp.smtplib, "SMTP_SSL", FakeSMTPSSL)

    result = resend_smtp.send_resend_smtp(
        api_key="re_secret",
        sender="LectureSift <no-reply@mail.lecturesift.com>",
        recipient="delivered@resend.dev",
        subject="Doğrulama kodu",
        text_body="Kod: 123456",
        html_body="<strong>123456</strong>",
        idempotency_key="verify/user-1",
        reply_to="support@lecturesift.com",
        port=465,
    )

    client = FakeSMTPSSL.instance
    assert result == "smtp-accepted"
    assert client.host == "smtp.resend.com"
    assert client.port == 465
    assert client.login_args == ("resend", "re_secret")
    assert client.message["From"] == "LectureSift <no-reply@mail.lecturesift.com>"
    assert client.message["To"] == "delivered@resend.dev"
    assert client.message["Reply-To"] == "support@lecturesift.com"
    assert client.message["Resend-Idempotency-Key"] == "verify/user-1"
    assert client.message.is_multipart()


def test_smtp_authentication_failure_becomes_invalid_key(monkeypatch):
    class RejectingSMTP(FakeSMTPSSL):
        def login(self, username, password):
            raise smtplib.SMTPAuthenticationError(535, b"Authentication credentials invalid")

    monkeypatch.setattr(resend_smtp.smtplib, "SMTP_SSL", RejectingSMTP)

    with pytest.raises(resend_smtp.ResendSMTPError) as captured:
        resend_smtp.send_resend_smtp(
            api_key="re_invalid",
            sender="LectureSift <no-reply@mail.lecturesift.com>",
            recipient="delivered@resend.dev",
            subject="Doğrulama kodu",
            text_body="Kod: 123456",
            html_body="<strong>123456</strong>",
            idempotency_key="verify/user-2",
            port=465,
        )

    assert captured.value.code == "invalid_api_key"
    assert captured.value.status == 535
    assert "re_invalid" not in str(captured.value)


def test_smtp_network_failure_tries_alternate_starttls_port(monkeypatch):
    class StartTLSClient:
        attempts = []
        accepted = None

        def __init__(self, host, port, timeout):
            self.host = host
            self.port = port
            self.timeout = timeout
            self.ehlo_count = 0
            self.started_tls = False
            self.login_args = None
            self.message = None
            StartTLSClient.attempts.append(port)
            if port == 587:
                raise OSError("port blocked")
            StartTLSClient.accepted = self

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def ehlo(self):
            self.ehlo_count += 1

        def starttls(self, context):
            self.started_tls = True
            self.context = context

        def login(self, username, password):
            self.login_args = (username, password)

        def send_message(self, message):
            self.message = message
            return {}

    monkeypatch.setattr(resend_smtp.smtplib, "SMTP", StartTLSClient)

    result = resend_smtp.send_resend_smtp(
        api_key="re_secret",
        sender="LectureSift <no-reply@mail.lecturesift.com>",
        recipient="delivered@resend.dev",
        subject="Doğrulama kodu",
        text_body="Kod: 123456",
        html_body="<strong>123456</strong>",
        idempotency_key="verify/user-alt-port",
    )

    client = StartTLSClient.accepted
    assert result == "smtp-accepted"
    assert StartTLSClient.attempts == [587, 2587]
    assert client.port == 2587
    assert client.started_tls is True
    assert client.ehlo_count == 2
    assert client.login_args == ("resend", "re_secret")


def test_api_403_uses_smtp_fallback(monkeypatch):
    monkeypatch.setattr(config, "RESEND_API_KEY", "re_secret")
    monkeypatch.setattr(config, "RESEND_FROM_EMAIL", "LectureSift <no-reply@mail.lecturesift.com>")
    monkeypatch.setattr(config, "BILLING_SUPPORT_EMAIL", "support@lecturesift.com")

    def reject_api(*args, **kwargs):
        raise ResendError(
            code=403,
            error_type="InternalServerError",
            message="Unknown error",
            suggested_action="",
        )

    captured = {}

    def accept_smtp(**kwargs):
        captured.update(kwargs)
        return "smtp-accepted"

    monkeypatch.setattr(email_auth.resend.Emails, "send", reject_api)
    monkeypatch.setattr(email_auth, "send_resend_smtp", accept_smtp)

    result = email_auth._send_verification_email(
        "delivered@resend.dev",
        "123456",
        "tr",
        "verify/user-3",
    )

    assert result == "smtp-accepted"
    assert captured["api_key"] == "re_secret"
    assert captured["sender"] == "LectureSift <no-reply@mail.lecturesift.com>"
    assert captured["recipient"] == "delivered@resend.dev"
    assert captured["idempotency_key"] == "verify/user-3"
