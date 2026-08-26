import json
import threading
import time
import traceback
import uuid
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from .config import APP_VERSION, MAX_VIDEO_BYTES, OPENAI_API_KEY, VIDEO_EXTENSIONS, WORK_DIR
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
) -> dict:
    return {
        "source_language": source_language or "auto",
        "output_language": output_language or "tr",
        "summary_style": summary_style or "standard",
        "quiz_count": max(3, min(int(quiz_count), 30)),
        "flashcard_count": max(5, min(int(flashcard_count), 60)),
        "translate_transcript": bool(translate_transcript),
    }


def _job_path(job_id: str) -> Path:
    path = WORK_DIR / job_id
    path.mkdir(parents=True, exist_ok=False)
    return path


def _raise_public(error: LectureSiftError) -> None:
    raise HTTPException(status_code=error.status_code, detail=error.public())


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
        "pdf_txt_exports": True,
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
        filename="LectureSift_Study_Pack_V4.zip",
    )


@app.post("/jobs")
async def create_job(
    file: UploadFile = File(...),
    source_language: str = Form("auto"),
    output_language: str = Form("tr"),
    summary_style: str = Form("standard"),
    quiz_count: int = Form(10),
    flashcard_count: int = Form(20),
    translate_transcript: bool = Form(True),
) -> dict:
    extension = Path(file.filename or "video.mp4").suffix.lower()
    if extension not in VIDEO_EXTENSIONS:
        _raise_public(LectureSiftError("LS-UPLOAD-01", "Bu video biçimi desteklenmiyor. MP4, MOV, MKV veya WebM kullan."))

    JOBS.cleanup_expired()
    job_id = str(uuid.uuid4())
    job_dir = _job_path(job_id)
    video_path = job_dir / f"input{extension}"
    total = 0
    try:
        with open(video_path, "wb") as output:
            while chunk := await file.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_VIDEO_BYTES:
                    _raise_public(LectureSiftError("LS-UPLOAD-02", "Video izin verilen dosya boyutunu aşıyor.", status_code=413))
                output.write(chunk)
    except HTTPException:
        video_path.unlink(missing_ok=True)
        raise

    options = _options(
        source_language,
        output_language,
        summary_style,
        quiz_count,
        flashcard_count,
        translate_transcript,
    )
    JOBS.create(job_id, job_dir, options, source_type="upload", file_size_bytes=total)
    threading.Thread(target=process_job, args=(job_id, video_path, options), daemon=True).start()
    return {"job_id": job_id, "status": "queued", "version": APP_VERSION}


@app.post("/jobs/url")
def create_url_job(
    video_url: str = Form(...),
    source_language: str = Form("auto"),
    output_language: str = Form("tr"),
    summary_style: str = Form("standard"),
    quiz_count: int = Form(10),
    flashcard_count: int = Form(20),
    translate_transcript: bool = Form(True),
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
