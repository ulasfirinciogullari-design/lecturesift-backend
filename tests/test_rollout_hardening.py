import uuid
import json
import threading
from pathlib import Path

from fastapi.testclient import TestClient
from botocore.exceptions import ClientError, EndpointConnectionError

from lecturesift.billing_service import register_user, verify_email
from lecturesift.storage import ObjectStorage
import lecturesift.jobs as jobs_module
import lecturesift.duration as duration_module
import lecturesift.durable_runtime as durable_runtime_module
import lecturesift.app as app_module
import lecturesift.storage as storage_module
import lecturesift.tasks as tasks_module
from lecturesift.jobs import JobStore
from main import app


client = TestClient(app)


def new_token() -> str:
    email = f"hardening-{uuid.uuid4()}@example.com"
    created = register_user(email, "Strong-test-password1", "Test", "User", country_code="TR")
    return verify_email(created["verification_token"])["token"]


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_guest_trial_survives_network_change():
    device = f"stable-device-{uuid.uuid4()}"
    first = client.post(
        "/billing/guest-session",
        headers={"x-forwarded-for": "198.51.100.10", "user-agent": "LectureSift-Test"},
        json={"device_id": device},
    )
    second = client.post(
        "/billing/guest-session",
        headers={"x-forwarded-for": "203.0.113.22", "user-agent": "LectureSift-Test"},
        json={"device_id": device},
    )
    assert first.status_code == second.status_code == 200
    assert first.json()["account"]["user"]["id"] == second.json()["account"]["user"]["id"]
    assert second.json()["resumed"] is True


def test_same_instagram_handle_cannot_reward_two_accounts():
    first_token = new_token()
    second_token = new_token()
    handle = f"shared_{uuid.uuid4().hex[:12]}"
    first = client.post(
        "/billing/instagram-reward",
        headers=auth(first_token),
        json={"handle": handle},
    )
    second = client.post(
        "/billing/instagram-reward",
        headers=auth(second_token),
        json={"handle": handle},
    )
    assert first.status_code == 200
    assert second.status_code == 400


def test_output_publication_excludes_original_source_media(tmp_path: Path):
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    source = job_dir / "part_001.mp4"
    source.write_bytes(b"source")
    remote = job_dir / "remote.webm"
    remote.write_bytes(b"remote")
    nested = job_dir / "sources" / "audio_001.mp4"
    nested.parent.mkdir()
    nested.write_bytes(b"nested")
    output = job_dir / "LectureSift_Study_Pack.zip"
    output.write_bytes(b"zip")

    assert ObjectStorage._is_source_media(source, job_dir, source.name) is True
    assert ObjectStorage._is_source_media(remote, job_dir, remote.name) is True
    assert ObjectStorage._is_source_media(nested, job_dir, "sources/audio_001.mp4") is True
    assert ObjectStorage._is_source_media(output, job_dir, output.name) is False


def test_worker_downloads_multiple_private_sources_concurrently_in_original_order(tmp_path, monkeypatch):
    gate = threading.Barrier(2, timeout=3)
    monkeypatch.setattr(tasks_module, "SOURCE_DOWNLOAD_PARALLELISM", 2)

    def download(key: str, destination: Path) -> Path:
        gate.wait()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(key, encoding="utf-8")
        return destination

    monkeypatch.setattr(tasks_module.STORAGE, "download_file", download)
    paths = tasks_module._download_sources(
        "parallel-job",
        "audio",
        ["jobs/source-first.mp4", "jobs/source-second.mp4"],
        tmp_path,
    )
    assert [path.name for path in paths] == ["audio_001.mp4", "audio_002.mp4"]
    assert [path.read_text(encoding="utf-8") for path in paths] == [
        "jobs/source-first.mp4",
        "jobs/source-second.mp4",
    ]


