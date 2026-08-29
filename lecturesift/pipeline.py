import shutil
import time
import traceback
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .ai import make_study_pack, transcribe, transcribe_timed, translate_transcript
from .billing_service import record_usage
from .config import (
    APP_VERSION,
    DOCUMENT_EXTENSIONS,
    PRECISE_TRANSCRIPT_TIMESTAMPS,
    TRANSCRIPTION_PARALLELISM,
)
from .costs import cost_context
from .documents import extract_documents
from .duration import media_duration_seconds
from .errors import normalize_error
from .exports import build_artifacts, build_binary_artifact
from .jobs import JOBS
from .media import convert_videos_to_mp3, extract_audio_chunks, has_audio_stream
from .slides import extract_slides


def _path_list(value: Path | list[Path] | tuple[Path, ...]) -> list[Path]:
    if isinstance(value, Path):
        return [value]
    return [Path(item) for item in value]


def _same_text(left: str, right: str) -> bool:
    return " ".join(left.casefold().split()) == " ".join(right.casefold().split())


def _public_options(options: dict) -> dict:
    return {key: value for key, value in options.items() if key != "billing_user_id"}


def _source_duration_seconds(paths: list[Path]) -> float:
    return media_duration_seconds(paths)


def _record_billing_usage(
    job_id: str,
    options: dict,
    paths: list[Path],
    *,
    duration_seconds: float | None = None,
) -> None:
    user_id = options.get("billing_user_id")
    if not user_id:
        return
    try:
        record_usage(
            str(user_id),
            job_id,
            _source_duration_seconds(paths) if duration_seconds is None else duration_seconds,
        )
    except Exception:
        # The completed study pack remains available if metering is temporarily unavailable.
        print("BILLING USAGE ERROR: metering is temporarily unavailable", flush=True)


def _transcript_timestamp(second: float) -> str:
    total = max(0, int(second))
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _audio_pipeline(
    job_id: str,
    video_paths: list[Path],
    job_dir: Path,
    options: dict,
) -> tuple[str, str, list[dict], str]:
    audio_chunks: list[Path] = []
    for index, video_path in enumerate(video_paths, 1):
        JOBS.update_task(job_id, "audio", 5 + 18 * (index - 1) / max(1, len(video_paths)), "audio_extract")
        if has_audio_stream(video_path):
            audio_chunks.extend(extract_audio_chunks(video_path, job_dir, prefix=f"audio_{index:03d}"))

    if not audio_chunks:
        JOBS.update_task(job_id, "audio", 100, "no_audio")
        return "", "", [], "none"

    chunk_durations = [_source_duration_seconds([path]) for path in audio_chunks]
    chunk_offsets: list[float] = []
    timeline_cursor = 0.0
    for duration in chunk_durations:
        chunk_offsets.append(timeline_cursor)
        timeline_cursor += duration
    timestamp_mode = "provider_segments" if PRECISE_TRANSCRIPT_TIMESTAMPS else "chunk_estimate"
    user_id = str(options.get("billing_user_id") or "") or None

    def transcribe_chunk(index: int, audio_path: Path) -> tuple[int, str, list[dict]]:
        chunk_duration = chunk_durations[index]
        offset = chunk_offsets[index]
        segments: list[dict] = []
        with cost_context(job_id, user_id):
            timed = None
            text = ""
            if PRECISE_TRANSCRIPT_TIMESTAMPS:
                timed = transcribe_timed(audio_path, options["source_language"])
                text = timed["text"]
            else:
                text = transcribe(audio_path, options["source_language"])
        if PRECISE_TRANSCRIPT_TIMESTAMPS:
            for segment in (timed or {}).get("segments", []):
                start = offset + float(segment["start"])
                end = offset + float(segment["end"])
                segments.append(
                    {
                        **segment,
                        "start": round(start, 2),
                        "end": round(max(start, end), 2),
                        "timestamp": _transcript_timestamp(start),
                    }
                )
        else:
            if text.strip():
                segments.append(
                    {
                        "start": round(offset, 2),
                        "end": round(offset + chunk_duration, 2),
                        "timestamp": _transcript_timestamp(offset),
                        "speaker": None,
                        "text": text.strip(),
                        "precision": "chunk_estimate",
                    }
                )
        return index, text.strip(), segments

    completed = 0
    ordered: list[tuple[str, list[dict]] | None] = [None] * len(audio_chunks)
    with ThreadPoolExecutor(
        max_workers=min(TRANSCRIPTION_PARALLELISM, len(audio_chunks)),
        thread_name_prefix="lecturesift-transcribe",
    ) as executor:
        futures = {
            executor.submit(transcribe_chunk, index, path): index
            for index, path in enumerate(audio_chunks)
        }
        for future in as_completed(futures):
            index, text, segments = future.result()
            ordered[index] = (text, segments)
            completed += 1
            JOBS.update_task(
                job_id,
                "audio",
                24 + 50 * completed / max(1, len(audio_chunks)),
                "transcription",
            )

    transcripts: list[str] = []
    transcript_segments: list[dict] = []
    for item in ordered:
        if not item:
            continue
        text, segments = item
        if text:
            transcripts.append(text)
        transcript_segments.extend(segments)
    original = "\n\n".join(transcripts)
    JOBS.update_task(job_id, "audio", 76, "transcript_ready")

    translated = ""
    if (
        options.get("translate_transcript", True)
        and original.strip()
        and not options.get("_defer_transcript_translation")
    ):
        JOBS.update_task(job_id, "audio", 80, "transcript_translation")
        candidate = translate_transcript(original, options["output_language"])
        if candidate.strip() and not _same_text(original, candidate):
            translated = candidate.strip()
    JOBS.update_task(job_id, "audio", 100, "audio_done")
    return original, translated, transcript_segments, timestamp_mode


