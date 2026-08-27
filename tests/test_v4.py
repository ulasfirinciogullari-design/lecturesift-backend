import json
import shutil
import subprocess
import uuid
import zipfile
from pathlib import Path

import cv2
import numpy as np
from fastapi.testclient import TestClient

from lecturesift.app import _options, app
from lecturesift.errors import normalize_error
from lecturesift.exports import build_artifacts
from lecturesift.jobs import JOBS
from lecturesift.media import convert_videos_to_mp3, extract_audio_chunks, validate_remote_url
from lecturesift.pipeline import process_job
from lecturesift.slides import presentation_score, read_frame_at, scan_candidate_timestamps


def billing_headers(client: TestClient) -> dict[str, str]:
    account = client.post(
        "/billing/register",
        json={"email": f"upload-{uuid.uuid4()}@example.com", "password": "strong-test-password"},
    )
    assert account.status_code == 200
    return {"Authorization": f"Bearer {account.json()['token']}"}


def synthetic_slide() -> np.ndarray:
    frame = np.full((720, 1280, 3), 244, dtype=np.uint8)
    cv2.rectangle(frame, (55, 45), (1225, 675), (210, 220, 235), 3)
    cv2.putText(frame, "ENERGY AND WORK", (100, 150), cv2.FONT_HERSHEY_SIMPLEX, 2.0, (25, 35, 70), 5)
    for index, value in enumerate(("Potential energy", "Kinetic energy", "Conservation law")):
        cv2.circle(frame, (125, 275 + index * 105), 10, (50, 95, 190), -1)
        cv2.putText(frame, value, (165, 290 + index * 105), cv2.FONT_HERSHEY_SIMPLEX, 1.25, (40, 45, 60), 3)
    return frame


def synthetic_scene() -> np.ndarray:
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    for row in range(frame.shape[0]):
        frame[row, :, 0] = 100 + row * 90 // frame.shape[0]
        frame[row, :, 1] = 145 + row * 60 // frame.shape[0]
        frame[row, :, 2] = 190 - row * 80 // frame.shape[0]
    cv2.circle(frame, (1000, 170), 95, (40, 185, 255), -1)
    cv2.rectangle(frame, (0, 500), (1280, 720), (52, 110, 70), -1)
    return frame


def synthetic_classroom_scene() -> np.ndarray:
    frame = np.full((720, 1280, 3), (205, 210, 212), dtype=np.uint8)
    cv2.rectangle(frame, (380, 120), (900, 470), (235, 235, 235), -1)
    cv2.rectangle(frame, (380, 120), (900, 470), (55, 65, 75), 8)
    for index in range(8):
        center = (100 + index * 150, 610 if index % 2 else 570)
        cv2.circle(frame, center, 58, (85, 125, 185), -1)
        cv2.rectangle(frame, (center[0] - 72, center[1] + 45), (center[0] + 72, 720), (55, 65, 95), -1)
    cv2.line(frame, (0, 90), (1280, 90), (120, 125, 130), 8)
    return frame


def synthetic_textured_office_scene() -> np.ndarray:
    frame = np.full((720, 1280, 3), (180, 150, 120), dtype=np.uint8)
    for y in range(0, 720, 20):
        cv2.line(frame, (0, y), (1280, y), (30 + y % 200, 60, 90), 3)
    for x in range(0, 1280, 25):
        cv2.line(frame, (x, 0), (x, 720), (60, 80 + x % 150, 120), 2)
    for y in (100, 330, 560):
        for x in (140, 500, 860, 1120):
            cv2.circle(frame, (x, y), 42, (85, 125, 185), -1)
    return frame


def test_slide_layout_scores_above_natural_scene():
    slide_score, slide_metrics = presentation_score(synthetic_slide())
    scene_score, scene_metrics = presentation_score(synthetic_scene())
    assert slide_metrics["has_layout"] is True
    assert slide_score >= 7
    assert scene_score < slide_score
    assert scene_metrics["has_layout"] is False


def test_classroom_people_band_is_not_a_slide():
    slide_score, slide_metrics = presentation_score(synthetic_slide())
    room_score, room_metrics = presentation_score(synthetic_classroom_scene())
    assert room_metrics["skin_band_max"] >= 0.25
    assert room_score < 7
    assert slide_score >= 7


