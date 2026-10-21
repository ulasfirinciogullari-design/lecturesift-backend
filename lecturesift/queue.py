"""Celery application used by the dedicated LectureSift background worker."""

import math

from celery import Celery

from .billing import LEGACY_PLANS, PLAN_BY_CODE
from .config import CELERY_BROKER_URL, CELERY_VISIBILITY_TIMEOUT_SECONDS, REDIS_URL
from .job_eta import MAX_SECONDS_PER_MEDIA_MINUTE, SUMMARY_STYLE_MULTIPLIERS, eta_seconds


def _longest_legitimate_job_seconds() -> int:
    """The ETA model's longest estimate for the largest job any plan admits.

    That is the longest per-job media allowance of every current or legacy
    plan (purchase snapshots are copies of these), at the largest upload size,
    with the slowest summary style and at the slowest processing rate the
    model accepts as a learned median. A job the user is shown an ETA for can
    therefore never outlive it.
    """
    plans = (*PLAN_BY_CODE.values(), *LEGACY_PLANS)
    media_minutes = max(plan.max_minutes_per_job for plan in plans)
    upload_bytes = max(plan.max_media_upload_mb for plan in plans) * 1024 * 1024
    slowest_style = max(SUMMARY_STYLE_MULTIPLIERS, key=SUMMARY_STYLE_MULTIPLIERS.__getitem__)
    return eta_seconds(media_minutes, upload_bytes, MAX_SECONDS_PER_MEDIA_MINUTE, summary_style=slowest_style)


# The soft limit raises SoftTimeLimitExceeded inside the job, which then fails
# through the normal terminal path; the hard limit kills a job that ignores it.
JOB_SOFT_TIME_LIMIT_SECONDS = math.ceil(_longest_legitimate_job_seconds() / 3600) * 3600
JOB_HARD_TIME_LIMIT_SECONDS = JOB_SOFT_TIME_LIMIT_SECONDS + 10 * 60
# Every delivery updates its job when it starts, so a job last updated more than
# the hard limit ago has no delivery still running, and by the visibility
# timeout the broker has redelivered a message a lost worker held. Past both,
# a queued/working job can no longer finish on its own.
STUCK_JOB_AFTER_SECONDS = max(JOB_HARD_TIME_LIMIT_SECONDS, CELERY_VISIBILITY_TIMEOUT_SECONDS) + 30 * 60


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
