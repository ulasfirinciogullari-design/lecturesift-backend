"""Production request throttling and API security headers."""

from __future__ import annotations

import hashlib
import threading
import time
from collections import defaultdict, deque

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from redis import Redis

from .config import RATE_LIMIT_ENABLED, REDIS_URL


_LIMITS = {
    ("POST", "/billing/register"): (6, 3600),
    ("POST", "/billing/login"): (15, 900),
    ("POST", "/billing/resend-verification"): (5, 3600),
    ("POST", "/billing/forgot-password"): (5, 3600),
    ("POST", "/billing/verify-email"): (20, 3600),
    ("POST", "/billing/verify-email-code"): (20, 3600),
    ("POST", "/billing/paytr/checkout"): (20, 3600),
    ("POST", "/billing/refunds"): (6, 3600),
    ("POST", "/billing/guest-session"): (8, 3600),
    ("POST", "/jobs"): (30, 3600),
    ("POST", "/jobs/url"): (30, 3600),
}
_LOCAL: dict[str, deque[float]] = defaultdict(deque)
_LOCK = threading.Lock()
_REDIS = Redis.from_url(REDIS_URL, decode_responses=True) if REDIS_URL else None
_INSTALLED = False


def _ip(request: Request) -> str:
    forwarded = (request.headers.get("x-forwarded-for") or "").split(",", 1)[0].strip()
    return forwarded or (request.client.host if request.client else "unknown")


def _key(request: Request, window_seconds: int) -> str:
    identity = hashlib.sha256(_ip(request).encode("utf-8")).hexdigest()[:24]
    window = int(time.time() // window_seconds)
    return f"lecturesift:ratelimit:{request.method}:{request.url.path}:{identity}:{window}"


def _allowed(request: Request, limit: int, window_seconds: int) -> tuple[bool, int]:
    key = _key(request, window_seconds)
    if _REDIS is not None:
        try:
            pipeline = _REDIS.pipeline()
            pipeline.incr(key)
            pipeline.expire(key, window_seconds + 5)
            count, _ = pipeline.execute()
            return int(count) <= limit, max(1, window_seconds - int(time.time() % window_seconds))
        except Exception:
            pass
    now = time.monotonic()
    with _LOCK:
        entries = _LOCAL[key]
        cutoff = now - window_seconds
        while entries and entries[0] <= cutoff:
            entries.popleft()
        if len(entries) >= limit:
            return False, max(1, int(window_seconds - (now - entries[0])))
        entries.append(now)
        return True, window_seconds


def install_security(app: FastAPI) -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    @app.middleware("http")
    async def lecturesift_security(request: Request, call_next):
        rule = _LIMITS.get((request.method.upper(), request.url.path))
        if RATE_LIMIT_ENABLED and rule and request.method.upper() != "OPTIONS":
            allowed, retry_after = _allowed(request, *rule)
            if not allowed:
                return JSONResponse(
                    status_code=429,
                    content={"detail": {"code": "LS-RATE-01", "message": "Çok fazla istek gönderildi. Bir süre sonra yeniden dene.", "retry_after": retry_after}},
                    headers={"Retry-After": str(retry_after)},
                )
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
        response.headers.setdefault("Cache-Control", "no-store")
        if request.url.scheme == "https":
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response

    _INSTALLED = True
