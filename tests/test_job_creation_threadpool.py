"""Job creation keeps blocking work off the event loop and never prunes old jobs."""
import threading
import uuid
from types import SimpleNamespace

from fastapi.testclient import TestClient

import lecturesift.app as app_module
from lecturesift.app import app
from lecturesift.billing_service import register_user, verify_email


def _token() -> str:
    registration = register_user(
        f"threadpool-{uuid.uuid4()}@example.com", "Strong-test-password1", "Thread", "Pool", country_code="TR"
    )
    return verify_email(registration["verification_token"])["token"]


def test_job_creation_runs_blocking_work_in_the_thread_pool(tmp_path, monkeypatch):
    threads: dict[str, list[threading.Thread]] = {}

    def recorded(name, function=None):
        def call(*args, **kwargs):
            threads.setdefault(name, []).append(threading.current_thread())
            return function(*args, **kwargs) if function else None

        return call

    def job_path(job_id):
        path = tmp_path / job_id
        path.mkdir()
        return path

    def no_pruning():
        raise AssertionError("expired results are pruned by the maintenance loop, not by an upload")

    # _options runs directly in the route, so it marks the event loop thread.
    monkeypatch.setattr(app_module, "_options", recorded("event-loop", app_module._options))
    monkeypatch.setattr(app_module, "validate_job_features", recorded("entitlement", app_module.validate_job_features))
    monkeypatch.setattr(app_module, "_require_ai_provider", recorded("provider"))
    monkeypatch.setattr(app_module, "_write_upload_chunk", recorded("upload", app_module._write_upload_chunk))
    monkeypatch.setattr(app_module, "_job_path", job_path)
    monkeypatch.setattr(app_module, "JOBS", SimpleNamespace(cleanup_expired=no_pruning, create=recorded("create")))
    monkeypatch.setattr(app_module, "threading", SimpleNamespace(
        Thread=lambda **kwargs: SimpleNamespace(start=lambda: None),
    ))

    response = TestClient(app).post(
        "/jobs",
        headers={"Authorization": f"Bearer {_token()}"},
        data={"quiz_count": "0", "flashcard_count": "0", "output_formats": "pdf"},
        files={"files": ("notes.pdf", b"%PDF-synthetic", "application/pdf")},
    )

    assert response.status_code == 200, response.text
    (event_loop,) = threads.pop("event-loop")
    assert set(threads) == {"entitlement", "provider", "upload", "create"}
    for name, used in threads.items():
        assert event_loop not in used, name
