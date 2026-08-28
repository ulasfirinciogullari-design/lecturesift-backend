import uuid

import pytest
from fastapi.testclient import TestClient

from lecturesift import config
from lecturesift.app import app
from lecturesift.billing_service import register_user, verify_email
from lecturesift.jobs import JOBS
from lecturesift.rollout_routes import install_rollout_routes
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


def test_owner_session_can_open_admin_without_exposing_admin_token(monkeypatch):
    email = f"owner-{uuid.uuid4()}@example.com"
    created = register_user(email, "Strong-test-password1", "Site", "Owner", country_code="TR")
    token = verify_email(created["verification_token"])["token"]
    monkeypatch.setattr(config, "BILLING_ADMIN_EMAILS", {email.casefold()})
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {token}"}
    account = client.get("/billing/me", headers=headers)
    overview = client.get("/billing/admin/overview", headers=headers)
    assert account.status_code == 200
    assert account.json()["account"]["is_admin"] is True
    assert overview.status_code == 200


def test_admin_can_monitor_processing_jobs_without_internal_paths(monkeypatch):
    install_rollout_routes(app)
    email = f"jobs-owner-{uuid.uuid4()}@example.com"
    created = register_user(email, "Strong-test-password1", "Jobs", "Owner", country_code="TR")
    token = verify_email(created["verification_token"])["token"]
    user_id = created["user"]["id"]
    monkeypatch.setattr(config, "BILLING_ADMIN_EMAILS", {email.casefold()})
    job_id = f"admin-job-{uuid.uuid4()}"
    JOBS.create(job_id, config.WORK_DIR / job_id, {"billing_user_id": user_id, "language": "tr"})
    try:
        response = TestClient(app).get(
            "/billing/admin/jobs",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        item = next(job for job in response.json()["jobs"] if job["job_id"] == job_id)
        assert item["owner_id"] == user_id
        assert item["status"] == "queued"
        assert "job_dir" not in item
        assert "billing_user_id" not in item["options"]
    finally:
        JOBS.delete_for_user(user_id)
