from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def replace(path: str, old: str, new: str, *, count: int = 1) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    found = text.count(old)
    if found != count:
        raise RuntimeError(f"{path}: expected {count} occurrence(s), found {found}: {old[:100]!r}")
    target.write_text(text.replace(old, new), encoding="utf-8")


def insert_after(path: str, marker: str, value: str) -> None:
    replace(path, marker, marker + value)


# ---- FastAPI integration -------------------------------------------------
insert_after(
    "lecturesift/app.py",
    "from .billing_service import (\n",
    "",
)
replace(
    "lecturesift/app.py",
    ")\nfrom .config import (\n    APP_VERSION,\n",
    ")\nfrom .commerce import (\n    complete_job_history,\n    preview_result_for_user,\n    record_manual_order_entitlement,\n    register_job_history,\n    require_download_access,\n    require_visual_translation_access,\n)\nfrom .config import (\n    APP_VERSION,\n    CORS_ALLOW_ORIGINS,\n",
)
replace(
    "lecturesift/app.py",
    '    allow_origins=["*"],\n',
    "    allow_origins=CORS_ALLOW_ORIGINS,\n",
)
replace(
    "lecturesift/app.py",
    "def _public_job(data: dict) -> dict:\n    result = data.copy()\n    for key in (\"job_dir\", \"result_path\", \"technical_error\"):\n        result.pop(key, None)\n    result[\"options\"] = {\n        key: value for key, value in result.get(\"options\", {}).items() if key != \"billing_user_id\"\n    }\n    return result\n\n\n",
    "def _public_job(data: dict) -> dict:\n    result = data.copy()\n    for key in (\"job_dir\", \"result_path\", \"technical_error\", \"source_keys\", \"queue_error\"):\n        result.pop(key, None)\n    result[\"options\"] = {\n        key: value for key, value in result.get(\"options\", {}).items() if key != \"billing_user_id\"\n    }\n    return result\n\n\ndef _download_access_or_402(user_id: str, job_id: str | None = None) -> dict:\n    try:\n        return require_download_access(user_id, job_id)\n    except BillingError as exc:\n        raise HTTPException(402, detail={\"code\": \"LS-BILL-22\", \"message\": str(exc), \"unlock_plan\": \"mini\"}) from exc\n\n\ndef _visual_translation_or_402(user_id: str) -> dict:\n    try:\n        return require_visual_translation_access(user_id)\n    except BillingError as exc:\n        raise HTTPException(402, detail={\"code\": \"LS-BILL-23\", \"message\": str(exc), \"unlock_plan\": \"mini\"}) from exc\n\n\n",
)
replace(
    "lecturesift/app.py",
    "    translate_transcript: bool,\n    slides_offset_seconds: float = 0,\n",
    "    translate_transcript: bool,\n    translate_visual_text: bool = False,\n    slides_offset_seconds: float = 0,\n",
)
replace(
    "lecturesift/app.py",
    '        "translate_transcript": translation_enabled,\n        "slides_offset_seconds":',
    '        "translate_transcript": translation_enabled,\n        "translate_visual_text": bool(translate_visual_text) and not (source != "auto" and source == output),\n        "slides_offset_seconds":',
)
replace(
    "lecturesift/app.py",
    "        account = approve_manual_order(reference)\n",
    "        account = approve_manual_order(reference)\n        record_manual_order_entitlement(reference)\n",
)
replace(
    "lecturesift/app.py",
    '    return json.loads(path.read_text(encoding="utf-8"))\n',
    '    result = json.loads(path.read_text(encoding="utf-8"))\n    return preview_result_for_user(user["id"], job_id, result)\n',
)
replace(
    "lecturesift/app.py",
    "@app.get(\"/jobs/{job_id}/slide/{filename}\")\ndef get_slide(job_id: str, filename: str, user: dict = Depends(_billing_user)) -> FileResponse:\n    data = _owned_job(job_id, user)\n    if Path(filename).name != filename:\n        raise HTTPException(400, detail={\"code\": \"LS-FILE-01\", \"message\": \"Geçersiz dosya adı.\"})\n    path = Path(data[\"job_dir\"]) / \"slides\" / filename\n    if not path.exists():\n        raise HTTPException(404, detail={\"code\": \"LS-FILE-02\", \"message\": \"Slayt görseli bulunamadı.\"})\n    return FileResponse(str(path), media_type=\"image/jpeg\")\n",
    "@app.get(\"/jobs/{job_id}/slide/{filename}\")\ndef get_slide(job_id: str, filename: str, user: dict = Depends(_billing_user)) -> FileResponse:\n    data = _owned_job(job_id, user)\n    if Path(filename).name != filename:\n        raise HTTPException(400, detail={\"code\": \"LS-FILE-01\", \"message\": \"Geçersiz dosya adı.\"})\n    result_path = Path(data[\"job_dir\"]) / \"result.json\"\n    if result_path.exists():\n        full = json.loads(result_path.read_text(encoding=\"utf-8\"))\n        preview = preview_result_for_user(user[\"id\"], job_id, full)\n        if preview.get(\"download_locked\"):\n            allowed = set()\n            for slide in preview.get(\"slides\") or []:\n                allowed.add(str(slide.get(\"file\") or \"\"))\n                allowed.add(str(slide.get(\"translated_file\") or \"\"))\n            if filename not in allowed:\n                _download_access_or_402(user[\"id\"], job_id)\n    path = Path(data[\"job_dir\"]) / \"slides\" / filename\n    if not path.exists():\n        raise HTTPException(404, detail={\"code\": \"LS-FILE-02\", \"message\": \"Slayt görseli bulunamadı.\"})\n    return FileResponse(str(path), media_type=\"image/jpeg\")\n",
)
replace(
    "lecturesift/app.py",
    "    data = _owned_job(job_id, user)\n    if data.get(\"status\") != \"done\":\n        raise HTTPException(409, detail={\"code\": \"LS-JOB-02\", \"message\": \"Ders analizi henüz tamamlanmadı.\"})\n    if Path(filename).name != filename:\n",
    "    data = _owned_job(job_id, user)\n    if data.get(\"status\") != \"done\":\n        raise HTTPException(409, detail={\"code\": \"LS-JOB-02\", \"message\": \"Ders analizi henüz tamamlanmadı.\"})\n    _download_access_or_402(user[\"id\"], job_id)\n    if Path(filename).name != filename:\n",
    count=1,
)
replace(
    "lecturesift/app.py",
    "    data = _owned_job(job_id, user)\n    if data.get(\"status\") != \"done\":\n        raise HTTPException(409, detail={\"code\": \"LS-JOB-02\", \"message\": \"Ders analizi henüz tamamlanmadı.\"})\n    return FileResponse(\n",
    "    data = _owned_job(job_id, user)\n    if data.get(\"status\") != \"done\":\n        raise HTTPException(409, detail={\"code\": \"LS-JOB-02\", \"message\": \"Ders analizi henüz tamamlanmadı.\"})\n    _download_access_or_402(user[\"id\"], job_id)\n    return FileResponse(\n",
)
replace(
    "lecturesift/app.py",
    "    translate_transcript: bool = Form(True),\n    slides_offset_seconds: float = Form(0),\n",
    "    translate_transcript: bool = Form(True),\n    translate_visual_text: bool = Form(False),\n    slides_offset_seconds: float = Form(0),\n",
    count=2,
)
replace(
    "lecturesift/app.py",
    "        translate_transcript,\n        slides_offset_seconds,\n",
    "        translate_transcript,\n        translate_visual_text,\n        slides_offset_seconds,\n",
    count=2,
)
replace(
    "lecturesift/app.py",
    "        validate_job_features(\n            billing_user[\"id\"],\n            quiz_count=options[\"quiz_count\"],\n            flashcard_count=options[\"flashcard_count\"],\n            output_formats=options[\"output_formats\"],\n            summary_style=options[\"summary_style\"],\n        )\n",
    "        validate_job_features(\n            billing_user[\"id\"],\n            quiz_count=options[\"quiz_count\"],\n            flashcard_count=options[\"flashcard_count\"],\n            output_formats=options[\"output_formats\"],\n            summary_style=options[\"summary_style\"],\n        )\n        if options.get(\"translate_visual_text\"):\n            _visual_translation_or_402(billing_user[\"id\"])\n        if options.get(\"job_type\") in {\"audio_export\", \"download_video\"}:\n            _download_access_or_402(billing_user[\"id\"])\n",
    count=2,
)
replace(
    "lecturesift/app.py",
    "    JOBS.create(\n        job_id,\n        job_dir,\n        options,\n        source_type=source_type,\n        source_layout=layout,\n        file_size_bytes=total,\n        audio_file_sizes=audio_sizes,\n        visual_file_sizes=visual_sizes,\n        source_file_count=len(audio_paths) + len(visual_paths),\n    )\n    threading.Thread(\n",
    "    JOBS.create(\n        job_id,\n        job_dir,\n        options,\n        source_type=source_type,\n        source_layout=layout,\n        file_size_bytes=total,\n        audio_file_sizes=audio_sizes,\n        visual_file_sizes=visual_sizes,\n        source_file_count=len(audio_paths) + len(visual_paths),\n    )\n    register_job_history(\n        billing_user[\"id\"], job_id, source_type=source_type,\n        job_type=options[\"job_type\"], output_language=options[\"output_language\"],\n        visual_translation_requested=bool(options.get(\"translate_visual_text\")),\n    )\n    threading.Thread(\n",
)
replace(
    "lecturesift/app.py",
    "    JOBS.create(job_id, job_dir, options, source_type=\"url\", source_url=url)\n    JOBS.update(job_id, status=\"working\", percent=3, stage=\"url_download\")\n",
    "    JOBS.create(job_id, job_dir, options, source_type=\"url\", source_url=url)\n    register_job_history(\n        billing_user[\"id\"], job_id, source_type=\"url\",\n        job_type=options[\"job_type\"], output_language=options[\"output_language\"],\n        visual_translation_requested=bool(options.get(\"translate_visual_text\")),\n    )\n    JOBS.update(job_id, status=\"working\", percent=3, stage=\"url_download\")\n",
)
replace(
    "lecturesift/app.py",
    "            JOBS.update(\n                job_id,\n                status=\"error\",\n                percent=0,\n                stage=\"error\",\n                error_code=normalized.code,\n                error=normalized.user_message,\n                technical_error=normalized.technical_message,\n                elapsed_seconds=round(time.time() - started, 1),\n            )\n",
    "            JOBS.update(\n                job_id,\n                status=\"error\",\n                percent=0,\n                stage=\"error\",\n                error_code=normalized.code,\n                error=normalized.user_message,\n                technical_error=normalized.technical_message,\n                elapsed_seconds=round(time.time() - started, 1),\n            )\n            complete_job_history(job_id, status=\"error\")\n",
)