def test_web_uploads_multiple_sources_to_r2_concurrently_and_preserves_order(tmp_path, monkeypatch):
    first = tmp_path / "first.pdf"
    second = tmp_path / "second.pdf"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    gate = threading.Barrier(2, timeout=3)
    uploaded = []

    monkeypatch.setattr(durable_runtime_module, "STORAGE_TRANSFER_PARALLELISM", 2)
    monkeypatch.setattr(
        durable_runtime_module.STORAGE,
        "source_key",
        lambda job_id, role, index, path: f"jobs/{job_id}/{role}_{index:03d}{path.suffix}",
    )

    def upload(_path: Path, key: str) -> str:
        gate.wait()
        uploaded.append(key)
        return key

    monkeypatch.setattr(durable_runtime_module.STORAGE, "upload_file", upload)
    audio, visual = durable_runtime_module._upload_job_sources(
        "parallel-source-upload",
        [first, second],
        [],
    )

    assert visual == []
    assert audio == [
        "jobs/parallel-source-upload/audio_001.pdf",
        "jobs/parallel-source-upload/audio_002.pdf",
    ]
    assert set(uploaded) == set(audio)


def test_partial_source_upload_is_removed_when_r2_transfer_fails(tmp_path, monkeypatch):
    first = tmp_path / "first.pdf"
    second = tmp_path / "second.pdf"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    deleted = []

    monkeypatch.setattr(durable_runtime_module, "STORAGE_TRANSFER_PARALLELISM", 1)
    monkeypatch.setattr(
        durable_runtime_module.STORAGE,
        "source_key",
        lambda job_id, role, index, path: f"jobs/{job_id}/{role}_{index:03d}{path.suffix}",
    )

    def upload(_path: Path, key: str) -> str:
        if key.endswith("002.pdf"):
            raise RuntimeError("temporary R2 failure")
        return key

    monkeypatch.setattr(durable_runtime_module.STORAGE, "upload_file", upload)
    monkeypatch.setattr(
        durable_runtime_module.STORAGE,
        "delete_keys",
        lambda keys: deleted.extend(keys) or len(keys),
    )

    try:
        durable_runtime_module._upload_job_sources("partial-source-upload", [first, second], [])
        raise AssertionError("upload failure was not raised")
    except RuntimeError as exc:
        assert str(exc) == "temporary R2 failure"
    assert deleted == [
        "jobs/partial-source-upload/audio_001.pdf",
        "jobs/partial-source-upload/audio_002.pdf",
    ]


def test_ambiguous_parallel_upload_failure_removes_every_planned_r2_key(tmp_path, monkeypatch):
    first = tmp_path / "first.pdf"
    second = tmp_path / "second.pdf"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    gate = threading.Barrier(2, timeout=3)
    committed = []
    deleted = []

    monkeypatch.setattr(durable_runtime_module, "STORAGE_TRANSFER_PARALLELISM", 2)
    monkeypatch.setattr(
        durable_runtime_module.STORAGE,
        "source_key",
        lambda job_id, role, index, path: f"jobs/{job_id}/{role}_{index:03d}{path.suffix}",
    )

    def upload(_path: Path, key: str) -> str:
        gate.wait()
        committed.append(key)
        if key.endswith("002.pdf"):
            # Model a timeout after R2 accepted the bytes but before the client
            # received a success response.
            raise TimeoutError("upload response was lost")
        return key

    monkeypatch.setattr(durable_runtime_module.STORAGE, "upload_file", upload)
    monkeypatch.setattr(
        durable_runtime_module.STORAGE,
        "delete_keys",
        lambda keys: deleted.extend(keys) or len(keys),
    )

    try:
        durable_runtime_module._upload_job_sources("ambiguous-upload", [first, second], [])
        raise AssertionError("ambiguous upload failure was not raised")
    except TimeoutError as exc:
        assert str(exc) == "upload response was lost"

    expected = [
        "jobs/ambiguous-upload/audio_001.pdf",
        "jobs/ambiguous-upload/audio_002.pdf",
    ]
    assert set(committed) == set(expected)
    assert deleted == expected