def _build_text_outputs(job_id: str, original_transcript: str, options: dict) -> tuple[dict, str]:
    """Build the study pack and optional full transcript translation concurrently."""
    user_id = str(options.get("billing_user_id") or "") or None

    def build_study_pack() -> dict:
        with cost_context(job_id, user_id):
            return make_study_pack(
                original_transcript,
                options["output_language"],
                options["summary_style"],
                options["quiz_count"],
                options["flashcard_count"],
            )

    def build_translation() -> str:
        with cost_context(job_id, user_id):
            candidate = translate_transcript(original_transcript, options["output_language"])
        if candidate.strip() and not _same_text(original_transcript, candidate):
            return candidate.strip()
        return ""

    source_language = str(options.get("source_language") or "").strip().casefold().split("-", 1)[0]
    output_language = str(options.get("output_language") or "").strip().casefold().split("-", 1)[0]
    explicitly_same_language = bool(
        source_language
        and source_language != "auto"
        and output_language
        and source_language == output_language
    )
    should_translate = bool(
        options.get("translate_transcript", True)
        and original_transcript.strip()
        and not explicitly_same_language
    )
    if not should_translate:
        study_pack = build_study_pack()
        try:
            JOBS.update(job_id, percent=89, stage="study_pack")
        except KeyError:
            pass
        return study_pack, ""

    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="lecturesift-text") as executor:
        study_future = executor.submit(build_study_pack)
        translation_future = executor.submit(build_translation)
        pending = {study_future: "study", translation_future: "translation"}
        completed: dict[str, object] = {}
        for future in as_completed(pending):
            completed[pending[future]] = future.result()
            try:
                JOBS.update(
                    job_id,
                    percent=80 if len(completed) == 1 else 89,
                    stage="study_pack",
                )
            except KeyError:
                # Unit-level helpers can run without a persisted job record.
                pass
        return dict(completed["study"]), str(completed["translation"])


def _aligned_timestamp(second: float) -> str:
    return f"{int(second // 60):02d}:{int(second % 60):02d}"


