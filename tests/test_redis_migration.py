from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "lecturesift_export_redis_state",
    ROOT / "deploy" / "export_redis_state.py",
)
assert SPEC and SPEC.loader
exporter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(exporter)


class FakeRedis:
    def __init__(self, payload: dict, *, state_lock: bool = False, processing_locks: int = 0):
        self.payload = payload
        self.state_lock = state_lock
        self.processing_locks = processing_locks

    def ping(self):
        return True

    def exists(self, _key):
        return int(self.state_lock)

    def scan_iter(self, **_kwargs):
        return iter(["lock"] * self.processing_locks)

    def get(self, _key):
        return json.dumps(self.payload)


def _run(monkeypatch, tmp_path: Path, fake: FakeRedis):
    output = tmp_path / "source-before.json"
    monkeypatch.setenv("SOURCE_REDIS_URL", "rediss://user:secret@example.invalid:6379/0")
    monkeypatch.setattr(exporter.Redis, "from_url", lambda *_args, **_kwargs: fake)
    monkeypatch.setattr(sys, "argv", ["export_redis_state.py", str(output)])
    exporter.main()
    return output


def test_redis_export_accepts_only_terminal_versioned_jobs(monkeypatch, tmp_path: Path):
    output = _run(
        monkeypatch,
        tmp_path,
        FakeRedis(
            {
                "version": 2,
                "saved_at": 10,
                "jobs": {
                    "done": {"status": "done"},
                    "failed": {"status": "error"},
                },
            }
        ),
    )

    exported = json.loads(output.read_text(encoding="utf-8"))
    assert exported["version"] == 2
    assert set(exported["jobs"]) == {"done", "failed"}


@pytest.mark.parametrize(
    ("fake", "message"),
    [
        (FakeRedis({"version": 2, "jobs": {"job": {"status": "working"}}}), "queued or working"),
        (FakeRedis({"version": 2, "jobs": {}}, state_lock=True), "state write lock"),
        (FakeRedis({"version": 2, "jobs": {}}, processing_locks=1), "processing locks"),
    ],
)
def test_redis_export_fails_closed_on_active_work(monkeypatch, tmp_path: Path, fake, message):
    with pytest.raises(RuntimeError, match=message):
        _run(monkeypatch, tmp_path, fake)


def test_redis_export_rejects_internal_target_url(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("SOURCE_REDIS_URL", "redis://redis:6379/0")
    monkeypatch.setattr(sys, "argv", ["export_redis_state.py", str(tmp_path / "source-before.json")])

    with pytest.raises(RuntimeError, match="remote source Redis"):
        exporter.main()
