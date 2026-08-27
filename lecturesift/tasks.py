import shutil
from pathlib import Path

from .config import WORK_DIR
from .jobs import JOBS
from .media import download_remote_video
from .pipeline import process_job
from .queue import celery_app
from .storage import STORAGE


def _fresh_job_dir(job_id: str) -> Path:
    job_dir = WORK_DIR / job_id
    if job_dir.exists():
        shutil.rmtree(job_dir, ignore_errors=True)
    job_dir.mkdir(parents=True, exist_ok=True)
    return job_dir


def _finish_remote(job_id: str, job_dir: Path) -> None:
    data = JOBS.get(job_id) or {}
    if data.get("status") != "done":
        raise RuntimeError(data.get("technical_error") or data.get("error") or "LectureSift job failed")
    manifest = STORAGE.publish_job(job_id, job_dir)
    JOBS.update(job_id, **manifest, worker_state="published")


@celery_app.task(bind=True, autoretry_for=(Exception,), retry_backoff=True, retry_jitter=True, max_retries=3)
def process_uploaded_job(self, job_id: str, audio_keys: list[str], options: dict, visual_keys: list[str] | None = None) -> None:
    job_dir = _fresh_job_dir(job_id)
    JOBS.update(job_id, job_dir=str(job_dir), status="working", worker_state="materializing", retry_count=self.request.retries)
    audio_paths: list[Path] = []
    for index, key in enumerate(audio_keys, 1):
        destination = job_dir / f"audio_{index:03d}{Path(key).suffix}"
        audio_paths.append(STORAGE.download_file(key, destination))
    visual_paths: list[Path] | None = None
    if visual_keys:
        visual_paths = []
        for index, key in enumerate(visual_keys, 1):
            destination = job_dir / f"visual_{index:03d}{Path(key).suffix}"
            visual_paths.append(STORAGE.download_file(key, destination))
    JOBS.update(job_id, worker_state="processing")
    process_job(job_id, audio_paths, options, visual_paths)
    _finish_remote(job_id, job_dir)


@celery_app.task(bind=True, autoretry_for=(Exception,), retry_backoff=True, retry_jitter=True, max_retries=3)
def process_url_job(self, job_id: str, url: str, options: dict) -> None:
    job_dir = _fresh_job_dir(job_id)
    JOBS.update(job_id, job_dir=str(job_dir), status="working", stage="url_download", worker_state="downloading", retry_count=self.request.retries)
    video_path = download_remote_video(url, job_dir)
    JOBS.update(job_id, worker_state="processing")
    process_job(job_id, video_path, options)
    _finish_remote(job_id, job_dir)
