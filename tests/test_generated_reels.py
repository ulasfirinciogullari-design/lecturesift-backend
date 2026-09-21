from datetime import date
import json
import subprocess

import lecturesift.generated_reels as reels
from lecturesift.daily_social import _reel_audio, daily_tip, render_tip_reel


def test_generated_reel_is_persisted_with_original_narration(monkeypatch):
    objects = {}
    monkeypatch.setattr(reels, "read_json", lambda key: objects.get(key))
    monkeypatch.setattr(reels, "write_json", lambda key, value: objects.__setitem__(key, value))
    monkeypatch.setattr(reels, "write_bytes", lambda key, value, _type: objects.__setitem__(key, value))
    monkeypatch.setattr(reels, "read_bytes", lambda key: objects.get(key))
    monkeypatch.setattr(reels, "_recent_reel_titles", lambda: [])
    monkeypatch.setattr(reels, "_recent_titles", lambda: [])
    calls = []

    def generate(day, recent, *, pillar_offset=0):
        calls.append((day, pillar_offset))
        return {
            "title": "Test yourself after class",
            "body": "A short check reveals exactly what you need to revisit.",
            "title_tr": "Dersten sonra kendini sına",
            "body_tr": "Kısa bir kontrol hangi konuya dönmen gerektiğini gösterir.",
            "keyword": "review after a lecture",
            "steps": [
                "Close the notes and name three key ideas.",
                "Write one question you still cannot answer.",
                "Revisit that explanation and answer from memory.",
            ],
        }

    monkeypatch.setattr(reels, "_generate", generate)
    monkeypatch.setattr(reels, "_speech", lambda _data: (b"original-mp3-audio", 14.2))
    day = date(2026, 10, 15)
    first = reels._ensure_post(day)
    second = reels._ensure_post(day)
    assert first["caption"] == second["caption"]
    assert calls == [(day, 3)]
    assert "Voice: AI-generated" in first["caption"]
    assert "LectureSift · reel · 2026-10-15" in first["caption"]
    assert reels.cover_for_day(day).startswith(b"\xff\xd8\xff")


def test_generated_reel_does_not_republish_a_recent_marker(monkeypatch):
    day = date(2026, 10, 15)
    class FakeClient:
        def get_account(self):
            return {"username": "lecturesift"}

        def get_recent_media(self, limit=50):
            return {"data": [{"caption": "LectureSift · reel · 2026-10-15"}]}

        def create_media_container(self, **kwargs):
            raise AssertionError("existing Reel must not be republished")

    monkeypatch.setattr(reels, "INSTAGRAM_DAILY_AUTOMATION_ENABLED", True)
    monkeypatch.setattr(reels, "_client", FakeClient)
    monkeypatch.setattr(reels, "_ensure_post", lambda _day: {"published_media_id": None})
    assert reels.publish_generated_reel(day)["status"] == "already_published"


def test_reel_prepare_command_never_publishes(monkeypatch):
    dates = []
    monkeypatch.setattr(reels.sys, "argv", ["generated_reels", "prepare", "2026-10-15"])
    monkeypatch.setattr(reels, "_ensure_post", lambda day: dates.append(day))
    monkeypatch.setattr(reels, "publish_generated_reel", lambda: (_ for _ in ()).throw(AssertionError("must not publish")))
    assert reels.main() == 0
    assert dates == [date(2026, 10, 15)]


def test_generated_reel_renderer_outputs_video_with_narration(tmp_path):
    day = date(2026, 9, 21)
    voice, seconds, _music = _reel_audio(day)
    video = render_tip_reel(daily_tip(day), voice.read_bytes(), seconds)
    path = tmp_path / "reel.mp4"
    path.write_bytes(video)
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "json", str(path)],
        capture_output=True, timeout=10, check=True,
    )
    stream_types = {item["codec_type"] for item in json.loads(result.stdout)["streams"]}
    assert stream_types == {"video", "audio"}
