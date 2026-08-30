from pathlib import Path
from types import SimpleNamespace

import lecturesift.ai as ai
import lecturesift.media as media
import lecturesift.pipeline as pipeline
import lecturesift.tasks as tasks
from lecturesift.app import _public_job


def _pack(title: str = "Pack") -> dict:
    return {
        "title": title,
        "summary": "Short source label.",
        "key_points": [],
        "important_terms": [],
        "notes": [],
        "exam_focus": [],
        "quiz": [],
        "flashcards": [],
    }


def test_worker_duration_options_are_private_and_do_not_mutate_input():
    original = {"output_language": "tr", "billing_user_id": "private-user"}

    measured = tasks._pipeline_options_with_durations(original, 91.25, 88.5)

    assert original == {"output_language": "tr", "billing_user_id": "private-user"}
    assert measured["_measured_audio_duration_seconds"] == 91.25
    assert measured["_measured_visual_duration_seconds"] == 88.5
    assert measured["_measured_duration_seconds"] == 91.25
    assert pipeline._public_options(measured) == {"output_language": "tr"}
    assert _public_job({"options": measured})["options"] == {"output_language": "tr"}


def test_billing_reuses_worker_duration_without_another_probe(monkeypatch):
    recorded = []
    monkeypatch.setattr(
        pipeline,
        "_source_duration_seconds",
        lambda _paths: (_ for _ in ()).throw(AssertionError("duration must not be probed twice")),
    )
    monkeypatch.setattr(
        pipeline,
        "record_usage",
        lambda user_id, job_id, seconds: recorded.append((user_id, job_id, seconds)),
    )

    pipeline._record_billing_usage(
        "job-fast",
        {"billing_user_id": "user-fast", "_measured_audio_duration_seconds": 125.5},
        [Path("source.mp4")],
    )

    assert recorded == [("user-fast", "job-fast", 125.5)]


def test_transcription_cost_reuses_chunk_duration_without_another_probe(monkeypatch):
    monkeypatch.setattr(
        ai,
        "media_duration_seconds",
        lambda _paths: (_ for _ in ()).throw(AssertionError("chunk duration must be reused")),
    )

    assert ai._transcription_cost_duration(Path("audio.mp3"), 42.75) == 42.75


def test_billing_uses_same_longest_source_duration_as_worker_quota(monkeypatch):
    recorded = []
    monkeypatch.setattr(
        pipeline,
        "_source_duration_seconds",
        lambda _paths: (_ for _ in ()).throw(AssertionError("duration must not be probed twice")),
    )
    monkeypatch.setattr(
        pipeline,
        "record_usage",
        lambda user_id, job_id, seconds: recorded.append((user_id, job_id, seconds)),
    )
    options = tasks._pipeline_options_with_durations(
        {"billing_user_id": "user-visual"},
        audio_duration=60.0,
        visual_duration=180.0,
    )

    pipeline._record_billing_usage("job-visual", options, [Path("source.mp4")])

    assert recorded == [("user-visual", "job-visual", 180.0)]


