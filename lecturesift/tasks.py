"""Retryable Celery tasks for long LectureSift media jobs."""

from __future__ import annotations

import logging
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from celery.exceptions import SoftTimeLimitExceeded
from celery.worker import state as worker_state
from celery.worker.request import Request

from .billing_service import BillingError, require_duration_entitlement, usage_multiplier_for
from .config import MEDIA_EXTENSIONS, SOURCE_DOWNLOAD_PARALLELISM, WORK_DIR
from .duration import media_duration_seconds
from .documents import extract_documents
from .document_samples import extraction_options, sample_metadata
from .errors import normalize_error
from .jobs import JOBS
from .pipeline_enhancements import install_pipeline_enhancements
from .queue import JOB_HARD_TIME_LIMIT_SECONDS, celery_app
from .rollout_service import estimate_eta_seconds, is_guest_user, record_runtime, reserve_guest_job
from .resource_limits import enforce_job_workspace
from .storage import STORAGE

install_pipeline_enhancements()
from .pipeline import process_job  # noqa: E402  (patched before binding)
from .provider_state import AI_PROVIDER_CIRCUIT

LOGGER = logging.getLogger(__name__)
_UNFINISHED_STATUSES = frozenset({"queued", "working"})


def _download_source_specs(
    job_id: str,
    specs: list[tuple[str, int, str]],
    job_dir: Path,
) -> list[Path]:
    def download(spec: tuple[str, int, str]) -> Path:
        role, index, key = spec
        suffix = Path(key).suffix or ".bin"
        destination = job_dir / "sources" / f"{role}_{index:03d}{suffix}"
        STORAGE.download_file(key, destination)
        return destination

    workers = min(SOURCE_DOWNLOAD_PARALLELISM, len(specs))
    if workers <= 1:
        return [download(spec) for spec in specs]
    with ThreadPoolExecutor(
        max_workers=workers,
        thread_name_prefix=f"lecturesift-source-{job_id[:8]}",
    ) as executor:
        # executor.map preserves input order while transfers finish concurrently.
        return list(executor.map(download, specs))


def _download_sources(job_id: str, role: str, keys: list[str], job_dir: Path) -> list[Path]:
    specs = [(role, index, key) for index, key in enumerate(keys, 1)]
    return _download_source_specs(job_id, specs, job_dir)


def _download_job_sources(
    job_id: str,
    audio_keys: list[str],
    visual_keys: list[str],
    job_dir: Path,
) -> tuple[list[Path], list[Path]]:
    audio_specs = [("audio", index, key) for index, key in enumerate(audio_keys, 1)]
    visual_specs = [("visual", index, key) for index, key in enumerate(visual_keys, 1)]
    downloaded = _download_source_specs(job_id, audio_specs + visual_specs, job_dir)
    split_at = len(audio_specs)
    return downloaded[:split_at], downloaded[split_at:]


def _enforce_minutes(user_id: str, job_id: str, duration: float, *, usage_multiplier: int = 1) -> None:
    if not user_id:
        return
    require_duration_entitlement(user_id, duration, usage_multiplier=usage_multiplier)
    if is_guest_user(user_id):
        reserve_guest_job(user_id, job_id, max(0.1, duration / 60.0))


def _measure_source_durations(
    audio_paths: list[Path],
    visual_paths: list[Path],
) -> tuple[float, float]:
    """Measure independent audio/visual source sets without serial probe waits."""
    if not visual_paths:
        return media_duration_seconds(audio_paths), 0.0
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="lecturesift-source-duration") as executor:
        audio_future = executor.submit(media_duration_seconds, audio_paths)
        visual_future = executor.submit(media_duration_seconds, visual_paths)
        return audio_future.result(), visual_future.result()


def _pipeline_options_with_durations(
    options: dict,
    audio_duration: float,
    visual_duration: float = 0.0,
) -> dict:
    """Attach private measurements without mutating the persisted/public options."""
    return {
        **options,
        "_measured_audio_duration_seconds": max(0.0, float(audio_duration)),
        "_measured_visual_duration_seconds": max(0.0, float(visual_duration)),
        "_measured_duration_seconds": max(0.0, float(audio_duration), float(visual_duration)),
    }


