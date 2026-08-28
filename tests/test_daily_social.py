from datetime import date

from io import BytesIO

from PIL import Image

import lecturesift.daily_social as daily_social
from lecturesift.daily_social import daily_tip, render_daily_image, render_daily_reel_cover


def test_daily_tip_is_stable_and_has_idempotency_marker():
    selected_day = date(2026, 8, 27)
    assert daily_tip(selected_day) == daily_tip(selected_day)
    assert "#LectureSiftGununNotu20260827" in daily_tip(selected_day).caption


def test_daily_image_is_a_jpeg():
    image = render_daily_image(date(2026, 8, 27))
    assert image.startswith(b"\xff\xd8\xff")
    assert len(image) > 10_000


def test_daily_reel_cover_is_vertical_and_readable():
    content = render_daily_reel_cover(date(2026, 8, 27))
    image = Image.open(BytesIO(content))
    assert image.format == "JPEG"
    assert image.size == (1080, 1920)


def test_daily_reel_uses_ffmpeg_without_exposing_secrets(monkeypatch, tmp_path):
    daily_social.render_daily_reel.cache_clear()
    monkeypatch.setattr(daily_social.tempfile, "mkdtemp", lambda **_kwargs: str(tmp_path))

    def fake_run(command, **kwargs):
        assert command[0] == "ffmpeg"
        assert "libx264" in command and "+faststart" in command
        assert kwargs == {"capture_output": True, "timeout": 75, "check": False}
        (tmp_path / "reel.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"0" * 12_000)
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr(daily_social.subprocess, "run", fake_run)
    content = daily_social.render_daily_reel(date(2026, 8, 27))
    assert content[4:8] == b"ftyp"


def test_daily_publisher_creates_a_reel_container(monkeypatch):
    captured = {}

    class FakeClient:
        def get_account(self):
            return {"username": "lecturesift"}

        def get_recent_media(self, limit=25):
            return {"data": []}

        def create_media_container(self, **kwargs):
            captured.update(kwargs)
            return {"id": "container-1"}

        def get_container_status(self, _container_id):
            return {"status_code": "FINISHED"}

        def publish_media(self, _container_id):
            return {"id": "media-1"}

    monkeypatch.setattr(daily_social, "INSTAGRAM_DAILY_AUTOMATION_ENABLED", True)
    monkeypatch.setattr(daily_social, "INSTAGRAM_DAILY_MEDIA_TYPE", "REELS")
    monkeypatch.setattr(daily_social, "PUBLIC_BASE_URL", "https://backend.example")
    monkeypatch.setattr(daily_social, "publish_next_launch_post", lambda: {"status": "launch_complete"})
    monkeypatch.setattr(daily_social, "_client", FakeClient)

    result = daily_social.publish_daily_post(date(2026, 8, 27))
    assert result["media_type"] == "REELS"
    assert captured["media_type"] == "REELS"
    assert captured["media_url"].endswith("/instagram/daily/reel/2026-08-27.mp4")
    assert captured["cover_url"].endswith("/instagram/daily/reel/2026-08-27.jpg")