def test_single_audio_chunk_reuses_worker_duration_for_timeline(tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"
    chunk = tmp_path / "audio_001.mp3"
    source.write_bytes(b"video")
    chunk.write_bytes(b"audio")
    monkeypatch.setattr(pipeline, "has_audio_stream", lambda _path: True)
    monkeypatch.setattr(pipeline, "extract_audio_chunks", lambda *_args, **_kwargs: [chunk])
    monkeypatch.setattr(
        pipeline,
        "media_duration_values",
        lambda _paths: (_ for _ in ()).throw(AssertionError("single chunk must reuse worker duration")),
    )
    monkeypatch.setattr(pipeline, "transcribe", lambda *_args: "Fast transcript")
    monkeypatch.setattr(
        pipeline,
        "JOBS",
        type("TaskProgress", (), {"update_task": staticmethod(lambda *_args, **_kwargs: None)})(),
    )

    original, translated, segments, mode = pipeline._audio_pipeline(
        "job-fast",
        [source],
        tmp_path,
        {
            "source_language": "auto",
            "output_language": "tr",
            "translate_transcript": False,
            "transcript_timestamps": False,
            "speaker_detection": False,
            "_measured_audio_duration_seconds": 75.25,
        },
    )

    assert original == "Fast transcript"
    assert translated == ""
    assert mode == "chunk_estimate"
    assert segments[0]["end"] == 75.25


def test_remote_download_format_matches_requested_work():
    assert media._remote_download_format("download_video", False) == "bv*+ba/b"
    assert media._remote_download_format("audio_export", True) == "bestaudio/best"
    assert media._remote_download_format("study_pack", False) == "bestaudio/best"
    assert "height<=720" in media._remote_download_format("study_pack", True)


def test_direct_media_url_keeps_existing_download_path(tmp_path, monkeypatch):
    expected = tmp_path / "remote.mp4"
    calls = []

    def direct(url: str, job_dir: Path) -> Path:
        calls.append((url, job_dir))
        return expected

    monkeypatch.setattr(media, "_download_direct_media", direct)

    result = media.download_remote_video(
        "https://cdn.example.com/lecture.mp4",
        tmp_path,
        job_type="audio_export",
        include_slides=False,
    )

    assert result == expected
    assert calls == [("https://cdn.example.com/lecture.mp4", tmp_path)]


def test_ffmpeg_text_output_is_decoded_portably_on_windows(monkeypatch):
    captured = {}

    def run(_command, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(media.subprocess, "run", run)

    media.run_command(["ffmpeg", "-version"])

    assert captured["encoding"] == "utf-8"
    assert captured["errors"] == "replace"


def test_empty_speaker_timeline_is_not_reported_as_fully_labeled():
    metadata = pipeline._transcript_speaker_metadata([], pipeline.TRANSCRIPT_MODE_SPEAKER)

    assert metadata["enabled"] is True
    assert metadata["all_segments_labeled"] is False


def test_quiz_card_only_short_job_uses_one_exercise_focused_request(monkeypatch):
    calls = []

    def request(source, language, style, quiz_count, card_count, **kwargs):
        calls.append((source, language, style, quiz_count, card_count, kwargs))
        result = _pack("Exercises")
        result["quiz"] = [{"question": "Q"}] * quiz_count
        result["flashcards"] = [{"front": "F?", "back": "B"}] * card_count
        return result

    monkeypatch.setattr(ai, "_request_study_pack", request)

    result = ai.make_study_pack(
        "A complete but short lecture transcript.",
        "en",
        "standard",
        4,
        6,
        False,
    )

    assert len(calls) == 1
    assert calls[0][2:5] == ("short", 4, 6)
    assert calls[0][5]["minimum_summary_words"] == 0
    assert len(result["quiz"]) == 4
    assert len(result["flashcards"]) == 6


def test_quiz_card_only_long_job_does_not_generate_section_exercises(monkeypatch):
    calls = []
    monkeypatch.setattr(ai, "LONG_TRANSCRIPT_THRESHOLD", 40)
    monkeypatch.setattr(ai, "STUDY_SECTION_CHARACTERS", 30)
    monkeypatch.setattr(ai, "STUDY_PACK_PARALLELISM", 1)

    def request(source, language, style, quiz_count, card_count, **kwargs):
        calls.append((kwargs.get("source_label", "TRANSCRIPT"), quiz_count, card_count))
        result = _pack(str(kwargs.get("source_label") or "Exercises"))
        result["quiz"] = [{"question": "Q"}] * quiz_count
        result["flashcards"] = [{"front": "F?", "back": "B"}] * card_count
        return result

    monkeypatch.setattr(ai, "_request_study_pack", request)

    result = ai.make_study_pack("Lecture sentence. " * 12, "en", "standard", 5, 7, False)

    section_calls = [item for item in calls if item[0].startswith("TRANSCRIPT SECTION")]
    assert len(section_calls) > 1
    assert all((quiz, cards) == (0, 0) for _label, quiz, cards in section_calls)
    assert calls[-1][1:] == (5, 7)
    assert len(result["quiz"]) == 5
    assert len(result["flashcards"]) == 7