def _preflight_worker_documents(job_id: str, paths: list[Path], options: dict) -> float:
    """Inspect document quotas on the worker before expensive OCR or AI work."""
    document_data = extract_documents(
        paths,
        source_language=str(options.get("source_language") or "auto"),
        enable_ocr=False,
        allow_ocr_pending=True,
        **extraction_options(options),
    )
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
        worker_state="preflighting",
        **sample_metadata(document_data),
    )
    return float(document_data["credit_seconds"])


def _enforce_uploaded_job_quota(
    user_id: str,
    job_id: str,
    duration: float,
    *,
    source_file_count: int,
    source_size_bytes: int,
    document_mode: bool,
    document_pages: int,
    ocr_pages: int,
    usage_multiplier: int = 1,
) -> None:
    """Apply the worker-side estimate without consuming a guest document trial early.

    Document OCR can materially change billable minutes.  The pipeline performs the
    final document entitlement check after extraction; reserving the anonymous trial
    here would otherwise commit it using an estimate rather than the real result.
    """
    guest_user = bool(user_id and is_guest_user(user_id))
    require_duration_entitlement(
        user_id,
        duration,
        source_file_count=source_file_count,
        source_size_bytes=source_size_bytes,
        document_mode=document_mode,
        document_pages=document_pages,
        ocr_pages=ocr_pages,
        usage_multiplier=usage_multiplier,
    )
    # A rejected source must leave the anonymous trial available. Reservation
    # remains atomic and happens before the worker starts paid processing.
    if guest_user and not document_mode:
        reserve_guest_job(user_id, job_id, max(0.1, duration / 60.0))


def _source_keys(
    data: dict | None = None,
    audio_keys: list[str] | None = None,
    visual_keys: list[str] | None = None,
) -> list[str]:
    stored = (data or {}).get("source_keys") or {}
    return list(
        dict.fromkeys(
            str(key)
            for key in [
                *list(audio_keys or []),
                *list(visual_keys or []),
                *list(stored.get("audio") or []),
                *list(stored.get("visual") or []),
            ]
            if str(key)
        )
    )


def _cleanup_terminal_sources(
    job_dir: Path | None,
    *,
    data: dict | None = None,
    audio_keys: list[str] | None = None,
    visual_keys: list[str] | None = None,
) -> None:
    """Best-effort source cleanup that never masks the terminal job error."""
    if job_dir is not None:
        try:
            _cleanup_sources(job_dir)
        except OSError:
            pass
    keys = _source_keys(data, audio_keys, visual_keys)
    if keys:
        try:
            STORAGE.delete_keys(keys)
        except Exception:
            pass


def _stored_job_dir(data: dict | None) -> Path | None:
    """Return only a job directory nested below WORK_DIR.

    An empty persisted path becomes ``Path('.')``; treating it as a cleanup
    target would be dangerously broad, so persisted paths are constrained here.
    """
    raw = str((data or {}).get("job_dir") or "").strip()
    if not raw:
        return None
    try:
        candidate = Path(raw).resolve()
        root = WORK_DIR.resolve()
        candidate.relative_to(root)
    except (OSError, ValueError):
        return None
    return candidate if candidate != root else None


def fail_unfinished_job(
    job_id: str,
    diagnostic: str,
    *,
    stale_before: float | None = None,
    job_dir: Path | None = None,
    audio_keys: list[str] | None = None,
    visual_keys: list[str] | None = None,
) -> bool:
    """Fail a job that can no longer finish, through the standard terminal path.

    Used for the worker time limits, a job left without any delivery, and the
    stuck-job sweep. The job gets the normal LS-SYSTEM-01 failure state and
    failure email, and its sources are released like any failed job's. Nothing
    is charged: usage is recorded only when a job completes. A job that has
    already finished (or, with ``stale_before``, made progress since) is left
    untouched; returns whether this call failed it.
    """
    error = normalize_error(RuntimeError(diagnostic))
    previous = JOBS.fail_unfinished(
        job_id,
        stale_before=stale_before,
        percent=0,
        stage="error",
        worker_state="failed",
        error_code=error.code,
        error=error.user_message,
        technical_error=diagnostic,
    )
    if previous is None:
        return False
    _cleanup_terminal_sources(
        job_dir or _stored_job_dir(previous),
        data=previous,
        audio_keys=audio_keys,
        visual_keys=visual_keys,
    )
    return True


