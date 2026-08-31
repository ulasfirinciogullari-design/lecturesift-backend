"""Export the one portable LectureSift Redis value during a frozen cutover."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
from urllib.parse import urlparse

from redis import Redis


STATE_KEY = "lecturesift:jobs:v2"
LOCK_KEY = "lecturesift:jobs:v2:write-lock"


def main() -> None:
    if len(sys.argv) != 2:
        raise RuntimeError("One private output path is required")
    output = Path(sys.argv[1])
    if output.name not in {
        "source-before.json",
        "source-after.json",
        "source-final.json",
    }:
        raise RuntimeError("Refusing an unexpected Redis migration output path")
    source_url = os.getenv("SOURCE_REDIS_URL", "")
    parsed_url = urlparse(source_url)
    if (
        parsed_url.scheme not in {"redis", "rediss"}
        or not parsed_url.hostname
        or parsed_url.hostname.casefold() in {"redis", "localhost", "127.0.0.1", "::1"}
    ):
        raise RuntimeError("A remote source Redis URL is required")

    client = Redis.from_url(
        source_url,
        decode_responses=True,
        socket_connect_timeout=10,
        socket_timeout=20,
        health_check_interval=15,
    )
    if not client.ping():
        raise RuntimeError("Source Redis did not answer the health probe")
    if client.exists(LOCK_KEY):
        raise RuntimeError("Source Redis still has an active state write lock")
    processing_locks = sum(
        1 for _ in client.scan_iter(match="lecturesift:job:*:processing", count=200)
    )
    if processing_locks:
        raise RuntimeError("Source Redis still has active processing locks")

    raw = client.get(STATE_KEY)
    if not raw:
        raw = json.dumps({"version": 2, "saved_at": 0, "jobs": {}}, separators=(",", ":"))
    payload = json.loads(raw)
    if not isinstance(payload, dict) or payload.get("version") != 2:
        raise RuntimeError("Source Redis state has an unsupported schema")
    jobs = payload.get("jobs")
    if not isinstance(jobs, dict) or any(not isinstance(value, dict) for value in jobs.values()):
        raise RuntimeError("Source Redis jobs payload is malformed")
    active = [
        str(job_id)
        for job_id, job in jobs.items()
        if str(job.get("status") or "") in {"queued", "working"}
    ]
    if active:
        raise RuntimeError("Source Redis still contains queued or working jobs")

    canonical = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    output.write_text(canonical, encoding="utf-8")
    output.chmod(0o600)
    terminal_counts: dict[str, int] = {}
    for job in jobs.values():
        status = str(job.get("status") or "unknown")
        terminal_counts[status] = terminal_counts.get(status, 0) + 1
    print(
        json.dumps(
            {
                "ok": True,
                "jobs": len(jobs),
                "terminal_counts": terminal_counts,
                "sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
                "active_locks": 0,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(json.dumps({"ok": False, "error_type": type(exc).__name__}, sort_keys=True))
        raise SystemExit(1) from None
