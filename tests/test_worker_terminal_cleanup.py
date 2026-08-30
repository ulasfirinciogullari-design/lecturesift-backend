from pathlib import Path
from types import SimpleNamespace

import pytest

import lecturesift.tasks as tasks_module
from lecturesift.billing_service import BillingError
from lecturesift.errors import LectureSiftError


class RetryScheduled(Exception):
    pass


class FakeTask:
    max_retries = 6

    def __init__(self, retries: int):
        self.request = SimpleNamespace(retries=retries)

    def retry(self, **_kwargs):
        raise RetryScheduled


def source_tree(tmp_path: Path) -> tuple[Path, Path]:
    job_dir = tmp_path / "job"
    source = job_dir / "sources" / "audio_001.pdf"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"private source")
    return job_dir, source


def test_quota_rejection_removes_local_and_remote_sources(tmp_path, monkeypatch):
    job_dir, source = source_tree(tmp_path)
    updates = []
    deleted = []
    monkeypatch.setattr(tasks_module.JOBS, "update", lambda job_id, **values: updates.append((job_id, values)))
    monkeypatch.setattr(
        tasks_module.STORAGE,
        "delete_keys",
        lambda keys: deleted.extend(keys) or len(keys),
    )

    result = tasks_module._quota_error(
        "quota-job",
        BillingError("limit reached"),
        job_dir=job_dir,
        data={"source_keys": {"audio": ["jobs/quota/audio_001.pdf"]}},
        audio_keys=["jobs/quota/audio_001.pdf"],
    )

    assert result == {"job_id": "quota-job", "status": "error", "error_code": "LS-BILL-10"}
    assert updates[-1][1]["worker_state"] == "rejected"
    assert not source.exists()
    assert deleted == ["jobs/quota/audio_001.pdf"]


def test_non_transient_worker_error_removes_sources(tmp_path, monkeypatch):
    job_dir, source = source_tree(tmp_path)
    deleted = []
    monkeypatch.setattr(tasks_module.JOBS, "update", lambda *_args, **_values: None)
    monkeypatch.setattr(
        tasks_module.STORAGE,
        "delete_keys",
        lambda keys: deleted.extend(keys) or len(keys),
    )

    result = tasks_module._retry_or_fail(
        FakeTask(retries=0),
        "invalid-document",
        LectureSiftError("LS-DOC-04", "Belge okunamadı."),
        job_dir=job_dir,
        audio_keys=["jobs/invalid/audio_001.pdf"],
    )

    assert result["error_code"] == "LS-DOC-04"
    assert not source.exists()
    assert deleted == ["jobs/invalid/audio_001.pdf"]


def test_transient_retry_preserves_sources(tmp_path, monkeypatch):
    job_dir, source = source_tree(tmp_path)
    deleted = []
    monkeypatch.setattr(tasks_module.JOBS, "update", lambda *_args, **_values: None)
    monkeypatch.setattr(
        tasks_module.STORAGE,
        "delete_keys",
        lambda keys: deleted.extend(keys) or len(keys),
    )

    with pytest.raises(RetryScheduled):
        tasks_module._retry_or_fail(
            FakeTask(retries=0),
            "retry-document",
            RuntimeError("429 rate limit"),
            job_dir=job_dir,
            audio_keys=["jobs/retry/audio_001.pdf"],
        )

    assert source.exists()
    assert deleted == []


def test_exhausted_retry_becomes_terminal_and_removes_sources(tmp_path, monkeypatch):
    job_dir, source = source_tree(tmp_path)
    deleted = []
    monkeypatch.setattr(tasks_module.JOBS, "update", lambda *_args, **_values: None)
    monkeypatch.setattr(
        tasks_module.STORAGE,
        "delete_keys",
        lambda keys: deleted.extend(keys) or len(keys),
    )

    with pytest.raises(RuntimeError, match="rate limit"):
        tasks_module._retry_or_fail(
            FakeTask(retries=6),
            "failed-document",
            RuntimeError("429 rate limit"),
            job_dir=job_dir,
            audio_keys=["jobs/failed/audio_001.pdf"],
        )

    assert not source.exists()
    assert deleted == ["jobs/failed/audio_001.pdf"]


def test_guest_document_reservation_waits_for_actual_ocr_usage(monkeypatch):
    reservations = []
    checks = []
    monkeypatch.setattr(tasks_module, "is_guest_user", lambda _user_id: True)
    monkeypatch.setattr(
        tasks_module,
        "reserve_guest_job",
        lambda user_id, job_id, minutes: reservations.append((user_id, job_id, minutes)),
    )
    monkeypatch.setattr(
        tasks_module,
        "require_duration_entitlement",
        lambda user_id, duration, **limits: checks.append((user_id, duration, limits)),
    )

    tasks_module._enforce_uploaded_job_quota(
        "guest-user",
        "document-job",
        120,
        source_file_count=1,
        source_size_bytes=2048,
        document_mode=True,
        document_pages=4,
        ocr_pages=4,
    )

    assert reservations == []
    assert checks == [
        (
            "guest-user",
            120,
            {
                "source_file_count": 1,
                "source_size_bytes": 2048,
                "document_mode": True,
                "document_pages": 4,
                "ocr_pages": 4,
            },
        )
    ]


def test_guest_media_reservation_remains_idempotent_by_job(monkeypatch):
    reservations = []
    monkeypatch.setattr(tasks_module, "is_guest_user", lambda _user_id: True)
    monkeypatch.setattr(
        tasks_module,
        "reserve_guest_job",
        lambda user_id, job_id, minutes: reservations.append((user_id, job_id, minutes)),
    )
    monkeypatch.setattr(tasks_module, "require_duration_entitlement", lambda *_args, **_kwargs: {})

    tasks_module._enforce_uploaded_job_quota(
        "guest-user",
        "media-job",
        90,
        source_file_count=1,
        source_size_bytes=4096,
        document_mode=False,
        document_pages=0,
        ocr_pages=0,
    )

    assert reservations == [("guest-user", "media-job", 1.5)]


def test_empty_persisted_job_dir_is_never_a_cleanup_target():
    assert tasks_module._stored_job_dir({"job_dir": ""}) is None
    assert tasks_module._stored_job_dir({"job_dir": "."}) is None
