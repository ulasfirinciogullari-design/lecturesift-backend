import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .ai import make_study_pack, transcribe, translate_transcript
from .errors import normalize_error
from .exports import build_artifacts
from .jobs import JOBS
from .media import extract_audio_chunks, has_audio_stream
from .slides import extract_slides


def _audio_pipeline(job_id: str, video_path: Path, job_dir: Path, options: dict) -> tuple[str, str]:
    if not has_audio_stream(video_path):
        JOBS.update_task(job_id, "audio", 100, "no_audio")
        return "", ""

    JOBS.update_task(job_id, "audio", 10, "audio_extract")
    audio_chunks = extract_audio_chunks(video_path, job_dir)
    transcripts: list[str] = []
    for index, audio_path in enumerate(audio_chunks, 1):
        JOBS.update_task(
            job_id,
            "audio",
            24 + 48 * (index - 1) / max(1, len(audio_chunks)),
            "transcription",
        )
        text = transcribe(audio_path, options["source_language"])
        if text.strip():
            transcripts.append(text.strip())
    original = "\n\n".join(transcripts)
    JOBS.update_task(job_id, "audio", 76, "transcript_ready")

    translated = ""
    if options.get("translate_transcript", True) and original.strip():
        JOBS.update_task(job_id, "audio", 80, "transcript_translation")
        translated = translate_transcript(original, options["output_language"])
    JOBS.update_task(job_id, "audio", 100, "audio_done")
    return original, translated


def _shift_slide_timeline(slides: list[dict], offset_seconds: float) -> None:
    if not offset_seconds:
        return
    for slide in slides:
        source_second = float(slide.get("second", 0))
        aligned_second = max(0.0, source_second + offset_seconds)
        slide["source_second"] = round(source_second, 1)
        slide["second"] = round(aligned_second, 1)
        slide["timestamp"] = f"{int(aligned_second // 60):02d}:{int(aligned_second % 60):02d}"


def process_job(
    job_id: str,
    audio_video_path: Path,
    options: dict,
    visual_video_path: Path | None = None,
) -> None:
    data = JOBS.get(job_id)
    if not data:
        return
    job_dir = Path(data["job_dir"])
    slides_dir = job_dir / "slides"
    visual_source = visual_video_path or audio_video_path
    dual_source = visual_video_path is not None
    started = time.time()

    try:
        JOBS.update(job_id, status="working", percent=8, stage="parallel_analysis", started=started)

        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="lecturesift") as executor:
            audio_future = executor.submit(_audio_pipeline, job_id, audio_video_path, job_dir, options)
            slides, diagnostics = extract_slides(
                visual_source,
                slides_dir,
                lambda percent, stage: JOBS.update_task(job_id, "visual", percent, stage),
            )
            original_transcript, translated_transcript = audio_future.result()

        timeline_offset = float(options.get("slides_offset_seconds", 0) or 0)
        _shift_slide_timeline(slides, timeline_offset)
        diagnostics["source_mode"] = "dual" if dual_source else "single"
        diagnostics["slides_offset_seconds"] = timeline_offset

        JOBS.update(job_id, percent=73, stage="study_pack")
        study_pack = make_study_pack(
            original_transcript,
            options["output_language"],
            options["summary_style"],
            options["quiz_count"],
            options["flashcard_count"],
        )

        JOBS.update(job_id, percent=90, stage="exports")
        result = {
            "version": "4.0",
            "job_id": job_id,
            "options": options,
            "sources": {
                "mode": "dual" if dual_source else "single",
                "audio": audio_video_path.name,
                "visual": visual_source.name,
                "slides_offset_seconds": timeline_offset,
            },
            "slides": slides,
            "diagnostics": diagnostics,
            "transcript_original": original_transcript,
            "transcript_translated": translated_transcript,
            "transcript": translated_transcript or original_transcript,
            **study_pack,
        }
        artifacts, zip_path = build_artifacts(job_dir, result, slides_dir)
        result["artifacts"] = artifacts

        elapsed = round(time.time() - started, 1)
        JOBS.update(
            job_id,
            status="done",
            percent=100,
            stage="done",
            elapsed_seconds=elapsed,
            result_path=str(zip_path),
        )
    except Exception as exc:
        normalized = normalize_error(exc)
        print(f"PROCESS ERROR [{normalized.code}]: {normalized.technical_message}", flush=True)
        traceback.print_exc()
        JOBS.update(
            job_id,
            status="error",
            percent=0,
            stage="error",
            error_code=normalized.code,
            error=normalized.user_message,
            technical_error=normalized.technical_message,
            elapsed_seconds=round(time.time() - started, 1),
        )