class JobRequest(Request):
    """Fail the stored job when the hard time limit kills its worker process.

    Celery acknowledges a hard-killed task without running any more task code,
    so without this the job would stay "working" until the stuck-job sweep.
    This runs in the worker's main process.
    """

    def on_timeout(self, soft, timeout):
        super().on_timeout(soft, timeout)
        if soft or worker_state.should_terminate:
            # A soft limit is handled inside the task; a cold shutdown leaves
            # the job to its redelivered message.
            return
        job_id = str((self.args or [None])[0] or (self.kwargs or {}).get("job_id") or "")
        if not job_id:
            return
        try:
            fail_unfinished_job(job_id, f"job_hard_time_limit:{timeout}s")
        except Exception:
            LOGGER.error("Could not record a job's hard time limit; the stuck-job sweep will fail it")


def _delivery_hard_limit(task) -> float:
    """This delivery's hard time limit: the job's own, or the global worst case."""
    hard, _soft = getattr(task.request, "timelimit", None) or (None, None)
    try:
        return float(hard) if hard and float(hard) > 0 else float(JOB_HARD_TIME_LIMIT_SECONDS)
    except (TypeError, ValueError):
        return float(JOB_HARD_TIME_LIMIT_SECONDS)


def _job_locked_elsewhere(task, job_id: str) -> dict:
    """Handle a delivery that keeps finding the job's processing lock taken."""
    if int(getattr(task.request, "retries", 0)) < task.max_retries:
        raise task.retry(countdown=60)
    status = (JOBS.get(job_id) or {}).get("status")
    if status not in _UNFINISHED_STATUSES:
        return {"job_id": job_id, "status": status or "missing"}
    if JOBS.processing_lock_held(job_id):
        # A live delivery still owns the job. The time limits end it within
        # JOB_HARD_TIME_LIMIT_SECONDS and the stuck-job sweep catches the rest.
        return {"job_id": job_id, "status": "duplicate"}
    # No delivery holds the job any more and this one is out of retries:
    # without a terminal state it would stay queued/working forever.
    if fail_unfinished_job(job_id, "processing_lock_unavailable"):
        return {"job_id": job_id, "status": "error", "error_code": "LS-SYSTEM-01"}
    return {"job_id": job_id, "status": (JOBS.get(job_id) or {}).get("status") or "missing"}


def _time_limit_error(
    job_id: str,
    *,
    job_dir: Path | None,
    audio_keys: list[str],
    visual_keys: list[str],
) -> dict:
    """The soft time limit ends a job for good instead of retrying it."""
    fail_unfinished_job(
        job_id,
        "job_soft_time_limit",
        job_dir=job_dir,
        audio_keys=audio_keys,
        visual_keys=visual_keys,
    )
    return {"job_id": job_id, "status": "error", "error_code": "LS-SYSTEM-01"}


def _quota_error(
    job_id: str,
    exc: BillingError,
    *,
    job_dir: Path | None = None,
    data: dict | None = None,
    audio_keys: list[str] | None = None,
    visual_keys: list[str] | None = None,
) -> dict:
    JOBS.update(
        job_id,
        status="error",
        percent=0,
        stage="error",
        worker_state="rejected",
        error_code="LS-BILL-10",
        error=str(exc),
        notify_failure=True,
        notification_context=getattr(exc, "notification_context", None),
    )
    _cleanup_terminal_sources(
        job_dir,
        data=data,
        audio_keys=audio_keys,
        visual_keys=visual_keys,
    )
    return {"job_id": job_id, "status": "error", "error_code": "LS-BILL-10"}


