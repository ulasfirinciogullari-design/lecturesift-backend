"""Rejected media must not consume the anonymous single-use processing trial."""

from dataclasses import asdict, replace
from types import SimpleNamespace

import pytest

from main import app as _installed_app  # noqa: F401: install the production dispatcher
from lecturesift import app, billing_service as billing, durable_runtime, tasks
from lecturesift.billing_service import BillingError


@pytest.fixture
def guest_quota(monkeypatch):
    # Small explicit limits keep the regression independent of catalog changes
    # and avoid creating large files while exercising real entitlement checks.
    plan = replace(
        billing.PLAN_BY_CODE["guest"],
        minutes=5,
        max_minutes_per_job=5,
        max_files_per_job=1,
        max_media_upload_mb=1,
    )
    status = {"plan": asdict(plan), "remaining_minutes": 5, "used_minutes": 0}
    monkeypatch.setattr(billing, "require_job_entitlement", lambda _user_id: status)
    for module in (tasks, durable_runtime):
        monkeypatch.setattr(module, "is_guest_user", lambda _user_id: True)
    return status


@pytest.mark.parametrize("rejected", ["file_count", "file_size", "duration"])
def test_worker_media_rejection_does_not_reserve_trial(monkeypatch, guest_quota, rejected):
    reservations = []
    monkeypatch.setattr(tasks, "reserve_guest_job", lambda *args: reservations.append(args))
    with pytest.raises(BillingError):
        tasks._enforce_uploaded_job_quota(
            "guest-user", "guest-media", 301 if rejected == "duration" else 60,
            source_file_count=2 if rejected == "file_count" else 1,
            source_size_bytes=1024 * 1024 + 1 if rejected == "file_size" else 1024,
            document_mode=False, document_pages=0, ocr_pages=0,
        )
    assert reservations == []
    assert guest_quota["remaining_minutes"] == 5


def test_worker_valid_media_checks_then_reserves_once(monkeypatch, guest_quota):
    events = []

    def check(*args, **kwargs):
        result = billing.require_duration_entitlement(*args, **kwargs)
        events.append("checked")
        return result

    monkeypatch.setattr(tasks, "require_duration_entitlement", check)
    monkeypatch.setattr(tasks, "reserve_guest_job", lambda *args: events.append(args))
    tasks._enforce_uploaded_job_quota(
        "guest-user", "guest-media", 120,
        source_file_count=1, source_size_bytes=1024,
        document_mode=False, document_pages=0, ocr_pages=0,
    )
    assert events == ["checked", ("guest-user", "guest-media", 2.0)]


def test_minutes_only_guest_check_preserves_trial_on_rejection(monkeypatch, guest_quota):
    reservations = []
    monkeypatch.setattr(tasks, "reserve_guest_job", lambda *args: reservations.append(args))
    with pytest.raises(BillingError):
        tasks._enforce_minutes("guest-user", "too-long", 301)
    assert reservations == []
    tasks._enforce_minutes("guest-user", "valid", 120)
    assert reservations == [("guest-user", "valid", 2.0)]


@pytest.mark.parametrize("rejected", ["file_count", "file_size", "duration", None])
def test_dispatch_checks_all_media_limits_before_reservation_and_queue(
    tmp_path, monkeypatch, guest_quota, rejected,
):
    paths = []
    for index in range(2 if rejected == "file_count" else 1):
        source = tmp_path / f"source-{index}.mp3"
        with source.open("wb") as stream:
            stream.truncate(1024 * 1024 + 1 if rejected == "file_size" else 1024)
        paths.append(source)
    state = {"job_id": "guest-media", "job_dir": str(tmp_path), "status": "accepted"}
    events = []

    def check(*args, **kwargs):
        result = billing.require_duration_entitlement(*args, **kwargs)
        events.append("checked")
        return result

    def upload(*_args):
        events.append("uploaded")
        return ["jobs/guest-media/audio_001.mp3"], []

    def queue(*_args):
        events.append("queued")
        return SimpleNamespace(id="synthetic-task")

    monkeypatch.setattr(durable_runtime, "JOBS", SimpleNamespace(
        get=lambda _job_id: state,
        update=lambda _job_id, **values: state.update(values),
    ))
    monkeypatch.setattr(durable_runtime, "_queue_ready", lambda: True)
    monkeypatch.setattr(durable_runtime, "media_duration_seconds",
                        lambda _paths: 301 if rejected == "duration" else 120)
    monkeypatch.setattr(durable_runtime, "estimate_eta_seconds", lambda *args, **kwargs: 10)
    monkeypatch.setattr(durable_runtime, "require_duration_entitlement", check)
    monkeypatch.setattr(durable_runtime, "reserve_guest_job", lambda *args: events.append(args))
    monkeypatch.setattr(durable_runtime, "_upload_job_sources", upload)
    monkeypatch.setattr(tasks, "process_uploaded_job", SimpleNamespace(delay=queue))

    app.process_job("guest-media", paths, {"billing_user_id": "guest-user"})

    if rejected:
        assert state["status"] == "error"
        assert state["error_code"] == "LS-GUEST-04"
        assert events == []
        assert all(path.exists() for path in paths)
    else:
        assert state["status"] == "queued"
        assert events == ["checked", ("guest-user", "guest-media", 2.0), "uploaded", "queued"]
        assert state["celery_task_id"] == "synthetic-task"
