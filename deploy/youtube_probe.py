"""One bounded public sample; external availability is not a unit-test result."""

import contextlib
import io
import json
import tempfile
import time
from pathlib import Path

from lecturesift import media
from lecturesift.errors import normalize_error

# Seven-second public sample from the pinned yt-dlp YouTube extractor tests.
SAMPLE_URL = "https://www.youtube.com/watch?v=x41yOUIvK2k"
media.MAX_VIDEO_BYTES = 16 * 1024 * 1024
started = time.monotonic()
result = {"sample_id": "x41yOUIvK2k", "runner": "github_actions", "production_verified": False}

try:
    with tempfile.TemporaryDirectory(prefix="lecturesift-youtube-probe-") as folder:
        # Do not retain provider diagnostics, signed stream URLs or media files.
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            path = media.download_remote_video(SAMPLE_URL, Path(folder), job_type="audio_export", include_slides=False)
            if not media.has_audio_stream(path):
                raise RuntimeError("Downloaded sample has no audio")
        result.update(status="downloaded", bytes=path.stat().st_size)
except Exception as exc:
    result.update(status="unavailable", error_code=normalize_error(exc).code)
    cause = str(exc.__cause__ or exc).lower()
    result["failure_kind"] = (
        "rate_limit" if "429" in cause else
        "bot_challenge" if "not a bot" in cause else
        "account_required" if "sign in" in cause else
        "http_403" if "403" in cause else "other"
    )

result["elapsed_seconds"] = round(time.monotonic() - started, 1)
print(json.dumps(result, sort_keys=True), flush=True)
raise SystemExit(0 if result["status"] == "downloaded" else 1)
