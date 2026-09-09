"""Retired remote-source requests cannot start work or spend a user's balance."""
import importlib
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from lecturesift import media


@pytest.mark.parametrize("url", [
    "https://www.youtube.com/watch?v=abcdefghijk", "https://youtu.be/abcdefghijk",
    "https://cdn.example.com/lesson.mp4", "http://127.0.0.1/private", "not a url",
])
def test_retired_download_never_uses_network_or_creates_a_file(tmp_path, monkeypatch, url):
    def unexpected(*args, **kwargs):
        pytest.fail("A removed source must not start media work")
    monkeypatch.setattr(media.subprocess, "run", unexpected)
    with pytest.raises(media.LectureSiftError) as caught:
        media.download_remote_video(url, tmp_path)
    assert caught.value.code == "LS-URL-06"
    assert caught.value.status_code == 410
    assert not list(tmp_path.iterdir())


def test_retired_api_rejects_even_a_stale_client_before_billing_or_job_creation(monkeypatch):
    application = importlib.import_module("lecturesift.app")
    def unexpected(*args, **kwargs):
        pytest.fail("A removed API must not perform billing, authentication or work")
    monkeypatch.setattr(application, "validate_job_features", unexpected)
    monkeypatch.setattr(application.JOBS, "create", unexpected)
    client = TestClient(application.app)
    for data in ({}, {"video_url": "https://youtu.be/abcdefghijk"}):
        response = client.post("/jobs/url", data=data)
        assert response.status_code == 410
        assert response.json()["detail"]["code"] == "LS-URL-06"
    assert "/jobs/url" not in application.app.openapi()["paths"]
    assert client.get("/health").json()["url_video_download"] is False


def test_queued_url_job_is_retired_but_completed_results_are_preserved(tmp_path, monkeypatch):
    from lecturesift import tasks
    from lecturesift.jobs import JOBS
    options = {"billing_user_id": "synthetic-retired-owner"}
    job_dir = tmp_path / "pending"
    job_dir.mkdir()
    JOBS.create("synthetic-retired-pending", job_dir, options, source_type="url")
    monkeypatch.setattr(tasks, "process_job", lambda *args, **kwargs: pytest.fail("No pipeline work"))
    result = tasks.process_url_job.run("synthetic-retired-pending", "https://youtu.be/abcdefghijk", options)
    assert result["status"] == "error"
    assert JOBS.get("synthetic-retired-pending")["error_code"] == "LS-URL-06"
    completed = tmp_path / "completed"
    completed.mkdir()
    artifact = completed / "keep.pdf"
    artifact.write_bytes(b"synthetic already completed file")
    JOBS.create("synthetic-retired-completed", completed, options, source_type="url")
    JOBS.update("synthetic-retired-completed", status="done")
    assert tasks.process_url_job.run("synthetic-retired-completed", "unused", options)["status"] == "done"
    assert artifact.exists()
