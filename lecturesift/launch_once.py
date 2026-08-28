"""One-time launch-step publisher used after a web deployment.

This deployment is intentionally gated to advance the launch grid from exactly
two completed cards to the third card. Marker checks keep retries idempotent,
and later unrelated deploys cannot advance the grid again.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from .daily_social import _client, publish_next_launch_post
from .instagram import InstagramAPIError, InstagramConfigurationError
from .launch_social import completed_indices

_STATUS_PATH = Path("/tmp/lecturesift-launch-bootstrap.json")
_EXPECTED_COMPLETED = [1, 2]


def _write_status(payload: dict) -> None:
    try:
        _STATUS_PATH.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def main() -> int:
    _write_status({"status": "waiting", "target_index": 3})
    time.sleep(30)
    last_error: Exception | None = None
    for attempt in range(4):
        try:
            recent = _client().get_recent_media(limit=50).get("data", [])
            completed = completed_indices(recent)
            if completed != _EXPECTED_COMPLETED:
                _write_status(
                    {
                        "status": "launch_step_not_ready",
                        "completed": completed,
                        "expected_completed": _EXPECTED_COMPLETED,
                        "target_index": 3,
                    }
                )
                print(f"Launch bootstrap skipped: completed={completed}", flush=True)
                return 0

            result = publish_next_launch_post(force=True)
            safe_result = {
                key: value
                for key, value in result.items()
                if key in {"status", "kind", "index", "completed"}
            }
            safe_result["target_index"] = 3
            _write_status(safe_result)
            print(f"Launch bootstrap: {result.get('status')}", flush=True)
            return 0
        except (InstagramAPIError, InstagramConfigurationError, RuntimeError, KeyError) as exc:
            last_error = exc
            _write_status(
                {
                    "status": "retrying" if attempt < 3 else "error",
                    "attempt": attempt + 1,
                    "target_index": 3,
                    "error_type": getattr(exc, "error_type", type(exc).__name__),
                }
            )
            if attempt < 3:
                time.sleep(20 * (attempt + 1))
    print(f"Launch bootstrap failed: {type(last_error).__name__ if last_error else 'unknown'}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
