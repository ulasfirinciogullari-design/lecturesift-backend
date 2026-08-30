"""Runtime dispatcher that separates the web API from long media processing."""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

from .billing_service import require_duration_entitlement
from .config import CELERY_BROKER_URL, REQUIRE_DURABLE_PROCESSING
from .duration import media_duration_seconds
from .documents import extract_documents
from .errors import normalize_error
from .jobs import JOBS
from .pipeline_enhancements import install_pipeline_enhancements
from .rollout_service import BillingError, estimate_eta_seconds, is_guest_user, record_runtime, reserve_guest_job
from .storage import STORAGE


_INSTALLED = False


def _queue_ready() -> bool:
    return bool(CELERY_BROKER_URL and STORAGE.remote and os.getenv("LECTURESIFT_WORKER") != "1")


def _durability_unavailable(job_id: str) -> None:
    JOBS.update(
        job_id,
        status="error",
        percent=0,
        stage="error",
        queue_mode="required_unavailable",
        worker_state="unavailable",
        error_code="LS-SYSTEM-01",
        error="Güvenli işleme altyapısı geçici olarak kullanılamıyor. Lütfen biraz sonra yeniden dene.",
    )


def _paths(value) -> list[Path]:
    if value is None:
        return []
    if isinstance(value, Path):
        return [value]
    return [Path(item) for item in value]


def _publish_if_configured(job_id: str, job_dir: Path) -> None:
    if not STORAGE.remote:
        return
    data = JOBS.get(job_id) or {}
    if data.get("status") != "done":
        return
    remote = STORAGE.publish_job(job_id, job_dir)
    if remote:
        JOBS.update(job_id, **remote)


def _preflight_documents(job_id: str, paths: list[Path], options: dict) -> float | None:
    """Inspect documents after the HTTP upload response has been released."""
    try:
        document_data = extract_documents(
            paths,
            source_language=str(options.get("source_language") or "auto"),
            enable_ocr=False,
            allow_ocr_pending=True,
        )
    except Exception as exc:
        normalized = normalize_error(exc)
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
        return None

    options.update(
        document_credit_seconds=float(document_data["credit_seconds"]),
        document_words=int(document_data["words"]),
        document_ocr_required=bool(document_data["ocr_required"]),
        document_pages=int(document_data["pages"]),
        document_ocr_pages=int(document_data["ocr_pages"]),
        document_estimated=bool(document_data["estimated"]),
    )
    JOBS.update(
        job_id,
        options=options,
        document_words=int(document_data["words"]),
        billable_minutes=int(document_data["credit_minutes"]),
        document_ocr_required=bool(document_data["ocr_required"]),
        document_pages=int(document_data["pages"]),
        document_ocr_pages=int(document_data["ocr_pages"]),
        usage_estimated=bool(document_data["estimated"]),
        stage="document_preflight",
    )
    return float(document_data["credit_seconds"])


