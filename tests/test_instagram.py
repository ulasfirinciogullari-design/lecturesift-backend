import hashlib
import hmac
from http.client import RemoteDisconnected
from urllib.error import HTTPError, URLError

import pytest
from fastapi.testclient import TestClient

from lecturesift.app import app, _instagram_client
from lecturesift.instagram import InstagramAPIError, InstagramClient


def client() -> InstagramClient:
    return InstagramClient("access-token", "account-123", "app-secret")


def test_account_request_uses_appsecret_proof_and_never_returns_secrets(monkeypatch):
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            return b'{"id":"account-123","username":"lecturesift"}'

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr("lecturesift.instagram.urlopen", fake_urlopen)
    result = client().get_account()
    expected = hmac.new(b"app-secret", b"access-token", hashlib.sha256).hexdigest()
    assert result["username"] == "lecturesift"
    assert "appsecret_proof=" + expected in captured["url"]


def test_api_error_does_not_include_request_or_credentials(monkeypatch):
    error = HTTPError("https://example.invalid", 400, "bad", {}, None)
    error.read = lambda: b'{"error":{"message":"Invalid token","type":"OAuthException"}}'
    monkeypatch.setattr("lecturesift.instagram.urlopen", lambda *_args, **_kwargs: (_ for _ in ()).throw(error))
    with pytest.raises(InstagramAPIError) as caught:
        client().get_account()
    assert str(caught.value) == "Invalid token"
    assert caught.value.error_type == "OAuthException"
    assert "access-token" not in str(caught.value)


def test_only_media_publish_waits_longer_than_other_graph_calls(monkeypatch):
    timeouts = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            return b'{"id":"x"}'

    def fake_urlopen(request, timeout):
        timeouts[request.full_url.split("?")[0].rsplit("/", 1)[-1]] = timeout
        return Response()

    monkeypatch.setattr("lecturesift.instagram.urlopen", fake_urlopen)
    api = client()
    api.get_recent_media()
    api.get_container_status("container-1")
    api.publish_media("container-1")
    assert timeouts == {"media": 15.0, "container-1": 15.0, "media_publish": 90.0}


def _http_error(code, body):
    error = HTTPError("https://example.invalid", code, "error", {}, None)
    error.read = lambda: body
    return error


@pytest.mark.parametrize(("failure", "transient"), [
    (lambda: TimeoutError("The read operation timed out"), True),
    (lambda: URLError(TimeoutError("timed out")), True),
    (lambda: ConnectionResetError(104, "Connection reset by peer"), True),
    (lambda: RemoteDisconnected("Remote end closed connection without response"), True),
    (lambda: _http_error(500, b'{"error":{"message":"Please retry","type":"OAuthException"}}'), True),
    (lambda: _http_error(503, b"<html>busy</html>"), True),
    (lambda: _http_error(400, b'{"error":{"message":"Please retry","type":"OAuthException","is_transient":true}}'), True),
    (lambda: _http_error(400, b'{"error":{"message":"Invalid parameter","type":"OAuthException"}}'), False),
])
def test_publish_failures_with_an_unknown_outcome_are_transient(monkeypatch, failure, transient):
    def fake_urlopen(*_args, **_kwargs):
        raise failure()

    monkeypatch.setattr("lecturesift.instagram.urlopen", fake_urlopen)
    with pytest.raises(InstagramAPIError) as caught:
        client().publish_media("container-1")
    assert caught.value.transient is transient
    assert "access-token" not in str(caught.value)


def test_instagram_health_returns_safe_account_metadata():
    class FakeClient:
        def get_account(self):
            return {"id": "1", "username": "lecturesift", "account_type": "BUSINESS", "media_count": 3}

    app.dependency_overrides[_instagram_client] = lambda: FakeClient()
    try:
        response = TestClient(app).get("/instagram/health")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json()["account"]["username"] == "lecturesift"
    assert "access_token" not in response.text


def test_publish_routes_are_disabled_without_admin_token(monkeypatch):
    monkeypatch.setattr("lecturesift.app.INSTAGRAM_ADMIN_TOKEN", "")
    response = TestClient(app).post(
        "/instagram/media",
        json={"media_url": "https://cdn.example.com/post.jpg", "caption": "test"},
    )
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "LS-IG-02"


def test_create_and_publish_media_require_admin_bearer(monkeypatch):
    class FakeClient:
        def create_media_container(self, **kwargs):
            assert kwargs["media_type"] == "IMAGE"
            return {"id": "container-1"}

        def publish_media(self, container_id):
            assert container_id == "container-1"
            return {"id": "media-1"}

    monkeypatch.setattr("lecturesift.app.INSTAGRAM_ADMIN_TOKEN", "admin-token")
    app.dependency_overrides[_instagram_client] = lambda: FakeClient()
    api = TestClient(app)
    try:
        unauthorized = api.post("/instagram/media", json={"media_url": "https://cdn.example.com/post.jpg"})
        created = api.post(
            "/instagram/media",
            headers={"Authorization": "Bearer admin-token"},
            json={"media_url": "https://cdn.example.com/post.jpg"},
        )
        published = api.post(
            "/instagram/media/publish",
            headers={"Authorization": "Bearer admin-token"},
            json={"container_id": "container-1"},
        )
    finally:
        app.dependency_overrides.clear()
    assert unauthorized.status_code == 401
    assert created.json()["container_id"] == "container-1"
    assert published.json()["media_id"] == "media-1"

