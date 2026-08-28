import json
import shutil
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from redis import Redis

from .config import JOB_TTL_SECONDS, REDIS_URL, WORK_DIR
from .storage import STORAGE


class JobStore:
    TASK_WEIGHTS = {"visual": 38.0, "audio": 32.0}
    REDIS_KEY = "lecturesift:jobs:v2"
    REDIS_LOCK_KEY = "lecturesift:jobs:v2:write-lock"

    def __init__(self) -> None:
        self._jobs: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()
        self._state_path = WORK_DIR / "jobs-state.json"
        self._redis: Redis | None = Redis.from_url(REDIS_URL, decode_responses=True) if REDIS_URL else None
        self._load()

    @contextmanager
    def _distributed_write_lock(self) -> Iterator[None]:
        """Prevent the web process and worker from overwriting each other's jobs.

        The local file fallback remains available if Redis is temporarily
        unreachable, while configured Redis deployments serialize full-state
        read/modify/write operations with a short renewable lock.
        """
        lock = None
        acquired = False
        if self._redis is not None:
            try:
                lock = self._redis.lock(
                    self.REDIS_LOCK_KEY,
                    timeout=30,
                    blocking_timeout=10,
                    thread_local=False,
                )
                acquired = bool(lock.acquire(blocking=True))
            except Exception:
                lock = None
                acquired = False
        try:
            yield
        finally:
            if lock is not None and acquired:
                try:
                    lock.release()
                except Exception:
                    pass

    @contextmanager
    def processing_lock(self, job_id: str) -> Iterator[bool]:
        """Keep one worker on a job while allowing fast recovery after a crash."""
        if self._redis is None:
            yield True
            return

        try:
            lock = self._redis.lock(
                f"lecturesift:job:{job_id}:processing",
                timeout=180,
                blocking_timeout=0,
                thread_local=False,
            )
            acquired = bool(lock.acquire(blocking=False))
        except Exception:
            # A Celery worker already depends on Redis. Fail closed so its
            # normal retry path cannot run the same long job twice.
            acquired = False
            lock = None
        if not acquired:
            yield False
            return

        stop = threading.Event()

        def renew() -> None:
            while not stop.wait(60):
                try:
                    if lock is not None:
                        lock.extend(180, replace_ttl=True)
                except Exception:
                    return

        refresher = threading.Thread(target=renew, daemon=True, name=f"job-lock-{job_id[:8]}")
        refresher.start()
        try:
            yield True
        finally:
            stop.set()
            if lock is not None:
                try:
                    lock.release()
                except Exception:
                    pass

    def _load(self) -> None:
        text = ""
        if self._redis is not None:
            try:
                text = self._redis.get(self.REDIS_KEY) or ""
            except Exception:
                text = ""
        if not text and self._state_path.exists():
            try:
                text = self._state_path.read_text(encoding="utf-8")
            except OSError:
                text = ""
        if not text:
            return
        try:
            payload = json.loads(text)
            jobs = payload.get("jobs", {}) if isinstance(payload, dict) else {}
            if isinstance(jobs, dict):
                self._jobs = {str(job_id): value for job_id, value in jobs.items() if isinstance(value, dict)}
        except (TypeError, ValueError):
            self._jobs = {}

    def _refresh_locked(self) -> None:
        if self._redis is None:
            return
        try:
            text = self._redis.get(self.REDIS_KEY) or ""
            if not text:
                return
            payload = json.loads(text)
            jobs = payload.get("jobs", {}) if isinstance(payload, dict) else {}
            if isinstance(jobs, dict):
                self._jobs = {str(job_id): value for job_id, value in jobs.items() if isinstance(value, dict)}
        except Exception:
            return

    def _flush_locked(self) -> None:
        payload = json.dumps(
            {"version": 2, "saved_at": time.time(), "jobs": self._jobs},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        temporary = self._state_path.with_suffix(".tmp")
        temporary.write_text(payload, encoding="utf-8")
        temporary.replace(self._state_path)
        if self._redis is not None:
            try:
                self._redis.set(self.REDIS_KEY, payload)
            except Exception:
                # Local fallback remains usable when Redis is temporarily down.
                pass

    def _materialize_completed(self, data: dict[str, Any]) -> dict[str, Any]:
        if data.get("status") != "done" or not data.get("remote_prefix") or not STORAGE.remote:
            return data
        job_id = str(data.get("job_id", ""))
        if not job_id:
            return data
        local_dir = WORK_DIR / job_id
        if not (local_dir / "result.json").exists():
            try:
                local_dir.mkdir(parents=True, exist_ok=True)
                STORAGE.materialize_job(job_id, local_dir)
            except Exception:
                return data
        data["job_dir"] = str(local_dir)
        remote_download_key = str(data.get("remote_download_key", ""))
        if remote_download_key:
            local_zip = local_dir / Path(remote_download_key).name
            if local_zip.exists():
                data["result_path"] = str(local_zip)
        return data

    def create(self, job_id: str, job_dir: Path, options: dict, **extra: Any) -> dict:
        now = time.time()
        data = {
            "job_id": job_id,
            "status": "queued",
            "percent": 3,
            "stage": "queued",
            "created": now,
            "updated": now,
            "job_dir": str(job_dir),
            "options": options,
            "tasks": {
                "visual": {"percent": 0, "stage": "waiting"},
                "audio": {"percent": 0, "stage": "waiting"},
            },
            **extra,
        }
        with self._lock, self._distributed_write_lock():
            self._refresh_locked()
            self._jobs[job_id] = data
            self._flush_locked()
        return data.copy()

    def get(self, job_id: str) -> dict | None:
        with self._lock:
            self._refresh_locked()
            data = self._jobs.get(job_id)
            return self._materialize_completed(data.copy()) if data else None

    def update(self, job_id: str, **values: Any) -> None:
        with self._lock, self._distributed_write_lock():
            self._refresh_locked()
            data = self._jobs[job_id]
            data.update(values)
            data["updated"] = time.time()
            self._flush_locked()

    def update_task(self, job_id: str, task: str, percent: float, stage: str) -> None:
        with self._lock, self._distributed_write_lock():
            self._refresh_locked()
            data = self._jobs[job_id]
            tasks = data.setdefault("tasks", {})
            tasks[task] = {"percent": max(0, min(100, round(percent))), "stage": stage}
            weighted = 8.0
            for name, weight in self.TASK_WEIGHTS.items():
                weighted += weight * float(tasks.get(name, {}).get("percent", 0)) / 100.0
            data["percent"] = min(70, round(weighted))
            data["stage"] = "parallel_analysis"
            data["updated"] = time.time()
            self._flush_locked()

    def public(self, job_id: str) -> dict | None:
        data = self.get(job_id)
        if not data:
            return None
        for key in (
            "job_dir",
            "result_path",
            "technical_error",
            "source_keys",
            "celery_task_id",
            "queue_error",
            "recovery_error",
        ):
            data.pop(key, None)
        options = dict(data.get("options") or {})
        options.pop("billing_user_id", None)
        data["options"] = options
        return data

    def list_for_user(self, user_id: str, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            self._refresh_locked()
            owned = [
                data.copy()
                for data in self._jobs.values()
                if data.get("options", {}).get("billing_user_id") == user_id
            ]
        owned.sort(key=lambda item: float(item.get("created", 0)), reverse=True)
        return owned[: max(1, min(100, int(limit)))]

    def list_for_admin(self, limit: int = 100) -> list[dict[str, Any]]:
        """Return a secret-free, newest-first operational view of all jobs."""
        safe_limit = max(1, min(250, int(limit)))
        with self._lock:
            self._refresh_locked()
            job_ids = sorted(
                self._jobs,
                key=lambda job_id: float(self._jobs[job_id].get("created", 0)),
                reverse=True,
            )[:safe_limit]
            owners = {
                job_id: str(
                    (self._jobs[job_id].get("options") or {}).get("billing_user_id") or ""
                )
                for job_id in job_ids
            }
        jobs: list[dict[str, Any]] = []
        for job_id in job_ids:
            item = self.public(job_id)
            if not item:
                continue
            item["owner_id"] = owners[job_id] or None
            jobs.append(item)
        return jobs

    def delete_for_user(self, user_id: str) -> dict[str, int]:
        removed: list[tuple[str, Path]] = []
        with self._lock, self._distributed_write_lock():
            self._refresh_locked()
            for job_id, data in list(self._jobs.items()):
                if data.get("options", {}).get("billing_user_id") != user_id:
                    continue
                removed.append((job_id, Path(data.get("job_dir", WORK_DIR / job_id))))
                del self._jobs[job_id]
            if removed:
                self._flush_locked()
        remote_files = 0
        for job_id, path in removed:
            if path.is_dir() and path.parent == WORK_DIR:
                shutil.rmtree(path, ignore_errors=True)
            try:
                remote_files += STORAGE.delete_job(job_id)
            except Exception:
                # Account closure must still invalidate access immediately;
                # remote deletion can be completed by the retention process.
                pass
        return {"jobs": len(removed), "remote_files": remote_files}

    def redis_health(self) -> dict[str, bool]:
        if self._redis is None:
            return {"configured": False, "connected": False}
        try:
            return {"configured": True, "connected": bool(self._redis.ping())}
        except Exception:
            return {"configured": True, "connected": False}

    def recoverable(self) -> list[dict[str, Any]]:
        with self._lock:
            self._refresh_locked()
            return [
                data.copy()
                for data in self._jobs.values()
                if data.get("status") in {"queued", "working"}
            ]

    def cleanup_expired(self) -> int:
        now = time.time()
        removed: list[tuple[str, Path]] = []
        with self._lock, self._distributed_write_lock():
            self._refresh_locked()
            for job_id, data in list(self._jobs.items()):
                retention_seconds = max(60, int(data.get("retention_seconds", JOB_TTL_SECONDS)))
                if float(data.get("updated", 0)) < now - retention_seconds:
                    removed.append((job_id, Path(data.get("job_dir", WORK_DIR / job_id))))
                    del self._jobs[job_id]
            if removed:
                self._flush_locked()
        for job_id, path in removed:
            if path.is_dir() and path.parent == WORK_DIR:
                shutil.rmtree(path, ignore_errors=True)
            try:
                STORAGE.delete_job(job_id)
            except Exception:
                # The metadata is already inaccessible. A bucket lifecycle rule
                # remains the final safety net if the provider is unavailable.
                pass
        return len(removed)


JOBS = JobStore()