def _retry_or_fail(
    task,
    job_id: str,
    exc: Exception,
    *,
    job_dir: Path | None = None,
    data: dict | None = None,
    audio_keys: list[str] | None = None,
    visual_keys: list[str] | None = None,
) -> dict:
    normalized = normalize_error(exc)
    AI_PROVIDER_CIRCUIT.trip_error(normalized)
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
            notify_failure=True,
        )
        _cleanup_terminal_sources(
            job_dir,
            data=data,
            audio_keys=audio_keys,
            visual_keys=visual_keys,
        )
        if normalized.code.startswith("LS-STORAGE-") and job_dir is not None:
            # A capacity failure must release every derived artifact, not only
            # source media, or the next queued job inherits the exhausted disk.
            shutil.rmtree(job_dir, ignore_errors=True)
        return {"job_id": job_id, "status": "error", "error_code": normalized.code}

    retries = int(getattr(task.request, "retries", 0))
    JOBS.update(
        job_id,
        # A retry goes back to the end of the queue (see the stuck-job sweep).
        **({"queued_at": time.time()} if retries < task.max_retries else {}),
        status="queued" if retries < task.max_retries else "error",
        stage="worker_retry" if retries < task.max_retries else "error",
        worker_state="retrying" if retries < task.max_retries else "failed",
        error=None if retries < task.max_retries else "İşlem worker üzerinde tamamlanamadı.",
        error_code=None if retries < task.max_retries else normalized.code,
        technical_error=str(exc),
        notify_failure=retries >= task.max_retries,
    )
    if retries >= task.max_retries:
        _cleanup_terminal_sources(
            job_dir,
            data=data,
            audio_keys=audio_keys,
            visual_keys=visual_keys,
        )
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
            and path.suffix.casefold() in MEDIA_EXTENSIONS
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
    enforce_job_workspace(job_dir, work_root=WORK_DIR)
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


