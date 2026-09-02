"""Runtime dispatcher that separates the web API from long media processing."""

from __future__ import annotations

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import config
from .billing_service import require_duration_entitlement
from .config import CELERY_BROKER_URL, REQUIRE_DURABLE_PROCESSING, STORAGE_TRANSFER_PARALLELISM
from .duration import media_duration_seconds
from .documents import extract_documents
from .errors import normalize_error
from .jobs import JOBS
from .pipeline_enhancements import install_pipeline_enhancements
from .rollout_service import BillingError, estimate_eta_seconds, is_guest_user, record_runtime, reserve_guest_job
from .storage import STORAGE


_INSTALLED = False


def _start_durable_recovery(recover) -> None:
    """Start recovery only when the public API is open for normal writes."""
    if config.current_maintenance_mode() != "off":
        return
    threading.Thread(
        target=recover,
        daemon=True,
        name="lecturesift-recovery",
    ).start()


def _queue_ready() -> bool:
    return bool(CELERY_BROKER_URL and STORAGE.remote and os.getenv("LECTURESIFT_WORKER") != "1")


def _durability_unavailable(job_id: str, diagnostic: str = "durable_runtime_unavailable") -> None:
    JOBS.update(
        job_id,
        status="error",
        percent=0,
        stage="error",
        queue_mode="required_unavailable",
        worker_state="unavailable",
        error_code="LS-SYSTEM-01",
        error="Güvenli işleme altyapısı geçici olarak kullanılamıyor. Lütfen biraz sonra yeniden dene.",
        infrastructure_diagnostic=diagnostic,
    )


def _paths(value) -> list[Path]:
    if value is None:
        return []
    if isinstance(value, Path):
        return [value]
    return [Path(item) for item in value]


def _upload_job_sources(
    job_id: str,
    audio_paths: list[Path],
    visual_paths: list[Path],
) -> tuple[list[str], list[str]]:
    """Upload independent sources concurrently while preserving user order."""
    audio_specs = [("audio", index, path) for index, path in enumerate(audio_paths, 1)]
    visual_specs = [("visual", index, path) for index, path in enumerate(visual_paths, 1)]
    specs = audio_specs + visual_specs
    # Build the complete key plan before starting any transfer.  An object-store
    # timeout can be ambiguous: the server may have committed the object even
    # though upload_file raised before returning.  Deleting only the keys whose
    # calls returned successfully would therefore leak private source files.
    planned = [
        (role, index, path, STORAGE.source_key(job_id, role, index, path))
        for role, index, path in specs
    ]
    planned_keys = [key for _role, _index, _path, key in planned]

    def upload(spec: tuple[str, int, Path, str]) -> str:
        _role, _index, path, key = spec
        STORAGE.upload_file(path, key)
        return key

    try:
        workers = min(STORAGE_TRANSFER_PARALLELISM, len(planned))
        if workers <= 1:
            keys = [upload(spec) for spec in planned]
        else:
            with ThreadPoolExecutor(
                max_workers=workers,
                thread_name_prefix=f"lecturesift-upload-{job_id[:8]}",
            ) as executor:
                keys = list(executor.map(upload, planned))
    except Exception:
        if planned_keys:
            try:
                STORAGE.delete_keys(planned_keys)
            except Exception:
                pass
        raise
    split_at = len(audio_specs)
    return keys[:split_at], keys[split_at:]


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
        queue_ready = _queue_ready()
        worker_document_preflight = bool(document_mode and not document_seconds and queue_ready)
        # In production the worker owns document inspection. This keeps PDF,
        # PowerPoint, and OCR preflight work off the small public web instance.
        if document_mode and not document_seconds and not queue_ready:
            preflight_seconds = _preflight_documents(job_id, audio_paths, options)
            if preflight_seconds is None:
                return
            document_seconds = preflight_seconds
        # Documents do not have a media duration.  While their page/text quota
        # inspection is pending on the worker, probing them with ffprobe is both
        # wasteful and misleading.  A zero-duration entitlement check below
        # validates the account, file count, and byte limit without consuming a
        # guest trial; the worker repeats it with the verified document minutes.
        duration = (
            0.0
            if worker_document_preflight
            else document_seconds
            or max(
                media_duration_seconds(audio_paths),
                media_duration_seconds(visual_paths) if visual_paths else 0.0,
            )
        )
        media_minutes = 0.0 if worker_document_preflight else max(0.1, duration / 60.0)
        size_bytes = sum(
            path.stat().st_size for path in audio_paths + visual_paths if path.exists()
        )
        user_id = str(options.get("billing_user_id", ""))
        guest_user = False
        try:
            if user_id:
                guest_user = is_guest_user(user_id)
                # A document trial is consumed only after full extraction/OCR
                # proves that the source is valid and reveals its real minutes.
                if guest_user and not document_mode:
                    reserve_guest_job(user_id, job_id, media_minutes)
                entitlement_duration = (
                    0.0
                    if document_mode and bool(options.get("document_estimated"))
                    else duration
                )
                require_duration_entitlement(
                    user_id,
                    entitlement_duration,
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

        eta = (
            None
            if worker_document_preflight
            else estimate_eta_seconds(
                media_minutes,
                size_bytes,
                job_type=str(options.get("job_type") or "study_pack"),
                summary_style=str(options.get("summary_style") or "standard"),
                source_kind="document" if document_mode else "media",
            )
        )
        JOBS.update(
            job_id,
            media_minutes=None if worker_document_preflight else round(media_minutes, 2),
            file_size_bytes=size_bytes,
            eta_seconds=eta,
            eta_started_at=None if worker_document_preflight else time.time(),
            usage_estimated=worker_document_preflight,
            document_preflight_deferred_to_worker=worker_document_preflight,
            guest_reservation_deferred_to_worker=bool(guest_user and worker_document_preflight),
            guest_reservation_deferred_to_pipeline=bool(guest_user and document_mode),
        )

        if REQUIRE_DURABLE_PROCESSING and not queue_ready:
            _durability_unavailable(job_id)
            return

        if queue_ready:
            audio_keys: list[str] = []
            visual_keys: list[str] = []
            try:
                from .tasks import process_uploaded_job

                audio_keys, visual_keys = _upload_job_sources(job_id, audio_paths, visual_paths)
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
                # The durable R2 copy is authoritative from this point on.
                # Releasing the web instance's temporary copies immediately
                # prevents large uploads from accumulating on ephemeral disk.
                for source_path in audio_paths + visual_paths:
                    try:
                        source_path.unlink(missing_ok=True)
                    except OSError:
                        pass
                return
            except Exception as exc:
                if audio_keys or visual_keys:
                    try:
                        STORAGE.delete_keys(audio_keys + visual_keys)
                    except Exception:
                        pass
                if REQUIRE_DURABLE_PROCESSING:
                    diagnostic = STORAGE.error_code(exc)
                    _durability_unavailable(job_id, diagnostic)
                    JOBS.update(job_id, technical_error=diagnostic)
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
        if config.current_maintenance_mode() != "off" or not _queue_ready():
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
        lambda: _start_durable_recovery(recover_durable_jobs),
    )
    app_module._lecturesift_durable_dispatch_installed = True
    _INSTALLED = True
