"""Worker time limits, the duplicate-delivery fix and the stuck-job sweep."""

from __future__ import annotations

import time
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from celery.exceptions import SoftTimeLimitExceeded

import lecturesift.durable_runtime as durable_runtime
import lecturesift.jobs as jobs_module
import lecturesift.pipeline as pipeline
import lecturesift.queue as queue
import lecturesift.tasks as tasks_module
from lecturesift import config
from lecturesift.billing import LEGACY_PLANS, PLAN_BY_CODE
from lecturesift.job_eta import BASELINE_SECONDS_PER_MEDIA_MINUTE, MAX_SECONDS_PER_MEDIA_MINUTE, eta_seconds

SOURCE_KEY = "jobs/stuck/audio_001.mp3"


class RetryScheduled(Exception):
    pass


class FakeTask:
    max_retries = 6

    def __init__(self, retries: int):
        self.request = SimpleNamespace(retries=retries)

    def retry(self, **_kwargs):
        raise RetryScheduled


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setattr(jobs_module, "WORK_DIR", tmp_path)
    monkeypatch.setattr(jobs_module, "REDIS_URL", "")
    monkeypatch.setattr(tasks_module, "WORK_DIR", tmp_path)
    monkeypatch.setattr(config, "current_maintenance_mode", lambda: "off")
    monkeypatch.delenv("LECTURESIFT_WORKER", raising=False)
    job_store = jobs_module.JobStore()
    for module in (tasks_module, durable_runtime, pipeline):
        monkeypatch.setattr(module, "JOBS", job_store)
    deleted: list[str] = []
    monkeypatch.setattr(tasks_module.STORAGE, "delete_keys", lambda keys: deleted.extend(keys) or len(keys))
    job_store.deleted_keys = deleted
    return job_store


def _job(store, tmp_path, job_id: str, status: str = "working", **extra) -> None:
    job_dir = tmp_path / job_id
    source = job_dir / "sources" / "audio_001.mp3"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"private source")
    store.create(
        job_id, job_dir, {"billing_user_id": "synthetic-user"},
        status=status, source_keys={"audio": [SOURCE_KEY]}, **extra,
    )


def _assert_failed_normally(store, tmp_path, job_id: str) -> None:
    data = store.metadata(job_id)
    assert data["status"] == "error" and data["stage"] == "error" and data["worker_state"] == "failed"
    assert data["error_code"] == "LS-SYSTEM-01"
    assert data["error"].startswith("İşlem beklenmeyen bir nedenle tamamlanamadı")
    assert data["_failure_notification"]["state"] == "pending"
    assert SOURCE_KEY in store.deleted_keys
    assert not (tmp_path / job_id / "sources").exists()


def test_time_limits_are_derived_from_the_longest_plan_job():
    plans = (*PLAN_BY_CODE.values(), *LEGACY_PLANS)
    longest_minutes = max(plan.max_minutes_per_job for plan in plans)
    assert longest_minutes == PLAN_BY_CODE["business"].max_minutes_per_job == 1440
    largest_upload = max(plan.max_media_upload_mb for plan in plans) * 1024 * 1024
    # Every ETA the model can show for an admitted job, whatever median it has
    # learned (it clamps learned rates), fits under the soft limit.
    for rate in (BASELINE_SECONDS_PER_MEDIA_MINUTE, MAX_SECONDS_PER_MEDIA_MINUTE, 10 * MAX_SECONDS_PER_MEDIA_MINUTE):
        for style in ("standard", "detailed", "exam"):
            estimate = eta_seconds(longest_minutes, largest_upload, rate, summary_style=style)
            assert estimate <= queue.JOB_SOFT_TIME_LIMIT_SECONDS
    assert queue.JOB_SOFT_TIME_LIMIT_SECONDS == 83 * 3600
    assert queue.JOB_HARD_TIME_LIMIT_SECONDS == queue.JOB_SOFT_TIME_LIMIT_SECONDS + 600
    assert queue.celery_app.conf.task_soft_time_limit == queue.JOB_SOFT_TIME_LIMIT_SECONDS
    assert queue.celery_app.conf.task_time_limit == queue.JOB_HARD_TIME_LIMIT_SECONDS
    assert queue.STUCK_JOB_AFTER_SECONDS > queue.JOB_HARD_TIME_LIMIT_SECONDS
    assert queue.STUCK_JOB_AFTER_SECONDS > config.CELERY_VISIBILITY_TIMEOUT_SECONDS
    assert tasks_module.process_uploaded_job.Request is tasks_module.JobRequest


