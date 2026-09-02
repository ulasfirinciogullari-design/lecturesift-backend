"""Short-lived, cross-process circuit breakers for paid AI providers.

The first provider billing/authentication failure is necessarily discovered by
an API call.  Once discovered, the worker records it here so the web process
can reject later AI jobs *before* accepting and uploading a large source file.
The marker expires automatically, allowing a cheap retry after an operator has
restored the provider balance or credentials.
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Any

from redis import Redis

from .config import REDIS_URL
from .errors import LectureSiftError


class AIProviderCircuitBreaker:
    REDIS_KEY = "lecturesift:provider:openai:block:v1"
    BLOCKING_CODES = frozenset({"LS-AI-01", "LS-AI-03"})

    def __init__(
        self,
        *,
        redis_url: str | None = REDIS_URL,
        redis_client: Any | None = None,
        cooldown_seconds: int | None = None,
    ) -> None:
        configured_cooldown = cooldown_seconds
        if configured_cooldown is None:
            try:
                configured_cooldown = int(os.getenv("AI_PROVIDER_BLOCK_SECONDS", "300"))
            except ValueError:
                configured_cooldown = 300
        self.cooldown_seconds = max(60, min(3600, int(configured_cooldown)))
        self._redis = (
            redis_client
            if redis_client is not None
            else (
                Redis.from_url(
                    redis_url,
                    decode_responses=True,
                    socket_connect_timeout=1,
                    socket_timeout=1,
                    retry_on_timeout=False,
                )
                if redis_url
                else None
            )
        )
        self._local: dict[str, Any] | None = None
        self._lock = threading.RLock()

    def trip(self, code: str) -> bool:
        """Block new AI jobs after a provider credit/authentication failure."""
        if code not in self.BLOCKING_CODES:
            return False
        now = time.time()
        state = {
            "provider": "openai",
            "code": code,
            "blocked_at": now,
            "retry_at": now + self.cooldown_seconds,
        }
        encoded = json.dumps(state, separators=(",", ":"))
        stored_remotely = False
        if self._redis is not None:
            try:
                self._redis.setex(self.REDIS_KEY, self.cooldown_seconds, encoded)
                stored_remotely = True
            except Exception:
                stored_remotely = False
        with self._lock:
            self._local = state
        return stored_remotely

    def trip_error(self, error: LectureSiftError) -> bool:
        return self.trip(error.code)

    @staticmethod
    def _decode(value: Any) -> dict[str, Any] | None:
        if isinstance(value, bytes):
            value = value.decode("utf-8", errors="replace")
        if not isinstance(value, str) or not value:
            return None
        try:
            state = json.loads(value)
        except (TypeError, ValueError):
            return None
        return state if isinstance(state, dict) else None

    def status(self) -> dict[str, Any] | None:
        state = None
        if self._redis is not None:
            try:
                state = self._decode(self._redis.get(self.REDIS_KEY))
            except Exception:
                state = None
        if state is None:
            with self._lock:
                state = dict(self._local) if self._local else None
        if not state:
            return None
        try:
            retry_at = float(state.get("retry_at") or 0)
        except (TypeError, ValueError):
            retry_at = 0
        if retry_at <= time.time():
            self.clear()
            return None
        return {
            "provider": "openai",
            "code": str(state.get("code") or "LS-AI-01"),
            "blocked": True,
            "retry_after_seconds": max(1, int(retry_at - time.time())),
        }

    def clear(self) -> None:
        if self._redis is not None:
            try:
                self._redis.delete(self.REDIS_KEY)
            except Exception:
                pass
        with self._lock:
            self._local = None

    def require_available(self) -> None:
        state = self.status()
        if not state:
            return
        code = str(state["code"])
        if code == "LS-AI-03":
            message = (
                "Yapay zekâ bağlantısı sunucu tarafında doğrulanamadı. "
                "Yönetici yapılandırmayı yeniledikten sonra tekrar dene."
            )
        else:
            message = (
                "LectureSift'in yapay zekâ sağlayıcı kredisi veya harcama limiti dolmuş. "
                "Bu senin plan dakikan değil; yönetici API bakiyesini yeniledikten sonra yeniden dene."
            )
        raise LectureSiftError(code, message, status_code=503)


AI_PROVIDER_CIRCUIT = AIProviderCircuitBreaker()