def test_queue_ready_document_defers_probe_and_guest_reservation_to_worker(tmp_path, monkeypatch):
    job_dir = tmp_path / "deferred-document"
    job_dir.mkdir()
    source = job_dir / "lecture.pdf"
    source.write_bytes(b"%PDF-1.7\n")
    job_id = f"deferred-document-{uuid.uuid4()}"
    user_id = f"deferred-guest-{uuid.uuid4()}"
    options = {
        "billing_user_id": user_id,
        "document_mode": True,
        "source_language": "tr",
        "job_type": "study_pack",
        "summary_style": "detailed",
    }
    jobs_module.JOBS.create(job_id, job_dir, options, source_type="document")
    entitlement_checks = []
    reservations = []
    queued = []

    class QueuedTask:
        id = "celery-deferred-document"

    class ProcessUploadedJob:
        @staticmethod
        def delay(queued_job_id, audio_keys, queued_options, visual_keys):
            queued.append((queued_job_id, audio_keys, dict(queued_options), visual_keys))
            return QueuedTask()

    def no_document_ffprobe(_paths):
        raise AssertionError("queue-ready documents must not be sent to ffprobe on the web")

    def check_entitlement(captured_user_id, duration, **kwargs):
        entitlement_checks.append((captured_user_id, duration, kwargs))
        return {}

    monkeypatch.setattr(durable_runtime_module, "_queue_ready", lambda: True)
    monkeypatch.setattr(durable_runtime_module, "media_duration_seconds", no_document_ffprobe)
    monkeypatch.setattr(durable_runtime_module, "is_guest_user", lambda _user_id: True)
    monkeypatch.setattr(
        durable_runtime_module,
        "reserve_guest_job",
        lambda *args: reservations.append(args),
    )
    monkeypatch.setattr(durable_runtime_module, "require_duration_entitlement", check_entitlement)
    monkeypatch.setattr(
        durable_runtime_module,
        "_upload_job_sources",
        lambda _job_id, _audio, _visual: (["jobs/deferred/audio_001.pdf"], []),
    )
    monkeypatch.setattr(tasks_module, "process_uploaded_job", ProcessUploadedJob())

    try:
        app_module.process_job(job_id, [source], options)
        data = jobs_module.JOBS.get(job_id) or {}
        assert reservations == []
        assert len(entitlement_checks) == 1
        assert entitlement_checks[0][0] == user_id
        assert entitlement_checks[0][1] == 0.0
        assert entitlement_checks[0][2]["document_mode"] is True
        assert entitlement_checks[0][2]["source_size_bytes"] == len(b"%PDF-1.7\n")
        assert queued and queued[0][0] == job_id
        assert "document_credit_seconds" not in queued[0][2]
        assert data["document_preflight_deferred_to_worker"] is True
        assert data["guest_reservation_deferred_to_worker"] is True
        assert data["guest_reservation_deferred_to_pipeline"] is True
        assert data["usage_estimated"] is True
        assert data["media_minutes"] is None
        assert data["eta_seconds"] is None
        assert source.exists() is False
    finally:
        jobs_module.JOBS.delete_for_user(user_id)


def test_worker_document_preflight_sets_quota_metadata(tmp_path):
    source = tmp_path / "lecture.txt"
    source.write_text("energy work force power " * 80, encoding="utf-8")
    job_id = f"worker-preflight-{uuid.uuid4()}"
    user_id = f"worker-preflight-user-{uuid.uuid4()}"
    options = {"source_language": "en", "document_mode": True, "billing_user_id": user_id}
    JOBS = tasks_module.JOBS
    JOBS.create(job_id, tmp_path, options)
    try:
        seconds = tasks_module._preflight_worker_documents(job_id, [source], options)
        inspected = JOBS.get(job_id)
        assert seconds >= 120
        assert options["document_credit_seconds"] == seconds
        assert inspected["billable_minutes"] >= 2
        assert inspected["document_words"] >= 300
        assert inspected["worker_state"] == "preflighting"
    finally:
        JOBS.delete_for_user(user_id)


def test_duration_probes_run_concurrently_and_keep_exact_sum(tmp_path, monkeypatch):
    gate = threading.Barrier(2, timeout=3)
    monkeypatch.setattr(duration_module, "DURATION_PROBE_PARALLELISM", 2)

    def probe(path: Path) -> float:
        gate.wait()
        return 11.5 if path.name == "first.mp4" else 8.5

    monkeypatch.setattr(duration_module, "file_duration_seconds", probe)
    assert duration_module.media_duration_seconds(
        [tmp_path / "first.mp4", tmp_path / "second.mp4"]
    ) == 20.0


