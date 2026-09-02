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
from lecturesift.queue import celery_app
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

    record_usage(user_id, f"usage-{uuid.uuid4()}", 1795 * 60)
    record_usage(user_id, f"usage-{uuid.uuid4()}", 10 * 60)
    status = account_status(user_id)
    assert status["used_minutes"] == 1805
    assert status["credit_minutes"] == 15
    assert status["remaining_minutes"] == 15
    with pytest.raises(BillingError, match="hesabında 15 dakika"):
        require_duration_entitlement(user_id, 16 * 60)


def test_paid_minute_package_uses_its_own_job_limits(monkeypatch):
    user_id, _ = _account()
    monkeypatch.setattr(config, "BILLING_BANK_IBAN", "TR000000000000000000000000")
    monkeypatch.setattr(config, "BILLING_BANK_ACCOUNT_HOLDER", "Test Holder")
    monkeypatch.setattr(config, "BILLING_SUPPORT_EMAIL", "support@example.com")
    monkeypatch.setattr(config, "LEGAL_OPERATOR_NAME", "LectureSift Test")
    monkeypatch.setattr(config, "LEGAL_OPERATOR_ADDRESS", "Test Address 1")
    monkeypatch.setattr(config, "LEGAL_OPERATOR_COUNTRY", "TR")
    monkeypatch.setattr(config, "LEGAL_OPERATOR_PHONE", "+905551112233")
    monkeypatch.setattr(config, "LEGAL_OPERATOR_EMAIL", "support@example.com")
    order = create_manual_order(user_id, "credit", "one_time")
    approve_manual_order(order["reference"])

    status = account_status(user_id)
    assert status["plan"]["code"] == "free"
    assert status["job_entitlements"]["limits"]["max_files_per_job"] == 8
    assert status["job_entitlements"]["limits"]["max_minutes_per_job"] == 180
    require_duration_entitlement(user_id, 120 * 60, source_file_count=8)
    with pytest.raises(BillingError, match="en fazla 8 kaynak"):
        require_duration_entitlement(user_id, 60, source_file_count=9)


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


def test_celery_visibility_timeout_is_consistent():
    expected = config.CELERY_VISIBILITY_TIMEOUT_SECONDS
    assert 60 * 60 <= expected <= config.JOB_TTL_SECONDS
    assert celery_app.conf.visibility_timeout == expected
    assert celery_app.conf.broker_transport_options["visibility_timeout"] == expected
    assert celery_app.conf.result_backend_transport_options["visibility_timeout"] == expected


def test_vps_compose_keeps_datastores_private_and_trusts_only_internal_proxy():
    root = Path(__file__).resolve().parents[1]
    compose = (root / "compose.yaml").read_text(encoding="utf-8")
    postgres = compose.split("\n  postgres:", 1)[1].split("\n  redis:", 1)[0]
    redis = compose.split("\n  redis:", 1)[1].split("\nnetworks:", 1)[0]

    assert "--forwarded-allow-ips=*" in compose
    assert "LECTURESIFT_DB_ENV_FILE" in postgres
    assert "LECTURESIFT_ENV_FILE" not in postgres
    assert "ports:" not in postgres
    assert "ports:" not in redis
    assert "internal: true" in compose


def test_vps_restore_discards_stale_redis_aof_before_loading_snapshot():
    root = Path(__file__).resolve().parents[1]
    restore = (root / "deploy" / "restore.sh").read_text(encoding="utf-8")
    converter = (root / "deploy" / "redis_rdb_to_aof.sh").read_text(encoding="utf-8")

    assert 'find "$DATA_DIR" -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +' in converter
    assert 'SOURCE_RDB="/restore/redis-dump.rdb"' in converter
    assert 'DATA_DIR="/data"' in converter
    assert "/probe/redis_rdb_to_aof.sh" in restore
    assert "lecturesift-api-work:/api-work" in restore
    assert "lecturesift-worker-work:/worker-work" in restore
    assert "sha256sum --check --strict -- SHA256SUMS" in restore
    assert "--maintenance-db postgres" in restore
    assert "--wait --wait-timeout 600" in restore
    assert '"$ROOT_DIR/deploy/preflight.sh"' in restore
    assert "up -d --wait --wait-timeout 600 postgres redis" in restore
    assert "up -d --no-deps --wait --wait-timeout 600 api worker" in restore
    assert "up -d --no-deps --wait --wait-timeout 180 caddy" in restore


