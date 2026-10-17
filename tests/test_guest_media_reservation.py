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
        assert events == ["checked", "uploaded", "queued"]
        assert state["guest_reservation_deferred_to_worker"] is True
        # The worker is the only owner of the atomic reservation, after it has
        # independently downloaded and validated this accepted source.
        monkeypatch.setattr(tasks, "reserve_guest_job", lambda *args: events.append(args))
        tasks._enforce_uploaded_job_quota(
            "guest-user", "guest-media", 120,
            source_file_count=1, source_size_bytes=1024,
            document_mode=False, document_pages=0, ocr_pages=0,
        )
        assert events[-1] == ("guest-user", "guest-media", 2.0)
        assert state["celery_task_id"] == "synthetic-task"


@pytest.fixture
def isolated_dispatch(tmp_path, monkeypatch, guest_quota):
    """Install a dispatcher with a recording local pipeline, never real media work."""
    from lecturesift import pipeline

    source = tmp_path / "lesson.mp3"
    source.write_bytes(b"synthetic-media-metadata-only")
    state = {"job_id": "guest-media", "job_dir": str(tmp_path), "status": "accepted"}
    events = []
    control = {"queue_ready": True, "failure": None}

    def check(*args, **kwargs):
        events.append("checked")
        return billing.require_duration_entitlement(*args, **kwargs)

    def reserve(*args):
        events.append(("reserved", *args))

    def upload(*args):
        events.append("upload")
        if control["failure"] == "upload":
            raise RuntimeError("synthetic object upload unavailable")
        return ["jobs/guest-media/audio_001.mp3"], []

    def enqueue(*args):
        events.append("enqueue")
        if control["failure"] == "enqueue":
            raise RuntimeError("synthetic broker unavailable")
        return SimpleNamespace(id="synthetic-task")

    def local(*args):
        events.append("local_processing")
        state["status"] = "working"

    monkeypatch.setattr(durable_runtime, "JOBS", SimpleNamespace(
        get=lambda _job: state,
        update=lambda _job, **values: state.update(values),
    ))
    monkeypatch.setattr(durable_runtime, "STORAGE", SimpleNamespace(
        delete_keys=lambda keys: events.append("cleanup_remote"),
        error_code=lambda exc: "synthetic_storage_unavailable",
    ))
    monkeypatch.setattr(durable_runtime, "REQUIRE_DURABLE_PROCESSING", True)
    monkeypatch.setattr(durable_runtime, "_queue_ready", lambda: control["queue_ready"])
    monkeypatch.setattr(durable_runtime, "media_duration_seconds", lambda paths: 120)
    monkeypatch.setattr(durable_runtime, "estimate_eta_seconds", lambda *a, **kw: 10)
    monkeypatch.setattr(durable_runtime, "require_duration_entitlement", check)
    monkeypatch.setattr(durable_runtime, "reserve_guest_job", reserve)
    monkeypatch.setattr(durable_runtime, "_upload_job_sources", upload)
    monkeypatch.setattr(tasks, "process_uploaded_job", SimpleNamespace(delay=enqueue))
    # The install function intentionally captures the pipeline once. Register
    # restoration before installing our isolated closure; suppress startup hooks.
    monkeypatch.setattr(app, "process_job", app.process_job)
    monkeypatch.setattr(app.app, "add_event_handler", lambda *a, **kw: None)
    monkeypatch.setattr(pipeline, "process_job", local)
    monkeypatch.setattr(durable_runtime, "install_pipeline_enhancements", lambda: None)
    monkeypatch.setattr(durable_runtime, "_INSTALLED", False)
    durable_runtime.install_durable_runtime()
    return SimpleNamespace(source=source, state=state, events=events, control=control)


@pytest.mark.parametrize("failure", ["unavailable", "upload", "enqueue"])
def test_preprocessing_infrastructure_failure_preserves_guest_trial(isolated_dispatch, guest_quota, failure):
    runtime = isolated_dispatch
    runtime.control["queue_ready"] = failure != "unavailable"
    runtime.control["failure"] = failure

    app.process_job("guest-media", [runtime.source], {"billing_user_id": "guest-user"})

    assert runtime.state["status"] == "error"
    assert runtime.state["error_code"] == "LS-SYSTEM-01"
    assert runtime.state["worker_state"] == "unavailable"
    assert not any(isinstance(event, tuple) and event[0] == "reserved" for event in runtime.events)
    assert "local_processing" not in runtime.events
    assert guest_quota["remaining_minutes"] == 5
    if failure == "unavailable":
        assert runtime.events == ["checked"]
    elif failure == "upload":
        assert runtime.events == ["checked", "upload"]
    else:
        assert runtime.events == ["checked", "upload", "enqueue", "cleanup_remote"]


