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
