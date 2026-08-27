import json
import shutil
import threading
import time
import traceback
import uuid
from pathlib import Path

import cv2
from fastapi import FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from .config import APP_VERSION, MAX_SOURCE_FILES, MAX_VIDEO_BYTES, OPENAI_API_KEY, VIDEO_EXTENSIONS, WORK_DIR
from .errors import LectureSiftError, normalize_error
from .jobs import JOBS
from .media import download_remote_video, validate_remote_url
from .pipeline import process_job
from .platform import PLATFORM, PUBLIC_PLATFORM_CONFIG


app = FastAPI(title="LectureSift Backend")
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


def _platform_call(function, *args, status_code: int = 400, **kwargs):
    try:
        return function(*args, **kwargs)
    except PermissionError as exc:
        raise HTTPException(403, detail={"code": "LS-AUTH-03", "message": str(exc)}) from exc
    except ValueError as exc:
        raise HTTPException(status_code, detail={"code": "LS-PLATFORM-01", "message": str(exc)}) from exc


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
                _raise_public(LectureSiftError("LS-UPLOAD-02", "Yüklenen video dosyalarının toplam boyutu izin verilen sınırı aşıyor.", status_code=413))
            output.write(chunk)
    return total


def _validate_upload_list(files: list[UploadFile], label: str) -> list[str]:
    if not files:
        _raise_public(LectureSiftError("LS-UPLOAD-03", f"{label} için en az bir video ekle."))
    if len(files) > MAX_SOURCE_FILES:
        _raise_public(LectureSiftError("LS-UPLOAD-04", f"{label} için en fazla {MAX_SOURCE_FILES} video eklenebilir."))
    return [_upload_extension(file) for file in files]


async def _save_upload_list(files: list[UploadFile], extensions: list[str], job_dir: Path, role: str, bytes_used: int) -> tuple[list[Path], int, list[int]]:
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


def _duration_minutes(paths: list[Path]) -> float:
    seconds = 0.0
    for path in paths:
        capture = cv2.VideoCapture(str(path))
        try:
            fps = float(capture.get(cv2.CAP_PROP_FPS) or 0)
            frames = float(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            if fps > 0 and frames > 0:
                seconds += frames / fps
        finally:
            capture.release()
    return round(seconds / 60.0, 2)


def _guest_key(request: Request, guest_id: str) -> str:
    host = request.client.host if request.client else "unknown"
    agent = request.headers.get("user-agent", "")[:120]
    return f"{host}|{agent}|{guest_id}"


@app.get("/")
def root() -> dict:
    return {"ok": True, "service": "LectureSift Backend", "frontend": "https://clever-horse-22b1a8.netlify.app/"}


@app.get("/health")
def health() -> dict:
    return {
        "ok": True,
        "version": APP_VERSION,
        "openai_key": bool(OPENAI_API_KEY),
        "study_pack": True,
        "async_jobs": True,
        "dual_source_upload": True,
        "multi_source_upload": True,
        "output_formats": ["pdf", "docx", "txt"],
        "audio_export": True,
        "url_video_download": True,
        "accounts": True,
        "bank_transfer": True,
        "admin_orders": True,
        "guest_trial": True,
        "instagram_reward": True,
        "eta": True,
    }


@app.get("/platform/config")
def platform_config() -> dict:
    return PUBLIC_PLATFORM_CONFIG


@app.get("/billing/plans")
def billing_plans(currency: str = "TRY") -> dict:
    return PLATFORM.prices(currency)


@app.post("/auth/request-code")
def auth_request_code(email: str = Form(...), purpose: str = Form("login"), session_token: str = Form("")) -> dict:
    return _platform_call(PLATFORM.request_code, email, purpose, session_token)


@app.post("/auth/verify-code")
def auth_verify_code(email: str = Form(...), code: str = Form(...), name: str = Form("")) -> dict:
    return _platform_call(PLATFORM.verify_code, email, code, name)


@app.get("/account/me")
def account_me(session_token: str = Header("", alias="X-LectureSift-Token")) -> dict:
    return _platform_call(PLATFORM.me, session_token, status_code=401)


@app.post("/account/profile")
def account_profile(
    name: str = Form(""),
    preferred_language: str = Form("tr"),
    session_token: str = Header("", alias="X-LectureSift-Token"),
) -> dict:
    return _platform_call(PLATFORM.update_profile, session_token, name, preferred_language, status_code=401)


@app.post("/billing/bank-transfer")
def create_bank_transfer(
    plan_id: str = Form(...),
    cycle: str = Form("monthly"),
    currency: str = Form("TRY"),
    session_token: str = Header("", alias="X-LectureSift-Token"),
) -> dict:
    return _platform_call(PLATFORM.create_order, session_token, plan_id, cycle, currency, status_code=401)


@app.get("/billing/orders")
def billing_orders(session_token: str = Header("", alias="X-LectureSift-Token")) -> list[dict]:
    return _platform_call(PLATFORM.list_orders, session_token, status_code=401)


@app.post("/rewards/instagram/claim")
def instagram_claim(handle: str = Form(...), session_token: str = Header("", alias="X-LectureSift-Token")) -> dict:
    return _platform_call(PLATFORM.claim_instagram, session_token, handle, status_code=401)


@app.get("/admin/orders")
def admin_orders(status: str = "pending_transfer", admin_token: str = Header("", alias="X-Admin-Token")) -> list[dict]:
    return _platform_call(PLATFORM.admin_orders, admin_token, status, status_code=403)


@app.post("/admin/orders/{order_no}/decision")
def admin_order_decision(order_no: str, approve: bool = Form(...), admin_token: str = Header("", alias="X-Admin-Token")) -> dict:
    return _platform_call(PLATFORM.decide_order, admin_token, order_no, approve, status_code=403)


@app.get("/admin/rewards")
def admin_rewards(admin_token: str = Header("", alias="X-Admin-Token")) -> list[dict]:
    return _platform_call(PLATFORM.admin_rewards, admin_token, status_code=403)


@app.post("/admin/rewards/{reward_id}/decision")
def admin_reward_decision(reward_id: str, approve: bool = Form(...), admin_token: str = Header("", alias="X-Admin-Token")) -> dict:
    return _platform_call(PLATFORM.decide_reward, admin_token, reward_id, approve, status_code=403)


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
    return FileResponse(data["result_path"], media_type="application/zip", filename="LectureSift_Paketi.zip")


@app.post("/jobs")
async def create_job(
    request: Request,
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
    session_token: str = Form(""),
    guest_id: str = Form(""),
) -> dict:
    upload_started = time.time()
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
            audio_paths, total, audio_sizes = await _save_upload_list(audio_uploads, audio_extensions, job_dir, "audio", total)
            visual_paths, total, visual_sizes = await _save_upload_list(visual_uploads, visual_extensions, job_dir, "visual", total)
        else:
            audio_paths, total, audio_sizes = await _save_upload_list(classic_uploads, classic_extensions, job_dir, "part", total)
            visual_paths, visual_sizes = [], []
    except Exception:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise

    media_minutes = _duration_minutes(audio_paths)
    if media_minutes <= 0:
        media_minutes = max(0.5, total / (8 * 1024 * 1024))
    try:
        usage = PLATFORM.authorize_minutes(session_token, _guest_key(request, guest_id), media_minutes)
    except ValueError as exc:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise HTTPException(402, detail={"code": "LS-USAGE-01", "message": str(exc)}) from exc

    upload_elapsed = max(time.time() - upload_started, 0.01)
    upload_bps = total / upload_elapsed
    eta_seconds = PLATFORM.eta_seconds(media_minutes, total, upload_bps)
    options = _options(source_language, output_language, summary_style, quiz_count, flashcard_count, translate_transcript, slides_offset_seconds, output_formats, job_type)
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
        media_minutes=media_minutes,
        upload_bps=round(upload_bps),
        eta_seconds=eta_seconds,
        usage_mode=usage.get("mode"),
    )
    threading.Thread(target=process_job, args=(job_id, audio_paths, options, visual_paths if layout == "separate" else None), daemon=True).start()
    return {"job_id": job_id, "status": "queued", "source_layout": layout, "media_minutes": media_minutes, "eta_seconds": eta_seconds, "usage": usage}


