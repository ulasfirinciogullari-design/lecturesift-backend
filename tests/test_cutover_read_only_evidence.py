from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import re
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


render_stop = _load("render_worker_stop_evidence", "deploy/render_worker_stop_evidence.py")
redis_manifest = _load("redis_logical_manifest", "deploy/redis_logical_manifest.py")
role_login = _load(
    "validate_postgres_role_login_probe",
    "deploy/validate_postgres_role_login_probe.py",
)


class FakeResponse:
    def __init__(self, payload, *, status: int = 200, content_type: str = "application/json"):
        self.status = status
        self._body = (
            payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
        )
        self._content_type = content_type

    def getheader(self, name: str):
        return self._content_type if name.lower() == "content-type" else None

    def read(self, amount: int):
        return self._body[:amount]


class FakeConnection:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
        self.closed = False

    def request(self, method, path, *, headers):
        self.requests.append((method, path, headers))

    def getresponse(self):
        return self.responses.pop(0)

    def close(self):
        self.closed = True


def _render_control():
    return {
        "RENDER_API_TOKEN": "rnd_" + "T" * 32,
        "RENDER_WORKER_SERVICE_ID": "srv-" + "a" * 20,
        "RENDER_WORKER_SERVICE_NAME": "lecturesift-worker",
    }


def _render_service(**updates):
    service = {
        "id": "srv-" + "a" * 20,
        "name": "lecturesift-worker",
        "ownerId": "tea-12345678",
        "type": "background_worker",
        "suspended": "suspended",
        "suspenders": ["user", "billing"],
        "createdAt": "ignored",
    }
    service.update(updates)
    return service


def test_render_stop_proof_uses_only_two_exact_gets_and_is_stable():
    connections = []

    def factory(host, port, *, context, timeout):
        assert (host, port, timeout) == ("api.render.com", 443, 15)
        assert context is not None
        connection = FakeConnection([FakeResponse(_render_service()), FakeResponse([])])
        connections.append(connection)
        return connection

    first = render_stop.worker_stop_digest(_render_control(), connection_factory=factory)
    assert len(first) == 64
    connection = connections[0]
    assert [item[:2] for item in connection.requests] == [
        ("GET", "/v1/services/srv-" + "a" * 20),
        ("GET", "/v1/services/srv-" + "a" * 20 + "/instances"),
    ]
    assert all(
        item[2]["Authorization"] == "Bearer " + _render_control()["RENDER_API_TOKEN"]
        for item in connection.requests
    )
    assert connection.closed is True

    def reordered_factory(*_args, **_kwargs):
        return FakeConnection(
            [
                FakeResponse(_render_service(suspenders=["billing", "user"], extra="ignored")),
                FakeResponse([]),
            ]
        )

    assert render_stop.worker_stop_digest(
        _render_control(), connection_factory=reordered_factory
    ) == first


@pytest.mark.parametrize(
    "service,instances",
    [
        (_render_service(suspended="not_suspended"), []),
        (_render_service(type="web_service"), []),
        (_render_service(name="another-worker"), []),
        (_render_service(), [{"id": "ins-active"}]),
    ],
)
def test_render_stop_proof_fails_closed_on_wrong_identity_state_or_instances(
    service, instances
):
    connection = FakeConnection([FakeResponse(service), FakeResponse(instances)])
    with pytest.raises(render_stop.StopEvidenceError):
        render_stop.worker_stop_digest(
            _render_control(), connection_factory=lambda *_args, **_kwargs: connection
        )


def test_render_stop_parser_is_exact_and_token_never_enters_errors():
    token = "rnd_" + "S" * 32
    parsed = render_stop.parse_control(
        (
            f"RENDER_API_TOKEN={token}\n"
            f"RENDER_WORKER_SERVICE_ID=srv-{'b' * 20}\n"
            "RENDER_WORKER_SERVICE_NAME=worker-prod\n"
        ).encode()
    )
    assert parsed["RENDER_API_TOKEN"] == token
    with pytest.raises(render_stop.StopEvidenceError) as exc:
        render_stop.parse_control(
            (
                f"RENDER_API_TOKEN={token}\n"
                f"RENDER_WORKER_SERVICE_ID=srv-{'b' * 20}\n"
                "RENDER_WORKER_SERVICE_NAME=worker-prod\n"
                "UNKNOWN=value\n"
            ).encode()
        )
    assert token not in str(exc.value)