@pytest.mark.parametrize("failure", ["unavailable", "upload", "enqueue"])
def test_optional_local_fallback_rechecks_and_reserves_immediately_before_processing(
    isolated_dispatch, monkeypatch, failure,
):
    runtime = isolated_dispatch
    runtime.control.update(queue_ready=failure != "unavailable", failure=failure)
    monkeypatch.setattr(durable_runtime, "REQUIRE_DURABLE_PROCESSING", False)

    app.process_job("guest-media", [runtime.source], {"billing_user_id": "guest-user"})

    assert runtime.events[-3:] == ["checked", ("reserved", "guest-user", "guest-media", 2.0), "local_processing"]
    assert sum(isinstance(event, tuple) and event[0] == "reserved" for event in runtime.events) == 1
    assert runtime.state["guest_reservation_deferred_to_worker"] is False
    assert runtime.state["status"] == "working"


@pytest.mark.parametrize("rejected_by", ["recheck", "reservation"])
def test_local_fallback_rejection_never_starts_processing(isolated_dispatch, monkeypatch, rejected_by):
    runtime = isolated_dispatch
    runtime.control["queue_ready"] = False
    monkeypatch.setattr(durable_runtime, "REQUIRE_DURABLE_PROCESSING", False)
    checks = []

    def check(*args, **kwargs):
        checks.append(True)
        if rejected_by == "recheck" and len(checks) == 2:
            raise BillingError("Trial was consumed by another accepted source")
        return billing.require_duration_entitlement(*args, **kwargs)

    def reserve(*args):
        if rejected_by == "reservation":
            raise BillingError("Another worker reserved the anonymous trial")
        pytest.fail("Rejected quota reached reservation")

    monkeypatch.setattr(durable_runtime, "require_duration_entitlement", check)
    monkeypatch.setattr(durable_runtime, "reserve_guest_job", reserve)

    app.process_job("guest-media", [runtime.source], {"billing_user_id": "guest-user"})

    assert len(checks) == 2
    assert runtime.state["status"] == "error" and runtime.state["error_code"] == "LS-GUEST-04"
    assert runtime.state["worker_state"] == "rejected"
    assert "local_processing" not in runtime.events


def test_worker_real_reservation_allows_same_job_retry_but_rejects_second_queued_job(
    tmp_path, monkeypatch, guest_quota,
):
    from sqlalchemy import create_engine, select
    from lecturesift import rollout_service

    engine = create_engine(f"sqlite:///{tmp_path / 'guest-reservation.db'}")
    billing.METADATA.create_all(engine)
    monkeypatch.setattr(rollout_service, "ENGINE", engine)
    monkeypatch.setattr(rollout_service, "init_rollout_database", lambda: None)
    now = rollout_service.utcnow()
    with engine.begin() as connection:
        connection.execute(billing.USERS.insert().values(
            id="guest-user", email="synthetic@guest.lecturesift.invalid",
            password_salt="synthetic", password_hash="synthetic",
            credit_minutes=0, created_at=now,
        ))
        connection.execute(rollout_service.GUEST_TRIALS.insert().values(
            fingerprint_hash="a" * 64, user_id="guest-user", job_id=None,
            media_minutes=None, created_at=now, last_seen_at=now,
        ))
    monkeypatch.setattr(tasks, "reserve_guest_job", rollout_service.reserve_guest_job)
    arguments = dict(source_file_count=1, source_size_bytes=1024,
                     document_mode=False, document_pages=0, ocr_pages=0)
    try:
        tasks._enforce_uploaded_job_quota("guest-user", "first-job", 120, **arguments)
        tasks._enforce_uploaded_job_quota("guest-user", "first-job", 120, **arguments)
        with pytest.raises(BillingError, match="daha önce"):
            tasks._enforce_uploaded_job_quota("guest-user", "second-job", 120, **arguments)
        with engine.connect() as connection:
            trial = connection.execute(select(rollout_service.GUEST_TRIALS)).one()
        assert trial.job_id == "first-job" and trial.media_minutes == 2.0
    finally:
        engine.dispose()