def test_generated_outputs_publish_to_object_storage_concurrently(tmp_path, monkeypatch):
    job_dir = tmp_path / "publish"
    (job_dir / "package").mkdir(parents=True)
    (job_dir / "result.json").write_text("{}", encoding="utf-8")
    (job_dir / "package" / "notes.txt").write_text("notes", encoding="utf-8")
    gate = threading.Barrier(2, timeout=3)
    uploaded = []
    storage = ObjectStorage.__new__(ObjectStorage)
    storage.bucket = "private-bucket"
    storage.remote = True
    storage._client = object()
    monkeypatch.setattr(storage_module, "STORAGE_TRANSFER_PARALLELISM", 2)

    def upload(path: Path, key: str) -> str:
        del path
        gate.wait()
        uploaded.append(key)
        return key

    storage.upload_file = upload
    result = storage.publish_job("parallel-publish", job_dir)
    assert set(uploaded) == {
        "jobs/parallel-publish/package/notes.txt",
        "jobs/parallel-publish/result.json",
    }
    assert result["remote_result_key"] == "jobs/parallel-publish/result.json"
    assert result["remote_file_count"] == 2


def test_object_storage_deletes_private_source_keys_in_batches():
    class FakeClient:
        def __init__(self):
            self.calls = []

        def delete_objects(self, **kwargs):
            self.calls.append(kwargs)

    storage = ObjectStorage.__new__(ObjectStorage)
    storage.bucket = "private-bucket"
    storage.remote = True
    storage._client = FakeClient()

    keys = [f"jobs/one/sources/audio_{index:04d}.mp4" for index in range(1001)]
    assert storage.delete_keys(keys + [keys[0], ""]) == 1001
    assert [len(call["Delete"]["Objects"]) for call in storage._client.calls] == [1000, 1]
    assert all(call["Bucket"] == "private-bucket" for call in storage._client.calls)


def test_object_storage_health_uses_bucket_scoped_object_permission():
    class FakeClient:
        def __init__(self):
            self.calls = []

        def list_objects_v2(self, **kwargs):
            self.calls.append(kwargs)
            return {"KeyCount": 0}

    storage = ObjectStorage.__new__(ObjectStorage)
    storage.bucket = "private-bucket"
    storage.remote = True
    storage._client = FakeClient()

    assert storage.health() == {"configured": True, "connected": True}
    assert storage._client.calls == [{"Bucket": "private-bucket", "MaxKeys": 1}]


def test_object_storage_health_reports_safe_invalid_credentials_diagnostic():
    class FakeClient:
        def list_objects_v2(self, **kwargs):
            raise ClientError(
                {
                    "Error": {"Code": "InvalidAccessKeyId", "Message": "secret detail"},
                    "ResponseMetadata": {"HTTPStatusCode": 403},
                },
                "ListObjectsV2",
            )

    storage = ObjectStorage.__new__(ObjectStorage)
    storage.bucket = "private-bucket"
    storage.remote = True
    storage._client = FakeClient()

    assert storage.health() == {
        "configured": True,
        "connected": False,
        "diagnostic": "credentials_invalid",
    }


def test_object_storage_health_reports_safe_endpoint_diagnostic():
    class FakeClient:
        def list_objects_v2(self, **kwargs):
            raise EndpointConnectionError(endpoint_url="https://private.example.invalid")

    storage = ObjectStorage.__new__(ObjectStorage)
    storage.bucket = "private-bucket"
    storage.remote = True
    storage._client = FakeClient()

    result = storage.health()

    assert result["configured"] is True
    assert result["connected"] is False
    assert result["diagnostic"] == "endpoint_unreachable"
    assert "private.example.invalid" not in str(result)