def _visual_pipeline(
    job_id: str,
    video_paths: list[Path],
    job_dir: Path,
    slides_dir: Path,
    slides_offset_seconds: float,
) -> tuple[list[dict], dict]:
    slides_dir.mkdir(parents=True, exist_ok=True)
    segments_dir = job_dir / "slide_segments"
    segments_dir.mkdir(parents=True, exist_ok=True)
    merged: list[dict] = []
    parts: list[dict] = []
    rejections: Counter[str] = Counter()
    timeline_cursor = 0.0
    diagnostic_totals = {"fast_candidates": 0, "presentation_candidates": 0, "persistent_groups": 0}

    for part_index, video_path in enumerate(video_paths, 1):
        part_dir = segments_dir / f"part_{part_index:03d}"

        def progress(percent: float, stage: str) -> None:
            combined = 100 * ((part_index - 1) + percent / 100) / max(1, len(video_paths))
            JOBS.update_task(job_id, "visual", combined, stage)

        manifest, diagnostics = extract_slides(video_path, part_dir, progress)
        duration = float(diagnostics.get("duration_seconds", 0) or 0)
        for slide in manifest:
            source_second = float(slide.get("second", 0) or 0)
            aligned_second = max(0.0, timeline_cursor + source_second + slides_offset_seconds)
            final_index = len(merged) + 1
            filename = f"slide_{final_index:03d}_{int(aligned_second // 60):02d}m{int(aligned_second % 60):02d}s.jpg"
            shutil.copy2(part_dir / slide["file"], slides_dir / filename)
            merged.append(
                {
                    **slide,
                    "file": filename,
                    "part": part_index,
                    "source_second": round(source_second, 1),
                    "second": round(aligned_second, 1),
                    "timestamp": _aligned_timestamp(aligned_second),
                }
            )
        for key in diagnostic_totals:
            diagnostic_totals[key] += int(diagnostics.get(key, 0) or 0)
        rejections.update(diagnostics.get("rejections", {}))
        parts.append(
            {
                "part": part_index,
                "file": video_path.name,
                "start_second": round(timeline_cursor, 1),
                "duration_seconds": round(duration, 1),
                "slides": len(manifest),
            }
        )
        timeline_cursor += duration

    shutil.rmtree(segments_dir, ignore_errors=True)
    diagnostics = {
        "engine": "v4-layout-persistence",
        "memory_mode": "timestamp_only",
        "source_parts": len(video_paths),
        "duration_seconds": round(timeline_cursor, 1),
        **diagnostic_totals,
        "final_unique_slides": len(merged),
        "slides_offset_seconds": slides_offset_seconds,
        "parts": parts,
        "rejections": dict(rejections),
    }
    JOBS.update_task(job_id, "visual", 100, "visual_done")
    return merged, diagnostics