@celery_app.task(
    bind=True, max_retries=6, name="lecturesift.process_uploaded_job", Request=JobRequest,
)
def process_uploaded_job(
    self,
    job_id: str,
    audio_keys: list[str],
    options: dict,
    visual_keys: list[str] | None = None,
) -> dict:
    with JOBS.processing_lock(job_id) as acquired:
        if not acquired:
            return _job_locked_elsewhere(self, job_id)

        data = JOBS.get(job_id)
        if not data:
            _cleanup_terminal_sources(
                None,
                audio_keys=list(audio_keys),
                visual_keys=list(visual_keys or []),
            )
            return {"job_id": job_id, "status": "missing"}
        if data.get("status") == "done" and data.get("remote_prefix"):
            _cleanup_terminal_sources(
                None,
                data=data,
                audio_keys=list(audio_keys),
                visual_keys=list(visual_keys or []),
            )
            return {"job_id": job_id, "status": "done"}
        if data.get("status") == "error":
            # Already failed for good (a time limit, the stuck-job sweep, or a
            # rejection); a late or redelivered message must not restart it.
            _cleanup_terminal_sources(
                _stored_job_dir(data),
                data=data,
                audio_keys=list(audio_keys),
                visual_keys=list(visual_keys or []),
            )
            return {"job_id": job_id, "status": "error"}
        try:
            resumed = _resume_publish(job_id, data)
            if resumed:
                return resumed
        except SoftTimeLimitExceeded:
            return _time_limit_error(
                job_id,
                job_dir=_stored_job_dir(data),
                audio_keys=list(audio_keys),
                visual_keys=list(visual_keys or []),
            )
        except Exception as exc:
            return _retry_or_fail(
                self,
                job_id,
                exc,
                job_dir=_stored_job_dir(data),
                data=data,
                audio_keys=list(audio_keys),
                visual_keys=list(visual_keys or []),
            )

        job_dir = WORK_DIR / job_id
        try:
            shutil.rmtree(job_dir, ignore_errors=True)
            job_dir.mkdir(parents=True, exist_ok=True)
            enforce_job_workspace(job_dir, reserve_full_budget=True, work_root=WORK_DIR)
            JOBS.update(
                job_id,
                job_dir=str(job_dir),
                status="working",
                stage="worker_download",
                worker_state="downloading",
                processing_deadline=time.time() + _delivery_hard_limit(self),
            )
            audio_paths, visual_paths = _download_job_sources(
                job_id,
                list(audio_keys),
                list(visual_keys or []),
                job_dir,
            )
            enforce_job_workspace(job_dir, reserve_full_budget=True, work_root=WORK_DIR)
            document_seconds = max(0.0, float(options.get("document_credit_seconds") or 0))
            if bool(options.get("document_mode")) and not document_seconds:
                document_seconds = _preflight_worker_documents(job_id, audio_paths, options)
            audio_duration = 0.0
            visual_duration = 0.0
            if document_seconds:
                duration = document_seconds
                pipeline_options = dict(options)
            else:
                audio_duration, visual_duration = _measure_source_durations(audio_paths, visual_paths)
                duration = max(audio_duration, visual_duration)
                pipeline_options = _pipeline_options_with_durations(
                    options,
                    audio_duration,
                    visual_duration,
                )
            media_minutes = max(0.1, duration / 60.0)
            try:
                user_id = str(options.get("billing_user_id", ""))
                source_size = sum(
                    path.stat().st_size
                    for path in audio_paths + visual_paths
                    if path.exists()
                )
                entitlement_duration = (
                    0.0
                    if bool(options.get("document_mode"))
                    and bool(options.get("document_estimated"))
                    else duration
                )
                _enforce_uploaded_job_quota(
                    user_id,
                    job_id,
                    entitlement_duration,
                    source_file_count=len(audio_paths) + len(visual_paths),
                    source_size_bytes=source_size,
                    document_mode=bool(options.get("document_mode")),
                    document_pages=int(options.get("document_pages") or 0),
                    ocr_pages=int(options.get("document_ocr_pages") or 0),
                    usage_multiplier=usage_multiplier_for(options),
                )
            except BillingError as exc:
                return _quota_error(
                    job_id,
                    exc,
                    job_dir=job_dir,
                    data=data,
                    audio_keys=list(audio_keys),
                    visual_keys=list(visual_keys or []),
                )

            started = time.time()
            JOBS.update(
                job_id,
                worker_state="processing",
                media_minutes=round(media_minutes, 2),
                file_size_bytes=source_size,
                eta_seconds=estimate_eta_seconds(
                    media_minutes,
                    source_size,
                    job_type=str(options.get("job_type") or "study_pack"),
                    summary_style=str(options.get("summary_style") or "standard"),
                    source_kind="document" if options.get("document_mode") else "media",
                ),
                eta_started_at=time.time(),
            )
            pipeline_options["_worker_managed"] = True
            process_job(job_id, audio_paths, pipeline_options, visual_paths or None)
            enforce_job_workspace(job_dir, work_root=WORK_DIR)
            finished = JOBS.get(job_id) or {}
            if finished.get("status") != "done":
                transient = _processing_error(finished)
                if transient:
                    raise transient
                JOBS.update(job_id, status="error", worker_state="rejected", notify_failure=True)
                _cleanup_terminal_sources(
                    job_dir,
                    data=finished or data,
                    audio_keys=list(audio_keys),
                    visual_keys=list(visual_keys or []),
                )
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
        except SoftTimeLimitExceeded:
            return _time_limit_error(
                job_id,
                job_dir=job_dir,
                audio_keys=list(audio_keys),
                visual_keys=list(visual_keys or []),
            )
        except Exception as exc:
            return _retry_or_fail(
                self,
                job_id,
                exc,
                job_dir=job_dir,
                data=JOBS.get(job_id) or data,
                audio_keys=list(audio_keys),
                visual_keys=list(visual_keys or []),
            )


@celery_app.task(bind=True, max_retries=0, name="lecturesift.process_url_job")
def process_url_job(self, job_id: str, url: str, options: dict) -> dict:
    """Drain legacy queued URL jobs without fetching a source or charging usage."""
    with JOBS.processing_lock(job_id) as acquired:
        if not acquired:
            return {"job_id": job_id, "status": "duplicate"}
        data = JOBS.get(job_id)
        if not data:
            return {"job_id": job_id, "status": "missing"}
        if data.get("status") == "done":
            return {"job_id": job_id, "status": "done"}
        JOBS.update(job_id, status="error", stage="error", worker_state="error", percent=0,
                    error_code="LS-URL-06",
                    error="Bağlantıyla kaynak ekleme kaldırıldı. Video, ses veya belge dosyanı yükle.",
                    notify_failure=True)
        _cleanup_terminal_sources(_stored_job_dir(data), data=data)
        return {"job_id": job_id, "status": "error"}
