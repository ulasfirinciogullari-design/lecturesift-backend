import threading
import time
import uuid

import lecturesift.pipeline as pipeline
from lecturesift.exports import _timestamped_transcript
from lecturesift.jobs import JOBS


def _job(tmp_path):
    job_id = f"timeline-{uuid.uuid4()}"
    JOBS.create(job_id, tmp_path, {"output_language": "en"})
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    chunk = tmp_path / "audio_001_000.mp3"
    chunk.write_bytes(b"audio")
    return job_id, source, chunk


def test_default_transcript_timeline_uses_clearly_marked_chunk_estimates(tmp_path, monkeypatch):
    job_id, source, chunk = _job(tmp_path)
    monkeypatch.setattr(pipeline, "PRECISE_TRANSCRIPT_TIMESTAMPS", False)
    monkeypatch.setattr(pipeline, "has_audio_stream", lambda _path: True)
    monkeypatch.setattr(pipeline, "extract_audio_chunks", lambda *_args, **_kwargs: [chunk])
    monkeypatch.setattr(pipeline, "_source_duration_seconds", lambda _paths: 125.0)
    monkeypatch.setattr(pipeline, "transcribe", lambda *_args: "Complete chunk transcript.")

    original, translated, segments, mode = pipeline._audio_pipeline(
        job_id,
        [source],
        tmp_path,
        {"source_language": "en", "output_language": "en", "translate_transcript": False},
    )

    assert original == "Complete chunk transcript."
    assert translated == ""
    assert mode == "chunk_estimate"
    assert segments == [{
        "start": 0.0,
        "end": 125.0,
        "timestamp": "00:00:00",
        "speaker": None,
        "text": "Complete chunk transcript.",
        "precision": "chunk_estimate",
    }]


def test_precise_transcript_segments_are_offset_on_the_full_timeline(tmp_path, monkeypatch):
    job_id, source, chunk = _job(tmp_path)
    monkeypatch.setattr(pipeline, "PRECISE_TRANSCRIPT_TIMESTAMPS", True)
    monkeypatch.setattr(pipeline, "has_audio_stream", lambda _path: True)
    monkeypatch.setattr(pipeline, "extract_audio_chunks", lambda *_args, **_kwargs: [chunk])
    monkeypatch.setattr(pipeline, "_source_duration_seconds", lambda _paths: 125.0)
    monkeypatch.setattr(
        pipeline,
        "transcribe_timed",
        lambda *_args: {
            "text": "Timed transcript.",
            "segments": [{"start": 2.5, "end": 8.0, "speaker": "A", "text": "Timed transcript.", "precision": "provider_segment"}],
        },
    )

    original, _, segments, mode = pipeline._audio_pipeline(
        job_id,
        [source],
        tmp_path,
        {"source_language": "en", "output_language": "en", "translate_transcript": False},
    )

    assert original == "Timed transcript."
    assert mode == "provider_segments"
    assert segments[0]["start"] == 2.5
    assert segments[0]["end"] == 8.0
    assert segments[0]["timestamp"] == "00:00:02"


def test_exported_original_transcript_includes_timestamps_and_speakers():
    text = _timestamped_transcript({
        "transcript_original": "Plain fallback",
        "transcript_segments": [
            {"timestamp": "01:02:03", "speaker": "A", "text": "Exam detail."},
            {"timestamp": "01:02:10", "speaker": None, "text": "Definition."},
        ],
    })
    assert "[01:02:03] A  Exam detail." in text
    assert "[01:02:10]  Definition." in text


def test_audio_chunks_transcribe_concurrently_but_keep_timeline_order(tmp_path, monkeypatch):
    job_id, source, first = _job(tmp_path)
    second = tmp_path / "audio_001_001.mp3"
    second.write_bytes(b"audio-two")
    gate = threading.Barrier(2, timeout=3)
    monkeypatch.setattr(pipeline, "PRECISE_TRANSCRIPT_TIMESTAMPS", False)
    monkeypatch.setattr(pipeline, "TRANSCRIPTION_PARALLELISM", 2)
    monkeypatch.setattr(pipeline, "has_audio_stream", lambda _path: True)
    monkeypatch.setattr(pipeline, "extract_audio_chunks", lambda *_args, **_kwargs: [first, second])
    monkeypatch.setattr(
        pipeline,
        "_source_duration_seconds",
        lambda paths: 60.0 if paths[0].name.endswith("000.mp3") else 90.0,
    )

    def fake_transcribe(path, _language):
        gate.wait()
        return "First chunk" if path == first else "Second chunk"

    monkeypatch.setattr(pipeline, "transcribe", fake_transcribe)
    original, _, segments, _ = pipeline._audio_pipeline(
        job_id,
        [source],
        tmp_path,
        {"source_language": "en", "output_language": "en", "translate_transcript": False},
    )

    assert original == "First chunk\n\nSecond chunk"
    assert [item["start"] for item in segments] == [0.0, 60.0]
    assert [item["end"] for item in segments] == [60.0, 150.0]


def test_study_pack_and_transcript_translation_run_concurrently(monkeypatch):
    gate = threading.Barrier(2, timeout=3)
    completed: list[str] = []

    def fake_study(*_args):
        gate.wait()
        time.sleep(0.02)
        completed.append("study")
        return {"title": "Parallel pack"}

    def fake_translation(*_args):
        gate.wait()
        time.sleep(0.02)
        completed.append("translation")
        return "Translated transcript"

    monkeypatch.setattr(pipeline, "make_study_pack", fake_study)
    monkeypatch.setattr(pipeline, "translate_transcript", fake_translation)
    pack, translated = pipeline._build_text_outputs(
        "parallel-job",
        "Original transcript",
        {
            "output_language": "tr",
            "summary_style": "standard",
            "quiz_count": 10,
            "flashcard_count": 20,
            "translate_transcript": True,
        },
    )

    assert pack["title"] == "Parallel pack"
    assert translated == "Translated transcript"
    assert sorted(completed) == ["study", "translation"]
