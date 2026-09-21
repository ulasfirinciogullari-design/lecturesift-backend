from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

import lecturesift.evergreen_social as social


def test_evergreen_card_is_persisted_and_reused(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    social._META.create_all(engine)
    monkeypatch.setattr(social, "_engine", lambda: engine)
    generated = []

    def generate(day, recent):
        generated.append((day, recent))
        return {
            "title": "Find the one idea you missed",
            "body": "A precise gap gives you a much better starting point for review.",
            "title_tr": "Kaçırdığın tek fikri bul",
            "body_tr": "Net bir eksik, tekrara başlamak için daha iyi bir noktadır.",
            "keyword": "review lecture notes",
            "steps": [
                "Close the notes and explain the main idea aloud.",
                "Mark the exact point where your explanation breaks.",
                "Open only that section and try explaining it again.",
            ],
        }

    monkeypatch.setattr(social, "_generate", generate)
    day = date(2026, 9, 22)
    first = social._ensure_post(day)
    second = social._ensure_post(day)
    assert len(generated) == 1
    assert first["caption"] == second["caption"]
    assert "LectureSift · idea · 2026-09-22" in first["caption"]
    assert "review lecture notes" in first["caption"]
    assert social.image_for_day(day).startswith(b"\xff\xd8\xff")


def test_evergreen_publisher_skips_a_post_already_on_instagram(monkeypatch):
    day = date(2026, 9, 22)
    class FakeClient:
        def get_account(self):
            return {"username": "lecturesift"}

        def get_recent_media(self, limit=50):
            return {"data": [{"caption": "LectureSift · idea · 2026-09-22"}]}

        def create_media_container(self, **kwargs):
            raise AssertionError("existing post must not be republished")

    monkeypatch.setattr(social, "INSTAGRAM_DAILY_AUTOMATION_ENABLED", True)
    monkeypatch.setattr(social, "_client", FakeClient)
    monkeypatch.setattr(social, "_ensure_post", lambda _day: {"published_media_id": None})
    assert social.publish_evergreen_post(day)["status"] == "already_published"


def test_generated_copy_rejects_duplicate_and_empty_steps():
    card = {
        "title": "Find the one idea you missed",
        "body": "A precise gap gives you a much better starting point for review.",
        "title_tr": "Kaçırdığın tek fikri bul",
        "body_tr": "Net bir eksik, tekrara başlamak için daha iyi bir noktadır.",
        "keyword": "review lecture notes",
        "steps": ["Close your notes and explain the main idea aloud."] * 3,
    }
    import pytest
    with pytest.raises(ValueError):
        social._validate(card, [])
    card["steps"] = [
        "Close the notes and explain the main idea aloud.",
        "Mark the exact point where your explanation breaks.",
        "Open only that section and try explaining it again.",
    ]
    with pytest.raises(ValueError):
        social._validate(card, [card["title"]])
    card["title"] = "Research proves this study method"
    with pytest.raises(ValueError):
        social._validate(card, [])
