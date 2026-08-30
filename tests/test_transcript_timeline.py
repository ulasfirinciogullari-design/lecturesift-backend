import threading
import time
import uuid

import lecturesift.duration as duration
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
    monkeypatch.setattr(pipeline, "has_audio_stream", lambda _path: True)

    def fake_extract(*_args, **kwargs):
        assert kwargs["segment_seconds"] == pipeline.FAST_TRANSCRIPTION_CHUNK_SECONDS
        return [chunk]

    monkeypatch.setattr(pipeline, "extract_audio_chunks", fake_extract)
    monkeypatch.setattr(pipeline, "media_duration_values", lambda _paths: [125.0])
    monkeypatch.setattr(pipeline, "transcribe", lambda *_args: "Complete chunk transcript.")

    original, translated, segments, mode = pipeline._audio_pipeline(
        job_id,
        [source],
        tmp_path,
        {
            "source_language": "en",
            "output_language": "en",
            "translate_transcript": False,
            "transcript_timestamps": False,
            "speaker_detection": False,
        },
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
        "chunk_index": 0,
        "chunk_id": "chunk_0001",
    }]


def test_precise_transcript_segments_are_offset_on_the_full_timeline(tmp_path, monkeypatch):
    job_id, source, chunk = _job(tmp_path)
    monkeypatch.setattr(pipeline, "has_audio_stream", lambda _path: True)

    def fake_extract(*_args, **kwargs):
        assert kwargs["segment_seconds"] == pipeline.PROVIDER_TRANSCRIPTION_CHUNK_SECONDS
        return [chunk]

    monkeypatch.setattr(pipeline, "extract_audio_chunks", fake_extract)
    monkeypatch.setattr(pipeline, "media_duration_values", lambda _paths: [125.0])
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
        {
            "source_language": "en",
            "output_language": "en",
            "translate_transcript": False,
            "transcript_timestamps": True,
            "speaker_detection": False,
        },
    )

    assert original == "Timed transcript."
    assert mode == "provider_segments"
    assert segments[0]["start"] == 2.5
    assert segments[0]["end"] == 8.0
    assert segments[0]["timestamp"] == "00:00:02"
    assert segments[0]["speaker"] is None
    assert "speaker_id" not in segments[0]
    assert "speaker_label" not in segments[0]


def test_speaker_detection_namespaces_provider_labels_per_audio_chunk(tmp_path, monkeypatch):
    job_id, source, first = _job(tmp_path)
    second = tmp_path / "audio_001_001.mp3"
    second.write_bytes(b"audio-two")
    monkeypatch.setattr(pipeline, "TRANSCRIPTION_PARALLELISM", 2)
    monkeypatch.setattr(pipeline, "has_audio_stream", lambda _path: True)
    def fake_extract(*_args, **kwargs):
        assert kwargs["segment_seconds"] == pipeline.PROVIDER_TRANSCRIPTION_CHUNK_SECONDS
        return [first, second]

    monkeypatch.setattr(pipeline, "extract_audio_chunks", fake_extract)
    monkeypatch.setattr(pipeline, "media_duration_values", lambda _paths: [60.0, 90.0])

    def fake_timed(path, _language, _duration=None):
        return {
            "text": "First voice" if path == first else "Second voice",
            "segments": [{
                "start": 1.0,
                "end": 3.0,
                "speaker": "A",
                "text": "First voice" if path == first else "Second voice",
                "precision": "provider_segment",
            }],
        }

    monkeypatch.setattr(pipeline, "transcribe_timed", fake_timed)
    original, _, segments, mode = pipeline._audio_pipeline(
        job_id,
        [source],
        tmp_path,
        {
            "source_language": "en",
            "output_language": "en",
            "translate_transcript": False,
            "transcript_timestamps": True,
            "speaker_detection": True,
        },
    )

    assert original == "First voice\n\nSecond voice"
    assert mode == "speaker_segments"
    assert [item["speaker_id"] for item in segments] == [
        "chunk_0001:a",
        "chunk_0002:a",
    ]
    assert [item["speaker"] for item in segments] == [
        "A [chunk_0001]",
        "A [chunk_0002]",
    ]
    metadata = pipeline._transcript_speaker_metadata(segments, mode)
    assert metadata["identity_scope"] == "audio_chunk"
    assert metadata["cross_chunk_identity_assumed"] is False
    assert len(metadata["speakers"]) == 2


def test_speaker_detection_marks_missing_provider_label_uncertain(tmp_path, monkeypatch):
    job_id, source, chunk = _job(tmp_path)
    monkeypatch.setattr(pipeline, "has_audio_stream", lambda _path: True)
    monkeypatch.setattr(pipeline, "extract_audio_chunks", lambda *_args, **_kwargs: [chunk])
    monkeypatch.setattr(pipeline, "media_duration_values", lambda _paths: [30.0])
    monkeypatch.setattr(
        pipeline,
        "transcribe_timed",
        lambda *_args: {
            "text": "Unlabeled voice",
            "segments": [{"start": 0.0, "end": 2.0, "speaker": None, "text": "Unlabeled voice"}],
        },
    )

    original, _, segments, mode = pipeline._audio_pipeline(
        job_id,
        [source],
        tmp_path,
        {
            "source_language": "en",
            "output_language": "en",
            "translate_transcript": False,
            "transcript_timestamps": True,
            "speaker_detection": True,
        },
    )

    assert original == "Unlabeled voice"
    assert segments[0]["speaker"] is None
    assert segments[0]["speaker_id"] is None
    assert segments[0]["speaker_uncertain"] is True
    metadata = pipeline._transcript_speaker_metadata(segments, mode)
    assert metadata["uncertain_segment_count"] == 1
    assert metadata["all_segments_labeled"] is False


