from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from lecturesift import daily_social
from lecturesift import studio_social as studio
from lecturesift.instagram import InstagramAPIError


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
    assert "{marker}" in row["caption_template"] and "{voice}" in row["caption_template"]
    reel = studio._caption(row, date(2026, 10, 18), "reel")
    carousel = studio._caption(row, date(2026, 10, 18), "carousel")
    assert reel.endswith("Seslendirme yapay zekâ ile üretildi.\nLectureSift · studio-reel · 2026-10-18")
    assert "Seslendirme" not in carousel and carousel.endswith("#LectureSift\nLectureSift · studio-carousel · 2026-10-18")


def test_rows_stored_with_the_old_disclosure_still_caption_correctly():
    row = {"caption_template": "Metin\n#LectureSift\nVoice: AI-generated.\n{marker}"}
    assert studio._caption(row, date(2026, 10, 18), "carousel") == "Metin\n#LectureSift\nLectureSift · studio-carousel · 2026-10-18"
    assert "Voice:" not in studio._caption(row, date(2026, 10, 18), "reel")


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


def _fake_storage(monkeypatch):
    store = {}
    monkeypatch.setattr(studio, "read_json", lambda key: store.get(key))
    monkeypatch.setattr(studio, "write_json", lambda key, value: store.__setitem__(key, value))
    monkeypatch.setattr(studio, "read_bytes", lambda key: store.get(key))
    monkeypatch.setattr(studio, "write_bytes", lambda key, content, content_type: store.__setitem__(key, content))
    monkeypatch.setattr(studio, "recent_json", lambda prefix, limit: [])
    monkeypatch.setattr(studio, "OPENAI_API_KEY", "")
    return store


def test_carousel_renders_without_voice_or_video(monkeypatch):
    store = _fake_storage(monkeypatch)
    monkeypatch.setattr(studio.reel_studio, "narrate", lambda *a, **k: pytest.fail("carousel must not narrate"))
    row = studio.prepare_carousel(date(2026, 10, 18))
    assert row["carousel_rendered"] and not row["rendered"]
    assert all(f"social/instagram/studio/2026-10-18/carousel-{i}.jpg" in store for i in range(1, 6))
    assert "social/instagram/studio/2026-10-18/reel.mp4" not in store


def test_narration_is_paid_for_once_even_when_the_render_fails(monkeypatch):
    _fake_storage(monkeypatch)
    monkeypatch.setattr(studio, "ELEVENLABS_API_KEY", "key")
    monkeypatch.setattr(studio, "ELEVENLABS_VOICE_ID", "voice")
    calls, silent_narrate = [], studio.reel_studio.narrate

    def narrate(row, voice=True):
        calls.append(1)
        silent = silent_narrate(row, voice=False)
        return studio.reel_studio.Narration(b"wav", silent.words, silent.scenes, silent.duration)

    def broken_render(row, narration, out):
        assert narration.audio == b"wav" and narration.words
        raise studio.reel_studio.StudioError("FFmpeg could not encode the Reel")

    monkeypatch.setattr(studio.reel_studio, "narrate", narrate)
    monkeypatch.setattr(studio.reel_studio, "render_reel", broken_render)
    for _ in range(2):
        with pytest.raises(studio.reel_studio.StudioError):
            studio.prepare(date(2026, 10, 18))
    assert calls == [1]


def test_a_failing_reel_does_not_hold_back_the_carousel(monkeypatch):
    rows = {}
    monkeypatch.setattr(studio, "INSTAGRAM_DAILY_AUTOMATION_ENABLED", True)
    monkeypatch.setattr(studio, "_row", lambda day: rows.get(day))
    monkeypatch.setattr(studio, "INSTAGRAM_REEL_HOUR", 13)
    monkeypatch.setattr(studio, "INSTAGRAM_CAROUSEL_HOUR", 13)

    def reel(day):
        raise studio.reel_studio.StudioError("FFmpeg could not encode the Reel")

    def carousel(day):
        rows[day] = {"carousel_media_id": "2"}
        return {"status": "published", "kind": "studio_carousel"}

    monkeypatch.setattr(studio, "publish_reel", reel)
    monkeypatch.setattr(studio, "publish_carousel", carousel)
    results = studio.run_scheduled(datetime(2026, 10, 18, 13, 5, tzinfo=ZoneInfo("Europe/Istanbul")))
    assert [r["status"] for r in results] == ["published", "failed"]
    assert results[1]["kind"] == "studio_reel" and "FFmpeg" in results[1]["error"]


