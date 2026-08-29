"""Retryable Celery tasks for long LectureSift media jobs."""

from __future__ import annotations

import shutil
import time
from pathlib import Path

from .billing_service import BillingError, require_duration_entitlement
from .config import VIDEO_EXTENSIONS, WORK_DIR
from .duration import media_duration_seconds
from .errors import normalize_error
from .jobs import JOBS
from .media import download_remote_video
from .pipeline_enhancements import install_pipeline_enhancements
from .queue import celery_app
from .rollout_service import is_guest_user, record_runtime, reserve_guest_job
from .storage import STORAGE

install_pipeline_enhancements()
from .pipeline import process_job  # noqa: E402  (patched before binding)


def _download_sources(job_id: str, role: str, keys: list[str], job_dir: Path) -> list[Path]:
    paths: list[Path] = []
    for index, key in enumerate(keys, 1):
        suffix = Path(key).suffix or ".bin"
        destination = job_dir / "sources" / f"{role}_{index:03d}{suffix}"
        STORAGE.download_file(key, destination)
        paths.append(destination)
    return paths


def _enforce_minutes(user_id: str, job_id: str, duration: float) -> None:
    if not user_id:
        return
    if is_guest_user(user_id):
        reserve_guest_job(user_id, job_id, max(0.1, duration / 60.0))
    else:
        require_duration_entitlement(user_id, duration)


def _quota_error(job_id: str, exc: BillingError) -> dict:
    JOBS.update(
        job_id,
        status="error",
        percent=0,
        stage="error",
        worker_state="rejected",
        error_code="LS-BILL-10",
        error=str(exc),
    )
    return {"job_id": job_id, "status": "error", "error_code": "LS-BILL-10"}


def _retry_or_fail(task, job_id: str, exc: Exception) -> dict:
    normalized = normalize_error(exc)
    if normalized.code not in {"LS-AI-02", "LS-SYSTEM-01"}:
        JOBS.update(
            job_id,
            status="error",
            percent=0,
            stage="error",
            worker_state="rejected",
            error_code=normalized.code,
            error=normalized.user_message,
            technical_error=normalized.technical_message,
        )
        return {"job_id": job_id, "status": "error", "error_code": normalized.code}

    retries = int(getattr(task.request, "retries", 0))
    JOBS.update(
        job_id,
        status="queued" if retries < task.max_retries else "error",
        stage="worker_retry" if retries < task.max_retries else "error",
        worker_state="retrying" if retries < task.max_retries else "failed",
        error=None if retries < task.max_retries else "İşlem worker üzerinde tamamlanamadı.",
        technical_error=str(exc),
    )
    if retries >= task.max_retries:
        raise exc
    raise task.retry(exc=exc, countdown=min(300, 15 * (2 ** retries)))


def _processing_error(finished: dict) -> Exception | None:
    if finished.get("status") == "done":
        return None
    if finished.get("error_code") in {"LS-AI-02", "LS-SYSTEM-01"}:
        return RuntimeError(
            str(finished.get("technical_error") or finished.get("error") or "Geçici işlem hatası")
        )
    return None


def _cleanup_sources(job_dir: Path) -> None:
    shutil.rmtree(job_dir / "sources", ignore_errors=True)
    for path in job_dir.iterdir() if job_dir.is_dir() else []:
        if (
            path.is_file()
            and path.suffix.casefold() in VIDEO_EXTENSIONS
            and path.name.startswith(("part_", "audio_", "visual_", "remote."))
        ):
            path.unlink(missing_ok=True)


def _delete_remote_sources(data: dict) -> int:
    source_keys = data.get("source_keys") or {}
    keys = [
        str(key)
        for role in ("audio", "visual")
        for key in list(source_keys.get(role) or [])
        if str(key)
    ]
    return STORAGE.delete_keys(keys)


def _resume_publish(job_id: str, data: dict) -> dict | None:
    result_path = Path(str(data.get("result_path") or ""))
    job_dir = Path(str(data.get("job_dir") or ""))
    if data.get("worker_state") != "retrying" or not result_path.is_file() or not job_dir.is_dir():
        return None
    JOBS.update(
        job_id,
        status="working",
        percent=99,
        stage="worker_publish",
        worker_state="publishing",
    )
    remote = STORAGE.publish_job(job_id, job_dir)
    _delete_remote_sources(data)
    _cleanup_sources(job_dir)
    JOBS.update(job_id, status="done", stage="done", worker_state="done", **remote)
    return {"job_id": job_id, "status": "done", **remote}


