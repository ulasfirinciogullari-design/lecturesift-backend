from __future__ import annotations

import json
import subprocess
import uuid
import zipfile
from pathlib import Path

import pytest

from lecturesift import media, pipeline
from lecturesift.exports import build_artifacts
from lecturesift.jobs import JOBS
from lecturesift.pipeline_enhancements import install_pipeline_enhancements


def _result() -> dict:
    return {
        "title": "Sesli Ders",
        "summary": "Ders ozeti.",
        "transcript_original": "Ders kaydi.",
        "transcript_translated": "",
        "slides": [],
        "quiz": [],
        "flashcards": [],
        "options": {
            "output_formats": [],
            "include_summary": True,
            "include_transcript": True,
            "include_slides": False,
        },
    }


def test_audio_is_an_artifact_and_zip_member_even_without_document_formats(tmp_path: Path):
    derived = tmp_path / "derived" / "normalized.mp3"
    derived.parent.mkdir()
    derived.write_bytes(b"ID3\x04study-audio")

    artifacts, zip_path = build_artifacts(
        tmp_path,
        _result(),
        tmp_path / "slides",
        audio_source=derived,
    )

    assert artifacts == [
        {
            "file": "LectureSift_Ders_Sesi.mp3",
            "label": "Ders Sesi (MP3)",
            "format": "MP3",
            "size_bytes": len(b"ID3\x04study-audio"),
        }
    ]
    assert (tmp_path / "package" / "LectureSift_Ders_Sesi.mp3").read_bytes() == b"ID3\x04study-audio"
    with zipfile.ZipFile(zip_path) as archive:
        assert archive.namelist() == ["LectureSift_Ders_Sesi.mp3"]
        assert archive.read("LectureSift_Ders_Sesi.mp3") == b"ID3\x04study-audio"
    persisted = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))
    assert persisted["artifacts"] == artifacts


def test_audio_export_rejects_files_outside_the_owning_job(tmp_path: Path):
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    outside = tmp_path / "private.mp3"
    outside.write_bytes(b"not-this-job")

    with pytest.raises(ValueError, match="escaped"):
        build_artifacts(
            job_dir,
            _result(),
            job_dir / "slides",
            audio_source=outside,
        )

    assert not (job_dir / "package" / "LectureSift_Ders_Sesi.mp3").exists()
    assert not list(job_dir.glob("*.zip"))


def test_rollout_archive_wrapper_forwards_audio_without_rebuilding_zip(tmp_path: Path):
    install_pipeline_enhancements()
    audio = tmp_path / "generated" / "lesson.mp3"
    audio.parent.mkdir()
    audio.write_bytes(b"ID3wrapper")

    artifacts, zip_path = pipeline.build_artifacts(
        tmp_path,
        _result(),
        tmp_path / "slides",
        audio_source=audio,
    )

    assert zip_path.name == "LectureSift_Study_Pack.zip"
    assert any(item["file"] == "LectureSift_Ders_Sesi.mp3" for item in artifacts)
    with zipfile.ZipFile(zip_path) as archive:
        assert "LectureSift_Ders_Sesi.mp3" in archive.namelist()


def test_media_pipeline_passes_generated_audio_to_package_and_cleans_workspace(
    tmp_path: Path,
    monkeypatch,
):
    source = tmp_path / "lecture.wav"
    source.write_bytes(b"source-audio")
    generated_dir = tmp_path / "study_audio_export"
    captured: dict = {}

    monkeypatch.setattr(
        pipeline,
        "_audio_pipeline",
        lambda *_args, **_kwargs: ("Transcript", "", [], "chunk_estimate"),
    )
    monkeypatch.setattr(
        pipeline,
        "_build_text_outputs",
        lambda *_args, **_kwargs: (
            {"title": "Audio", "summary": "Summary", "quiz": [], "flashcards": []},
            "",
        ),
    )

    def prepare(paths: list[Path], job_dir: Path) -> Path:
        assert paths == [source]
        output_dir = job_dir / "study_audio_export"
        output_dir.mkdir()
        output = output_dir / "LectureSift_Ders_Sesi.mp3"
        output.write_bytes(b"generated-mp3")
        return output

    def package(job_dir: Path, result: dict, _slides: Path, *, audio_source: Path | None = None):
        captured["source"] = audio_source
        captured["audio_bytes"] = audio_source.read_bytes()
        captured["result"] = result
        archive = job_dir / "study.zip"
        archive.write_bytes(b"zip")
        return [{"file": "LectureSift_Ders_Sesi.mp3"}], archive

    monkeypatch.setattr(pipeline, "_prepare_study_audio", prepare)
    monkeypatch.setattr(pipeline, "build_artifacts", package)
    options = {
        "source_language": "tr",
        "output_language": "tr",
        "summary_style": "short",
        "quiz_count": 0,
        "flashcard_count": 0,
        "translate_transcript": False,
        "include_slides": False,
        "_measured_audio_duration_seconds": 1,
    }
    job_id = f"zip-audio-{uuid.uuid4()}"
    JOBS.create(job_id, tmp_path, options)

    pipeline.process_job(job_id, source, options)

    assert JOBS.get(job_id)["status"] == "done"
    assert captured["source"].name == "LectureSift_Ders_Sesi.mp3"
    assert captured["audio_bytes"] == b"generated-mp3"
    assert captured["result"]["options"]["summary_style"] == "detailed"
    assert not generated_dir.exists()


