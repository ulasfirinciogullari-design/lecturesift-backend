from types import SimpleNamespace

from lecturesift import ai


def _pack(title: str) -> dict:
    return {
        "title": title,
        "summary": f"Summary for {title}",
        "key_points": [title],
        "important_terms": [],
        "notes": [],
        "exam_focus": [],
        "quiz": [],
        "flashcards": [],
    }


def test_short_study_pack_uses_the_complete_transcript(monkeypatch):
    calls = []

    def fake(source, *_args, **kwargs):
        calls.append((source, kwargs))
        return _pack("short")

    monkeypatch.setattr(ai, "_request_study_pack", fake)
    transcript = ("complete source " * 5_000) + "FINAL_SENTENCE_MARKER"
    assert len(transcript) < ai.LONG_TRANSCRIPT_THRESHOLD

    result = ai.make_study_pack(transcript, "en", "standard", 10, 20)

    assert result["title"] == "short"
    assert len(calls) == 1
    assert calls[0][0] == transcript
    assert calls[0][0].endswith("FINAL_SENTENCE_MARKER")


def test_long_study_pack_processes_every_section_before_final_synthesis(monkeypatch):
    calls = []

    def fake(source, *_args, **kwargs):
        label = kwargs.get("source_label", "TRANSCRIPT")
        calls.append((source, label, kwargs))
        return _pack(label)

    monkeypatch.setattr(ai, "_request_study_pack", fake)
    transcript = (
        "START_MARKER "
        + ("first topic detail " * 2_000)
        + " MIDDLE_MARKER "
        + ("later topic detail " * 3_500)
        + " END_MARKER"
    )
    assert len(transcript) > ai.LONG_TRANSCRIPT_THRESHOLD

    result = ai.make_study_pack(transcript, "en", "standard", 12, 24)

    section_calls = [call for call in calls if call[1].startswith("TRANSCRIPT SECTION")]
    final_calls = [call for call in calls if call[1] == "ORDERED SECTION DIGESTS"]
    assert len(section_calls) >= 4
    assert len(final_calls) == 1
    assert max(len(source) for source, *_ in section_calls) <= ai.STUDY_SECTION_CHARACTERS
    assert any("START_MARKER" in source for source, *_ in section_calls)
    assert any("MIDDLE_MARKER" in source for source, *_ in section_calls)
    assert any("END_MARKER" in source for source, *_ in section_calls)
    for index in range(1, len(section_calls) + 1):
        assert f"TRANSCRIPT SECTION {index} OF {len(section_calls)}" in final_calls[0][0]
    assert result["title"] == "ORDERED SECTION DIGESTS"


def test_source_code_does_not_silently_slice_long_transcripts():
    source = ai.__file__
    with open(source, encoding="utf-8") as stream:
        content = stream.read()
    assert "transcript[:100000]" not in content
    assert "transcript[:100_000]" not in content


def test_precise_transcription_requests_diarized_segments(monkeypatch, tmp_path):
    captured = {}

    class Transcriptions:
        @staticmethod
        def create(**kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                text="Opening explanation.",
                duration=12.5,
                segments=[
                    SimpleNamespace(start=0.4, end=4.8, speaker="A", text="Opening explanation."),
                ],
            )

    fake_client = SimpleNamespace(audio=SimpleNamespace(transcriptions=Transcriptions()))
    monkeypatch.setattr(ai, "_CLIENT", fake_client)
    audio = tmp_path / "sample.mp3"
    audio.write_bytes(b"audio")

    result = ai.transcribe_timed(audio, "en")

    assert captured["model"] == "gpt-4o-transcribe-diarize"
    assert captured["response_format"] == "diarized_json"
    assert captured["chunking_strategy"] == "auto"
    assert captured["language"] == "en"
    assert result["segments"][0] == {
        "start": 0.4,
        "end": 4.8,
        "speaker": "A",
        "text": "Opening explanation.",
        "precision": "provider_segment",
    }