@celery_app.task(bind=True, max_retries=6, name="lecturesift.process_uploaded_job")
def process_uploaded_job(
    self,
    job_id: str,
    audio_keys: list[str],
    options: dict,
    visual_keys: list[str] | None = None,
) -> dict:
    with JOBS.processing_lock(job_id) as acquired:
        if not acquired:
            if int(getattr(self.request, "retries", 0)) >= self.max_retries:
                return {"job_id": job_id, "status": "duplicate"}
            raise self.retry(countdown=60)

        data = JOBS.get(job_id)
        if not data:
            return {"job_id": job_id, "status": "missing"}
        if data.get("status") == "done" and data.get("remote_prefix"):
            return {"job_id": job_id, "status": "done"}
        try:
            resumed = _resume_publish(job_id, data)
            if resumed:
                return resumed
        except Exception as exc:
            return _retry_or_fail(self, job_id, exc)

        job_dir = WORK_DIR / job_id
        try:
            shutil.rmtree(job_dir, ignore_errors=True)
            job_dir.mkdir(parents=True, exist_ok=True)
            JOBS.update(
                job_id,
                job_dir=str(job_dir),
                status="working",
                stage="worker_download",
                worker_state="downloading",
            )
            audio_paths = _download_sources(job_id, "audio", list(audio_keys), job_dir)
            visual_paths = _download_sources(job_id, "visual", list(visual_keys or []), job_dir)
            document_seconds = max(0.0, float(options.get("document_credit_seconds") or 0))
            duration = document_seconds or max(
                media_duration_seconds(audio_paths),
                media_duration_seconds(visual_paths) if visual_paths else 0.0,
            )
            media_minutes = max(0.1, duration / 60.0)
            try:
                _enforce_minutes(str(options.get("billing_user_id", "")), job_id, duration)
            except BillingError as exc:
                return _quota_error(job_id, exc)

            started = time.time()
            source_size = sum(path.stat().st_size for path in audio_paths + visual_paths if path.exists())
            JOBS.update(
                job_id,
                worker_state="processing",
                media_minutes=round(media_minutes, 2),
                file_size_bytes=source_size,
            )
            process_job(job_id, audio_paths, options, visual_paths or None)
            finished = JOBS.get(job_id) or {}
            if finished.get("status") != "done":
                transient = _processing_error(finished)
                if transient:
                    raise transient
                return {"job_id": job_id, "status": finished.get("status", "error")}

            # The pipeline creates local output first. Do not expose a terminal
            # status until the durable copy is available to every web instance.
            JOBS.update(
                job_id,
                status="working",
                percent=99,
                stage="worker_publish",
                worker_state="publishing",
            )
            remote = STORAGE.publish_job(job_id, job_dir)
            _delete_remote_sources(finished)
            elapsed = float(finished.get("elapsed_seconds") or (time.time() - started))
            record_runtime(job_id, media_minutes, elapsed, source_size)
            _cleanup_sources(job_dir)
            JOBS.update(
                job_id,
                status="done",
                percent=100,
                stage="done",
                worker_state="done",
                **remote,
            )
            return {"job_id": job_id, "status": "done", **remote}
        except Exception as exc:
            return _retry_or_fail(self, job_id, exc)


@celery_app.task(bind=True, max_retries=6, name="lecturesift.process_url_job")
def process_url_job(self, job_id: str, url: str, options: dict) -> dict:
    with JOBS.processing_lock(job_id) as acquired:
        if not acquired:
            if int(getattr(self.request, "retries", 0)) >= self.max_retries:
                return {"job_id": job_id, "status": "duplicate"}
            raise self.retry(countdown=60)

        data = JOBS.get(job_id)
        if not data:
            return {"job_id": job_id, "status": "missing"}
        if data.get("status") == "done" and data.get("remote_prefix"):
            return {"job_id": job_id, "status": "done"}
        try:
            resumed = _resume_publish(job_id, data)
            if resumed:
                return resumed
        except Exception as exc:
            return _retry_or_fail(self, job_id, exc)

        job_dir = WORK_DIR / job_id
        try:
            shutil.rmtree(job_dir, ignore_errors=True)
            job_dir.mkdir(parents=True, exist_ok=True)
            JOBS.update(job_id, job_dir=str(job_dir), status="working", stage="url_download", worker_state="downloading")
            video_path = download_remote_video(url, job_dir)
            duration = media_duration_seconds([video_path])
            try:
                _enforce_minutes(str(options.get("billing_user_id", "")), job_id, duration)
            except BillingError as exc:
                return _quota_error(job_id, exc)

            media_minutes = max(0.1, duration / 60.0)
            source_size = video_path.stat().st_size if video_path.exists() else 0
            started = time.time()
            JOBS.update(
                job_id,
                percent=8,
                stage="parallel_analysis",
                worker_state="processing",
                media_minutes=round(media_minutes, 2),
                file_size_bytes=source_size,
            )
            process_job(job_id, video_path, options)
            finished = JOBS.get(job_id) or {}
            if finished.get("status") != "done":
                transient = _processing_error(finished)
                if transient:
                    raise transient
                return {"job_id": job_id, "status": finished.get("status", "error")}

            remote = STORAGE.publish_job(job_id, job_dir)
            elapsed = float(finished.get("elapsed_seconds") or (time.time() - started))
            record_runtime(job_id, media_minutes, elapsed, source_size)
            video_path.unlink(missing_ok=True)
            JOBS.update(job_id, worker_state="done", **remote)
            return {"job_id": job_id, "status": "done", **remote}
        except Exception as exc:
            return _retry_or_fail(self, job_id, exc)
