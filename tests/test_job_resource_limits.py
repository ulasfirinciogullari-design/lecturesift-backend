from __future__ import annotations

from collections import namedtuple
from pathlib import Path
from types import SimpleNamespace

import pytest

from lecturesift.errors import LectureSiftError
from lecturesift import resource_limits
import lecturesift.tasks as tasks_module


DiskUsage = namedtuple("DiskUsage", "total used free")


def test_workspace_budget_counts_nested_regular_files(tmp_path, monkeypatch):
    root = tmp_path / "work"
    job = root / "job-1"
    (job / "nested").mkdir(parents=True)
    (job / "source.bin").write_bytes(b"a" * 10)
    (job / "nested" / "result.bin").write_bytes(b"b" * 12)
    monkeypatch.setattr(resource_limits, "MAX_JOB_WORK_BYTES", 32)
    monkeypatch.setattr(resource_limits, "HOST_DISK_RESERVE_BYTES", 8)
    monkeypatch.setattr(
        resource_limits.shutil,
        "disk_usage",
        lambda _path: DiskUsage(100, 20, 80),
    )

    assert resource_limits.job_workspace_bytes(job, work_root=root) == 22
    assert resource_limits.enforce_job_workspace(job, work_root=root) == 22


def test_workspace_budget_rejects_oversize_and_preserves_disk_reserve(tmp_path, monkeypatch):
    root = tmp_path / "work"
    job = root / "job-2"
    job.mkdir(parents=True)
    (job / "source.bin").write_bytes(b"a" * 20)
    monkeypatch.setattr(resource_limits, "MAX_JOB_WORK_BYTES", 24)
    monkeypatch.setattr(resource_limits, "HOST_DISK_RESERVE_BYTES", 10)
    monkeypatch.setattr(
        resource_limits.shutil,
        "disk_usage",
        lambda _path: DiskUsage(100, 88, 12),
    )

    with pytest.raises(LectureSiftError) as over:
        resource_limits.enforce_job_workspace(
            job,
            additional_bytes=5,
            work_root=root,
        )
    assert over.value.code == "LS-STORAGE-03"
    assert over.value.status_code == 507

    with pytest.raises(LectureSiftError) as reserve:
        resource_limits.enforce_job_workspace(
            job,
            reserve_full_budget=True,
            work_root=root,
        )
    assert reserve.value.code == "LS-STORAGE-01"
    assert reserve.value.status_code == 507


def test_workspace_budget_rejects_paths_outside_work_root(tmp_path, monkeypatch):
    root = tmp_path / "work"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.setattr(resource_limits, "MAX_JOB_WORK_BYTES", 32)

    with pytest.raises(LectureSiftError, match="escaped") as error:
        resource_limits.enforce_job_workspace(outside, work_root=root)
    assert error.value.code == "LS-STORAGE-02"


def test_streaming_upload_checks_capacity_before_each_write():
    app_source = (Path(__file__).resolve().parents[1] / "lecturesift" / "app.py").read_text(
        encoding="utf-8"
    )

    check = "enforce_job_workspace("
    write = "output.write(chunk)"
    assert check in app_source
    assert app_source.index(check, app_source.index("async def _save_upload")) < app_source.index(
        write, app_source.index("async def _save_upload")
    )


def test_worker_capacity_failure_removes_entire_job_workspace(tmp_path, monkeypatch):
    job = tmp_path / "job"
    (job / "sources").mkdir(parents=True)
    (job / "sources" / "source.bin").write_bytes(b"source")
    (job / "large-derived.bin").write_bytes(b"derived")
    monkeypatch.setattr(tasks_module.JOBS, "update", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(tasks_module.STORAGE, "delete_keys", lambda _keys: 1)
    task = SimpleNamespace(max_retries=6, request=SimpleNamespace(retries=0))

    result = tasks_module._retry_or_fail(
        task,
        "capacity-job",
        LectureSiftError("LS-STORAGE-03", "Alan dolu", status_code=507),
        job_dir=job,
        audio_keys=["jobs/capacity-job/sources/audio_001.bin"],
    )

    assert result["error_code"] == "LS-STORAGE-03"
    assert not job.exists()