def test_render_stop_rejects_duplicate_json_without_echoing_body():
    secret_body = b'{"id":"one","id":"secret-value"}'
    connection = FakeConnection([FakeResponse(secret_body), FakeResponse([])])
    with pytest.raises(render_stop.StopEvidenceError) as exc:
        render_stop.worker_stop_digest(
            _render_control(), connection_factory=lambda *_args, **_kwargs: connection
        )
    assert "secret-value" not in str(exc.value)


@pytest.mark.skipif(os.name == "nt", reason="Windows does not preserve POSIX private modes")
def test_render_control_file_requires_private_single_link(tmp_path, monkeypatch):
    path = tmp_path / "control.env"
    path.write_text(
        "RENDER_API_TOKEN=rnd_" + "A" * 32 + "\n"
        "RENDER_WORKER_SERVICE_ID=srv-" + "c" * 20 + "\n"
        "RENDER_WORKER_SERVICE_NAME=worker-prod\n",
        encoding="utf-8",
    )
    path.chmod(0o600)
    details = path.stat()
    monkeypatch.setattr(render_stop, "CONTROL_OWNER_UID", details.st_uid)
    monkeypatch.setattr(render_stop, "CONTROL_OWNER_GID", details.st_gid)
    assert render_stop.load_control(path)["RENDER_WORKER_SERVICE_NAME"] == "worker-prod"
    path.chmod(0o644)
    with pytest.raises(render_stop.StopEvidenceError):
        render_stop.load_control(path)


class FakeRedis:
    def __init__(self, entries, *, lock: bytes | None = None, reverse: bool = False):
        self.entries = dict(entries)
        self.lock = lock
        self.reverse = reverse
        self.calls = []

    def ping(self):
        self.calls.append(("ping",))
        return True

    def scan(self, cursor, *, count):
        self.calls.append(("scan", cursor, count))
        keys = list(self.entries)
        if self.lock is not None:
            keys.append(redis_manifest.MIGRATION_LOCK_KEY)
        if self.reverse:
            keys.reverse()
        return 0, keys

    def type(self, key):
        self.calls.append(("type", key))
        if key == redis_manifest.MIGRATION_LOCK_KEY:
            return b"string" if self.lock is not None else b"none"
        return self.entries[key][0]

    def dump(self, key):
        self.calls.append(("dump", key))
        return self.entries[key][1]

    def get(self, key):
        self.calls.append(("get", key))
        assert key == redis_manifest.MIGRATION_LOCK_KEY
        return self.lock

    def pttl(self, key):
        self.calls.append(("pttl", key))
        assert key == redis_manifest.MIGRATION_LOCK_KEY
        return 60_000 if self.lock is not None else -2

    def execute_command(self, command, key):
        self.calls.append(("execute_command", command, key))
        assert command == "PEXPIRETIME"
        return self.entries[key][2]


def _redis_entries(job_dump: bytes = b"job-dump", other_dump: bytes = b"other-dump"):
    return {
        redis_manifest.JOB_STATE_KEY: (b"string", job_dump, -1),
        b"private:opaque:key": (b"hash", other_dump, 2_000_000_000_000),
    }


def test_redis_manifest_is_deterministic_salted_and_read_only():
    salt = bytes.fromhex("11" * 32)
    first_client = FakeRedis(_redis_entries())
    second_client = FakeRedis(_redis_entries(), reverse=True)
    first = redis_manifest.logical_manifest(
        first_client, salt, policy="steady", projection="full"
    )
    second = redis_manifest.logical_manifest(
        second_client, salt, policy="steady", projection="full"
    )
    assert first == second
    assert first.key_count == 2 and len(first.digest) == 64
    assert "private:opaque:key" not in first.digest
    allowed = {"ping", "scan", "type", "dump", "execute_command"}
    assert {call[0] for call in first_client.calls} <= allowed
    assert not ({"set", "delete", "eval", "expire"} & {call[0] for call in first_client.calls})
    different_salt = redis_manifest.logical_manifest(
        FakeRedis(_redis_entries()), bytes.fromhex("22" * 32), policy="steady", projection="full"
    )
    assert different_salt.digest != first.digest


