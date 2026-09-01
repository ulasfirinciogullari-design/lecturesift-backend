from __future__ import annotations

import importlib.util
from pathlib import Path
import socket
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "lecturesift_source_redis_guard", ROOT / "deploy" / "source_redis_guard.py"
)
assert SPEC and SPEC.loader
guard = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = guard
SPEC.loader.exec_module(guard)


class FakeConnection:
    def __init__(self, endpoint, *, state: bytes, broker_state: list[int]):
        self.endpoint = endpoint
        self.state = state
        self.broker_state = broker_state

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _traceback):
        return None

    def command(self, *parts):
        assert parts[0] == "EVAL_RO"
        if parts[1] == guard.STATE_LUA:
            return [self.state, 0, 0]
        assert parts[1] == guard.BROKER_LUA
        return list(self.broker_state)


def _factory(*, state: bytes, broker_state: list[int]):
    endpoints = []

    def create(endpoint):
        endpoints.append(endpoint)
        return FakeConnection(endpoint, state=state, broker_state=broker_state)

    return endpoints, create


def test_guard_verifies_state_and_broker_endpoints_independently():
    endpoints, create = _factory(
        state=b'{"version":2,"saved_at":1,"jobs":{"done":{"status":"done"}}}',
        broker_state=[0, 0, 0],
    )
    canonical, jobs, counts = guard.inspect_source(
        "rediss://default:state-secret@state.render.com:6380/0",
        "rediss://:broker-secret@broker.render.com:6381/1?ssl_cert_reqs=required",
        connection_factory=create,
    )
    assert [endpoint.host for endpoint in endpoints] == [
        "state.render.com",
        "broker.render.com",
    ]
    assert [endpoint.database for endpoint in endpoints] == [0, 1]
    assert endpoints[1].username is None
    assert jobs == 1 and counts == {"done": 1, "error": 0, "unknown": 0}
    assert canonical == b'{"jobs":{"done":{"status":"done"}},"saved_at":1,"version":2}'


@pytest.mark.parametrize(
    "state,broker_state",
    [
        (b'{"version":2,"jobs":{"job":{"status":"working"}}}', [0, 0, 0]),
        (b'{"version":2,"jobs":{}}', [1, 0, 0]),
        (b'{"version":2,"jobs":{}}', [0, 1, 0]),
        (b'{"version":2,"jobs":{}}', [0, 0, 1]),
    ],
)
def test_guard_rejects_active_logical_or_broker_work(state, broker_state):
    _endpoints, create = _factory(state=state, broker_state=broker_state)
    with pytest.raises(guard.GuardError):
        guard.inspect_source(
            "rediss://default:secret@state.render.com/0",
            "rediss://default:secret@broker.render.com/0",
            connection_factory=create,
        )


def test_guard_requires_certificate_verified_tls_urls():
    for url in (
        "redis://default:secret@state.render.com/0",
        "rediss://default:secret@localhost/0",
        "rediss://default:secret@state.render.com/0?ssl_cert_reqs=none",
        "rediss://default@state.render.com/0",
    ):
        with pytest.raises(guard.GuardError):
            guard.parse_endpoint(url, label="SOURCE_REDIS_URL")


def test_guard_rejects_private_or_loopback_dns_answers(monkeypatch):
    endpoint = guard.parse_endpoint(
        "rediss://default:secret@state.render.com/0", label="SOURCE_REDIS_URL"
    )
    for address in ("127.0.0.1", "10.0.0.8", "169.254.169.254", "::1"):
        family = socket.AF_INET6 if ":" in address else socket.AF_INET
        socket_address = (address, 6379, 0, 0) if family == socket.AF_INET6 else (address, 6379)
        monkeypatch.setattr(
            guard.socket,
            "getaddrinfo",
            lambda *_args, family=family, socket_address=socket_address, **_kwargs: [
                (family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", socket_address)
            ],
        )
        with pytest.raises(guard.GuardError, match="non-public"):
            guard.resolve_public_addresses(endpoint)


def test_terminal_diagnostics_never_echo_candidate_controlled_status():
    payload, _canonical, counts = guard._decode_state(
        b'{"version":2,"jobs":{"job":{"status":"secret\\nINJECTED"}}}'
    )
    assert payload["jobs"]["job"]["status"] == "secret\nINJECTED"
    assert counts == {"done": 0, "error": 0, "unknown": 1}
    assert "secret" not in str(counts) and "INJECTED" not in str(counts)


def test_cutover_scripts_use_host_guard_not_candidate_redis_clients():
    for name, function_name in (
        ("migrate_postgres.sh", "assert_render_worker_and_queue_stopped()"),
        ("seed_first_cutover_backup.sh", "assert_source_frozen_and_idle()"),
        ("finalize_provider_cutover.sh", "assert_source_frozen_and_idle()"),
    ):
        script = (ROOT / "deploy" / name).read_text(encoding="utf-8")
        function = script.split(function_name, 1)[1].split("\n}", 1)[0]
        assert 'python3 "$SOURCE_REDIS_GUARD" assert-idle' in function
        assert "lecturesift-backend:local" not in function
        assert "from redis import Redis" not in function

    migration = (ROOT / "deploy" / "migrate_redis_state.sh").read_text(
        encoding="utf-8"
    )
    assert migration.count('python3 "$SOURCE_REDIS_GUARD" export') == 3
    assert "export_redis_state.py" not in migration
    assert "lecturesift-backend:local" not in migration


def test_guard_has_no_third_party_client_and_reads_both_secret_envs():
    helper = (ROOT / "deploy" / "source_redis_guard.py").read_text(encoding="utf-8")
    assert "from redis import" not in helper
    assert "import redis" not in helper
    assert 'os.environ.get("SOURCE_REDIS_URL", "")' in helper
    assert 'os.environ.get("SOURCE_CELERY_BROKER_URL", "")' in helper
    assert "ssl.create_default_context()" in helper
    assert "ssl.TLSVersion.TLSv1_2" in helper
