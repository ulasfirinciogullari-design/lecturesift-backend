"""One bounded public sample; external availability is not a unit-test result."""

import contextlib
import io
import json
import os
import urllib.request
import tempfile
import time
from pathlib import Path

from lecturesift import media
from lecturesift.errors import normalize_error

# Sanitized attestation diagnostics distinguish an installed plugin from a token
# actually generated. Never print provider logs, tokens, cookies or signed URLs.
attestation_diagnostics = {"provider_seen": False, "token_generated": False, "provider_error": False}


class ProbeLogger:
    def debug(self, message):
        if "PO Token Providers:" in message and "bgutil:http" in message:
            attestation_diagnostics["provider_seen"] = True
        if "Generated POT:" in message:
            attestation_diagnostics["token_generated"] = True

    def warning(self, message):
        if "bgutil" in message.lower():
            attestation_diagnostics["provider_error"] = True

    def error(self, message):
        pass


original_downloader = media.yt_dlp.YoutubeDL


def instrumented_downloader(options):
    options["logger"] = ProbeLogger()
    options["verbose"] = True
    options["no_warnings"] = False
    options.setdefault("extractor_args", {}).setdefault("youtube", {})["pot_trace"] = ["true"]
    return original_downloader(options)


media.yt_dlp.YoutubeDL = instrumented_downloader

# Seven-second public sample from the pinned yt-dlp YouTube extractor tests.
SAMPLE_URL = "https://www.youtube.com/watch?v=x41yOUIvK2k"
media.MAX_VIDEO_BYTES = 16 * 1024 * 1024
pot_url = os.getenv("YOUTUBE_POT_BASE_URL", "")
if pot_url:
    for attempt in range(10):
        try:
            with urllib.request.urlopen(pot_url + "/ping", timeout=2) as ping:
                if json.load(ping).get("version") != "2.0.0":
                    raise ValueError("Unexpected attestation service version")
            break
        except Exception:
            if attempt == 9:
                print(json.dumps({"status": "attestation_service_unavailable", "production_verified": False, "attestation": bool(pot_url)}))
                raise SystemExit(1)
            time.sleep(1)
started = time.monotonic()
result = {"sample_id": "x41yOUIvK2k", "runner": "github_actions", "production_verified": False, "attestation": bool(pot_url)}

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
result["attestation_diagnostics"] = attestation_diagnostics
print(json.dumps(result, sort_keys=True), flush=True)
raise SystemExit(0 if result["status"] == "downloaded" else 1)
