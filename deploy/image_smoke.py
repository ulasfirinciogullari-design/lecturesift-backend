"""Fail-fast production image capability check with no external credentials."""

from __future__ import annotations

import importlib
import json
import os
import re
import subprocess
from pathlib import Path


MODULES = (
    "cv2",
    "docx",
    "PIL",
    "pptx",
    "pypdfium2",
)
OCR_LANGUAGES = {
    "ara",
    "chi_sim",
    "deu",
    "eng",
    "fra",
    "hin",
    "ita",
    "jpn",
    "kor",
    "osd",
    "por",
    "rus",
    "spa",
    "tur",
}


build_revision = os.getenv("LECTURESIFT_BUILD_REVISION", "unknown").strip().lower()
if build_revision != "unknown" and re.fullmatch(r"[0-9a-f]{40}", build_revision) is None:
    raise SystemExit("LECTURESIFT_BUILD_REVISION must be unknown or one full commit id")


for module_name in MODULES:
    importlib.import_module(module_name)

for binary in ("ffmpeg", "ffprobe"):
    subprocess.run(
        [binary, "-version"],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

language_output = subprocess.check_output(
    ["tesseract", "--list-langs"],
    text=True,
    stderr=subprocess.DEVNULL,
)
available_languages = set(language_output.splitlines()[1:])
missing_languages = sorted(OCR_LANGUAGES - available_languages)
if missing_languages:
    raise SystemExit(f"Missing OCR languages: {', '.join(missing_languages)}")

probe_path = Path("/var/lib/lecturesift/image-smoke-probe")
probe_path.write_text("ok", encoding="utf-8")
probe_path.unlink()

print(
    json.dumps(
        {
            "ok": True,
            "modules": list(MODULES),
            "ocr_languages": sorted(OCR_LANGUAGES),
            "revision": build_revision,
            "work_dir_writable": True,
        },
        sort_keys=True,
    )
)