def test_textured_office_scene_is_not_a_slide():
    room_score, room_metrics = presentation_score(synthetic_textured_office_scene())
    assert room_metrics["natural_scene"] is True
    assert room_score < 7


def test_private_url_is_rejected():
    try:
        validate_remote_url("http://127.0.0.1/video.mp4")
    except Exception as error:
        assert getattr(error, "code", None) == "LS-URL-04"
    else:
        raise AssertionError("private URL was accepted")


def test_quota_error_is_human_readable():
    error = normalize_error(RuntimeError("429 insufficient_quota: exceeded your current quota"))
    assert error.code == "LS-AI-01"
    assert "kota" in error.user_message.lower()


def test_health_and_unsupported_upload():
    client = TestClient(app)
    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["version"] == "4.1"
    response = client.post(
        "/jobs",
        files={"file": ("notes.txt", b"not a video", "text/plain")},
        headers=billing_headers(client),
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "LS-UPLOAD-01"


def test_default_zip_contains_only_pdfs(tmp_path: Path):
    slides = tmp_path / "slides"
    slides.mkdir()
    cv2.imwrite(str(slides / "slide_001_00m05s.jpg"), synthetic_slide())
    result = {
        "title": "Enerjiye Giriş",
        "summary": "İş ve enerji arasındaki temel ilişki.",
        "key_points": ["Enerji korunur."],
        "important_terms": [{"term": "İş", "definition": "Kuvvetin yol boyunca etkisi."}],
        "notes": [{"heading": "Temel ayrım", "content": "Kinetik ve potansiyel enerji.", "bullets": ["Birim joule'dür."]}],
        "exam_focus": ["Enerji dönüşümleri"],
        "quiz": [{"question": "Enerji birimi nedir?", "options": ["Joule", "Watt"], "answer_index": 0, "explanation": "SI birimi joule'dür."}],
        "flashcards": [{"front": "Enerji korunumu", "back": "Toplam enerji sabit kalır."}],
        "transcript_original": "Bugün enerji ve iş konusunu konuşacağız.",
        "transcript_translated": "Today we will discuss energy and work.",
        "diagnostics": {"final_unique_slides": 1},
        "slides": [{"file": "slide_001_00m05s.jpg", "timestamp": "00:05", "second": 5}],
        "options": {"output_formats": ["pdf"]},
    }
    artifacts, zip_path = build_artifacts(tmp_path, result, slides)
    assert zip_path.exists()
    assert {item["file"] for item in artifacts} >= {
        "Ozet.pdf",
        "Transkript_Orijinal.pdf",
        "Transkript_Ceviri.pdf",
        "Slaytlar.pdf",
    }
    assert all(item["file"].endswith(".pdf") for item in artifacts)
    parsed = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))
    assert parsed["artifacts"]
    with zipfile.ZipFile(zip_path) as archive:
        assert "Ders_Notlari.pdf" in archive.namelist()
        assert "Slaytlar.pdf" in archive.namelist()
        assert all(name.endswith(".pdf") for name in archive.namelist())


def test_selected_word_and_txt_exports(tmp_path: Path):
    result = {
        "title": "Biçim Testi",
        "summary": "Seçilen biçimler üretilir.",
        "transcript_original": "Tek transkript.",
        "transcript_translated": "",
        "slides": [],
        "quiz": [],
        "flashcards": [],
        "options": {"output_formats": ["docx", "txt"]},
    }
    artifacts, zip_path = build_artifacts(tmp_path, result, tmp_path / "slides")
    names = {item["file"] for item in artifacts}
    assert "Ozet.docx" in names and "Ozet.txt" in names
    assert "Transkript_Ceviri.docx" not in names
    with zipfile.ZipFile(zip_path) as archive:
        assert all(name.endswith((".docx", ".txt")) for name in archive.namelist())


def test_same_explicit_language_disables_duplicate_translation():
    options = _options("tr", "tr", "standard", 10, 20, True, output_formats="pdf,docx,txt")
    assert options["translate_transcript"] is False
    assert options["output_formats"] == ["pdf", "docx", "txt"]