def _process_job(
    job_id: str,
    audio_video_paths: Path | list[Path] | tuple[Path, ...],
    options: dict,
    visual_video_paths: Path | list[Path] | tuple[Path, ...] | None = None,
) -> None:
    data = JOBS.get(job_id)
    if not data:
        return
    job_dir = Path(data["job_dir"])
    slides_dir = job_dir / "slides"
    audio_sources = _path_list(audio_video_paths)
    visual_sources = _path_list(visual_video_paths) if visual_video_paths is not None else list(audio_sources)
    source_mode = "separate" if visual_video_paths is not None else "classic"
    started = time.time()

    try:
        JOBS.update(job_id, status="working", percent=8, stage="parallel_analysis", started=started)

        document_sources = [path for path in audio_sources if path.suffix.casefold() in DOCUMENT_EXTENSIONS]
        if document_sources:
            if len(document_sources) != len(audio_sources) or visual_video_paths is not None:
                raise ValueError("Document and media sources cannot be mixed in one job.")
            JOBS.update(job_id, percent=28, stage="document_extraction")
            expected_ocr_pages = max(1, int(options.get("document_ocr_pages") or 1))

            def update_ocr_progress(completed: int, total: int) -> None:
                denominator = max(1, total, expected_ocr_pages)
                percent = 28 + min(30, round(30 * completed / denominator))
                JOBS.update(
                    job_id,
                    percent=percent,
                    stage="document_ocr" if options.get("document_ocr_required") else "document_extraction",
                    ocr_pages_completed=completed,
                    ocr_pages_total=max(total, expected_ocr_pages),
                )

            document_data = extract_documents(
                document_sources,
                source_language=options.get("source_language", "auto"),
                progress_callback=update_ocr_progress,
            )
            JOBS.update(job_id, percent=62, stage="study_pack")
            study_pack = make_study_pack(
                document_data["text"],
                options["output_language"],
                options["summary_style"],
                options["quiz_count"],
                options["flashcard_count"],
            )
            JOBS.update(job_id, percent=90, stage="exports")
            slides_dir.mkdir(parents=True, exist_ok=True)
            result = {
                "version": APP_VERSION,
                "job_id": job_id,
                "options": _public_options(options),
                "sources": {
                    "mode": "documents",
                    "documents": document_data["documents"],
                    "source_files": [path.name for path in document_sources],
                    "words": document_data["words"],
                    "credit_minutes": document_data["credit_minutes"],
                },
                "slides": [],
                "diagnostics": {
                    "engine": "document-text-ocr-v2",
                    "characters": document_data["characters"],
                    "words": document_data["words"],
                    "source_parts": len(document_sources),
                    "ocr_used": document_data["ocr_used"],
                    "ocr_pages": document_data["ocr_pages"],
                },
                "transcript_original": document_data["text"],
                "transcript_translated": "",
                "transcript": document_data["text"],
                "transcript_segments": [],
                "transcript_timestamps_mode": "none",
                "timeline": [],
                **study_pack,
            }
            artifacts, zip_path = build_artifacts(job_dir, result, slides_dir)
            result["artifacts"] = artifacts
            elapsed = round(time.time() - started, 1)
            JOBS.update(
                job_id,
                status="done",
                title=result.get("title") or "LectureSift Belge Paketi",
                percent=100,
                stage="done",
                elapsed_seconds=elapsed,
                document_words=document_data["words"],
                billable_minutes=document_data["credit_minutes"],
                result_path=str(zip_path),
            )
            _record_billing_usage(
                job_id,
                options,
                document_sources,
                duration_seconds=float(document_data["credit_seconds"]),
            )
            return

        if options.get("job_type") == "audio_export":
            JOBS.update(job_id, percent=35, stage="audio_extract")
            audio_path = convert_videos_to_mp3(audio_sources, job_dir)
            JOBS.update(job_id, percent=88, stage="exports")
            result = {
                "version": APP_VERSION,
                "job_id": job_id,
                "job_type": "audio_export",
                "options": _public_options(options),
                "title": "LectureSift MP3",
                "summary": "Video sesi MP3 dosyasına dönüştürüldü.",
                "slides": [],
                "transcript_original": "",
                "transcript_translated": "",
                "quiz": [],
                "flashcards": [],
                "sources": {"mode": source_mode, "audio_files": [path.name for path in audio_sources]},
            }
            artifacts, zip_path = build_binary_artifact(
                job_dir, result, audio_path, "LectureSift_Ders_Sesi.mp3", "Ders Sesi (MP3)"
            )
            result["artifacts"] = artifacts
            JOBS.update(
                job_id,
                status="done",
                title=result["title"],
                percent=100,
                stage="done",
                elapsed_seconds=round(time.time() - started, 1),
                result_path=str(zip_path),
            )
            _record_billing_usage(job_id, options, audio_sources)
            return

        if options.get("job_type") == "download_video":
            source = audio_sources[0]
            JOBS.update(job_id, percent=88, stage="exports")
            filename = f"LectureSift_Indirilen_Video{source.suffix.lower() or '.mp4'}"
            result = {
                "version": APP_VERSION,
                "job_id": job_id,
                "job_type": "download_video",
                "options": _public_options(options),
                "title": "LectureSift Video İndirme",
                "summary": "Video bağlantıdan indirilmeye hazırlandı.",
                "slides": [],
                "transcript_original": "",
                "transcript_translated": "",
                "quiz": [],
                "flashcards": [],
                "sources": {"mode": "url", "audio_files": [source.name]},
            }
            artifacts, zip_path = build_binary_artifact(job_dir, result, source, filename, "İndirilen Video")
            result["artifacts"] = artifacts
            JOBS.update(
                job_id,
                status="done",
                title=result["title"],
                percent=100,
                stage="done",
                elapsed_seconds=round(time.time() - started, 1),
                result_path=str(zip_path),
            )
            _record_billing_usage(job_id, options, audio_sources)
            return

        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="lecturesift") as executor:
            # Context variables do not cross thread boundaries automatically.
            # Wrap the audio branch so its OpenAI calls remain attributable to
            # the same job and account in the cost ledger.
            def costed_audio_pipeline():
                with cost_context(job_id, str(options.get("billing_user_id") or "") or None):
                    return _audio_pipeline(
                        job_id,
                        audio_sources,
                        job_dir,
                        {**options, "_defer_transcript_translation": True},
                    )

            audio_future = executor.submit(costed_audio_pipeline)
            slides, diagnostics = _visual_pipeline(
                job_id,
                visual_sources,
                job_dir,
                slides_dir,
                float(options.get("slides_offset_seconds", 0) or 0),
            )
            original_transcript, translated_transcript, transcript_segments, timestamp_mode = audio_future.result()

        diagnostics["source_mode"] = source_mode

        JOBS.update(job_id, percent=73, stage="study_pack")
        study_pack, translated_transcript = _build_text_outputs(job_id, original_transcript, options)

        JOBS.update(job_id, percent=90, stage="exports")
        result = {
            "version": APP_VERSION,
            "job_id": job_id,
            "options": _public_options(options),
            "sources": {
                "mode": source_mode,
                "audio": audio_sources[0].name,
                "visual": visual_sources[0].name,
                "audio_files": [path.name for path in audio_sources],
                "visual_files": [path.name for path in visual_sources],
                "slides_offset_seconds": float(options.get("slides_offset_seconds", 0) or 0),
            },
            "slides": slides,
            "diagnostics": diagnostics,
            "transcript_original": original_transcript,
            "transcript_translated": translated_transcript,
            "transcript": translated_transcript or original_transcript,
            "transcript_segments": transcript_segments,
            "transcript_timestamps_mode": timestamp_mode,
            "timeline": sorted(
                [
                    {
                        "type": "transcript",
                        "second": item["start"],
                        "timestamp": item["timestamp"],
                        "speaker": item.get("speaker"),
                        "text": item["text"],
                    }
                    for item in transcript_segments
                ]
                + [
                    {
                        "type": "slide",
                        "second": item["second"],
                        "timestamp": item["timestamp"],
                        "file": item["file"],
                    }
                    for item in slides
                ],
                key=lambda item: float(item["second"]),
            ),
            **study_pack,
        }
        artifacts, zip_path = build_artifacts(job_dir, result, slides_dir)
        result["artifacts"] = artifacts

        elapsed = round(time.time() - started, 1)
        JOBS.update(
            job_id,
            status="done",
            title=result.get("title") or "LectureSift Ders Paketi",
            percent=100,
            stage="done",
            elapsed_seconds=elapsed,
            result_path=str(zip_path),
        )
        _record_billing_usage(job_id, options, audio_sources)
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


def process_job(
    job_id: str,
    audio_video_paths: Path | list[Path] | tuple[Path, ...],
    options: dict,
    visual_video_paths: Path | list[Path] | tuple[Path, ...] | None = None,
) -> None:
    """Run one job with cost attribution that also propagates to AI helpers."""
    with cost_context(job_id, str(options.get("billing_user_id") or "") or None):
        _process_job(job_id, audio_video_paths, options, visual_video_paths)