@app.post("/jobs/url")
def create_url_job(
    request: Request,
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
    session_token: str = Form(""),
    guest_id: str = Form(""),
) -> dict:
    try:
        url = validate_remote_url(video_url)
    except LectureSiftError as exc:
        _raise_public(exc)

    JOBS.cleanup_expired()
    job_id = str(uuid.uuid4())
    job_dir = _job_path(job_id)
    options = _options(source_language, output_language, summary_style, quiz_count, flashcard_count, translate_transcript, slides_offset_seconds, output_formats, job_type)
    JOBS.create(job_id, job_dir, options, source_type="url", source_url=url, eta_seconds=0)
    JOBS.update(job_id, status="working", percent=3, stage="url_download")

    def worker() -> None:
        started = time.time()
        try:
            video_path = download_remote_video(url, job_dir)
            size = video_path.stat().st_size if video_path.exists() else 0
            media_minutes = _duration_minutes([video_path]) or max(0.5, size / (8 * 1024 * 1024))
            usage = PLATFORM.authorize_minutes(session_token, _guest_key(request, guest_id), media_minutes)
            eta_seconds = PLATFORM.eta_seconds(media_minutes, size, 0)
            JOBS.update(job_id, percent=8, stage="parallel_analysis", file_size_bytes=size, media_minutes=media_minutes, eta_seconds=eta_seconds, usage_mode=usage.get("mode"))
            process_job(job_id, video_path, options)
        except ValueError as exc:
            JOBS.update(job_id, status="error", percent=0, stage="error", error_code="LS-USAGE-01", error=str(exc), elapsed_seconds=round(time.time() - started, 1))
        except Exception as exc:
            normalized = normalize_error(exc)
            print(f"URL ERROR [{normalized.code}]: {normalized.technical_message}", flush=True)
            traceback.print_exc()
            JOBS.update(job_id, status="error", percent=0, stage="error", error_code=normalized.code, error=normalized.user_message, technical_error=normalized.technical_message, elapsed_seconds=round(time.time() - started, 1))

    threading.Thread(target=worker, daemon=True).start()
    return {"job_id": job_id, "status": "working"}