def test_pipeline_lets_the_soft_time_limit_end_the_job(store, tmp_path, monkeypatch):
    _job(store, tmp_path, "pdf-job")
    pdf = tmp_path / "pdf-job" / "sources" / "lesson.pdf"
    pdf.write_bytes(b"%PDF-")

    def hang(*args, **kwargs):
        raise SoftTimeLimitExceeded()

    monkeypatch.setattr(pipeline, "extract_documents", hang)
    with pytest.raises(SoftTimeLimitExceeded):
        pipeline._process_job("pdf-job", [pdf], {"billing_user_id": "synthetic-user"})
    # Not recorded as a transient system error, which the task would retry.
    assert store.metadata("pdf-job")["status"] == "working"


def test_soft_time_limit_fails_the_job_through_the_normal_path_without_retry(store, tmp_path, monkeypatch):
    _job(store, tmp_path, "slow-job", status="queued")

    def time_out(*args, **kwargs):
        raise SoftTimeLimitExceeded()

    monkeypatch.setattr(tasks_module, "_download_job_sources", time_out)
    monkeypatch.setattr(tasks_module, "_retry_or_fail", lambda *a, **kw: pytest.fail("must not retry"))
    result = tasks_module.process_uploaded_job("slow-job", [SOURCE_KEY], {"billing_user_id": "synthetic-user"})
    assert result == {"job_id": "slow-job", "status": "error", "error_code": "LS-SYSTEM-01"}
    data = store.metadata("slow-job")
    assert data["technical_error"] == "job_soft_time_limit"
    assert data["_failure_notification"]["state"] == "pending"
    assert SOURCE_KEY in store.deleted_keys


def test_hard_time_limit_fails_the_stored_job_from_the_worker_process(store, tmp_path, monkeypatch):
    _job(store, tmp_path, "killed-job")
    _job(store, tmp_path, "finished-job", status="done")
    calls = []
    monkeypatch.setattr(tasks_module.Request, "on_timeout", lambda self, soft, timeout: calls.append(soft))

    def request(job_id):
        item = object.__new__(tasks_module.JobRequest)
        item._args = [job_id, [SOURCE_KEY], {}, None]
        item._kwargs = {}
        return item

    request("killed-job").on_timeout(True, 50400)
    assert store.metadata("killed-job")["status"] == "working"
    request("killed-job").on_timeout(False, 51000)
    _assert_failed_normally(store, tmp_path, "killed-job")
    assert store.metadata("killed-job")["technical_error"] == "job_hard_time_limit:51000s"
    request("finished-job").on_timeout(False, 51000)
    assert store.metadata("finished-job")["status"] == "done"
    assert calls == [True, False, False]


def test_failed_job_is_never_restarted_by_a_late_delivery(store, tmp_path, monkeypatch):
    _job(store, tmp_path, "failed-job", status="error", error_code="LS-SYSTEM-01")
    monkeypatch.setattr(tasks_module, "_download_job_sources", lambda *a, **kw: pytest.fail("must not restart"))
    result = tasks_module.process_uploaded_job("failed-job", [SOURCE_KEY], {"billing_user_id": "synthetic-user"})
    assert result == {"job_id": "failed-job", "status": "error"}
    assert store.metadata("failed-job")["status"] == "error"
    assert SOURCE_KEY in store.deleted_keys


def test_duplicate_delivery_retries_while_the_lock_is_taken(store, tmp_path):
    _job(store, tmp_path, "busy-job")
    with pytest.raises(RetryScheduled):
        tasks_module._job_locked_elsewhere(FakeTask(retries=5), "busy-job")


def test_duplicate_delivery_reports_the_real_outcome_or_leaves_a_live_owner(store, tmp_path, monkeypatch):
    _job(store, tmp_path, "done-job", status="done")
    _job(store, tmp_path, "owned-job")
    assert tasks_module._job_locked_elsewhere(FakeTask(6), "done-job") == {"job_id": "done-job", "status": "done"}
    monkeypatch.setattr(store, "processing_lock_held", lambda job_id: True)
    assert tasks_module._job_locked_elsewhere(FakeTask(6), "owned-job")["status"] == "duplicate"
    assert store.metadata("owned-job")["status"] == "working"


def test_duplicate_delivery_never_leaves_an_ownerless_job_working(store, tmp_path, monkeypatch):
    _job(store, tmp_path, "orphan-job")
    monkeypatch.setattr(store, "processing_lock_held", lambda job_id: False)
    result = tasks_module._job_locked_elsewhere(FakeTask(6), "orphan-job")
    assert result == {"job_id": "orphan-job", "status": "error", "error_code": "LS-SYSTEM-01"}
    _assert_failed_normally(store, tmp_path, "orphan-job")


