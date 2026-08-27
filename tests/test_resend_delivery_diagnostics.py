import pytest
from fastapi import HTTPException
from resend.exceptions import ResendError

import lecturesift.email_auth as email_auth
from lecturesift import config
from lecturesift.resend_smtp import ResendSMTPError


def test_resend_rejection_retains_only_safe_provider_metadata(monkeypatch):
    monkeypatch.setattr(config, "RESEND_API_KEY", "re_hidden_test_value")
    monkeypatch.setattr(config, "RESEND_FROM_EMAIL", "LectureSift <no-reply@mail.lecturesift.com>")

    def reject(*args, **kwargs):
        raise ResendError(
            code=403,
            error_type="invalid_api_key",
            message="The supplied credential was rejected.",
            suggested_action="Generate a new key.",
        )

    def reject_smtp(**kwargs):
        raise ResendSMTPError("invalid_api_key", status=535)

    monkeypatch.setattr(email_auth.resend.Emails, "send", reject)
    monkeypatch.setattr(email_auth, "send_resend_smtp", reject_smtp)

    with pytest.raises(email_auth.EmailDeliveryError) as captured:
        email_auth._send_verification_email(
            "delivered@resend.dev",
            "123456",
            "tr",
            "diagnostic-test",
        )

    error = captured.value
    assert error.provider_status == 535
    assert error.provider_code == "invalid_api_key"
    assert "credential" not in str(error).casefold()
    assert "re_hidden_test_value" not in str(error)


def test_resend_sdk_receives_sender_content_and_idempotency(monkeypatch):
    monkeypatch.setattr(config, "RESEND_API_KEY", "re_hidden_test_value")
    monkeypatch.setattr(config, "RESEND_FROM_EMAIL", "LectureSift <no-reply@mail.lecturesift.com>")
    monkeypatch.setattr(config, "BILLING_SUPPORT_EMAIL", "support@lecturesift.com")
    captured: dict = {}

    def accept(params, options):
        captured.update(params=params, options=options)
        return {"id": "email_test_id"}

    monkeypatch.setattr(email_auth.resend.Emails, "send", accept)
    result = email_auth._send_verification_email(
        "delivered@resend.dev",
        "123456",
        "tr",
        "verification/test-id",
    )

    assert result == "email_test_id"
    assert captured["params"]["from"] == "LectureSift <no-reply@mail.lecturesift.com>"
    assert captured["params"]["to"] == ["delivered@resend.dev"]
    assert captured["params"]["reply_to"] == "support@lecturesift.com"
    assert captured["options"] == {"idempotency_key": "verification/test-id"}


def test_http_adapter_exposes_status_and_code_without_provider_message():
    error = email_auth.EmailVerificationError(
        "Doğrulama e-postası gönderilemedi.",
        code="LS-AUTH-04",
        status_code=503,
        provider_status=403,
        provider_code="invalid_api_key",
    )

    with pytest.raises(HTTPException) as captured:
        email_auth._raise_http(error)

    detail = captured.value.detail
    assert detail == {
        "code": "LS-AUTH-04",
        "message": "Doğrulama e-postası gönderilemedi.",
        "provider_status": 403,
        "provider_code": "invalid_api_key",
    }