def test_reel_is_rendered_ahead_of_its_slot_after_the_carousel(monkeypatch):
    rows, prepared = {}, []
    monkeypatch.setattr(studio, "INSTAGRAM_DAILY_AUTOMATION_ENABLED", True)
    monkeypatch.setattr(studio, "_row", lambda day: rows.get(day))
    monkeypatch.setattr(studio, "INSTAGRAM_REEL_HOUR", 20)
    monkeypatch.setattr(studio, "INSTAGRAM_CAROUSEL_HOUR", 13)

    def carousel(day):
        rows[day] = {"carousel_media_id": "2", "rendered": False}
        return {"status": "published"}

    def prepare(day):
        prepared.append(day)
        rows[day] = {**rows[day], "rendered": True}

    monkeypatch.setattr(studio, "publish_carousel", carousel)
    monkeypatch.setattr(studio, "prepare", prepare)
    tz = ZoneInfo("Europe/Istanbul")
    first = studio.run_scheduled(datetime(2026, 10, 18, 13, 5, tzinfo=tz))
    assert [r["status"] for r in first] == ["published", "reel_ready"]
    assert studio.run_scheduled(datetime(2026, 10, 18, 13, 20, tzinfo=tz))[0]["status"] == "nothing_due"
    assert len(prepared) == 1


def test_narration_is_stored_outside_the_post_history(monkeypatch):
    store = _fake_storage(monkeypatch)
    monkeypatch.setattr(studio, "ELEVENLABS_API_KEY", "key")
    monkeypatch.setattr(studio, "ELEVENLABS_VOICE_ID", "voice")
    silent_narrate = studio.reel_studio.narrate
    monkeypatch.setattr(studio.reel_studio, "narrate", lambda row, voice=True: studio.reel_studio.Narration(
        b"wav", *(lambda n: (n.words, n.scenes, n.duration))(silent_narrate(row, voice=False))))
    monkeypatch.setattr(studio.reel_studio, "render_reel", lambda *a: (_ for _ in ()).throw(studio.reel_studio.StudioError("x")))
    with pytest.raises(studio.reel_studio.StudioError):
        studio.prepare(date(2026, 10, 18))
    json_keys = [k for k in store if k.endswith(".json") and k.startswith(studio._PREFIX)]
    assert json_keys == ["social/instagram/studio/2026-10-18/post.json"]
    assert "social/instagram/studio-voice/2026-10-18/narration.json" in store


def test_run_command_exits_non_zero_when_a_slot_fails(monkeypatch):
    monkeypatch.setattr(studio, "run_scheduled", lambda: [{"status": "published"}, {"status": "failed"}])
    assert studio.main(["run"]) == 1
    monkeypatch.setattr(studio, "run_scheduled", lambda: [{"status": "nothing_due"}])
    assert studio.main(["run"]) == 0


class _TimeoutGraph:
    """Fake Graph client whose media_publish times out, either after Meta created the post or before."""

    def __init__(self, creates_post: bool):
        self.creates_post = creates_post
        self.media, self.captions = [], {}
        self.publish_calls = self.recent_calls = 0

    def get_account(self):
        return {"username": "lecturesift"}

    def get_recent_media(self, limit=25):
        self.recent_calls += 1
        return {"data": list(self.media)}

    def create_media_container(self, *, media_url, caption="", media_type="IMAGE", cover_url=None):
        self.captions["container-1"] = caption
        return {"id": "container-1"}

    def create_carousel_item(self, *, image_url):
        return {"id": "item-" + image_url.rsplit("-", 1)[-1]}

    def create_carousel(self, *, children, caption=""):
        assert len(children) == studio.CAROUSEL_SLIDES
        self.captions["container-1"] = caption
        return {"id": "container-1"}

    def get_container_status(self, _container_id):
        return {"status_code": "FINISHED"}

    def publish_media(self, container_id):
        self.publish_calls += 1
        if self.creates_post:
            self.media.insert(0, {"id": "media-9", "caption": self.captions[container_id]})
        raise InstagramAPIError("Instagram API is temporarily unavailable", transient=True)


