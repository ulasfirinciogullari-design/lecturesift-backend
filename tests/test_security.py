import uuid

import pytest
from fastapi.testclient import TestClient

from lecturesift.app import app
from lecturesift.security import RateLimitExceeded, RateLimiter


def test_rate_limiter_hashes_identifiers_and_blocks_repeated_attempts():
    limiter = RateLimiter()
    identity = f"private-{uuid.uuid4()}@example.com"
    limiter.check("test", identity, limit=2, window_seconds=60)
    limiter.check("test", identity, limit=2, window_seconds=60)
    with pytest.raises(RateLimitExceeded):
        limiter.check("test", identity, limit=2, window_seconds=60)
    assert identity not in " ".join(limiter._local)


def test_login_rate_limit_returns_retry_after_without_account_disclosure():
    client = TestClient(app)
    email = f"rate-{uuid.uuid4()}@example.com"
    responses = [
        client.post("/billing/login", json={"email": email, "password": "Wrong-password1"})
        for _ in range(11)
    ]
    assert all(response.status_code == 401 for response in responses[:10])
    assert responses[-1].status_code == 429
    assert responses[-1].headers.get("retry-after")
    assert responses[-1].json()["detail"]["code"] == "LS-SEC-01"


def test_cors_accepts_lecturesift_previews_and_rejects_unknown_origins():
    client = TestClient(app)
    headers = {
        "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Headers": "authorization,content-type",
    }
    allowed = client.options(
        "/health",
        headers={"Origin": "https://deploy-preview-16--clever-horse-22b1a8.netlify.app", **headers},
    )
    blocked = client.options(
        "/health",
        headers={"Origin": "https://example-attacker.invalid", **headers},
    )
    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"].startswith("https://deploy-preview-")
    assert "access-control-allow-origin" not in blocked.headers