# ---- Pipeline: timestamped transcript sections + translated slide images --
replace(
    "lecturesift/pipeline.py",
    "from .slides import extract_slides\n",
    "from .slides import extract_slides\nfrom .visual_translation import translate_slide_images\n",
)
replace(
    "lecturesift/pipeline.py",
    "def _audio_pipeline(job_id: str, video_paths: list[Path], job_dir: Path, options: dict) -> tuple[str, str]:\n    audio_chunks: list[Path] = []\n",
    "def _audio_pipeline(job_id: str, video_paths: list[Path], job_dir: Path, options: dict) -> tuple[str, str, list[dict]]:\n    audio_chunks: list[Path] = []\n",
)
replace(
    "lecturesift/pipeline.py",
    "        return \"\", \"\"\n\n    transcripts: list[str] = []\n",
    "        return \"\", \"\", []\n\n    transcripts: list[str] = []\n    transcript_segments: list[dict] = []\n    timeline_cursor = 0.0\n",
)
replace(
    "lecturesift/pipeline.py",
    "        text = transcribe(audio_path, options[\"source_language\"])\n        if text.strip():\n            transcripts.append(text.strip())\n",
    "        text = transcribe(audio_path, options[\"source_language\"])\n        duration = _source_duration_seconds([audio_path])\n        if text.strip():\n            transcripts.append(text.strip())\n            transcript_segments.append({\n                \"index\": len(transcript_segments) + 1,\n                \"start_second\": round(timeline_cursor, 1),\n                \"end_second\": round(timeline_cursor + duration, 1),\n                \"text\": text.strip(),\n            })\n        timeline_cursor += duration\n",
)
replace(
    "lecturesift/pipeline.py",
    "    return original, translated\n",
    "    return original, translated, transcript_segments\n",
)
replace(
    "lecturesift/pipeline.py",
    "            original_transcript, translated_transcript = audio_future.result()\n\n        diagnostics[\"source_mode\"] = source_mode\n\n        JOBS.update(job_id, percent=73, stage=\"study_pack\")\n",
    "            original_transcript, translated_transcript, transcript_segments = audio_future.result()\n\n        diagnostics[\"source_mode\"] = source_mode\n        if options.get(\"translate_visual_text\") and slides:\n            JOBS.update(job_id, percent=71, stage=\"visual_translation\")\n            slides, visual_diagnostics = translate_slide_images(\n                slides, slides_dir, options[\"output_language\"],\n                progress=lambda percent, stage: JOBS.update(job_id, percent=min(72, 70 + percent * 0.02), stage=stage),\n            )\n            diagnostics[\"visual_translation\"] = visual_diagnostics\n        else:\n            diagnostics[\"visual_translation\"] = {\"requested\": False, \"translated_slides\": 0, \"translated_regions\": 0}\n\n        JOBS.update(job_id, percent=73, stage=\"study_pack\")\n",
)
replace(
    "lecturesift/pipeline.py",
    "            options[\"flashcard_count\"],\n        )\n",
    "            options[\"flashcard_count\"],\n            transcript_segments=transcript_segments,\n        )\n",
    count=1,
)
replace(
    "lecturesift/pipeline.py",
    '            "transcript": translated_transcript or original_transcript,\n            **study_pack,\n',
    '            "transcript": translated_transcript or original_transcript,\n            "transcript_segments": transcript_segments,\n            **study_pack,\n',
)

# Hidden guest plan must never unlock downloads or visual translation.
replace(
    "lecturesift/rollout_service.py",
    "        (\"short\", \"standard\"),\n        1,\n    ),\n",
    "        (\"short\", \"standard\"),\n        1,\n        download_enabled=False,\n        visual_translation=False,\n        output_retention_days=1,\n    ),\n",
)

print("Product integration patch applied.")