def test_duration_value_probes_run_concurrently_and_preserve_path_order(tmp_path, monkeypatch):
    first = tmp_path / "first.mp3"
    second = tmp_path / "second.mp3"
    gate = threading.Barrier(2, timeout=3)
    monkeypatch.setattr(duration, "DURATION_PROBE_PARALLELISM", 2)

    def probe(path):
        gate.wait()
        return 12.5 if path == first else 7.25

    monkeypatch.setattr(duration, "file_duration_seconds", probe)
    assert duration.media_duration_values([first, second]) == [12.5, 7.25]


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
    monkeypatch.setattr(pipeline, "TRANSCRIPTION_PARALLELISM", 2)
    monkeypatch.setattr(pipeline, "has_audio_stream", lambda _path: True)
    monkeypatch.setattr(pipeline, "extract_audio_chunks", lambda *_args, **_kwargs: [first, second])
    monkeypatch.setattr(pipeline, "media_duration_values", lambda _paths: [60.0, 90.0])

    def fake_transcribe(path, _language, _duration=None):
        gate.wait()
        return "First chunk" if path == first else "Second chunk"

    monkeypatch.setattr(pipeline, "transcribe", fake_transcribe)
    original, _, segments, _ = pipeline._audio_pipeline(
        job_id,
        [source],
        tmp_path,
        {
            "source_language": "en",
            "output_language": "en",
            "translate_transcript": False,
            "transcript_timestamps": False,
            "speaker_detection": False,
        },
    )

    assert original == "First chunk\n\nSecond chunk"
    assert [item["start"] for item in segments] == [0.0, 60.0]
    assert [item["end"] for item in segments] == [60.0, 150.0]


def test_multiple_media_sources_prepare_audio_concurrently_and_keep_source_order(tmp_path, monkeypatch):
    job_id, first_source, first_chunk = _job(tmp_path)
    second_source = tmp_path / "source-two.mp4"
    second_source.write_bytes(b"source-two")
    second_chunk = tmp_path / "audio_002_000.mp3"
    second_chunk.write_bytes(b"audio-two")
    gate = threading.Barrier(2, timeout=3)

    monkeypatch.setattr(pipeline, "MEDIA_PREP_PARALLELISM", 2)
    monkeypatch.setattr(pipeline, "has_audio_stream", lambda _path: True)

    def fake_extract(path, _job_dir, prefix, segment_seconds):
        gate.wait()
        assert prefix in {"audio_001", "audio_002"}
        assert segment_seconds == pipeline.FAST_TRANSCRIPTION_CHUNK_SECONDS
        return [first_chunk if path == first_source else second_chunk]

    monkeypatch.setattr(pipeline, "extract_audio_chunks", fake_extract)
    monkeypatch.setattr(pipeline, "media_duration_values", lambda _paths: [40.0, 50.0])
    monkeypatch.setattr(
        pipeline,
        "transcribe",
        lambda path, _language, _duration=None: "First source" if path == first_chunk else "Second source",
    )

    original, _, segments, _ = pipeline._audio_pipeline(
        job_id,
        [first_source, second_source],
        tmp_path,
        {
            "source_language": "en",
            "output_language": "en",
            "translate_transcript": False,
            "transcript_timestamps": False,
            "speaker_detection": False,
        },
    )

    assert original == "First source\n\nSecond source"
    assert [item["start"] for item in segments] == [0.0, 40.0]
    assert [item["end"] for item in segments] == [40.0, 90.0]


def test_study_pack_and_transcript_translation_run_concurrently(monkeypatch):
    gate = threading.Barrier(2, timeout=3)
    completed: list[str] = []
    progress: list[int] = []

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
    monkeypatch.setattr(
        pipeline,
        "JOBS",
        type("ProgressRecorder", (), {"update": staticmethod(lambda _job_id, **values: progress.append(values["percent"]))})(),
    )
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
    assert progress == [80, 89]


def test_explicit_same_language_skips_redundant_full_translation(monkeypatch):
    calls = []
    monkeypatch.setattr(pipeline, "make_study_pack", lambda *_args: {"title": "Same-language pack"})
    monkeypatch.setattr(
        pipeline,
        "translate_transcript",
        lambda *_args: calls.append("translation") or "should not run",
    )

    pack, translated = pipeline._build_text_outputs(
        "same-language-job",
        "Original English transcript",
        {
            "source_language": "en-US",
            "output_language": "en",
            "summary_style": "standard",
            "quiz_count": 10,
            "flashcard_count": 20,
            "translate_transcript": True,
        },
    )

    assert pack["title"] == "Same-language pack"
    assert translated == ""
    assert calls == []
