from datetime import date
import json
import subprocess

import lecturesift.generated_reels as reels
from lecturesift.daily_social import _reel_audio


def _growth_concept():
    return {
        "format_version": 2,
        "series": "lecture-to-quiz",
        "student_problem": "rereading notes",
        "source": "52 sayfalık biyoloji dersi",
        "question": "Mitozda kromozomlar hangi evrede ortada dizilir?",
        "answer": "Metafaz",
        "keyword": "ders notlarından test oluşturma",
        "topic_hashtag": "#Biyoloji",
        "hook_tr": "52 sayfayı tekrar okuma",
        "hook_en": "Turn lecture notes into a recall test",
        "payoff_tr": "Dersi çalışma paketine çevir ve sadece unuttuğun yere dön.",
        "voice_tr": "Notları baştan okuma. LectureSift ile dersi çalışma paketine çevir, testi çöz ve sadece unuttuğun yere dön.",
        "caption_tr": "Ders notlarından test oluşturma yöntemiyle önce hafızanı kontrol et, sonra yalnızca eksik olduğun bölümü yeniden çalış.",
    }


def test_generated_reel_is_persisted_with_original_narration(monkeypatch):
    objects = {}
    monkeypatch.setattr(reels, "read_json", lambda key: objects.get(key))
    monkeypatch.setattr(reels, "write_json", lambda key, value: objects.__setitem__(key, value))
    monkeypatch.setattr(reels, "write_bytes", lambda key, value, _type: objects.__setitem__(key, value))
    monkeypatch.setattr(reels, "read_bytes", lambda key: objects.get(key))
    monkeypatch.setattr(reels, "_recent_reel_hooks", lambda: [])
    monkeypatch.setattr(reels, "_recent_titles", lambda: [])
    calls = []

    def generate(day, recent):
        calls.append((day, recent))
        return _growth_concept()

    monkeypatch.setattr(reels, "_generate_concept", generate)
    monkeypatch.setattr(reels, "_speech", lambda _data: (b"original-mp3-audio", 9.2))
    day = date(2026, 10, 15)
    first = reels._ensure_post(day)
    second = reels._ensure_post(day)
    assert first["caption"] == second["caption"]
    assert len(calls) == 1 and calls[0][0] == day
    assert first["format_version"] == 2
    assert first["hook_tr"] == "52 sayfayı tekrar okuma"
    assert first["caption"].count("#") == 5
    assert "#Biyoloji" in first["caption"]
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
    video = reels.render_growth_reel(_growth_concept(), voice.read_bytes(), seconds)
    path = tmp_path / "reel.mp4"
    path.write_bytes(video)
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "json", str(path)],
        capture_output=True, timeout=10, check=True,
    )
    stream_types = {item["codec_type"] for item in json.loads(result.stdout)["streams"]}
    assert stream_types == {"video", "audio"}
