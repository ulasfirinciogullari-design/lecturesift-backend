"""Runtime dispatcher separating the web API from long media processing."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import cv2

from .commerce import attach_remote_output, complete_job_history
from .config import CELERY_BROKER_URL
from .jobs import JOBS
from .pipeline_enhancements import install_pipeline_enhancements
from .rollout_service import BillingError, estimate_eta_seconds, is_guest_user, record_runtime, reserve_guest_job
from .storage import STORAGE


_INSTALLED = False


def _paths(value) -> list[Path]:
    if value is None:
        return []
    if isinstance(value, Path):
        return [value]
    return [Path(item) for item in value]


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


def _result_title(job_dir: Path) -> str:
    try:
        value = json.loads((job_dir / "result.json").read_text(encoding="utf-8"))
        return str(value.get("title") or "LectureSift dersi")
    except Exception:
        return "LectureSift dersi"


def _publish_if_configured(job_id: str, job_dir: Path) -> dict:
    data = JOBS.get(job_id) or {}
    if data.get("status") != "done":
        return {}
    zip_path = Path(str(data.get("result_path") or ""))
    remote: dict = {}
    if STORAGE.remote and zip_path.exists():
        remote = STORAGE.publish_output(job_id, zip_path)
        JOBS.update(job_id, **remote)
        attach_remote_output(job_id, str(remote.get("remote_download_key") or ""), int(remote.get("remote_output_size_bytes") or zip_path.stat().st_size))
    complete_job_history(
        job_id,
        status="done",
        title=_result_title(job_dir),
        media_minutes=float(data.get("media_minutes") or 0),
        remote_download_key=str(remote.get("remote_download_key") or ""),
        output_size_bytes=int(remote.get("remote_output_size_bytes")) if remote.get("remote_output_size_bytes") is not None else (zip_path.stat().st_size if zip_path.exists() else None),
    )
    return remote


def install_durable_runtime() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    install_pipeline_enhancements()
    from . import app as app_module
    from . import pipeline
    local_process_job = pipeline.process_job

    def dispatch(job_id: str, audio_video_paths, options: dict, visual_video_paths=None) -> None:
        audio_paths = _paths(audio_video_paths)
        visual_paths = _paths(visual_video_paths)
        data = JOBS.get(job_id)
        if not data:
            return
        duration = max(_duration_seconds(audio_paths), _duration_seconds(visual_paths) if visual_paths else 0.0)
        media_minutes = max(0.1, duration / 60.0)
        size_bytes = sum(path.stat().st_size for path in audio_paths + visual_paths if path.exists())
        user_id = str(options.get("billing_user_id", ""))
        try:
            if user_id and is_guest_user(user_id):
                reserve_guest_job(user_id, job_id, media_minutes)
        except BillingError as exc:
            JOBS.update(job_id, status="error", percent=0, stage="error", error_code="LS-GUEST-04", error=str(exc))
            complete_job_history(job_id, status="error", media_minutes=media_minutes)
            return
        eta = estimate_eta_seconds(media_minutes, size_bytes)
        JOBS.update(job_id, media_minutes=round(media_minutes, 2), file_size_bytes=size_bytes, eta_seconds=eta, eta_started_at=time.time())
        queue_enabled = bool(CELERY_BROKER_URL and STORAGE.remote and os.getenv("LECTURESIFT_WORKER") != "1")
        if queue_enabled:
            try:
                from .tasks import process_uploaded_job
                audio_keys: list[str] = []
                visual_keys: list[str] = []
                for index, path in enumerate(audio_paths, 1):
                    key = STORAGE.source_key(job_id, "audio", index, path)
                    STORAGE.upload_file(path, key)
                    audio_keys.append(key)
                for index, path in enumerate(visual_paths, 1):
                    key = STORAGE.source_key(job_id, "visual", index, path)
                    STORAGE.upload_file(path, key)
                    visual_keys.append(key)
                JOBS.update(job_id, status="queued", percent=5, stage="queued_worker", queue_mode="celery", worker_state="queued", source_keys={"audio": audio_keys, "visual": visual_keys})
                task = process_uploaded_job.delay(job_id, audio_keys, options, visual_keys or None)
                JOBS.update(job_id, celery_task_id=task.id)
                for path in audio_paths + visual_paths:
                    path.unlink(missing_ok=True)
                return
            except Exception:
                STORAGE.delete_transient_job(job_id)
                JOBS.update(job_id, queue_mode="fallback", worker_state="local_fallback")
        started = time.time()
        local_process_job(job_id, audio_paths, options, visual_paths or None)
        finished = JOBS.get(job_id) or {}
        if finished.get("status") == "done":
            elapsed = float(finished.get("elapsed_seconds") or (time.time() - started))
            record_runtime(job_id, media_minutes, elapsed, size_bytes)
            _publish_if_configured(job_id, Path(finished.get("job_dir") or data["job_dir"]))
        else:
            complete_job_history(job_id, status=str(finished.get("status") or "error"), media_minutes=media_minutes)

    app_module.process_job = dispatch
    app_module._lecturesift_durable_dispatch_installed = True
    _INSTALLED = True
