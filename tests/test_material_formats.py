from __future__ import annotations

import shutil
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import lecturesift.pipeline as pipeline
from lecturesift import config
from lecturesift.billing_service import (
    BillingError,
    _subscription_usage_period_start,
    register_user,
    require_duration_entitlement,
    verify_email,
)
from lecturesift.jobs import JOBS
from lecturesift.media import extract_audio_chunks, has_audio_stream


EXPECTED_AUDIO_EXTENSIONS = {
    ".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".oga", ".opus",
    ".wma", ".aiff", ".aif", ".mka",
}
EXPECTED_VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".mpeg", ".mpg", ".m4v"}


def test_public_material_contract_covers_every_advertised_media_format():
    assert config.AUDIO_EXTENSIONS == EXPECTED_AUDIO_EXTENSIONS
    assert config.VIDEO_EXTENSIONS == EXPECTED_VIDEO_EXTENSIONS
    assert config.MEDIA_EXTENSIONS == EXPECTED_AUDIO_EXTENSIONS | EXPECTED_VIDEO_EXTENSIONS


@pytest.mark.parametrize(
    ("suffix", "codec"),
    [
        (".mp3", "libmp3lame"),
        (".wav", "pcm_s16le"),
        (".m4a", "aac"),
        (".aac", "aac"),
        (".flac", "flac"),
        (".ogg", "libvorbis"),
        (".oga", "libvorbis"),
        (".opus", "libopus"),
        (".wma", "wmav2"),
        (".aiff", "pcm_s16be"),
        (".aif", "pcm_s16be"),
        (".mka", "flac"),
    ],
)
def test_every_audio_format_is_decoded_and_normalized_by_ffmpeg(
    tmp_path: Path,
    suffix: str,
    codec: str,
):
    assert shutil.which("ffmpeg"), "FFmpeg must be installed; format tests may not be skipped"
    source = tmp_path / f"lecture{suffix}"
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "sine=frequency=660:duration=0.8",
            "-c:a", codec, str(source),
        ],
        check=True,
    )
    assert source.is_file() and source.stat().st_size > 0
    assert has_audio_stream(source) is True

    normalized = tmp_path / "normalized"
    normalized.mkdir()
    chunks = extract_audio_chunks(source, normalized, prefix=suffix.lstrip("."))
    assert chunks
    assert all(path.suffix == ".mp3" and path.stat().st_size > 0 for path in chunks)


@pytest.mark.parametrize(
    ("suffix", "video_codec", "audio_codec"),
    [
        (".mp4", "mpeg4", "aac"),
        (".mov", "mpeg4", "aac"),
        (".mkv", "mpeg4", "aac"),
        (".webm", "libvpx", "libopus"),
        (".mpeg", "mpeg2video", "mp2"),
        (".mpg", "mpeg2video", "mp2"),
        (".m4v", "mpeg4", "aac"),
    ],
)
def test_every_video_container_exposes_audio_for_processing(
    tmp_path: Path,
    suffix: str,
    video_codec: str,
    audio_codec: str,
):
    assert shutil.which("ffmpeg"), "FFmpeg must be installed; format tests may not be skipped"
    source = tmp_path / f"lecture{suffix}"
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "color=c=blue:s=320x180:d=0.8",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=0.8",
            "-shortest", "-c:v", video_codec, "-c:a", audio_codec, str(source),
        ],
        check=True,
    )
    assert source.is_file() and source.stat().st_size > 0
    assert has_audio_stream(source) is True
    normalized = tmp_path / "normalized"
    normalized.mkdir()
    assert extract_audio_chunks(source, normalized, prefix=suffix.lstrip("."))


