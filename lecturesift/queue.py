"""Celery application used by the dedicated LectureSift background worker."""

import math

from celery import Celery

from .billing import LEGACY_PLANS, PLAN_BY_CODE
from .config import CELERY_BROKER_URL, CELERY_VISIBILITY_TIMEOUT_SECONDS, REDIS_URL
from .job_eta import MAX_SECONDS_PER_MEDIA_MINUTE, SUMMARY_STYLE_MULTIPLIERS, eta_seconds


_PLANS = (*PLAN_BY_CODE.values(), *LEGACY_PLANS)
LONGEST_JOB_MINUTES = max(plan.max_minutes_per_job for plan in _PLANS)


def _longest_legitimate_job_seconds() -> int:
    """The ETA model's longest estimate for the largest job any plan admits.

    That is the longest per-job media allowance of every current or legacy
    plan (purchase snapshots are copies of these), at the largest upload size,
    with the slowest summary style and at the slowest processing rate the
    model accepts as a learned median. A job the user is shown an ETA for can
    therefore never outlive it.
    """
    upload_bytes = max(plan.max_media_upload_mb for plan in _PLANS) * 1024 * 1024
    slowest_style = max(SUMMARY_STYLE_MULTIPLIERS, key=SUMMARY_STYLE_MULTIPLIERS.__getitem__)
    return eta_seconds(
        LONGEST_JOB_MINUTES, upload_bytes, MAX_SECONDS_PER_MEDIA_MINUTE, summary_style=slowest_style,
    )


# The soft limit raises SoftTimeLimitExceeded inside the job, which then fails
# through the normal terminal path; the hard limit kills a job that ignores it.
# These global values are the worst case: each new job is enqueued with its own,
# shorter limits from job_time_limits(), and they apply only to deliveries
# enqueued without (legacy records, legacy URL jobs).
JOB_HARD_LIMIT_GRACE_SECONDS = 10 * 60
JOB_SOFT_TIME_LIMIT_SECONDS = math.ceil(_longest_legitimate_job_seconds() / 3600) * 3600
JOB_HARD_TIME_LIMIT_SECONDS = JOB_SOFT_TIME_LIMIT_SECONDS + JOB_HARD_LIMIT_GRACE_SECONDS
# By the visibility timeout the broker has redelivered a message a lost worker
# held; the margin covers the redelivered message reaching the worker.
JOB_REDELIVERY_WINDOW_SECONDS = CELERY_VISIBILITY_TIMEOUT_SECONDS + 30 * 60
# A queued job may wait behind the longest admitted job on the single worker,
# so one that has recorded nothing for longer than that and the redelivery
# window can no longer start on its own. A started job uses its own deadline.
STUCK_JOB_AFTER_SECONDS = JOB_HARD_TIME_LIMIT_SECONDS + JOB_REDELIVERY_WINDOW_SECONDS
# A job's own limit: its ETA at the slowest rate the model accepts plus this
# margin, and never under the floor, since short files and documents still
# download, extract, OCR and wait on AI providers.
JOB_TIME_LIMIT_MARGIN_SECONDS = 30 * 60
JOB_MIN_SOFT_TIME_LIMIT_SECONDS = 60 * 60


def job_time_limits(
    media_minutes: float,
    size_bytes: int = 0,
    *,
    job_type: str = "study_pack",
    summary_style: str = "standard",
    source_kind: str = "media",
) -> tuple[int, int]:
    """Soft and hard time limit, in seconds, for one job of this size.

    No ETA the model can show for the job exceeds the soft limit, whatever
    median it has learned, and neither limit exceeds the global worst case.
    """
    estimate = eta_seconds(
        min(float(media_minutes), LONGEST_JOB_MINUTES),
        size_bytes,
        MAX_SECONDS_PER_MEDIA_MINUTE,
        job_type=job_type,
        summary_style=summary_style,
        source_kind=source_kind,
    )
    soft = math.ceil((estimate + JOB_TIME_LIMIT_MARGIN_SECONDS) / 60) * 60
    soft = min(JOB_SOFT_TIME_LIMIT_SECONDS, max(JOB_MIN_SOFT_TIME_LIMIT_SECONDS, soft))
    return soft, soft + JOB_HARD_LIMIT_GRACE_SECONDS


broker = CELERY_BROKER_URL or REDIS_URL or "memory://"
backend = REDIS_URL or CELERY_BROKER_URL or "cache+memory://"

celery_app = Celery(
    "lecturesift",
    broker=broker,
    backend=backend,
    include=["lecturesift.tasks"],
)
celery_app.conf.update(
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_track_started=True,
    task_soft_time_limit=JOB_SOFT_TIME_LIMIT_SECONDS,
    task_time_limit=JOB_HARD_TIME_LIMIT_SECONDS,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
    broker_transport_options={"visibility_timeout": CELERY_VISIBILITY_TIMEOUT_SECONDS},
    result_backend_transport_options={"visibility_timeout": CELERY_VISIBILITY_TIMEOUT_SECONDS},
    visibility_timeout=CELERY_VISIBILITY_TIMEOUT_SECONDS,
    result_expires=24 * 60 * 60,
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
)


def worker_health(timeout: float = 1.0) -> dict[str, bool | int]:
    configured = bool(CELERY_BROKER_URL or REDIS_URL)
    if not configured:
        return {"configured": False, "reachable": False, "workers": 0}
    try:
        replies = celery_app.control.ping(timeout=timeout) or []
        return {"configured": True, "reachable": bool(replies), "workers": len(replies)}
    except Exception:
        return {"configured": True, "reachable": False, "workers": 0}