def _studio_publish_setup(monkeypatch, graph, day):
    store = _fake_storage(monkeypatch)
    store[studio._key(day, "post.json")] = {
        **studio.build_row(studio._CURATED[0], day), "date": day.isoformat(), "rendered": True,
        "carousel_rendered": True, "reel_media_id": None, "carousel_media_id": None,
    }
    monkeypatch.setattr(studio, "_client", lambda: graph)
    monkeypatch.setattr(studio, "prepare", studio._row)
    monkeypatch.setattr(studio, "prepare_carousel", studio._row)
    monkeypatch.setattr(studio, "_verify_public_video", lambda _url: None)
    monkeypatch.setattr(daily_social.time, "sleep", lambda _seconds: None)
    return store


@pytest.mark.parametrize("kind", ["reel", "carousel"])
def test_publish_that_times_out_after_meta_created_the_post_is_recorded(monkeypatch, kind):
    day = date(2026, 10, 19)
    graph = _TimeoutGraph(creates_post=True)
    store = _studio_publish_setup(monkeypatch, graph, day)
    result = getattr(studio, f"publish_{kind}")(day)
    assert result == {"status": "published", "kind": f"studio_{kind}", "date": "2026-10-19", "media_id": "media-9"}
    assert store[studio._key(day, "post.json")][f"{kind}_media_id"] == "media-9"
    assert graph.publish_calls == 1


@pytest.mark.parametrize("kind", ["reel", "carousel"])
def test_publish_that_times_out_without_a_post_fails_without_republishing(monkeypatch, kind):
    day = date(2026, 10, 19)
    graph = _TimeoutGraph(creates_post=False)
    store = _studio_publish_setup(monkeypatch, graph, day)
    with pytest.raises(InstagramAPIError) as caught:
        getattr(studio, f"publish_{kind}")(day)
    assert caught.value.error_type == "publish_outcome_unknown"
    assert "not in recent media" in str(caught.value)
    assert graph.publish_calls == 1
    assert graph.recent_calls == 4  # the check before publishing, then three looks for the marker
    assert store[studio._key(day, "post.json")][f"{kind}_media_id"] is None


def test_a_definitive_publish_error_is_not_retried_or_masked(monkeypatch):
    day = date(2026, 10, 19)
    graph = _TimeoutGraph(creates_post=False)
    _studio_publish_setup(monkeypatch, graph, day)

    def reject(_container_id):
        graph.publish_calls += 1
        raise InstagramAPIError("The media is not ready for publishing", error_type="OAuthException")

    graph.publish_media = reject
    with pytest.raises(InstagramAPIError, match="not ready"):
        studio.publish_reel(day)
    assert graph.publish_calls == 1 and graph.recent_calls == 1


def _reel_slot(monkeypatch, graph, day):
    store = _studio_publish_setup(monkeypatch, graph, day)
    store[studio._key(day, "post.json")]["carousel_media_id"] = "carousel-1"
    monkeypatch.setattr(studio, "INSTAGRAM_DAILY_AUTOMATION_ENABLED", True)
    monkeypatch.setattr(studio, "INSTAGRAM_REEL_HOUR", 20)
    monkeypatch.setattr(studio, "INSTAGRAM_CAROUSEL_HOUR", 13)
    return store


def test_scheduled_reel_slot_succeeds_when_meta_answers_publish_late(monkeypatch):
    day, tz = date(2026, 10, 19), ZoneInfo("Europe/Istanbul")
    graph = _TimeoutGraph(creates_post=True)
    _reel_slot(monkeypatch, graph, day)
    assert [r["status"] for r in studio.run_scheduled(datetime(2026, 10, 19, 20, 0, tzinfo=tz))] == ["published"]
    assert studio.run_scheduled(datetime(2026, 10, 19, 20, 15, tzinfo=tz))[0]["status"] == "nothing_due"
    assert graph.publish_calls == 1


def test_scheduled_reel_slot_with_unknown_outcome_is_settled_by_the_next_run(monkeypatch):
    day, tz = date(2026, 10, 19), ZoneInfo("Europe/Istanbul")
    graph = _TimeoutGraph(creates_post=False)
    store = _reel_slot(monkeypatch, graph, day)
    first = studio.run_scheduled(datetime(2026, 10, 19, 20, 0, tzinfo=tz))
    assert first[0]["status"] == "failed" and "outcome unknown" in first[0]["error"]
    graph.media.insert(0, {"id": "media-late", "caption": graph.captions["container-1"]})  # listed late
    assert studio.run_scheduled(datetime(2026, 10, 19, 20, 15, tzinfo=tz))[0]["status"] == "already_published"
    assert store[studio._key(day, "post.json")]["reel_media_id"] == "media-late"
    assert graph.publish_calls == 1