def test_processing_lock_held_reads_redis_and_fails_safe(store):
    class Redis:
        def __init__(self, held):
            self.held = held

        def exists(self, key):
            if self.held is None:
                raise RuntimeError("redis unavailable")
            assert key == "lecturesift:job:some-job:processing"
            return int(self.held)

    for held, expected in ((True, True), (False, False), (None, True)):
        store._redis = Redis(held)
        assert store.processing_lock_held("some-job") is expected
    store._redis = None


def test_sweep_fails_only_jobs_that_can_no_longer_finish(store, tmp_path, monkeypatch):
    _job(store, tmp_path, "stuck-working")
    _job(store, tmp_path, "stuck-queued", status="queued")
    _job(store, tmp_path, "fresh-working")
    _job(store, tmp_path, "locked-working")
    _job(store, tmp_path, "old-done", status="done")
    stale = time.time() - queue.STUCK_JOB_AFTER_SECONDS - 60
    for job_id in ("stuck-working", "stuck-queued", "locked-working", "old-done"):
        store._jobs[job_id]["updated"] = stale
    # Queued before every other unfinished job, so nothing runs ahead of it.
    store._jobs["stuck-queued"]["updated"] = stale - 60
    store._flush_locked()
    real_lock = store.processing_lock

    @contextmanager
    def lock(job_id):
        if job_id == "locked-working":
            yield False
            return
        with real_lock(job_id) as acquired:
            yield acquired

    monkeypatch.setattr(store, "processing_lock", lock)
    assert durable_runtime.sweep_stuck_jobs() == 2
    for job_id in ("stuck-working", "stuck-queued"):
        _assert_failed_normally(store, tmp_path, job_id)
        assert store.metadata(job_id)["technical_error"] == "stuck_job_sweep"
    assert store.metadata("fresh-working")["status"] == "working"
    assert store.metadata("locked-working")["status"] == "working"
    assert store.metadata("old-done")["status"] == "done"
    assert durable_runtime.sweep_stuck_jobs() == 0


def test_sweep_waits_during_maintenance_and_on_the_worker(store, tmp_path, monkeypatch):
    _job(store, tmp_path, "stuck-job")
    store._jobs["stuck-job"]["updated"] = 0
    store._flush_locked()
    monkeypatch.setattr(config, "current_maintenance_mode", lambda: "drain")
    assert durable_runtime.sweep_stuck_jobs() == 0
    monkeypatch.setattr(config, "current_maintenance_mode", lambda: "off")
    monkeypatch.setenv("LECTURESIFT_WORKER", "1")
    assert durable_runtime.sweep_stuck_jobs() == 0
    assert store.metadata("stuck-job")["status"] == "working"


def test_sweep_never_overrides_progress_recorded_after_its_check(store, tmp_path):
    _job(store, tmp_path, "racing-job")
    stale_before = time.time() - 60
    assert tasks_module.fail_unfinished_job("racing-job", "stuck_job_sweep", stale_before=stale_before) is False
    assert store.metadata("racing-job")["status"] == "working"


def test_each_job_gets_limits_sized_from_its_own_length():
    short_soft, short_hard = queue.job_time_limits(20, 50 * 1024 * 1024, summary_style="detailed")
    assert queue.JOB_MIN_SOFT_TIME_LIMIT_SECONDS < short_soft <= 2 * 3600
    assert short_hard == short_soft + queue.JOB_HARD_LIMIT_GRACE_SECONDS
    # Documents and very short files still get the floor for download, OCR and AI calls.
    assert queue.job_time_limits(0.1)[0] == queue.JOB_MIN_SOFT_TIME_LIMIT_SECONDS
    assert queue.job_time_limits(3, source_kind="document")[0] == queue.JOB_MIN_SOFT_TIME_LIMIT_SECONDS
    # Never above the global worst case, and every ETA the model can show fits.
    assert queue.job_time_limits(10_000, 2**40, summary_style="detailed")[0] == queue.JOB_SOFT_TIME_LIMIT_SECONDS
    for minutes in (1, 20, 90, 600, 1440):
        soft, _hard = queue.job_time_limits(minutes, 0, summary_style="exam")
        assert eta_seconds(minutes, 0, 10 * MAX_SECONDS_PER_MEDIA_MINUTE, summary_style="exam") <= soft


