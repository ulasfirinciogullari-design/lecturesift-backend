from datetime import timedelta
from pathlib import Path
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update

import lecturesift.rollout_routes as rollout_routes
from lecturesift.app import app
from lecturesift.billing_service import (
    ENGINE,
    USERS,
    BillingError,
    account_status,
    approve_manual_order,
    create_manual_order,
    record_usage,
    register_user,
    require_duration_entitlement,
    verify_email,
)
from lecturesift.jobs import JOBS
from lecturesift import config
import lecturesift.jobs as jobs_module
from lecturesift.tasks import _processing_error


def _account() -> tuple[str, str]:
    created = register_user(
        f"reliability-{uuid.uuid4()}@example.com",
        "Strong-test-password1",
        "Test",
        "User",
        country_code="TR",
    )
    verified = verify_email(created["verification_token"])
    return verified["user"]["id"], verified["token"]


def test_subscription_overflow_spends_extra_minutes(monkeypatch):
    user_id, _ = _account()
    monkeypatch.setattr(config, "BILLING_BANK_IBAN", "TR000000000000000000000000")
    monkeypatch.setattr(config, "BILLING_BANK_ACCOUNT_HOLDER", "Test Holder")
    monkeypatch.setattr(config, "BILLING_SUPPORT_EMAIL", "support@example.com")
    monkeypatch.setattr(config, "LEGAL_OPERATOR_NAME", "LectureSift Test")
    monkeypatch.setattr(config, "LEGAL_OPERATOR_ADDRESS", "Test Address 1")
    monkeypatch.setattr(config, "LEGAL_OPERATOR_COUNTRY", "TR")
    monkeypatch.setattr(config, "LEGAL_OPERATOR_PHONE", "+905551112233")
    monkeypatch.setattr(config, "LEGAL_OPERATOR_EMAIL", "support@example.com")
    order = create_manual_order(user_id, "plus", "monthly")
    approve_manual_order(order["reference"])
    with ENGINE.begin() as connection:
        connection.execute(update(USERS).where(USERS.c.id == user_id).values(credit_minutes=20))

    record_usage(user_id, f"usage-{uuid.uuid4()}", 2395 * 60)
    record_usage(user_id, f"usage-{uuid.uuid4()}", 10 * 60)
    status = account_status(user_id)
    assert status["used_minutes"] == 2405
    assert status["credit_minutes"] == 15
    assert status["remaining_minutes"] == 15
    with pytest.raises(BillingError, match="hesabında 15 dakika"):
        require_duration_entitlement(user_id, 16 * 60)


def test_rollout_health_checks_connections_and_worker(monkeypatch):
    monkeypatch.setattr(config, "CELERY_BROKER_URL", "redis://queue")
    monkeypatch.setattr(config, "REQUIRE_DURABLE_PROCESSING", True)
    monkeypatch.setattr(rollout_routes.JOBS, "redis_health", lambda: {"configured": True, "connected": True})
    monkeypatch.setattr(rollout_routes.STORAGE, "health", lambda: {"configured": True, "connected": True})
    monkeypatch.setattr(
        rollout_routes,
        "worker_health",
        lambda: {"configured": True, "reachable": True, "workers": 1},
    )
    body = rollout_routes.rollout_health()
    assert body["durable_processing_ready"] is True
    assert body["durable_processing_required"] is True
    assert body["worker"]["workers"] == 1


def test_job_history_is_owned_and_redacts_runtime_fields(tmp_path: Path):
    user_id, token = _account()
    job_id = f"history-{uuid.uuid4()}"
    job_dir = tmp_path / job_id
    job_dir.mkdir()
    JOBS.create(
        job_id,
        job_dir,
        {"billing_user_id": user_id, "output_language": "tr"},
        source_keys={"audio": ["jobs/private/source.mp4"]},
        celery_task_id="private-task-id",
        retention_seconds=int(timedelta(days=7).total_seconds()),
    )

    response = TestClient(app).get(
        "/jobs",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    item = next(row for row in response.json()["jobs"] if row["job_id"] == job_id)
    assert "source_keys" not in item
    assert "celery_task_id" not in item
    assert "billing_user_id" not in item["options"]


def test_worker_lock_fails_closed_when_redis_is_unavailable(tmp_path: Path, monkeypatch):
    class BrokenRedis:
        def lock(self, *args, **kwargs):
            raise RuntimeError("redis unavailable")

    monkeypatch.setattr(jobs_module, "WORK_DIR", tmp_path)
    monkeypatch.setattr(jobs_module, "REDIS_URL", "")
    store = jobs_module.JobStore()
    store._redis = BrokenRedis()
    with store.processing_lock("job-one") as acquired:
        assert acquired is False


def test_transient_pipeline_errors_are_retryable():
    assert _processing_error(
        {"status": "error", "error_code": "LS-AI-02", "technical_error": "rate limit"}
    ) is not None
    assert _processing_error(
        {"status": "error", "error_code": "LS-VIDEO-02", "technical_error": "bad codec"}
    ) is None


def test_expired_job_cleanup_removes_local_and_remote_files(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(jobs_module, "WORK_DIR", tmp_path)
    monkeypatch.setattr(jobs_module, "REDIS_URL", "")
    deleted = []
    monkeypatch.setattr(jobs_module.STORAGE, "delete_job", lambda job_id: deleted.append(job_id) or 2)
    store = jobs_module.JobStore()
    job_id = "expired-job"
    job_dir = tmp_path / job_id
    job_dir.mkdir()
    store.create(job_id, job_dir, {}, retention_seconds=60)
    store._jobs[job_id]["updated"] = 0
    store._flush_locked()

    assert store.cleanup_expired() == 1
    assert deleted == [job_id]
    assert not job_dir.exists()


def test_missing_completed_archive_returns_retryable_response(tmp_path: Path):
    user_id, token = _account()
    job_id = f"download-race-{uuid.uuid4()}"
    job_dir = tmp_path / job_id
    job_dir.mkdir()
    JOBS.create(
        job_id,
        job_dir,
        {"billing_user_id": user_id, "download_entitled": True},
    )
    JOBS.update(
        job_id,
        status="done",
        result_path=str(job_dir / "missing.zip"),
    )

    response = TestClient(app).get(
        f"/jobs/{job_id}/download",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "LS-JOB-04"
