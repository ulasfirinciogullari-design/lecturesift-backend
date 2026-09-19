"""Payment retries must stop at the boundary where delivery becomes uncertain."""

import json
import logging

import httpx
import pytest

from lecturesift import config, payments


@pytest.fixture
def provider(monkeypatch):
    monkeypatch.setattr(config, "IYZICO_BASE_URL", "https://api.iyzipay.com")
    monkeypatch.setattr(config, "IYZICO_API_KEY", "synthetic-api-key")
    monkeypatch.setattr(config, "IYZICO_SECRET_KEY", "synthetic-secret-key")
    delays = []
    monkeypatch.setattr(payments.time, "sleep", delays.append)
    return delays


@pytest.mark.parametrize("path", [payments.IYZICO_INITIALIZE_PATH, payments.IYZICO_RETRIEVE_PATH])
@pytest.mark.parametrize("failure", [httpx.ConnectError, httpx.ConnectTimeout])
def test_connection_failure_recovers_before_sending_same_payment(monkeypatch, provider, path, failure):
    requests = []
    payload = {"conversationId": "order-123", "price": "59.90", "token": "private-token"}

    def post(url, *, content, headers, timeout):
        requests.append((url, content))
        assert timeout.connect <= 5.0
        if len(requests) < 3:
            raise failure("Connection reset before request delivery")
        return httpx.Response(200, json={"status": "success"}, request=httpx.Request("POST", url))

    monkeypatch.setattr(payments.httpx, "post", post)

    assert payments._iyzico_post(path, payload) == {"status": "success"}
    assert len(requests) == 3
    assert len(set(requests)) == 1
    assert requests[0][0] == f"https://api.iyzipay.com{path}"
    assert json.loads(requests[0][1]) == payload
    assert len(provider) == 2
    assert sum(provider) <= 1.0


@pytest.mark.parametrize("failure", [httpx.ConnectError, httpx.ConnectTimeout])
def test_persistent_connection_failure_is_bounded_and_logs_no_secrets(monkeypatch, provider, caplog, failure):
    calls = []
    private = "buyer@example.com private-token synthetic-api-key synthetic-secret-key"

    def post(*args, **kwargs):
        calls.append(1)
        raise failure(private)

    monkeypatch.setattr(payments.httpx, "post", post)
    with caplog.at_level(logging.WARNING, logger="lecturesift.payments"):
        with pytest.raises(payments.PaymentProviderError, match="şu anda ulaşılamıyor") as raised:
            payments._iyzico_post(payments.IYZICO_INITIALIZE_PATH, {"buyer": private})

    assert len(calls) == 3
    assert len(provider) == 2
    assert failure.__name__ in caplog.text
    assert "operation=initialize" in caplog.text
    for secret in private.split():
        assert secret not in caplog.text
        assert secret not in str(raised.value)
    assert all(record.exc_info is None for record in caplog.records)


@pytest.mark.parametrize("failure", [
    httpx.ReadError, httpx.WriteError, httpx.ReadTimeout, httpx.WriteTimeout,
    httpx.RemoteProtocolError, httpx.PoolTimeout, httpx.ProxyError,
])
def test_uncertain_delivery_is_never_retried(monkeypatch, provider, failure):
    calls = []

    def post(*args, **kwargs):
        calls.append(1)
        raise failure("Provider may already have received this payment request")

    monkeypatch.setattr(payments.httpx, "post", post)
    with pytest.raises(payments.PaymentProviderError):
        payments._iyzico_post(payments.IYZICO_INITIALIZE_PATH, {"conversationId": "order-123"})

    assert calls == [1]
    assert provider == []


@pytest.mark.parametrize("status, content", [
    (200, b"private malformed body"),
    (302, b"private redirect body"),
    (400, b"private rejected body"),
    (429, b"private rate-limit body"),
    (500, b"private failure body"),
    (503, b"private unavailable body"),
])
def test_provider_response_is_not_replayed_or_logged(monkeypatch, provider, caplog, status, content):
    calls = []

    def post(url, **kwargs):
        calls.append(1)
        return httpx.Response(status, content=content, request=httpx.Request("POST", url))

    monkeypatch.setattr(payments.httpx, "post", post)
    with caplog.at_level(logging.WARNING, logger="lecturesift.payments"):
        with pytest.raises(payments.PaymentProviderError):
            payments._iyzico_post(payments.IYZICO_RETRIEVE_PATH, {"token": "private-token"})

    assert calls == [1]
    assert provider == []
    assert "operation=retrieve" in caplog.text
    assert "private" not in caplog.text
    if status != 200:
        assert f"status={status}" in caplog.text
    assert all(record.exc_info is None for record in caplog.records)
