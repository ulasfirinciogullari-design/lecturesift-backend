"""Synthetic downloader regressions; no media provider or network is contacted."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from lecturesift import media
from lecturesift.errors import LectureSiftError, normalize_error


@pytest.fixture
def downloader(monkeypatch, tmp_path):
    state = {"options": None, "error": None, "file": "remote.mp4", "data": b"synthetic media"}
    monkeypatch.setattr(media, "validate_remote_url", lambda url: url)

    class FakeDownloader:
        def __init__(self, options):
            state["options"] = options

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def extract_info(self, url, download):
            state["url"] = url
            assert download is True
            if state["error"]:
                raise RuntimeError(state["error"])
            path = tmp_path / state["file"]
            path.write_bytes(state["data"])
            return {"requested_downloads": [{"filepath": str(path)}]}

        def prepare_filename(self, _info):
            return str(tmp_path / state["file"])

    monkeypatch.setattr(media.yt_dlp, "YoutubeDL", FakeDownloader)
    return state


@pytest.mark.parametrize("url", [
    "https://www.youtube.com/watch?v=abcdefghijk",
    "https://m.youtube.com/shorts/abcdefghijk",
    "https://youtu.be/abcdefghijk",
    "https://www.youtube-nocookie.com/embed/abcdefghijk",
])
def test_youtube_uses_extractor_and_packaged_solver_before_page_discovery(downloader, tmp_path, monkeypatch, url):
    path = media.download_remote_video(url, tmp_path, job_type="audio_export")
    assert path.read_bytes() == downloader["data"]
    assert downloader["options"]["format"] == "bestaudio/best"
    assert downloader["options"]["js_runtimes"] == {"deno": {}}
    assert downloader["options"]["remote_components"] == []


def test_youtube_lookalike_is_rejected_before_downloading(downloader, tmp_path):
    with pytest.raises(LectureSiftError) as caught:
        media.download_remote_video("https://youtube.com.example.org/watch?v=abcdefghijk", tmp_path)
    assert caught.value.code == "LS-URL-05"
    assert downloader["options"] is None


@pytest.mark.parametrize("filename,data", [("remote.mp4.part", b"partial"), ("remote.mp4", b"")])
def test_partial_or_empty_download_is_never_returned(downloader, tmp_path, filename, data):
    downloader.update(file=filename, data=data)
    with pytest.raises(RuntimeError, match="No downloadable video"):
        media.download_remote_video("https://youtu.be/abcdefghijk", tmp_path)


def test_final_merged_size_is_checked(downloader, tmp_path, monkeypatch):
    monkeypatch.setattr(media, "MAX_VIDEO_BYTES", 3)
    with pytest.raises(LectureSiftError) as caught:
        media.download_remote_video("https://youtu.be/abcdefghijk", tmp_path)
    assert caught.value.code == "LS-UPLOAD-02"
    assert not (tmp_path / "remote.mp4").exists()


@pytest.mark.parametrize("error", ["HTTP Error 429", "Sign in to confirm you're not a bot"])
def test_provider_block_remains_a_url_error_not_an_ai_quota_error(downloader, tmp_path, error):
    downloader["error"] = error
    with pytest.raises(RuntimeError) as caught:
        media.download_remote_video("https://youtu.be/abcdefghijk", tmp_path)
    assert normalize_error(caught.value).code == "LS-URL-02"


def test_private_url_rejected_before_extractor(downloader, tmp_path, monkeypatch):
    def reject(_url):
        raise LectureSiftError("LS-URL-04", "Private address")

    monkeypatch.setattr(media, "validate_remote_url", reject)
    with pytest.raises(LectureSiftError, match="Private address"):
        media.download_remote_video("https://youtu.be/abcdefghijk", tmp_path)
    assert downloader["options"] is None


URL_CASES = [
    ("https://www.youtube.com/watch?v=abcdefghijk&list=ignored&si=tracking", True),
    ("https://youtu.be/abcdefghijk?t=30", True),
    ("http://m.youtube.com/watch?v=abcdefghijk", True),
    ("https://music.youtube.com/watch?v=abcdefghijk", True),
    ("https://www.youtube.com/shorts/abcdefghijk", True),
    ("https://www.youtube.com/live/abcdefghijk", True),
    ("https://www.youtube-nocookie.com/embed/abcdefghijk", True),
    ("https://www.youtube.com/playlist?list=anything", False),
    ("https://www.youtube.com/watch?v=abcdefghijk&v=ABCDEFGHIJK", False),
    ("https://www.youtube.com/redirect?q=https://example.org", False),
    ("https://www.youtube.com/@channel", False),
    ("https://youtu.be/short", False),
    ("https://youtube.com.example.org/watch?v=abcdefghijk", False),
    ("https://youtube.com@evil.example/watch?v=abcdefghijk", False),
    ("https://name:password@youtube.com/watch?v=abcdefghijk", False),
    ("https://www.youtube.com:8080/watch?v=abcdefghijk", False),
    ("ftp://youtube.com/watch?v=abcdefghijk", False),
    ("https://example.org/video.mp4", False),
    ("https://vimeo.com/12345", False),
    ("http://127.0.0.1/video.mp4", False),
    ("", False),
]


@pytest.mark.parametrize("url,accepted", URL_CASES)
def test_youtube_input_contract_before_network(monkeypatch, url, accepted):
    validated = []
    monkeypatch.setattr(media, "validate_remote_url", lambda value: validated.append(value) or value)
    if accepted:
        assert media.validate_youtube_url(url) == "https://www.youtube.com/watch?v=abcdefghijk"
        assert validated == ["https://www.youtube.com/watch?v=abcdefghijk"]
    else:
        with pytest.raises(LectureSiftError) as caught:
            media.validate_youtube_url(url)
        assert caught.value.code == "LS-URL-05"
        assert validated == []


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is required")
def test_browser_and_server_use_the_same_youtube_input_contract():
    root = Path(__file__).resolve().parents[1]
    source = (root / "frontend/app.js").read_text()
    normalizer = source[source.index("function normalizeYouTubeUrl("):source.index("const $ =")]
    script = normalizer + "\nconsole.log(JSON.stringify(" + json.dumps([url for url, _ in URL_CASES]) + ".map(normalizeYouTubeUrl)));"
    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True, timeout=5)
    assert json.loads(result.stdout) == ["https://www.youtube.com/watch?v=abcdefghijk" if accepted else "" for _, accepted in URL_CASES]


def test_url_rejected_by_api_before_plan_checks_or_job_creation(monkeypatch):
    import importlib
    from fastapi import HTTPException

    application = importlib.import_module("lecturesift.app")
    def unexpected(*_args, **_kwargs):
        pytest.fail("Invalid URLs must be rejected before plan checks or work")
    monkeypatch.setattr(application, "validate_job_features", unexpected)
    monkeypatch.setattr(application, "start_url_job", unexpected)
    with pytest.raises(HTTPException) as caught:
        application.create_url_job(video_url="https://example.org/video.mp4", billing_user={"id": "synthetic"})
    assert caught.value.status_code == 422
    assert caught.value.detail["code"] == "LS-URL-05"