def test_completed_job_poll_does_not_fetch_missing_archive(tmp_path: Path, monkeypatch):
    job_id = "remote-job"
    local_dir = tmp_path / job_id
    local_dir.mkdir()
    (local_dir / "result.json").write_text(json.dumps({"ok": True}), encoding="utf-8")
    calls = []

    remote_key = f"jobs/{job_id}/package/LectureSift_Study_Pack.zip"

    def materialize(selected_job_id: str, destination: Path, keys: list[str]) -> int:
        calls.append((selected_job_id, destination, keys))
        archive = destination / "package" / "LectureSift_Study_Pack.zip"
        archive.parent.mkdir(parents=True, exist_ok=True)
        archive.write_bytes(b"PK-test")
        return 1

    monkeypatch.setattr(jobs_module, "WORK_DIR", tmp_path)
    monkeypatch.setattr(jobs_module.STORAGE, "remote", True)
    monkeypatch.setattr(jobs_module.STORAGE, "materialize_files", materialize)
    monkeypatch.setattr(
        jobs_module.STORAGE,
        "materialize_job",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("full job tree must not be materialized")),
    )
    store = JobStore.__new__(JobStore)
    data = {
        "job_id": job_id,
        "status": "done",
        "remote_prefix": f"jobs/{job_id}/",
        "remote_result_key": f"jobs/{job_id}/result.json",
        "remote_download_key": remote_key,
        "result_path": "/worker-only/LectureSift_Study_Pack.zip",
    }

    materialized = store._materialize_completed(data)

    assert calls == []
    assert materialized["job_dir"] == str(local_dir)
    assert materialized["result_path"] == "/worker-only/LectureSift_Study_Pack.zip"

    archive_path = store.ensure_local_file(materialized, remote_key)

    assert calls == [(job_id, local_dir, [remote_key])]
    assert archive_path is not None
    assert archive_path.read_bytes() == b"PK-test"


def test_completed_job_fetches_result_then_archive_only_on_demand(tmp_path: Path, monkeypatch):
    job_id = "targeted-remote-job"
    local_dir = tmp_path / job_id
    result_key = f"jobs/{job_id}/result.json"
    archive_key = f"jobs/{job_id}/LectureSift_Study_Pack.zip"
    calls = []

    def materialize(selected_job_id: str, destination: Path, keys: list[str]) -> int:
        calls.append((selected_job_id, list(keys)))
        for key in keys:
            relative = key.removeprefix(f"jobs/{selected_job_id}/")
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if key == result_key:
                target.write_text("{}", encoding="utf-8")
            else:
                target.write_bytes(b"PK")
        return len(keys)

    monkeypatch.setattr(jobs_module, "WORK_DIR", tmp_path)
    monkeypatch.setattr(jobs_module.STORAGE, "remote", True)
    monkeypatch.setattr(jobs_module.STORAGE, "materialize_files", materialize)
    monkeypatch.setattr(
        jobs_module.STORAGE,
        "materialize_job",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("full job tree must not be materialized")),
    )
    store = JobStore.__new__(JobStore)
    data = {
        "job_id": job_id,
        "status": "done",
        "remote_prefix": f"jobs/{job_id}/",
        "remote_result_key": result_key,
        "remote_download_key": archive_key,
        "result_path": "/worker-only/LectureSift_Study_Pack.zip",
    }

    materialized = store._materialize_completed(data)

    assert calls == [(job_id, [result_key])]
    assert (local_dir / "result.json").is_file()
    assert not (local_dir / "LectureSift_Study_Pack.zip").exists()
    assert materialized["result_path"] == "/worker-only/LectureSift_Study_Pack.zip"

    archive_path = store.ensure_local_file(materialized, archive_key)

    assert calls == [(job_id, [result_key]), (job_id, [archive_key])]
    assert archive_path is not None
    assert archive_path.read_bytes() == b"PK"


