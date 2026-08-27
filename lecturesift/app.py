import json
import shutil
import threading
import time
import traceback
import uuid
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from .config import APP_VERSION, MAX_SOURCE_FILES, MAX_VIDEO_BYTES, OPENAI_API_KEY, VIDEO_EXTENSIONS, WORK_DIR
from .errors import LectureSiftError, normalize_error
from .jobs import JOBS
from .media import download_remote_video, validate_remote_url
from .pipeline import process_job


app = FastAPI(title=f"LectureSift Backend V{APP_VERSION}")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _options(
    source_language: str,
    output_language: str,
    summary_style: str,
    quiz_count: int,
    flashcard_count: int,
    translate_transcript: bool,
    slides_offset_seconds: float = 0,
    output_formats: str = "pdf",
    job_type: str = "study_pack",
) -> dict:
    source = source_language or "auto"
    output = output_language or "tr"
    translation_enabled = bool(translate_transcript) and not (source != "auto" and source == output)
    formats = [value for value in dict.fromkeys(output_formats.lower().replace(" ", "").split(",")) if value in {"pdf", "docx", "txt"}]
    if not formats:
        formats = ["pdf"]
    selected_job_type = job_type if job_type in {"study_pack", "audio_export", "download_video"} else "study_pack"
    return {
        "source_language": source,
        "output_language": output,
        "summary_style": summary_style or "standard",
        "quiz_count": max(3, min(int(quiz_count), 30)),
        "flashcard_count": max(5, min(int(flashcard_count), 60)),
        "translate_transcript": translation_enabled,
        "slides_offset_seconds": max(-3600.0, min(float(slides_offset_seconds or 0), 3600.0)),
        "output_formats": formats,
        "job_type": selected_job_type,
    }


def _job_path(job_id: str) -> Path:
    path = WORK_DIR / job_id
    path.mkdir(parents=True, exist_ok=False)
    return path


def _raise_public(error: LectureSiftError) -> None:
    raise HTTPException(status_code=error.status_code, detail=error.public())


def _upload_extension(file: UploadFile) -> str:
    extension = Path(file.filename or "video.mp4").suffix.lower()
    if extension not in VIDEO_EXTENSIONS:
        _raise_public(LectureSiftError("LS-UPLOAD-01", "Bu video biçimi desteklenmiyor. MP4, MOV, MKV veya WebM kullan."))
    return extension


async def _save_upload(file: UploadFile, destination: Path, bytes_used: int = 0) -> int:
    total = bytes_used
    with open(destination, "wb") as output:
        while chunk := await file.read(1024 * 1024):
            total += len(chunk)
            if total > MAX_VIDEO_BYTES:
                _raise_public(
                    LectureSiftError(
                        "LS-UPLOAD-02",
                        "Yüklenen video dosyalarının toplam boyutu izin verilen sınırı aşıyor.",
                        status_code=413,
                    )
                )
            output.write(chunk)
    return total


def _validate_upload_list(files: list[UploadFile], label: str) -> list[str]:
    if not files:
        _raise_public(LectureSiftError("LS-UPLOAD-03", f"{label} için en az bir video ekle."))
    if len(files) > MAX_SOURCE_FILES:
        _raise_public(LectureSiftError("LS-UPLOAD-04", f"{label} için en fazla {MAX_SOURCE_FILES} video eklenebilir."))
    return [_upload_extension(file) for file in files]


async def _save_upload_list(
    files: list[UploadFile],
    extensions: list[str],
    job_dir: Path,
    role: str,
    bytes_used: int,
) -> tuple[list[Path], int, list[int]]:
    paths: list[Path] = []
    sizes: list[int] = []
    total = bytes_used
    for index, (file, extension) in enumerate(zip(files, extensions), 1):
        destination = job_dir / f"{role}_{index:03d}{extension}"
        before = total
        total = await _save_upload(file, destination, total)
        paths.append(destination)
        sizes.append(total - before)
    return paths, total, sizes


@app.get("/")
def root() -> dict:
    return {
        "ok": True,
        "service": f"LectureSift Backend V{APP_VERSION}",
        "frontend": "https://clever-horse-22b1a8.netlify.app/",
    }