def test_a_started_short_job_is_swept_after_its_own_deadline(store, tmp_path):
    soft, hard = queue.job_time_limits(20)
    started = time.time() - 9 * 3600
    for job_id in ("dead-short", "live-short"):
        _job(store, tmp_path, job_id, time_limits=[soft, hard])
        store._jobs[job_id]["updated"] = started
    store._jobs["dead-short"]["processing_deadline"] = started + hard
    # A delivery that started later, with the global worst-case limit.
    store._jobs["live-short"]["processing_deadline"] = time.time() - 600 + queue.JOB_HARD_TIME_LIMIT_SECONDS
    # Not started yet: it may wait behind the longest job, so the global threshold applies.
    _job(store, tmp_path, "queued-short", status="queued", time_limits=[soft, hard])
    store._jobs["queued-short"]["updated"] = started
    store._flush_locked()
    assert started + hard + queue.JOB_REDELIVERY_WINDOW_SECONDS < time.time()

    assert durable_runtime.sweep_stuck_jobs() == 1
    _assert_failed_normally(store, tmp_path, "dead-short")
    assert store.metadata("dead-short")["technical_error"] == "stuck_job_sweep"
    assert store.metadata("live-short")["status"] == "working"
    assert store.metadata("queued-short")["status"] == "queued"


def test_a_delivery_records_the_deadline_of_its_own_limit(store, tmp_path, monkeypatch):
    _job(store, tmp_path, "limited-job", status="queued")

    def stop(*_args):
        raise RuntimeError("synthetic stop after the start was recorded")

    monkeypatch.setattr(tasks_module, "_download_job_sources", stop)
    monkeypatch.setattr(tasks_module, "_retry_or_fail", lambda *args, **kwargs: {"status": "stopped"})
    task = tasks_module.process_uploaded_job
    task.push_request(timelimit=(5400, 4800), retries=0)
    try:
        before = time.time()
        task.run("limited-job", [SOURCE_KEY], {"billing_user_id": "synthetic-user"})
    finally:
        task.pop_request()
    deadline = store.metadata("limited-job")["processing_deadline"]
    assert before + 5400 <= deadline <= time.time() + 5400


def test_new_and_recovered_jobs_are_enqueued_with_their_own_limits():
    sent = []
    task = SimpleNamespace(
        apply_async=lambda args, **limits: sent.append((args, limits)) or "sent",
        delay=lambda *args: sent.append((args, None)) or "sent",
    )
    durable_runtime._enqueue_uploaded_job(task, "job", ["key"], {}, [], (3600, 4200))
    durable_runtime._enqueue_uploaded_job(
        task, "old", ["key"], {}, [], durable_runtime.stored_time_limits({"job_id": "old"}),
    )
    assert sent == [
        (("job", ["key"], {}, None), {"soft_time_limit": 3600, "time_limit": 4200}),
        (("old", ["key"], {}, None), None),
    ]
    assert durable_runtime.stored_time_limits({"time_limits": [3600, 4200]}) == (3600, 4200)
    assert durable_runtime.stored_time_limits({"time_limits": ["bad"]}) is None


def test_a_queued_job_behind_two_maximum_jobs_is_not_swept(store, tmp_path):
    max_limits = [queue.JOB_SOFT_TIME_LIMIT_SECONDS, queue.JOB_HARD_TIME_LIMIT_SECONDS]
    short_limits = list(queue.job_time_limits(20))
    queued_at = time.time() - queue.JOB_HARD_TIME_LIMIT_SECONDS - queue.JOB_REDELIVERY_WINDOW_SECONDS - 3600
    # The first maximum job is running, the second waits, and the short one waits behind both.
    _job(store, tmp_path, "max-running", time_limits=max_limits, queued_at=queued_at,
         processing_deadline=time.time() + 3600)
    _job(store, tmp_path, "max-waiting", status="queued", time_limits=max_limits, queued_at=queued_at + 1)
    _job(store, tmp_path, "short-waiting", status="queued", time_limits=short_limits, queued_at=queued_at + 2)
    for job_id in ("max-running", "max-waiting", "short-waiting"):
        store._jobs[job_id]["updated"] = queued_at
    store._flush_locked()
    # Old enough for any single-job threshold, still valid behind the queue.
    assert time.time() - queued_at > queue.STUCK_JOB_AFTER_SECONDS

    assert durable_runtime.sweep_stuck_jobs() == 0
    for job_id in ("max-running", "max-waiting", "short-waiting"):
        assert store.metadata(job_id)["status"] in {"queued", "working"}

    # With nothing ahead any more, its own limit and the redelivery window have passed.
    for job_id in ("max-running", "max-waiting"):
        store.update(job_id, status="done")
    assert durable_runtime.sweep_stuck_jobs() == 1
    _assert_failed_normally(store, tmp_path, "short-waiting")
