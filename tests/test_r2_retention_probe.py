from __future__ import annotations

import datetime as dt
import importlib.util
import sys
import uuid
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "deploy" / "r2_retention_probe.py"
SPEC = importlib.util.spec_from_file_location("r2_retention_probe", SCRIPT_PATH)
assert SPEC and SPEC.loader
probe = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = probe
SPEC.loader.exec_module(probe)


VALID_REPOSITORY = (
    "s3:https://0123456789abcdef0123456789abcdef.eu.r2.cloudflarestorage.com/"
    "lecturesift-production-backups/restic"
)
CREATED_AT = dt.datetime(2026, 8, 31, 19, 2, 3, tzinfo=dt.timezone.utc)
PROBE_UUID = uuid.UUID("12345678-1234-5678-90ab-1234567890ab")


class FakeClient:
    def __init__(self, *, delete_error=None, corrupt_after_delete=False):
        self.calls = []
        self.body = b""
        self.metadata = {}
        self.delete_error = delete_error
        self.corrupt_after_delete = corrupt_after_delete
        self.after_delete = False

    def request(self, method, key, *, body=b"", metadata=None, if_none_match=False):
        self.calls.append((method, key, body, metadata, if_none_match))
        if method == "PUT":
            self.body = body
            self.metadata = dict(metadata or {})
            return probe.HttpResponse(200, {}, b"")
        if method == "DELETE":
            self.after_delete = True
            if self.delete_error:
                raise self.delete_error
            return probe.HttpResponse(204, {}, b"")
        assert method == "GET"
        response_body = b"corrupt" if self.after_delete and self.corrupt_after_delete else self.body
        headers = {f"x-amz-meta-{key}": value for key, value in self.metadata.items()}
        return probe.HttpResponse(200, headers, response_body)


def _run(client):
    return probe.run_probe(
        client,
        created_at=CREATED_AT,
        probe_uuid=PROBE_UUID,
        nonce="ab" * 32,
    )


def test_repository_target_is_exact_and_fail_closed():
    target = probe.parse_repository(VALID_REPOSITORY)
    assert target.account_id == "0123456789abcdef0123456789abcdef"
    assert target.bucket == "lecturesift-production-backups"
    assert target.repository_prefix == "restic"

    rejected = (
        "https://0123456789abcdef0123456789abcdef.eu.r2.cloudflarestorage.com/"
        "lecturesift-production-backups/restic",
        VALID_REPOSITORY.replace("https://", "http://"),
        VALID_REPOSITORY.replace("lecturesift-production-backups", "another-bucket"),
        VALID_REPOSITORY.replace("/restic", "/restic/data"),
        VALID_REPOSITORY + "/",
        VALID_REPOSITORY + "?unsafe=yes",
        VALID_REPOSITORY.replace(".eu.r2.", ".r2."),
        VALID_REPOSITORY.replace("0123456789abcdef0123456789abcdef", "not-an-account"),
    )
    for repository in rejected:
        with pytest.raises(probe.ProbeError):
            probe.parse_repository(repository)


def test_probe_only_touches_one_reserved_unique_object_and_verifies_twice():
    lock_error = probe.S3HttpError(
        status=403,
        code="AccessDenied",
        message="Object is protected by a bucket lock retention rule",
    )
    client = FakeClient(delete_error=lock_error)

    result = _run(client)

    expected_key = (
        "restic/data/.lecturesift-retention-probes/v1/"
        "probe-20260831T190203Z-123456781234567890ab1234567890ab.json"
    )
    assert [call[0] for call in client.calls] == ["PUT", "GET", "DELETE", "GET"]
    assert {call[1] for call in client.calls} == {expected_key}
    assert client.calls[0][4] is True
    metadata = client.calls[0][3]
    assert metadata["lecturesift-purpose"] == "immutable-retention-probe"
    assert metadata["lecturesift-retention-days"] == "90"
    assert metadata["lecturesift-sha256"] == result["payload_sha256"]
    assert result["status"] == "immutable-retention-verified"
    assert result["delete_result"] == "AccessDenied"


def test_generic_permission_denial_is_not_accepted_as_lock_proof():
    generic_denial = probe.S3HttpError(
        status=403,
        code="AccessDenied",
        message="The provided access key does not have permission to delete objects",
    )
    client = FakeClient(delete_error=generic_denial)

    with pytest.raises(probe.ProbeError, match="did not identify"):
        _run(client)

    assert [call[0] for call in client.calls] == ["PUT", "GET", "DELETE"]


def test_successful_delete_fails_the_probe_and_never_touches_another_key():
    client = FakeClient()

    with pytest.raises(probe.ProbeError, match="DELETE succeeded"):
        _run(client)

    assert [call[0] for call in client.calls] == ["PUT", "GET", "DELETE"]
    assert len({call[1] for call in client.calls}) == 1


def test_post_delete_hash_or_metadata_change_fails_closed():
    lock_error = probe.S3HttpError(
        status=409,
        code="ObjectLocked",
        message="The object is locked until its retention period expires",
    )
    client = FakeClient(delete_error=lock_error, corrupt_after_delete=True)

    with pytest.raises(probe.ProbeError, match="hash did not match"):
        _run(client)


def test_arbitrary_object_keys_are_refused_before_request_construction():
    for key in (
        "restic/data/abcdef",
        "restic/snapshots/probe.json",
        "restic/index/probe.json",
        "restic/locks/probe.json",
        "restic/data/.lecturesift-retention-probes/v1/../pack",
    ):
        with pytest.raises(probe.ProbeError):
            probe.validate_probe_key(key)


def test_service_uses_only_root_host_backup_environment():
    service = (ROOT / "deploy" / "lecturesift-r2-retention-probe.service").read_text(
        encoding="utf-8"
    )
    docs = (ROOT / "deploy" / "R2_RETENTION_PROBE.md").read_text(encoding="utf-8")

    assert "User=root" in service
    assert "Group=root" in service
    assert "EnvironmentFile=/etc/lecturesift/restic.env" in service
    assert "CREATE-ONE-IMMUTABLE-R2-PROBE" in service
    assert "api.env" not in service
    assert "worker.env" not in service
    assert "docker compose" not in service
    assert "never lists the bucket" in docs
    assert "Generic permission failure" not in docs  # wording stays unambiguous and lowercase
    assert "generic permission failure is not proof" in docs