@app.get("/health")
def health() -> dict:
    return {
        "ok": True,
        "version": APP_VERSION,
        "openai_key": bool(OPENAI_API_KEY),
        "slide_engine": "v4-layout-persistence",
        "study_pack": True,
        "async_jobs": True,
        "dual_source_upload": True,
        "multi_source_upload": True,
        "output_formats": ["pdf", "docx", "txt"],
        "audio_export": True,
        "url_video_download": True,
    }


@app.get("/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    data = JOBS.public(job_id)
    if not data:
        raise HTTPException(404, detail={"code": "LS-JOB-01", "message": "İşlem kaydı bulunamadı veya süresi doldu."})
    return data


@app.get("/jobs/{job_id}/result")
def get_result(job_id: str) -> dict:
    data = JOBS.get(job_id)
    if not data:
        raise HTTPException(404, detail={"code": "LS-JOB-01", "message": "İşlem kaydı bulunamadı veya süresi doldu."})
    if data.get("status") != "done":
        raise HTTPException(409, detail={"code": "LS-JOB-02", "message": "Ders analizi henüz tamamlanmadı."})
    path = Path(data["job_dir"]) / "result.json"
    if not path.exists():
        raise HTTPException(404, detail={"code": "LS-JOB-03", "message": "Sonuç dosyası bulunamadı."})
    return json.loads(path.read_text(encoding="utf-8"))


@app.get("/jobs/{job_id}/slide/{filename}")
def get_slide(job_id: str, filename: str) -> FileResponse:
    data = JOBS.get(job_id)
    if not data:
        raise HTTPException(404, detail={"code": "LS-JOB-01", "message": "İşlem kaydı bulunamadı."})
    if Path(filename).name != filename:
        raise HTTPException(400, detail={"code": "LS-FILE-01", "message": "Geçersiz dosya adı."})
    path = Path(data["job_dir"]) / "slides" / filename
    if not path.exists():
        raise HTTPException(404, detail={"code": "LS-FILE-02", "message": "Slayt görseli bulunamadı."})
    return FileResponse(str(path), media_type="image/jpeg")


@app.get("/jobs/{job_id}/artifact/{filename}")
def get_artifact(job_id: str, filename: str) -> FileResponse:
    data = JOBS.get(job_id)
    if not data:
        raise HTTPException(404, detail={"code": "LS-JOB-01", "message": "İşlem kaydı bulunamadı."})
    if data.get("status") != "done":
        raise HTTPException(409, detail={"code": "LS-JOB-02", "message": "Ders analizi henüz tamamlanmadı."})
    if Path(filename).name != filename:
        raise HTTPException(400, detail={"code": "LS-FILE-01", "message": "Geçersiz dosya adı."})
    path = Path(data["job_dir"]) / "package" / filename
    if not path.exists() or not path.is_file():
        raise HTTPException(404, detail={"code": "LS-FILE-02", "message": "Çıktı dosyası bulunamadı."})
    return FileResponse(str(path), filename=filename)


@app.get("/jobs/{job_id}/download")
def download(job_id: str) -> FileResponse:
    data = JOBS.get(job_id)
    if not data:
        raise HTTPException(404, detail={"code": "LS-JOB-01", "message": "İşlem kaydı bulunamadı."})
    if data.get("status") != "done":
        raise HTTPException(409, detail={"code": "LS-JOB-02", "message": "Ders analizi henüz tamamlanmadı."})
    return FileResponse(
        data["result_path"],
        media_type="application/zip",
        filename="LectureSift_Paketi_V4.1.zip",
    )


@app.post("/jobs")
async def create_job(
    file: UploadFile | None = File(None),
    slides_file: UploadFile | None = File(None),
    files: list[UploadFile] | None = File(None),
    audio_files: list[UploadFile] | None = File(None),
    visual_files: list[UploadFile] | None = File(None),
    source_layout: str = Form("classic"),
    source_language: str = Form("auto"),
    output_language: str = Form("tr"),
    summary_style: str = Form("standard"),
    quiz_count: int = Form(10),
    flashcard_count: int = Form(20),
    translate_transcript: bool = Form(True),
    slides_offset_seconds: float = Form(0),
    output_formats: str = Form("pdf"),
    job_type: str = Form("study_pack"),
) -> dict:
    layout = "separate" if source_layout == "separate" or slides_file or visual_files else "classic"
    if layout == "separate":
        audio_uploads = list(audio_files or ([] if file is None else [file]))
        visual_uploads = list(visual_files or ([] if slides_file is None else [slides_file]))
        audio_extensions = _validate_upload_list(audio_uploads, "Ses kaynağı")
        visual_extensions = _validate_upload_list(visual_uploads, "Görüntü/slayt kaynağı")
    else:
        classic_uploads = list(files or ([] if file is None else [file]))
        classic_extensions = _validate_upload_list(classic_uploads, "Ders kaynağı")

    JOBS.cleanup_expired()
    job_id = str(uuid.uuid4())
    job_dir = _job_path(job_id)
    total = 0
    try:
        if layout == "separate":
            audio_paths, total, audio_sizes = await _save_upload_list(
                audio_uploads, audio_extensions, job_dir, "audio", total
            )
            visual_paths, total, visual_sizes = await _save_upload_list(
                visual_uploads, visual_extensions, job_dir, "visual", total
            )
        else:
            audio_paths, total, audio_sizes = await _save_upload_list(
                classic_uploads, classic_extensions, job_dir, "part", total
            )
            visual_paths = []
            visual_sizes = []
    except Exception:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise

    options = _options(
        source_language,
        output_language,
        summary_style,
        quiz_count,
        flashcard_count,
        translate_transcript,
        slides_offset_seconds,
        output_formats,
        job_type,
    )
    source_type = "upload_separate" if layout == "separate" else ("upload_multi" if len(audio_paths) > 1 else "upload")
    JOBS.create(
        job_id,
        job_dir,
        options,
        source_type=source_type,
        source_layout=layout,
        file_size_bytes=total,
        audio_file_sizes=audio_sizes,
        visual_file_sizes=visual_sizes,
        source_file_count=len(audio_paths) + len(visual_paths),
    )
    threading.Thread(
        target=process_job,
        args=(job_id, audio_paths, options, visual_paths if layout == "separate" else None),
        daemon=True,
    ).start()
    return {"job_id": job_id, "status": "queued", "version": APP_VERSION, "source_layout": layout}


@app.post("/jobs/url")
def create_url_job(
    video_url: str = Form(...),
    source_language: str = Form("auto"),
    output_language: str = Form("tr"),
    summary_style: str = Form("standard"),
    quiz_count: int = Form(10),
    flashcard_count: int = Form(20),
    translate_transcript: bool = Form(True),
    slides_offset_seconds: float = Form(0),
    output_formats: str = Form("pdf"),
    job_type: str = Form("study_pack"),
) -> dict:
    try:
        url = validate_remote_url(video_url)
    except LectureSiftError as exc:
        _raise_public(exc)

    JOBS.cleanup_expired()
    job_id = str(uuid.uuid4())
    job_dir = _job_path(job_id)
    options = _options(
        source_language,
        output_language,
        summary_style,
        quiz_count,
        flashcard_count,
        translate_transcript,
        slides_offset_seconds,
        output_formats,
        job_type,
    )
    JOBS.create(job_id, job_dir, options, source_type="url", source_url=url)
    JOBS.update(job_id, status="working", percent=3, stage="url_download")

    def worker() -> None:
        started = time.time()
        try:
            video_path = download_remote_video(url, job_dir)
            JOBS.update(job_id, percent=8, stage="parallel_analysis")
            process_job(job_id, video_path, options)
        except Exception as exc:
            normalized = normalize_error(exc)
            print(f"URL ERROR [{normalized.code}]: {normalized.technical_message}", flush=True)
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

    threading.Thread(target=worker, daemon=True).start()
    return {"job_id": job_id, "status": "working", "version": APP_VERSION}