def test_audio_only_study_pack_skips_visual_pipeline(tmp_path: Path, monkeypatch):
    source = tmp_path / "lecture.mp3"
    source.write_bytes(b"synthetic audio placeholder")
    job_id = f"audio-only-{uuid.uuid4()}"
    options = {
        "source_language": "tr",
        "output_language": "tr",
        "summary_style": "standard",
        "quiz_count": 10,
        "flashcard_count": 20,
        "translate_transcript": False,
        "output_formats": ["pdf"],
        "job_type": "study_pack",
    }
    captured = {}

    monkeypatch.setattr(
        pipeline,
        "_audio_pipeline",
        lambda *_args, **_kwargs: (
            "Enerji kapalı bir sistemde korunur.",
            "",
            [{"start": 0.0, "end": 1.0, "timestamp": "00:00:00", "speaker": None, "text": "Enerji korunur."}],
            "chunk_estimate",
        ),
    )
    monkeypatch.setattr(
        pipeline,
        "_visual_pipeline",
        lambda *_args, **_kwargs: pytest.fail("audio-only jobs must not run visual extraction"),
    )
    monkeypatch.setattr(
        pipeline,
        "_build_text_outputs",
        lambda *_args, **_kwargs: (
            {"title": "Enerji", "summary": "Enerji korunur.", "quiz": [], "flashcards": []},
            "",
        ),
    )
    # This unit test uses placeholder bytes and isolates visual routing.  MP3
    # generation has its own real-FFmpeg and packaging coverage.
    monkeypatch.setattr(pipeline, "_prepare_study_audio", lambda *_args, **_kwargs: None)

    def fake_artifacts(job_dir, result, _slides_dir, *, audio_source=None):
        del audio_source
        captured.update(result)
        archive = Path(job_dir) / "package.zip"
        archive.write_bytes(b"zip")
        return [], archive

    monkeypatch.setattr(pipeline, "build_artifacts", fake_artifacts)
    JOBS.create(job_id, tmp_path, options)
    pipeline.process_job(job_id, source, options)

    assert JOBS.get(job_id)["status"] == "done"
    assert captured["diagnostics"]["engine"] == "audio-only"
    assert captured["sources"]["visual"] is None
    assert captured["slides"] == []


def test_free_plan_source_limits_are_enforced_server_side():
    created = register_user(
        f"limits-{uuid.uuid4()}@example.com",
        "Strong-test-password1",
        "Limit",
        "Test",
    )
    user_id = verify_email(created["verification_token"])["user"]["id"]

    with pytest.raises(BillingError, match="tek iş sınırı 30 dakikadır"):
        require_duration_entitlement(user_id, 31 * 60)
    with pytest.raises(BillingError, match="en fazla 3 kaynak"):
        require_duration_entitlement(user_id, 60, source_file_count=4)
    with pytest.raises(BillingError, match="en fazla 50 belge sayfası"):
        require_duration_entitlement(user_id, 60, document_mode=True, document_pages=51)
    with pytest.raises(BillingError, match="en fazla 20 taranmış sayfaya OCR"):
        require_duration_entitlement(user_id, 60, document_mode=True, ocr_pages=21)
    require_duration_entitlement(
        user_id,
        60,
        document_mode=True,
        source_size_bytes=25 * 1024 * 1024,
    )
    with pytest.raises(BillingError, match="yükleme sınırı 25 MB"):
        require_duration_entitlement(
            user_id,
            60,
            document_mode=True,
            source_size_bytes=25 * 1024 * 1024 + 1,
        )


def test_global_document_capacity_is_one_hundred_mebibytes():
    assert config.MAX_DOCUMENT_BYTES == 100 * 1024 * 1024


def test_annual_subscription_allowance_renews_each_calendar_month():
    starts_at = datetime(2026, 1, 31, 14, 30, tzinfo=timezone.utc)
    subscription = SimpleNamespace(interval="annual", starts_at=starts_at)
    assert _subscription_usage_period_start(
        subscription,
        datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc),
    ) == datetime(2026, 2, 28, 14, 30, tzinfo=timezone.utc)
    assert _subscription_usage_period_start(
        subscription,
        datetime(2026, 3, 31, 16, 0, tzinfo=timezone.utc),
    ) == datetime(2026, 3, 31, 14, 30, tzinfo=timezone.utc)
