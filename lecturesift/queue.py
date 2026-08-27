from celery import Celery

from .config import CELERY_BROKER_URL


celery_app = Celery(
    "lecturesift",
    broker=CELERY_BROKER_URL or "redis://localhost:6379/0",
    backend=CELERY_BROKER_URL or "redis://localhost:6379/0",
    include=["lecturesift.tasks"],
)
celery_app.conf.update(
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_reject_on_worker_lost=True,
    broker_connection_retry_on_startup=True,
    result_expires=21600,
)
