from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "lecturesift_rehearsal_formats_e2e",
    ROOT / "deploy" / "rehearsal_formats_e2e.py",
)
assert SPEC and SPEC.loader
rehearsal = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(rehearsal)


@pytest.mark.parametrize(
    ("job", "terminal"),
    [
        ({"status": "done", "queue_mode": "celery", "worker_state": "done"}, True),
        ({"status": "done", "queue_mode": "celery", "worker_state": "publishing"}, False),
        ({"status": "done", "queue_mode": "celery", "worker_state": "processing"}, False),
        ({"status": "error", "queue_mode": "celery", "worker_state": "failed"}, True),
        ({"status": "error", "queue_mode": "celery", "worker_state": "rejected"}, True),
        ({"status": "error", "queue_mode": "celery", "worker_state": "unavailable"}, True),
        ({"status": "error", "queue_mode": "celery", "worker_state": "retrying"}, False),
        ({"status": "queued", "queue_mode": "celery", "worker_state": "queued"}, False),
        ({"status": "done", "queue_mode": "inline"}, True),
        ({"status": "error", "queue_mode": "inline"}, True),
        ({}, False),
    ],
)
def test_rehearsal_cleanup_requires_durable_terminal_state(job, terminal):
    assert rehearsal._job_is_durably_terminal(job) is terminal
