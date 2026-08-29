"""Deployment-time Instagram safety check for the LectureSift account.

The helper keeps the completed 3:4 launch grid idempotent and, after the daily
publishing window, repairs a missing bilingual Reel exactly once by relying on
the same marker-based checks as the scheduled publisher.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

from .daily_social import _client, publish_daily_post, publish_next_launch_post
from .instagram import InstagramAPIError, InstagramConfigurationError
from .launch_social import LAUNCH_POSTS, completed_indices

_STATUS_PATH = Path("/tmp/lecturesift-launch-bootstrap.json")
_FINAL_PRIOR = list(range(1, 9))
_COMPLETE = list(range(1, 10))
_DAILY_WINDOW_UTC_HOUR = 17  # 20:00 Europe/Istanbul year-round.


def _write_status(payload: dict) -> None:
    try:
        _STATUS_PATH.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def _today_marker(day: date) -> str:
    return f"#LectureSiftGununNotu{day:%Y%m%d}"


def main() -> int:
    _write_status({"status": "waiting"})
    time.sleep(30)
    last_error: Exception | None = None

    for attempt in range(4):
        try:
            client = _client()
            recent = client.get_recent_media(limit=50).get("data", [])
            completed = completed_indices(recent)

            if completed == _FINAL_PRIOR:
                result = publish_next_launch_post(force=True)
                _write_status({"status": result.get("status"), "kind": "launch", "index": result.get("index")})
                print(f"Instagram deployment check: {result.get('status')}", flush=True)
                return 0

            if completed != _COMPLETE:
                _write_status(
                    {
                        "status": "launch_not_complete",
                        "completed": completed,
                        "expected_completed": _COMPLETE,
                    }
                )
                print(f"Instagram deployment check skipped: completed={completed}", flush=True)
                return 0

            now_utc = datetime.now(timezone.utc)
            if now_utc.hour < _DAILY_WINDOW_UTC_HOUR:
                _write_status({"status": "daily_window_not_open", "completed": completed})
                print("Instagram deployment check: daily window not open", flush=True)
                return 0

            selected_day = now_utc.date()
            marker = _today_marker(selected_day)
            if any(marker in (item.get("caption") or "") for item in recent):
                _write_status({"status": "daily_already_published", "date": selected_day.isoformat()})
                print("Instagram deployment check: daily Reel already published", flush=True)
                return 0

            result = publish_daily_post(selected_day)
            safe_result = {
                key: value
                for key, value in result.items()
                if key in {"status", "kind", "media_type", "date"}
            }
            _write_status(safe_result)
            print(f"Instagram deployment check: {result.get('status')}", flush=True)
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

    print(f"Instagram deployment check failed: {type(last_error).__name__ if last_error else 'unknown'}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