def test_remote_result_slide_artifact_and_download_endpoints_fetch_one_object_each(
    tmp_path: Path,
    monkeypatch,
):
    token = new_token()
    user_id = client.get("/billing/me", headers=auth(token)).json()["account"]["user"]["id"]
    job_id = f"remote-endpoints-{uuid.uuid4()}"
    prefix = f"jobs/{job_id}/"
    result_key = f"{prefix}result.json"
    archive_key = f"{prefix}package/LectureSift_Study_Pack.zip"
    data = {
        "job_id": job_id,
        "status": "done",
        "job_dir": "/worker-only/job",
        "result_path": "/worker-only/LectureSift_Study_Pack.zip",
        "remote_prefix": prefix,
        "remote_result_key": result_key,
        "remote_download_key": archive_key,
        "options": {"billing_user_id": user_id, "download_entitled": True},
    }
    calls: list[tuple[str, str]] = []

    def ensure_local_file(
        selected: dict,
        remote_key: str = "",
        *,
        local_relative: str = "",
    ) -> Path:
        assert selected["job_id"] == job_id
        calls.append((remote_key, local_relative))
        relative = remote_key.removeprefix(prefix) if remote_key else local_relative
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if relative == "result.json":
            target.write_text(json.dumps({"title": "Remote result"}), encoding="utf-8")
        elif relative.endswith(".zip"):
            target.write_bytes(b"PK-remote")
        elif relative.endswith(".jpg"):
            target.write_bytes(b"JPEG-remote")
        else:
            target.write_bytes(b"PDF-remote")
        return target

    monkeypatch.setattr(jobs_module.JOBS, "get", lambda selected_job_id: data.copy() if selected_job_id == job_id else None)
    monkeypatch.setattr(jobs_module.JOBS, "ensure_local_file", ensure_local_file)

    result = client.get(f"/jobs/{job_id}/result", headers=auth(token))
    slide = client.get(f"/jobs/{job_id}/slide/slide_001.jpg", headers=auth(token))
    artifact = client.get(f"/jobs/{job_id}/artifact/notes.pdf", headers=auth(token))
    archive = client.get(f"/jobs/{job_id}/download", headers=auth(token))

    assert result.status_code == 200
    assert result.json()["title"] == "Remote result"
    assert slide.status_code == 200 and slide.content == b"JPEG-remote"
    assert artifact.status_code == 200 and artifact.content == b"PDF-remote"
    assert archive.status_code == 200 and archive.content == b"PK-remote"
    assert calls == [
        (result_key, "result.json"),
        ("", "slides/slide_001.jpg"),
        ("", "package/notes.pdf"),
        (archive_key, ""),
    ]


def test_object_storage_materializes_only_valid_requested_job_keys(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(storage_module, "STORAGE_TRANSFER_PARALLELISM", 1)
    storage = ObjectStorage.__new__(ObjectStorage)
    storage.bucket = "private-bucket"
    storage.remote = True
    storage._client = object()
    downloaded = []

    def download(key: str, destination: Path) -> Path:
        downloaded.append((key, destination.relative_to(tmp_path).as_posix()))
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"data")
        return destination

    storage.download_file = download
    count = storage.materialize_files(
        "selected-job",
        tmp_path,
        [
            "jobs/selected-job/result.json",
            "jobs/selected-job/package/LectureSift_Study_Pack.zip",
            "jobs/selected-job/result.json",
            "jobs/other-job/private.txt",
            "jobs/selected-job/../escape.txt",
        ],
    )

    assert count == 2
    assert downloaded == [
        ("jobs/selected-job/result.json", "result.json"),
        ("jobs/selected-job/package/LectureSift_Study_Pack.zip", "package/LectureSift_Study_Pack.zip"),
    ]


def test_repeated_rounded_task_progress_skips_state_flush(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(jobs_module, "WORK_DIR", tmp_path)
    monkeypatch.setattr(jobs_module, "REDIS_URL", "")
    store = JobStore()
    job_dir = tmp_path / "progress-job"
    job_dir.mkdir()
    store.create("progress-job", job_dir, {})
    flushes = []
    monkeypatch.setattr(store, "_flush_locked", lambda: flushes.append(True))

    store.update_task("progress-job", "audio", 12.1, "transcription")
    store.update_task("progress-job", "audio", 12.4, "transcription")
    assert len(flushes) == 1

    store.update_task("progress-job", "audio", 12.6, "transcription")
    store.update_task("progress-job", "audio", 12.6, "transcript_ready")
    assert len(flushes) == 3