def install_durable_runtime() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    install_pipeline_enhancements()

    from . import app as app_module
    from . import pipeline

    local_process_job = pipeline.process_job
    local_start_url_job = app_module.start_url_job

    def dispatch(job_id: str, audio_video_paths, options: dict, visual_video_paths=None) -> None:
        audio_paths = _paths(audio_video_paths)
        visual_paths = _paths(visual_video_paths)
        data = JOBS.get(job_id)
        if not data:
            return
        document_mode = bool(options.get("document_mode"))
        document_seconds = max(0.0, float(options.get("document_credit_seconds") or 0))
        if document_mode and not document_seconds:
            preflight_seconds = _preflight_documents(job_id, audio_paths, options)
            if preflight_seconds is None:
                return
            document_seconds = preflight_seconds
        duration = document_seconds or max(
            media_duration_seconds(audio_paths),
            media_duration_seconds(visual_paths) if visual_paths else 0.0,
        )
        media_minutes = max(0.1, duration / 60.0)
        size_bytes = sum(
            path.stat().st_size for path in audio_paths + visual_paths if path.exists()
        )
        user_id = str(options.get("billing_user_id", ""))
        guest_user = False
        try:
            if user_id:
                guest_user = is_guest_user(user_id)
                if guest_user:
                    reserve_guest_job(user_id, job_id, media_minutes)
                require_duration_entitlement(
                    user_id,
                    duration,
                    source_file_count=len(audio_paths) + len(visual_paths),
                    source_size_bytes=size_bytes,
                    document_mode=document_mode,
                    document_pages=int(options.get("document_pages") or 0),
                    ocr_pages=int(options.get("document_ocr_pages") or 0),
                )
        except BillingError as exc:
            JOBS.update(
                job_id,
                status="error",
                percent=0,
                stage="error",
                error_code="LS-GUEST-04" if guest_user else "LS-BILL-10",
                error=str(exc),
            )
            return

        eta = estimate_eta_seconds(
            media_minutes,
            size_bytes,
            job_type=str(options.get("job_type") or "study_pack"),
            summary_style=str(options.get("summary_style") or "standard"),
            source_kind="document" if document_mode else "media",
        )
        JOBS.update(
            job_id,
            media_minutes=round(media_minutes, 2),
            file_size_bytes=size_bytes,
            eta_seconds=eta,
            eta_started_at=time.time(),
        )

        if REQUIRE_DURABLE_PROCESSING and not _queue_ready():
            _durability_unavailable(job_id)
            return

        if _queue_ready():
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
                JOBS.update(
                    job_id,
                    status="queued",
                    percent=5,
                    stage="queued_worker",
                    queue_mode="celery",
                    worker_state="queued",
                    source_keys={"audio": audio_keys, "visual": visual_keys},
                )
                task = process_uploaded_job.delay(job_id, audio_keys, options, visual_keys or None)
                JOBS.update(job_id, celery_task_id=task.id)
                return
            except Exception as exc:
                if REQUIRE_DURABLE_PROCESSING:
                    _durability_unavailable(job_id)
                    JOBS.update(job_id, technical_error=str(exc))
                    return
                # The current in-process path remains available if the queue or
                # object store has a transient configuration problem.
                JOBS.update(
                    job_id,
                    queue_mode="fallback",
                    worker_state="local_fallback",
                    queue_error=str(exc),
                )

        started = time.time()
        local_process_job(job_id, audio_paths, options, visual_paths or None)
        finished = JOBS.get(job_id) or {}
        if finished.get("status") == "done":
            elapsed = float(finished.get("elapsed_seconds") or (time.time() - started))
            record_runtime(job_id, media_minutes, elapsed, size_bytes)
            _publish_if_configured(job_id, Path(finished.get("job_dir") or data["job_dir"]))
            for source_path in audio_paths + visual_paths:
                try:
                    source_path.unlink(missing_ok=True)
                except OSError:
                    pass

    def dispatch_url(job_id: str, url: str, job_dir: Path, options: dict) -> str:
        if not _queue_ready():
            if REQUIRE_DURABLE_PROCESSING:
                _durability_unavailable(job_id)
                return "error"
            return local_start_url_job(job_id, url, job_dir, options)
        try:
            from .tasks import process_url_job

            JOBS.update(
                job_id,
                status="queued",
                percent=5,
                stage="queued_worker",
                queue_mode="celery",
                worker_state="queued",
            )
            task = process_url_job.delay(job_id, url, options)
            JOBS.update(job_id, celery_task_id=task.id)
            return "queued"
        except Exception as exc:
            if REQUIRE_DURABLE_PROCESSING:
                _durability_unavailable(job_id)
                JOBS.update(job_id, technical_error=str(exc))
                return "error"
            JOBS.update(
                job_id,
                queue_mode="fallback",
                worker_state="local_fallback",
                queue_error=str(exc),
            )
            return local_start_url_job(job_id, url, job_dir, options)

    def recover_durable_jobs() -> None:
        if not _queue_ready():
            return
        from .tasks import process_uploaded_job, process_url_job

        for data in JOBS.recoverable():
            job_id = str(data.get("job_id", ""))
            options = data.get("options") or {}
            if not job_id or not options:
                continue
            source_keys = data.get("source_keys") or {}
            audio_keys = list(source_keys.get("audio") or [])
            visual_keys = list(source_keys.get("visual") or [])
            try:
                if audio_keys:
                    task = process_uploaded_job.delay(job_id, audio_keys, options, visual_keys or None)
                elif data.get("source_type") == "url" and data.get("source_url"):
                    task = process_url_job.delay(job_id, str(data["source_url"]), options)
                else:
                    continue
                JOBS.update(
                    job_id,
                    status="queued",
                    stage="recovered_queue",
                    worker_state="requeued",
                    celery_task_id=task.id,
                    recovered_at=time.time(),
                )
            except Exception as exc:
                JOBS.update(job_id, recovery_error=str(exc))

    app_module.process_job = dispatch
    app_module.start_url_job = dispatch_url
    app_module.app.add_event_handler(
        "startup",
        lambda: threading.Thread(
            target=recover_durable_jobs,
            daemon=True,
            name="lecturesift-recovery",
        ).start(),
    )
    app_module._lecturesift_durable_dispatch_installed = True
    _INSTALLED = True
