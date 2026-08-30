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


def test_completed_job_materializes_missing_nested_download(tmp_path: Path, monkeypatch):
    job_id = "remote-job"
    local_dir = tmp_path / job_id
    local_dir.mkdir()
    (local_dir / "result.json").write_text(json.dumps({"ok": True}), encoding="utf-8")
    calls = []

    def materialize(selected_job_id: str, destination: Path) -> int:
        calls.append((selected_job_id, destination))
        archive = destination / "package" / "LectureSift_Study_Pack.zip"
        archive.parent.mkdir(parents=True, exist_ok=True)
        archive.write_bytes(b"PK-test")
        return 1

    monkeypatch.setattr(jobs_module, "WORK_DIR", tmp_path)
    monkeypatch.setattr(jobs_module.STORAGE, "remote", True)
    monkeypatch.setattr(jobs_module.STORAGE, "materialize_job", materialize)
    store = JobStore.__new__(JobStore)
    data = {
        "job_id": job_id,
        "status": "done",
        "remote_prefix": f"jobs/{job_id}/",
        "remote_download_key": f"jobs/{job_id}/package/LectureSift_Study_Pack.zip",
        "result_path": "/worker-only/LectureSift_Study_Pack.zip",
    }

    materialized = store._materialize_completed(data)

    assert calls == [(job_id, local_dir)]
    assert Path(materialized["result_path"]).read_bytes() == b"PK-test"
