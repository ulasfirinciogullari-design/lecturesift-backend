"""Celery application used by the dedicated LectureSift background worker."""

from celery import Celery

from .config import CELERY_BROKER_URL, CELERY_VISIBILITY_TIMEOUT_SECONDS, REDIS_URL


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