def test_audio_probe_failure_is_not_misclassified_as_a_silent_video(tmp_path: Path, monkeypatch):
    source = tmp_path / "unreadable.mp4"
    source.write_bytes(b"media")
    monkeypatch.setattr(
        media.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=["ffprobe"],
            returncode=1,
            stdout="",
            stderr="temporary probe failure",
        ),
    )

    with pytest.raises(RuntimeError, match="ffprobe could not inspect"):
        media.has_audio_stream(source)


def test_silent_media_keeps_study_pack_valid_without_audio_artifact(tmp_path: Path, monkeypatch):
    source = tmp_path / "silent.mp4"
    source.write_bytes(b"silent-video")
    captured = {}
    monkeypatch.setattr(
        pipeline,
        "_audio_pipeline",
        lambda *_args, **_kwargs: ("", "", [], "none"),
    )
    monkeypatch.setattr(
        pipeline,
        "_build_text_outputs",
        lambda *_args, **_kwargs: (
            {"title": "Silent", "summary": "", "quiz": [], "flashcards": []},
            "",
        ),
    )
    monkeypatch.setattr(pipeline, "_prepare_study_audio", lambda *_args, **_kwargs: None)

    def package(job_dir: Path, _result: dict, _slides: Path, *, audio_source=None):
        captured["audio_source"] = audio_source
        archive = job_dir / "silent.zip"
        archive.write_bytes(b"zip")
        return [], archive

    monkeypatch.setattr(pipeline, "build_artifacts", package)
    options = {
        "source_language": "tr",
        "output_language": "tr",
        "summary_style": "detailed",
        "quiz_count": 0,
        "flashcard_count": 0,
        "translate_transcript": False,
        "include_slides": False,
        "_measured_audio_duration_seconds": 1,
    }
    job_id = f"zip-silent-{uuid.uuid4()}"
    JOBS.create(job_id, tmp_path, options)

    pipeline.process_job(job_id, source, options)

    assert JOBS.get(job_id)["status"] == "done"
    assert captured["audio_source"] is None


def test_audio_conversion_workspace_is_removed_when_packaging_fails(tmp_path: Path, monkeypatch):
    source = tmp_path / "lecture.wav"
    source.write_bytes(b"source")
    generated_dir = tmp_path / "study_audio_export"
    monkeypatch.setattr(
        pipeline,
        "_audio_pipeline",
        lambda *_args, **_kwargs: ("Transcript", "", [], "chunk_estimate"),
    )
    monkeypatch.setattr(
        pipeline,
        "_build_text_outputs",
        lambda *_args, **_kwargs: (
            {"title": "Audio", "summary": "Summary", "quiz": [], "flashcards": []},
            "",
        ),
    )

    def prepare(_paths: list[Path], job_dir: Path) -> Path:
        output_dir = job_dir / "study_audio_export"
        output_dir.mkdir()
        output = output_dir / "LectureSift_Ders_Sesi.mp3"
        output.write_bytes(b"generated")
        return output

    monkeypatch.setattr(pipeline, "_prepare_study_audio", prepare)
    monkeypatch.setattr(
        pipeline,
        "build_artifacts",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("archive failed")),
    )
    options = {
        "source_language": "tr",
        "output_language": "tr",
        "summary_style": "detailed",
        "quiz_count": 0,
        "flashcard_count": 0,
        "translate_transcript": False,
        "include_slides": False,
        "_measured_audio_duration_seconds": 1,
    }
    job_id = f"zip-cleanup-{uuid.uuid4()}"
    JOBS.create(job_id, tmp_path, options)

    pipeline.process_job(job_id, source, options)

    assert JOBS.get(job_id)["status"] == "error"
    assert not generated_dir.exists()
