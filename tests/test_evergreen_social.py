from datetime import date

import lecturesift.evergreen_social as social


def test_evergreen_card_is_persisted_and_reused(monkeypatch):
    objects = {}
    monkeypatch.setattr(social, "read_json", lambda key: objects.get(key))
    monkeypatch.setattr(social, "write_json", lambda key, value: objects.__setitem__(key, value))
    monkeypatch.setattr(social, "_recent_titles", lambda: [])
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


def test_prepare_command_never_publishes(monkeypatch):
    monkeypatch.setattr(social.sys, "argv", ["evergreen_social", "prepare", "2026-09-23"])
    monkeypatch.setattr(social, "_ensure_post", lambda day: {"title": "Prepared"})
    monkeypatch.setattr(social, "publish_evergreen_post", lambda: (_ for _ in ()).throw(AssertionError("must not publish")))
    assert social.main() == 0


def test_generated_card_requires_an_example_and_memory_check():
    import pytest

    card = {
        "title": "Map each note to a question",
        "body": "A question gives a scattered lecture note a useful place to live.",
        "title_tr": "Her notu bir soruya bağla",
        "body_tr": "Bir soru, dağınık ders notuna anlamlı bir yer verir.",
        "keyword": "organize lecture notes",
        "steps": [
            "Write one question above each section of your notes.",
            "For example, put your ATP definition under 'Why do cells need ATP?'",
            "Hide your notes and answer each question aloud before checking.",
        ],
    }
    assert social._validate(card, [])["title"] == card["title"]
    with pytest.raises(ValueError, match="example"):
        social._validate({**card, "steps": [card["steps"][0], "Use different colours for each section.", card["steps"][2]]}, [])
    with pytest.raises(ValueError, match="closed-note"):
        social._validate({**card, "steps": [*card["steps"][:2], "Read the questions again and underline important terms."]}, [])


def test_generation_revises_a_card_rejected_for_long_steps(monkeypatch):
    from types import SimpleNamespace

    calls = []

    def create(**kwargs):
        calls.append(list(kwargs["messages"]))
        return SimpleNamespace(choices=[SimpleNamespace(
            finish_reason="stop", message=SimpleNamespace(refusal=None, content="{}"),
        )])

    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))

    def validate(data, recent_titles):
        if len(calls) == 1:
            raise ValueError("Generated study steps are too short or too long")
        return {"title": "Revised"}

    monkeypatch.setattr(social, "OPENAI_API_KEY", "available")
    monkeypatch.setattr(social, "OpenAI", FakeOpenAI)
    monkeypatch.setattr(social, "_validate", validate)
    assert social._generate(date(2026, 9, 24), []) == {"title": "Revised"}
    assert len(calls) == 2
    assert "too short or too long" in calls[1][-1]["content"]