def test_vps_backup_credentials_are_not_injected_into_application_containers():
    root = Path(__file__).resolve().parents[1]
    runtime_example = (root / "deploy" / "env.example").read_text(encoding="utf-8")
    restic_example = (root / "deploy" / "restic.env.example").read_text(encoding="utf-8")
    backup = (root / "deploy" / "backup.sh").read_text(encoding="utf-8")
    restore_drill = (root / "deploy" / "restic_restore_rehearsal.sh").read_text(encoding="utf-8")
    service = (root / "deploy" / "lecturesift-backup.service").read_text(encoding="utf-8")

    assert "RESTIC_PASSWORD=" not in runtime_example
    assert "RESTIC_PASSWORD=" in restic_example
    assert "/etc/lecturesift/restic.env" in backup
    assert "/etc/lecturesift/restic.env" in restore_drill
    assert "LECTURESIFT_RESTIC_ENV_FILE=/etc/lecturesift/restic.env" in service
    assert "Requires=docker.service lecturesift.service" not in service


def test_vps_redis_cutover_is_logical_frozen_and_version_scoped():
    root = Path(__file__).resolve().parents[1]
    migration = (root / "deploy" / "migrate_redis_state.sh").read_text(encoding="utf-8")
    exporter = (root / "deploy" / "export_redis_state.py").read_text(encoding="utf-8")

    assert "LECTURESIFT_SOURCE_FROZEN" in migration
    assert "source-before.json" in migration and "source-after.json" in migration
    assert "lecturesift:jobs:v2" in migration
    assert "redis-dump.rdb" not in migration
    assert 'STATE_KEY = "lecturesift:jobs:v2"' in exporter
    assert '{"queued", "working"}' in exporter
    assert "processing_locks" in exporter


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


def test_job_store_write_fails_closed_when_distributed_lock_is_held(tmp_path: Path, monkeypatch):
    class HeldLock:
        def acquire(self, *, blocking):
            return False

    class LockedRedis:
        def lock(self, *args, **kwargs):
            return HeldLock()

    monkeypatch.setattr(jobs_module, "WORK_DIR", tmp_path)
    monkeypatch.setattr(jobs_module, "REDIS_URL", "")
    store = jobs_module.JobStore()
    store._redis = LockedRedis()

    with pytest.raises(RuntimeError, match="write lock"):
        store.create("fenced-job", tmp_path / "fenced-job", {})
    assert "fenced-job" not in store._jobs


def test_required_job_store_does_not_swallow_redis_read_or_write_failures(tmp_path: Path, monkeypatch):
    class AcquiredLock:
        def acquire(self, *, blocking):
            return True

        def release(self):
            return None

    class BrokenRedis:
        fail_read = True

        def lock(self, *args, **kwargs):
            return AcquiredLock()

        def get(self, key):
            if self.fail_read:
                raise RuntimeError("read failed")
            return ""

        def set(self, key, value):
            raise RuntimeError("write failed")

    monkeypatch.setattr(jobs_module, "WORK_DIR", tmp_path)
    monkeypatch.setattr(jobs_module, "REDIS_URL", "")
    monkeypatch.setattr(config, "REQUIRE_DURABLE_PROCESSING", True)
    store = jobs_module.JobStore()
    broken = BrokenRedis()
    store._redis = broken

    with pytest.raises(RuntimeError, match="job-store read"):
        store.get("missing")
    broken.fail_read = False
    with pytest.raises(RuntimeError, match="job-store write"):
        store.create("uncommitted-job", tmp_path / "uncommitted-job", {})


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
