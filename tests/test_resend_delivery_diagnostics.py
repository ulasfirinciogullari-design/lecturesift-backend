import pytest
from fastapi import HTTPException

import lecturesift.email_auth as email_auth
from lecturesift import config


class FakeResponse:
    status_code = 403
    is_success = False

    @staticmethod
    def json() -> dict:
        return {
            "name": "invalid_api_key",
            "message": "The supplied credential was rejected.",
        }


def test_resend_rejection_retains_only_safe_provider_metadata(monkeypatch):
    monkeypatch.setattr(config, "RESEND_API_KEY", "re_hidden_test_value")
    monkeypatch.setattr(config, "RESEND_FROM_EMAIL", "LectureSift <no-reply@mail.lecturesift.com>")
    monkeypatch.setattr(email_auth.httpx, "post", lambda *args, **kwargs: FakeResponse())

    with pytest.raises(email_auth.EmailDeliveryError) as captured:
        email_auth._send_verification_email(
            "delivered@resend.dev",
            "123456",
            "tr",
            "diagnostic-test",
        )

    error = captured.value
    assert error.provider_status == 403
    assert error.provider_code == "invalid_api_key"
    assert "credential" not in str(error).casefold()
    assert "re_hidden_test_value" not in str(error)


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
