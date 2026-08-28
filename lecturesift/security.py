"""Small distributed-capable rate limiter for sensitive public endpoints."""

from __future__ import annotations

import hashlib
import threading
import time

from redis import Redis

from .config import REDIS_URL


class RateLimitExceeded(Exception):
    def __init__(self, retry_after: int):
        super().__init__("Çok fazla deneme yapıldı. Lütfen biraz sonra tekrar dene.")
        self.retry_after = max(1, int(retry_after))


class RateLimiter:
    def __init__(self) -> None:
        self._redis = Redis.from_url(REDIS_URL, decode_responses=True) if REDIS_URL else None
        self._local: dict[str, tuple[int, int]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _key(scope: str, identity: str, window_seconds: int, now: int) -> tuple[str, int]:
        safe_scope = "".join(char for char in scope if char.isalnum() or char in "-_")[:40]
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        bucket = now // window_seconds
        return f"lecturesift:rate:{safe_scope}:{digest}:{bucket}", bucket

    def check(self, scope: str, identity: str, *, limit: int, window_seconds: int) -> None:
        now = int(time.time())
        safe_window = max(1, int(window_seconds))
        safe_limit = max(1, int(limit))
        key, bucket = self._key(scope, identity, safe_window, now)
        retry_after = safe_window - (now % safe_window)
        if self._redis is not None:
            try:
                pipeline = self._redis.pipeline(transaction=True)
                pipeline.incr(key)
                pipeline.expire(key, safe_window + 5)
                count, _ = pipeline.execute()
                if int(count) > safe_limit:
                    raise RateLimitExceeded(retry_after)
                return
            except RateLimitExceeded:
                raise
            except Exception:
                # Authentication remains available during a Redis incident;
                # the process-local fallback still limits repeated attempts.
                pass
        with self._lock:
            count, stored_bucket = self._local.get(key, (0, bucket))
            count = count + 1 if stored_bucket == bucket else 1
            self._local[key] = (count, bucket)
            if len(self._local) > 10_000:
                self._local = {
                    item_key: value
                    for item_key, value in self._local.items()
                    if value[1] >= bucket - 1
                }
            if count > safe_limit:
                raise RateLimitExceeded(retry_after)


RATE_LIMITER = RateLimiter()
