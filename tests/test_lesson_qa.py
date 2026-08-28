import json
from types import SimpleNamespace

from lecturesift import ai


def test_lesson_question_uses_ranked_context_and_filters_unknown_citations(monkeypatch):
    captured = {}

    def create(**kwargs):
        captured.update(kwargs)
        content = json.dumps(
            {
                "answer": "Mitoz iki yavru hücre oluşturur.",
                "source_ids": ["S2", "S999", "S2"],
                "insufficient": False,
            }
        )
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])

    fake = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    monkeypatch.setattr(ai, "_client", lambda: fake)
    result = {
        "summary": "Hücre bölünmesi",
        "transcript_segments": [
            {"timestamp": "00:00:10", "text": "Dersin giriş kısmı."},
            {"timestamp": "00:04:20", "speaker": "Öğretmen", "text": "Mitoz iki yavru hücre oluşturur."},
        ],
    }

    answer = ai.answer_lesson_question(result, "Mitozun sonucu nedir?", "tr")

    assert answer["answer"].startswith("Mitoz")
    assert answer["citations"] == [
        {
            "id": "S2",
            "timestamp": "00:04:20",
            "speaker": "Öğretmen",
            "excerpt": "Mitoz iki yavru hücre oluşturur.",
        }
    ]
    assert "S999" not in captured["messages"][1]["content"]


def test_lesson_question_rejects_invalid_length():
    try:
        ai.answer_lesson_question({}, "?", "tr")
    except Exception as exc:
        assert getattr(exc, "code", "") == "LS-AI-06"
    else:
        raise AssertionError("Short questions must be rejected")
