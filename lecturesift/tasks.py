"""Retryable Celery tasks for long LectureSift media jobs."""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

import cv2

from .commerce import attach_remote_output, complete_job_history
from .config import WORK_DIR
from .jobs import JOBS
from .pipeline_enhancements import install_pipeline_enhancements
from .queue import celery_app
from .rollout_service import is_guest_user, record_runtime, reserve_guest_job
from .storage import STORAGE

install_pipeline_enhancements()
from .pipeline import process_job  # noqa: E402


def _duration_seconds(paths: list[Path]) -> float:
    total = 0.0
    for path in paths:
        capture = cv2.VideoCapture(str(path))
        try:
            fps = float(capture.get(cv2.CAP_PROP_FPS) or 0)
            frames = float(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            if fps > 0 and frames > 0:
                total += frames / fps
        finally:
            capture.release()
    return total


def _download_sources(job_id: str, role: str, keys: list[str], job_dir: Path) -> list[Path]:
    paths: list[Path] = []
    for index, key in enumerate(keys, 1):
        suffix = Path(key).suffix or ".bin"
        destination = job_dir / "sources" / f"{role}_{index:03d}{suffix}"
        STORAGE.download_file(key, destination)
        paths.append(destination)
    return paths


def _title(job_dir: Path) -> str:
    try:
        body = json.loads((job_dir / "result.json").read_text(encoding="utf-8"))
        return str(body.get("title") or "LectureSift dersi")
    except Exception:
        return "LectureSift dersi"


@celery_app.task(bind=True, max_retries=4, name="lecturesift.process_uploaded_job")
def process_uploaded_job(self, job_id: str, audio_keys: list[str], options: dict, visual_keys: list[str] | None = None) -> dict:
    data = JOBS.get(job_id)
    if not data:
        STORAGE.delete_transient_job(job_id)
        return {"job_id": job_id, "status": "missing"}
    if data.get("status") == "done" and data.get("remote_download_key"):
        STORAGE.delete_transient_job(job_id)
        return {"job_id": job_id, "status": "done"}
    job_dir = WORK_DIR / job_id
    try:
        shutil.rmtree(job_dir, ignore_errors=True)
        job_dir.mkdir(parents=True, exist_ok=True)
        JOBS.update(job_id, job_dir=str(job_dir), status="working", stage="worker_download", worker_state="downloading")
        audio_paths = _download_sources(job_id, "audio", list(audio_keys), job_dir)
        visual_paths = _download_sources(job_id, "visual", list(visual_keys or []), job_dir)
        duration = max(_duration_seconds(audio_paths), _duration_seconds(visual_paths) if visual_paths else 0.0)
        media_minutes = max(0.1, duration / 60.0)
        user_id = str(options.get("billing_user_id", ""))
        if user_id and is_guest_user(user_id):
            reserve_guest_job(user_id, job_id, media_minutes)
        started = time.time()
        source_size = sum(path.stat().st_size for path in audio_paths + visual_paths if path.exists())
        JOBS.update(job_id, worker_state="processing", media_minutes=round(media_minutes, 2), file_size_bytes=source_size)
        process_job(job_id, audio_paths, options, visual_paths or None)
        finished = JOBS.get(job_id) or {}
        if finished.get("status") != "done":
            complete_job_history(job_id, status=str(finished.get("status") or "error"), media_minutes=media_minutes)
            return {"job_id": job_id, "status": finished.get("status", "error")}
        zip_path = Path(str(finished.get("result_path") or ""))
        remote = STORAGE.publish_output(job_id, zip_path)
        elapsed = float(finished.get("elapsed_seconds") or (time.time() - started))
        record_runtime(job_id, media_minutes, elapsed, source_size)
        JOBS.update(job_id, worker_state="done", **remote)
        attach_remote_output(job_id, str(remote.get("remote_download_key") or ""), int(remote.get("remote_output_size_bytes") or zip_path.stat().st_size))
        complete_job_history(job_id, status="done", title=_title(job_dir), media_minutes=media_minutes, remote_download_key=str(remote.get("remote_download_key") or ""), output_size_bytes=int(remote.get("remote_output_size_bytes") or zip_path.stat().st_size))
        STORAGE.delete_transient_job(job_id)
        shutil.rmtree(job_dir / "sources", ignore_errors=True)
        return {"job_id": job_id, "status": "done", **remote}
    except Exception as exc:
        retries = int(getattr(self.request, "retries", 0))
        final = retries >= self.max_retries
        JOBS.update(job_id, status="error" if final else "queued", stage="error" if final else "worker_retry", worker_state="failed" if final else "retrying", error="İşlem worker üzerinde tamamlanamadı." if final else None, technical_error=str(exc))
        complete_job_history(job_id, status="error" if final else "queued")
        if final:
            STORAGE.delete_transient_job(job_id)
            raise
        raise self.retry(exc=exc, countdown=min(300, 15 * (2 ** retries)))
