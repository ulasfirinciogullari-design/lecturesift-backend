import json
import shutil
import threading
import time
from pathlib import Path
from typing import Any

from redis import Redis

from .config import JOB_TTL_SECONDS, REDIS_URL, WORK_DIR
from .storage import STORAGE


class JobStore:
    TASK_WEIGHTS = {"visual": 38.0, "audio": 32.0}
    REDIS_KEY = "lecturesift:jobs:v3"

    def __init__(self) -> None:
        self._jobs: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()
        self._state_path = WORK_DIR / "jobs-state.json"
        self._redis: Redis | None = Redis.from_url(REDIS_URL, decode_responses=True) if REDIS_URL else None
        self._load()

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
        payload = json.dumps({"version": 3, "saved_at": time.time(), "jobs": self._jobs}, ensure_ascii=False, separators=(",", ":"))
        temporary = self._state_path.with_suffix(".tmp")
        temporary.write_text(payload, encoding="utf-8")
        temporary.replace(self._state_path)
        if self._redis is not None:
            try:
                self._redis.set(self.REDIS_KEY, payload)
            except Exception:
                pass

    def _materialize_completed(self, data: dict[str, Any]) -> dict[str, Any]:
        key = str(data.get("remote_download_key") or "")
        if data.get("status") != "done" or not key or not STORAGE.remote:
            return data
        job_id = str(data.get("job_id") or "")
        if not job_id:
            return data
        local_dir = WORK_DIR / job_id
        result_path = local_dir / "result.json"
        local_zip = local_dir / Path(key).name
        if not result_path.exists() or not local_zip.exists():
            try:
                local_dir.mkdir(parents=True, exist_ok=True)
                STORAGE.materialize_output(job_id, key, local_dir)
            except Exception:
                return data
        data["job_dir"] = str(local_dir)
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
            "tasks": {"visual": {"percent": 0, "stage": "waiting"}, "audio": {"percent": 0, "stage": "waiting"}},
            **extra,
        }
        with self._lock:
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
        with self._lock:
            self._refresh_locked()
            data = self._jobs.get(job_id)
            if data is None:
                return
            data.update(values)
            data["updated"] = time.time()
            self._flush_locked()

    def update_task(self, job_id: str, task: str, percent: float, stage: str) -> None:
        with self._lock:
            self._refresh_locked()
            data = self._jobs.get(job_id)
            if data is None:
                return
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
        for key in ("job_dir", "result_path", "technical_error", "source_keys", "queue_error"):
            data.pop(key, None)
        return data

    def recoverable(self) -> list[dict[str, Any]]:
        with self._lock:
            self._refresh_locked()
            return [data.copy() for data in self._jobs.values() if data.get("status") in {"queued", "working"}]

    def remove(self, job_id: str) -> dict | None:
        with self._lock:
            self._refresh_locked()
            data = self._jobs.pop(job_id, None)
            if data is not None:
                self._flush_locked()
        if data:
            path = Path(data.get("job_dir") or WORK_DIR / job_id)
            if path.is_dir() and path.parent == WORK_DIR:
                shutil.rmtree(path, ignore_errors=True)
        return data.copy() if data else None

    def cleanup_expired(self) -> int:
        cutoff = time.time() - JOB_TTL_SECONDS
        removed: list[Path] = []
        with self._lock:
            self._refresh_locked()
            for job_id, data in list(self._jobs.items()):
                if float(data.get("updated", 0)) < cutoff:
                    path = Path(data.get("job_dir") or WORK_DIR / job_id)
                    if path.is_dir() and path.parent == WORK_DIR:
                        removed.append(path)
                    if data.get("status") != "done" or not data.get("remote_download_key"):
                        del self._jobs[job_id]
            if removed:
                self._flush_locked()
        for path in removed:
            shutil.rmtree(path, ignore_errors=True)
        return len(removed)


JOBS = JobStore()