def test_redis_manifest_binds_type_dump_and_absolute_expiry():
    salt = bytes.fromhex("33" * 32)
    baseline = redis_manifest.logical_manifest(
        FakeRedis(_redis_entries()), salt, policy="steady", projection="full"
    ).digest
    changed_dump = _redis_entries(other_dump=b"changed")
    changed_type = _redis_entries()
    changed_type[b"private:opaque:key"] = (b"set", b"other-dump", 2_000_000_000_000)
    changed_expiry = _redis_entries()
    changed_expiry[b"private:opaque:key"] = (b"hash", b"other-dump", 2_000_000_000_001)
    for entries in (changed_dump, changed_type, changed_expiry):
        assert redis_manifest.logical_manifest(
            FakeRedis(entries), salt, policy="steady", projection="full"
        ).digest != baseline


def test_non_job_projection_allows_only_job_state_replacement():
    salt = bytes.fromhex("44" * 32)
    token = b"12345678-1234-4234-9234-123456789abc"
    before = FakeRedis(_redis_entries(job_dump=b"before"), lock=token)
    after = FakeRedis(_redis_entries(job_dump=b"after"), lock=token)
    before_non_job = redis_manifest.logical_manifest(
        before,
        salt,
        policy="migration",
        projection="non-job",
        expected_lock_token=token,
    )
    after_non_job = redis_manifest.logical_manifest(
        after,
        salt,
        policy="migration",
        projection="non-job",
        expected_lock_token=token,
    )
    assert before_non_job == after_non_job
    assert redis_manifest.logical_manifest(
        FakeRedis(_redis_entries(job_dump=b"before"), lock=token),
        salt,
        policy="migration",
        projection="full",
        expected_lock_token=token,
    ).digest != redis_manifest.logical_manifest(
        FakeRedis(_redis_entries(job_dump=b"after"), lock=token),
        salt,
        policy="migration",
        projection="full",
        expected_lock_token=token,
    ).digest


def test_redis_manifest_rejects_unproved_or_wrong_migration_lock():
    salt = bytes.fromhex("55" * 32)
    token = b"12345678-1234-4234-9234-123456789abc"
    with pytest.raises(redis_manifest.ManifestError):
        redis_manifest.logical_manifest(
            FakeRedis(_redis_entries(), lock=token),
            salt,
            policy="steady",
            projection="full",
        )
    with pytest.raises(redis_manifest.ManifestError):
        redis_manifest.logical_manifest(
            FakeRedis(_redis_entries(), lock=token),
            salt,
            policy="migration",
            projection="full",
            expected_lock_token=b"87654321-4321-4321-8321-cba987654321",
        )


def test_stdlib_redis_reader_exposes_only_read_commands(monkeypatch):
    reader = object.__new__(redis_manifest.RESPRedisReader)
    calls = []

    def command(*parts):
        calls.append(parts)
        responses = {
            "PING": b"PONG",
            "SCAN": [b"0", []],
            "TYPE": b"string",
            "DUMP": b"serialized",
            "GET": b"value",
            "PTTL": 1000,
            "PEXPIRETIME": 2_000_000_000_000,
        }
        return responses[parts[0]]

    monkeypatch.setattr(reader, "_command", command)
    assert reader.ping() is True
    assert reader.scan(0, count=10) == (0, [])
    assert reader.type(b"key") == b"string"
    assert reader.dump(b"key") == b"serialized"
    assert reader.get(b"key") == b"value"
    assert reader.pttl(b"key") == 1000
    assert reader.execute_command("PEXPIRETIME", b"key") == 2_000_000_000_000
    assert {parts[0] for parts in calls} == {
        "PING",
        "SCAN",
        "TYPE",
        "DUMP",
        "GET",
        "PTTL",
        "PEXPIRETIME",
    }
    with pytest.raises(redis_manifest.ManifestError):
        reader.execute_command("SET", b"key")


