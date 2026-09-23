import importlib.util
import io
import json
from email.message import Message
from pathlib import Path
from urllib.error import HTTPError, URLError

import pytest


SPEC = importlib.util.spec_from_file_location(
    "frontend_delivery", Path(__file__).resolve().parents[1] / "scripts/check_frontend_delivery.py",
)
delivery = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(delivery)


class Response(io.BytesIO):
    def __init__(self, body, url, code=200):
        super().__init__(body)
        self.url, self.code = url, code
        self.headers = Message()
        self.headers["X-Nf-Request-Id"] = "synthetic-request-id"

    def geturl(self):
        return self.url


def healthy(request, timeout):
    url = request.full_url
    body = b"public content"
    if url == delivery.DEPLOYS_URL:
        body = json.dumps([
            {"id": "failed", "context": "production", "state": "error", "published_at": None},
            {"id": "published", "context": "production", "state": "ready",
             "published_at": "2026-09-23T22:52:46Z", "commit_ref": "a" * 40},
        ]).encode()
    elif url.endswith("/document-summary"):
        body = b'<script src="/assets/i18n/tr-123.js"></script>'
    return Response(body, url)


def test_first_failure_is_preserved_after_later_success():
    attempts = []

    def flaky(request, timeout):
        attempts.append(request.full_url)
        if request.full_url == delivery.ORIGINS[0] + "/favicon.svg" and attempts.count(request.full_url) == 1:
            headers = Message()
            headers["X-Nf-Request-Id"] = "synthetic-500-id"
            raise HTTPError(request.full_url, 500, "Internal error", headers, io.BytesIO(b"Error"))
        return healthy(request, timeout)

    report = delivery.check_delivery(flaky)
    assert report["delivery_ok"] is False
    favicon = [row for row in report["observations"] if row["url"] == delivery.ORIGINS[0] + "/favicon.svg"]
    assert [row["status"] for row in favicon] == [500, 200]
    assert favicon[0]["x-nf-request-id"] == "synthetic-500-id"
    assert report["deployment"]["id"] == "published"
    assert len(attempts) == 45  # Two rounds, two origins, ten paths and a discovered bundle.


def test_runtime_file_failure_fails_delivery_even_when_pages_are_available():
    def broken_asset(request, timeout):
        if "/assets/i18n/" in request.full_url:
            return Response(b"not found", request.full_url, 404)
        return healthy(request, timeout)

    report = delivery.check_delivery(broken_asset, rounds=1)
    assert not report["delivery_ok"]
    assert sum(not row["ok"] for row in report["observations"]) == 2


@pytest.mark.parametrize("body", [b"", b"x" * (delivery.MAX_BYTES + 1)])
def test_empty_or_oversized_success_is_not_healthy(body):
    row, _ = delivery.probe(delivery.ORIGINS[0], lambda request, timeout: Response(body, request.full_url))
    assert row["ok"] is False


def test_redirect_outside_public_frontend_is_blocked():
    with pytest.raises(URLError):
        delivery.PublicRedirects().redirect_request(None, None, 302, "Found", {}, "https://example.com/private")


def test_remote_runtime_url_is_not_followed():
    parser = delivery.RuntimeAssets()
    parser.feed('<script src="https://elsewhere.test/assets/i18n/tr.js"></script>'
                '<script src="//elsewhere.test/assets/i18n/tr.js"></script>'
                '<script src="/assets/i18n/tr.js?user=private"></script>')
    assert parser.paths == set()


def test_network_error_keeps_a_failed_observation():
    def unavailable(request, timeout):
        raise URLError("connection failed")

    row, _ = delivery.probe(delivery.ORIGINS[0], unavailable)
    assert row["ok"] is False
    assert row["status"] is None
