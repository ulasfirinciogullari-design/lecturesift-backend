from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from lecturesift import studio_social as studio


def _draft(**overrides):
    data = {
        "source": "Kimya 101 · Asitler", "file": "kimya-sunum.pdf",
        "question": "pH değeri 7'nin altında olan çözelti nasıldır?",
        "options": ["Asidik", "Bazik", "Nötr", "Tuzlu"], "answer": 0,
        "hook_line1": "Kimya notlarını", "hook_line2": "3. kez mi okuyorsun?", "hook_highlight": "3. kez",
        "voice_hook": "Kimya notlarını üçüncü kez mi okuyorsun?",
        "caption_body": "Asit ve baz konusunu tekrar okumak yerine kendine tek bir soru sor, eksik yeri hemen gör.",
        "topic_hashtag": "#Kimya",
    }
    return {**data, **overrides}


def test_valid_lesson_becomes_a_complete_row():
    lesson = studio.validate_lesson(_draft(), "Kimya", [])
    hook = lesson.pop("hook")
    row = studio.build_row(lesson, date(2026, 10, 18), hook)
    assert row["hook"] == ["Kimya notlarını", "3. kez mi okuyorsun?"]
    assert [line["scene"] for line in row["voice"]] == ["hook", "problem", "product", "quiz", "answer", "cta"]
    assert row["voice"][4]["text"] == "Cevap: Asidik."
    assert "{marker}" in row["caption_template"] and "Voice: AI-generated." in row["caption_template"]
    assert studio._caption(row, date(2026, 10, 18), "reel").endswith("LectureSift · studio-reel · 2026-10-18")


@pytest.mark.parametrize("overrides", [
    {"options": ["Asidik", "Asidik", "Nötr", "Tuzlu"]},
    {"answer": 4},
    {"question": "pH 7'nin altı nedir"},
    {"hook_highlight": "yok"},
    {"voice_hook": "Kimya notlarını 3 kez mi okuyorsun?"},
    {"caption_body": "Bu yöntem %100 garanti başarı getirir, hemen dene ve sınavı geç, kesin işe yarar."},
    {"topic_hashtag": "Kimya"},
])
def test_invalid_drafts_are_rejected(overrides):
    with pytest.raises(ValueError):
        studio.validate_lesson(_draft(**overrides), "Kimya", [])


def test_repeated_question_is_rejected():
    with pytest.raises(ValueError, match="repeats"):
        studio.validate_lesson(_draft(), "Kimya", ["pH değeri 7'nin altında olan çözelti nasıldır?"])


def test_curated_lessons_build_valid_rows():
    for index, lesson in enumerate(studio._CURATED):
        row = studio.build_row(lesson, date.fromordinal(date(2026, 10, 18).toordinal() + index))
        assert row["options"][row["answer"]] in row["voice"][4]["text"]
        assert len(row["options"]) == 4


def test_lesson_falls_back_to_curated_without_openai(monkeypatch):
    monkeypatch.setattr(studio, "OPENAI_API_KEY", "")
    lesson, hook = studio.generate_lesson(date(2026, 10, 18), [])
    assert lesson in studio._CURATED and hook is None


def test_schedule_publishes_reel_then_carousel(monkeypatch):
    calls, rows = [], {}
    monkeypatch.setattr(studio, "INSTAGRAM_DAILY_AUTOMATION_ENABLED", True)
    monkeypatch.setattr(studio, "_row", lambda day: rows.get(day))
    monkeypatch.setattr(studio, "INSTAGRAM_REEL_HOUR", 11)
    monkeypatch.setattr(studio, "INSTAGRAM_CAROUSEL_HOUR", 19)

    def reel(day):
        calls.append("reel")
        rows[day] = {**rows.get(day, {}), "reel_media_id": "1"}
        return {"status": "published"}

    def carousel(day):
        calls.append("carousel")
        rows[day] = {**rows.get(day, {}), "carousel_media_id": "2"}
        return {"status": "published"}

    monkeypatch.setattr(studio, "publish_reel", reel)
    monkeypatch.setattr(studio, "publish_carousel", carousel)
    tz = ZoneInfo("Europe/Istanbul")
    assert studio.run_scheduled(datetime(2026, 10, 18, 9, 0, tzinfo=tz))[0]["status"] == "nothing_due"
    studio.run_scheduled(datetime(2026, 10, 18, 11, 5, tzinfo=tz))
    studio.run_scheduled(datetime(2026, 10, 18, 12, 5, tzinfo=tz))
    studio.run_scheduled(datetime(2026, 10, 18, 19, 5, tzinfo=tz))
    studio.run_scheduled(datetime(2026, 10, 18, 20, 5, tzinfo=tz))
    assert calls == ["reel", "carousel"]


def test_asset_names_are_allow_listed(monkeypatch):
    monkeypatch.setattr(studio, "read_bytes", lambda key: b"x")
    assert studio.asset(date(2026, 10, 18), "reel.mp4") == b"x"
    assert studio.asset(date(2026, 10, 18), "post.json") is None
    assert studio.asset(date(2026, 10, 18), "../secret") is None