def test_cutover_scripts_never_send_celery_control_and_bind_new_digests():
    scripts = {
        name: (ROOT / "deploy" / name).read_text(encoding="utf-8")
        for name in (
            "migrate_postgres.sh",
            "migrate_redis_state.sh",
            "seed_first_cutover_backup.sh",
            "finalize_provider_cutover.sh",
            "rollback_postgres_to_render.sh",
        )
    }
    for script in scripts.values():
        assert "from celery import Celery" not in script
        assert "control.ping" not in script
        assert "render_worker_stop_evidence.py" in script
    for name in (
        "migrate_postgres.sh",
        "migrate_redis_state.sh",
        "seed_first_cutover_backup.sh",
        "finalize_provider_cutover.sh",
    ):
        assert "source-worker-stop-evidence-sha256" in scripts[name]
    for name in (
        "migrate_redis_state.sh",
        "seed_first_cutover_backup.sh",
        "finalize_provider_cutover.sh",
    ):
        assert "target-redis-manifest-sha256" in scripts[name]


def test_target_manifest_wrapper_mounts_no_production_environment():
    wrapper = (ROOT / "deploy" / "target_redis_manifest.sh").read_text(encoding="utf-8")
    helper = (ROOT / "deploy" / "redis_logical_manifest.py").read_text(encoding="utf-8")
    assert "--env-file" not in wrapper
    assert "/etc/lecturesift/runtime.env" not in wrapper
    assert "/etc/lecturesift/api.env" not in wrapper
    assert "lecturesift-backend:local" not in wrapper
    assert "docker run" not in wrapper
    assert "redis:7.4-alpine" in wrapper
    assert "docker image inspect --format '{{.Id}}' \"$EXPECTED_IMAGE\"" in wrapper
    assert "'{{.Id}}|{{.Image}}|{{.Config.Image}}|" in wrapper
    assert '"$redis_image_id" == "$expected_image_id"' in wrapper
    assert "lecturesift_backend" in wrapper
    assert "redis_identity" in wrapper and '"$identity_after" == "$identity_before"' in wrapper
    assert "python3 \"$TOOL\"" in wrapper
    assert "from redis import" not in helper
    assert "class RESPRedisReader" in helper


def test_exact_rehearsal_reads_the_live_redis_snapshot_with_eval_ro():
    wrapper = (ROOT / "deploy" / "run_exact_rehearsal.sh").read_text(
        encoding="utf-8"
    )
    assert 'redis-cli --json EVAL_RO "$redis_lua"' in wrapper
    assert 'redis-cli --json EVAL "$redis_lua"' not in wrapper


def _shell_function(script: str, name: str) -> str:
    return script.split(f"{name}() {{", 1)[1].split("\n}\n", 1)[0]


def test_cutover_queue_inspection_is_redis_enforced_read_only():
    for script_name in (
        "finalize_provider_cutover.sh",
        "seed_first_cutover_backup.sh",
    ):
        script = (ROOT / "deploy" / script_name).read_text(encoding="utf-8")
        queue_gate = _shell_function(script, "assert_target_queue_idle")
        assert "redis-cli --raw EVAL_RO '" in queue_gate
        assert "redis-cli --raw EVAL '" not in queue_gate

    migration = (ROOT / "deploy" / "migrate_redis_state.sh").read_text(
        encoding="utf-8"
    )
    broker_gate = _shell_function(migration, "assert_target_broker_empty")
    assert "redis-cli --raw EVAL_RO '" in broker_gate
    assert "redis-cli --raw EVAL '" not in broker_gate

    # Token-checked lock release is the deliberately mutating Lua operation.
    for function_name in (
        "release_target_lock_best_effort",
        "release_target_lock_strict",
    ):
        lock_release = _shell_function(migration, function_name)
        assert "redis-cli --raw EVAL" in lock_release
        assert "redis-cli --raw EVAL_RO" not in lock_release


