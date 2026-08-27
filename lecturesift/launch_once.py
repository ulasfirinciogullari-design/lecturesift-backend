"""One-time first-launch publisher used after a web deployment.

It only publishes when no launch marker exists, so later deploys cannot advance
or duplicate the launch grid. The regular Render cron publishes subsequent cards.
"""

from __future__ import annotations

import sys
import time

from .daily_social import publish_next_launch_post
from .instagram import InstagramAPIError, InstagramConfigurationError


def main() -> int:
    time.sleep(30)
    last_error: Exception | None = None
    for attempt in range(4):
        try:
            result = publish_next_launch_post(only_if_none_completed=True)
            print(f"Launch bootstrap: {result.get('status')}", flush=True)
            return 0
        except (InstagramAPIError, InstagramConfigurationError, RuntimeError, KeyError) as exc:
            last_error = exc
            if attempt < 3:
                time.sleep(20 * (attempt + 1))
    print(f"Launch bootstrap failed: {last_error}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
