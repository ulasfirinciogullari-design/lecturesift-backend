import uuid
from pathlib import Path

from fastapi.testclient import TestClient

from lecturesift.billing_service import register_user, verify_email
from lecturesift.storage import ObjectStorage
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