def _role_record(kind: str, user: str, **updates):
    value = {
        "kind": kind,
        "current_user": user,
        "session_user": user,
        "database": "lecturesift",
        "server_version_num": "180001",
        "login": True,
        "superuser": kind == "owner",
        "inherit": kind == "owner",
        "createdb": False,
        "createrole": False,
        "replication": False,
        "bypassrls": False,
        "connect": True,
        "temporary": kind == "owner",
        "public_create": kind == "owner",
        "search_path": "lecturesift_api,public" if kind == "api" else "lecturesift_worker,public" if kind == "worker" else '"$user",public',
        "transaction_read_only": True,
    }
    value.update(updates)
    return value


def _write_role_probe(path: Path, *, elevated_api: bool = False):
    records = [
        _role_record("owner", "lecturesift_owner"),
        _role_record("api", "lecturesift_app", createdb=elevated_api),
        _role_record("worker", "lecturesift_worker"),
    ]
    path.write_text(
        "ROLE_LOGIN_MANIFEST|v1\n"
        + "".join("ROLE|" + json.dumps(item) + "\n" for item in records)
        + "ROLE_LOGIN_COMPLETE|v1|3\n",
        encoding="utf-8",
    )


def test_postgres_role_login_manifest_is_exact_and_rejects_elevation(tmp_path):
    raw = tmp_path / "role.raw"
    _write_role_probe(raw)
    canonical = role_login.canonicalize(
        raw,
        database="lecturesift",
        owner_user="lecturesift_owner",
        api_user="lecturesift_app",
        worker_user="lecturesift_worker",
    )
    assert canonical.startswith("ROLE_LOGIN_MANIFEST|v1\nDATABASE|")
    assert canonical.endswith("ROLE_LOGIN_COMPLETE|v1|3\n")
    _write_role_probe(raw, elevated_api=True)
    with pytest.raises(role_login.ProbeError):
        role_login.canonicalize(
            raw,
            database="lecturesift",
            owner_user="lecturesift_owner",
            api_user="lecturesift_app",
            worker_user="lecturesift_worker",
        )


def test_postgres_role_probe_uses_trusted_tcp_read_only_container():
    script = (ROOT / "deploy" / "postgres_role_login_probe.sh").read_text(encoding="utf-8")
    assert re.search(
        r'pinned_postgres_image="postgres:18-bookworm@sha256:[0-9a-f]{64}"', script
    )
    assert "docker image inspect --format '{{.Id}}' \"$pinned_postgres_image\"" in script
    assert "'{{.Id}}|{{.Image}}|{{.Config.Image}}|" in script
    assert '"$postgres_image_id" == "$pinned_postgres_image_id"' in script
    assert "lecturesift-backend:local" not in script
    assert "--host 127.0.0.1" in script
    assert "default_transaction_read_only=on" in script
    assert "IFS= read -r PGPASSWORD" in script
    assert "export PGPASSWORD" in script


def test_first_start_is_crash_fenced_and_failure_stops_all_public_writers():
    preflight = (ROOT / "deploy" / "preflight.sh").read_text(encoding="utf-8")
    service = (ROOT / "deploy" / "lecturesift.service").read_text(encoding="utf-8")
    verifier = (ROOT / "deploy" / "verify_provider_first_start.sh").read_text(encoding="utf-8")
    assert "provider-first-start.in-progress" in preflight
    assert "first-start-status" in preflight
    assert "verify_provider_first_start.sh arm" in service
    assert "verify_provider_first_start.sh complete" in service
    assert "ExecStopPost=/usr/bin/docker compose stop --timeout 120 api worker caddy egress-proxy" in service
    assert service.index("verify_provider_first_start.sh arm") < service.index(
        "docker compose up -d --remove-orphans"
    ) < service.index("verify_provider_first_start.sh complete")
    assert "MANIFEST_COMPLETE" in verifier
    assert "verify_schema_transition.py" in verifier


def test_first_start_complete_bypasses_status_while_the_gate_is_armed():
    verifier = (ROOT / "deploy" / "verify_provider_first_start.sh").read_text(
        encoding="utf-8"
    )

    complete_branch = verifier.index('if [[ "$MODE" == "complete" ]]')
    complete_call = verifier.index("complete-first-start", complete_branch)
    complete_exit = verifier.index("exit 0", complete_call)
    status_call = verifier.index("first-start-status")
    arm_call = verifier.index("arm-first-start")

    assert complete_branch < complete_call < complete_exit < status_call < arm_call
