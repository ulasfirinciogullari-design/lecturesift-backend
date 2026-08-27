"""One-time first-launch publisher used after a web deployment.

It only publishes when no launch marker exists, so later deploys cannot advance
or duplicate the launch grid. The regular Render cron publishes subsequent cards.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from .daily_social import publish_next_launch_post
from .instagram import InstagramAPIError, InstagramConfigurationError

_STATUS_PATH = Path("/tmp/lecturesift-launch-bootstrap.json")


def _write_status(payload: dict) -> None:
    try:
        _STATUS_PATH.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def main() -> int:
    _write_status({"status": "waiting"})
    time.sleep(30)
    last_error: Exception | None = None
    for attempt in range(4):
        try:
            result = publish_next_launch_post(only_if_none_completed=True, force=True)
            safe_result = {
                key: value
                for key, value in result.items()
                if key in {"status", "kind", "index", "completed"}
            }
            _write_status(safe_result)
            print(f"Launch bootstrap: {result.get('status')}", flush=True)
            return 0
        except (InstagramAPIError, InstagramConfigurationError, RuntimeError, KeyError) as exc:
            last_error = exc
            _write_status(
                {
                    "status": "retrying" if attempt < 3 else "error",
                    "attempt": attempt + 1,
                    "error_type": getattr(exc, "error_type", type(exc).__name__),
                }
            )
            if attempt < 3:
                time.sleep(20 * (attempt + 1))
    print(f"Launch bootstrap failed: {type(last_error).__name__ if last_error else 'unknown'}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
