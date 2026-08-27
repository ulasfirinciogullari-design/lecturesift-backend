from pathlib import Path

import lecturesift.jobs as jobs_module


def test_job_store_persists_and_reloads(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs_module, "WORK_DIR", tmp_path)

    first = jobs_module.JobStore()
    job_dir = tmp_path / "job-1"
    job_dir.mkdir()
    first.create(
        "job-1",
        job_dir,
        {"output_language": "tr"},
        source_type="upload",
    )
    first.update("job-1", status="working", percent=42, stage="transcription")

    state_path = tmp_path / "jobs-state.json"
    assert state_path.exists()

    second = jobs_module.JobStore()
    restored = second.get("job-1")

    assert restored is not None
    assert restored["status"] == "working"
    assert restored["percent"] == 42
    assert restored["stage"] == "transcription"
    assert restored["job_dir"] == str(job_dir)
    assert second.recoverable()[0]["job_id"] == "job-1"


def test_job_store_cleanup_removes_expired_state(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs_module, "WORK_DIR", tmp_path)
    monkeypatch.setattr(jobs_module, "JOB_TTL_SECONDS", 1)

    store = jobs_module.JobStore()
    job_dir = tmp_path / "job-old"
    job_dir.mkdir()
    store.create("job-old", job_dir, {})
    store.update("job-old", updated=0)

    # update() refreshes the timestamp, so directly age the persisted record for this unit test.
    store._jobs["job-old"]["updated"] = 0
    with store._lock:
        store._flush_locked()

    assert store.cleanup_expired() == 1
    assert store.get("job-old") is None
    assert not job_dir.exists()

    reloaded = jobs_module.JobStore()
    assert reloaded.get("job-old") is None
