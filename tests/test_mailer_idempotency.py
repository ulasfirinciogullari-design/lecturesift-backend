import json

import pytest

from lecturesift import config, mailer


@pytest.fixture
def resend(monkeypatch):
    monkeypatch.setattr(config, "EMAIL_PROVIDER", "resend")
    monkeypatch.setattr(config, "EMAIL_FROM", "LectureSift <mail@example.invalid>")
    monkeypatch.setattr(config, "RESEND_API_KEY", "synthetic-test-key")
    requests = []

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b'{"id":"synthetic-message"}'

    def send(request, timeout):
        requests.append(request)
        assert timeout == 15
        return Response()

    monkeypatch.setattr(mailer.urllib.request, "urlopen", send)
    return requests


def test_failure_email_passes_stable_idempotency_header_and_reply_address(resend):
    result = mailer.send_transactional_email(
        "student@example.invalid", "Status", "<p>Explanation</p>", "Explanation",
        reply_to="support@example.invalid", idempotency_key="job-failure/lesson-1/v1",
    )
    assert result == "synthetic-message"
    request, = resend
    assert request.get_header("Idempotency-key") == "job-failure/lesson-1/v1"
    body = json.loads(request.data)
    assert body["reply_to"] == "support@example.invalid"
    assert body["to"] == ["student@example.invalid"]
    assert "idempotency_key" not in body


def test_existing_transactional_email_does_not_add_an_idempotency_header(resend):
    mailer.send_transactional_email("student@example.invalid", "Verify", "<p>Verify</p>", "Verify")
    request, = resend
    assert request.get_header("Idempotency-key") is None


@pytest.mark.parametrize("key", ["a" * 257, "job\r\nBcc: victim@example.invalid", "not a key"])
def test_invalid_idempotency_key_cannot_reach_mail_provider(resend, key):
    with pytest.raises(mailer.EmailDeliveryError):
        mailer.send_transactional_email("student@example.invalid", "Status", "", "", idempotency_key=key)
    assert not resend


def test_idempotent_send_requires_provider_receipt_before_reporting_success(resend, monkeypatch):
    class MissingReceipt:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b'{}'

    monkeypatch.setattr(mailer.urllib.request, "urlopen", lambda *a, **k: MissingReceipt())
    with pytest.raises(mailer.EmailDeliveryError, match="doğrulanamadı"):
        mailer.send_transactional_email("student@example.invalid", "Status", "", "",
            idempotency_key="job-failure/lesson-1/v1")