def test_no_audio_scene_video_completes_without_false_slides(tmp_path: Path):
    video = tmp_path / "scene.mp4"
    writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"mp4v"), 5.0, (1280, 720))
    assert writer.isOpened()
    frame = synthetic_scene()
    for _ in range(40):
        writer.write(frame)
    writer.release()

    job_id = f"test-{uuid.uuid4()}"
    options = {
        "source_language": "auto",
        "output_language": "tr",
        "summary_style": "standard",
        "quiz_count": 10,
        "flashcard_count": 20,
        "translate_transcript": True,
    }
    JOBS.create(job_id, tmp_path, options)
    process_job(job_id, video, options)
    job = JOBS.get(job_id)
    assert job["status"] == "done"
    result = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))
    assert result["slides"] == []
    assert result["diagnostics"]["final_unique_slides"] == 0
    assert Path(job["result_path"]).exists()


def test_dual_source_uses_audio_video_for_audio_and_slide_video_for_visuals(tmp_path: Path, monkeypatch):
    audio_video = tmp_path / "speaker.mp4"
    visual_video = tmp_path / "presentation.mp4"
    speaker_frame = tmp_path / "speaker.jpg"
    cv2.imwrite(str(speaker_frame), synthetic_scene())
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loop",
            "1",
            "-i",
            str(speaker_frame),
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=660:duration=8",
            "-t",
            "8",
            "-c:v",
            "mpeg4",
            "-q:v",
            "7",
            "-c:a",
            "aac",
            str(audio_video),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    slide_writer = cv2.VideoWriter(str(visual_video), cv2.VideoWriter_fourcc(*"mp4v"), 5.0, (1280, 720))
    assert slide_writer.isOpened()
    for _ in range(40):
        slide_writer.write(synthetic_slide())
    slide_writer.release()

    transcribed_chunks: list[str] = []
    monkeypatch.setattr(
        "lecturesift.pipeline.transcribe",
        lambda path, _language: transcribed_chunks.append(path.name) or "Bu ses ana videodan geldi.",
    )
    monkeypatch.setattr("lecturesift.pipeline.translate_transcript", lambda text, _language: f"Çeviri: {text}")
    monkeypatch.setattr(
        "lecturesift.pipeline.make_study_pack",
        lambda *_args: {"title": "Çift Kaynak Testi", "summary": "Ses ve slayt ayrı kayıtlardan işlendi."},
    )

    job_id = f"test-{uuid.uuid4()}"
    options = {
        "source_language": "auto",
        "output_language": "tr",
        "summary_style": "standard",
        "quiz_count": 10,
        "flashcard_count": 20,
        "translate_transcript": True,
        "slides_offset_seconds": 2.5,
    }
    JOBS.create(job_id, tmp_path, options, source_type="upload_separate")
    process_job(job_id, audio_video, options, visual_video)

    job = JOBS.get(job_id)
    result = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))
    assert job["status"] == "done"
    assert result["sources"]["mode"] == "separate"
    assert result["sources"]["audio"] == "speaker.mp4"
    assert result["sources"]["visual"] == "presentation.mp4"
    assert result["diagnostics"]["source_mode"] == "separate"
    assert transcribed_chunks == ["audio_001_000.mp3"]
    assert result["transcript_original"] == "Bu ses ana videodan geldi."
    assert len(result["slides"]) == 1
    assert result["slides"][0]["second"] == result["slides"][0]["source_second"] + 2.5


def test_second_upload_rejects_unsupported_video_format():
    client = TestClient(app)
    response = client.post(
        "/jobs",
        files={
            "file": ("audio.mp4", b"audio", "video/mp4"),
            "slides_file": ("slides.txt", b"not video", "text/plain"),
        },
        headers=billing_headers(client),
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "LS-UPLOAD-01"


def test_dual_source_upload_endpoint_saves_both_roles(tmp_path: Path, monkeypatch):
    captured: dict = {}

    class DeferredThread:
        def __init__(self, target, args, daemon):
            captured["target"] = target
            captured["args"] = args
            captured["daemon"] = daemon

        def start(self):
            captured["started"] = True

    monkeypatch.setattr("lecturesift.app.WORK_DIR", tmp_path)
    monkeypatch.setattr("lecturesift.app.threading.Thread", DeferredThread)
    client = TestClient(app)
    response = client.post(
        "/jobs",
        files={
            "file": ("speaker.mp4", b"audio-source", "video/mp4"),
            "slides_file": ("slides.webm", b"visual-source", "video/webm"),
        },
        data={"slides_offset_seconds": "1.5"},
        headers=billing_headers(client),
    )

    assert response.status_code == 200
    job = JOBS.get(response.json()["job_id"])
    audio_paths, visual_paths = captured["args"][1], captured["args"][3]
    assert captured["started"] is True
    assert job["source_type"] == "upload_separate"
    assert job["file_size_bytes"] == len(b"audio-source") + len(b"visual-source")
    assert job["audio_file_sizes"] == [len(b"audio-source")]
    assert job["visual_file_sizes"] == [len(b"visual-source")]
    assert captured["args"][2]["slides_offset_seconds"] == 1.5
    assert audio_paths[0].read_bytes() == b"audio-source"
    assert visual_paths[0].read_bytes() == b"visual-source"
    shutil.rmtree(Path(job["job_dir"]), ignore_errors=True)


def test_short_static_slide_is_preserved(tmp_path: Path):
    video = tmp_path / "slide.mp4"
    writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"mp4v"), 5.0, (1280, 720))
    assert writer.isOpened()
    frame = synthetic_slide()
    for _ in range(40):
        writer.write(frame)
    writer.release()

    from lecturesift.slides import extract_slides

    manifest, diagnostics = extract_slides(video, tmp_path / "slide-output", lambda *_: None)
    assert len(manifest) == 1
    assert diagnostics["final_unique_slides"] == 1


def test_audio_is_prepared_in_bounded_chunks(tmp_path: Path):
    video = tmp_path / "audio-video.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=320x240:d=1",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=880:duration=1",
            "-shortest",
            "-c:v",
            "mpeg4",
            "-c:a",
            "aac",
            str(video),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    chunks = extract_audio_chunks(video, tmp_path)
    assert [path.name for path in chunks] == ["audio_000.mp3"]
    assert chunks[0].stat().st_size > 0


def test_multi_upload_preserves_user_order(tmp_path: Path, monkeypatch):
    captured: dict = {}

    class DeferredThread:
        def __init__(self, target, args, daemon):
            captured["args"] = args

        def start(self):
            pass

    monkeypatch.setattr("lecturesift.app.WORK_DIR", tmp_path)
    monkeypatch.setattr("lecturesift.app.threading.Thread", DeferredThread)
    client = TestClient(app)
    response = client.post(
        "/jobs",
        files=[
            ("files", ("third.mp4", b"3", "video/mp4")),
            ("files", ("first.webm", b"1", "video/webm")),
            ("files", ("second.mov", b"2", "video/quicktime")),
        ],
        headers=billing_headers(client),
    )
    assert response.status_code == 200
    paths = captured["args"][1]
    assert [path.name for path in paths] == ["part_001.mp4", "part_002.webm", "part_003.mov"]
    assert [path.read_bytes() for path in paths] == [b"3", b"1", b"2"]
    shutil.rmtree(Path(JOBS.get(response.json()["job_id"])["job_dir"]), ignore_errors=True)


def test_multiple_videos_convert_to_one_mp3(tmp_path: Path):
    videos = []
    for index, frequency in enumerate((440, 880), 1):
        video = tmp_path / f"source-{index}.mp4"
        subprocess.run(
            [
                "ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=blue:s=160x120:d=0.5",
                "-f", "lavfi", "-i", f"sine=frequency={frequency}:duration=0.5", "-shortest",
                "-c:v", "mpeg4", "-c:a", "aac", str(video),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        videos.append(video)
    result = convert_videos_to_mp3(videos, tmp_path)
    assert result.name == "LectureSift_Ders_Sesi.mp3"
    assert result.stat().st_size > 0


def test_webm_sampling_keeps_real_timestamps(tmp_path: Path):
    video = tmp_path / "timeline.webm"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=white:s=320x180:d=3",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=320x180:d=3",
            "-filter_complex",
            "[0:v][1:v]concat=n=2:v=1:a=0[v]",
            "-map",
            "[v]",
            "-c:v",
            "libvpx",
            "-deadline",
            "realtime",
            "-cpu-used",
            "8",
            str(video),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    timestamps, duration = scan_candidate_timestamps(video, lambda *_: None)
    before = read_frame_at(video, 1.0)
    after = read_frame_at(video, 4.0)
    assert duration >= 5.5
    assert any(2.5 <= second <= 4.5 for second in timestamps)
    assert before is not None and after is not None
    assert float(before.mean()) > 200
    assert float(after.mean()) < 30
