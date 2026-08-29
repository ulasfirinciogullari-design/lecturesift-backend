from __future__ import annotations

import importlib
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str


MODULES = {
    "fastapi": "fastapi",
    "uvicorn": "uvicorn",
    "python-multipart": "multipart",
    "openai": "openai",
    "opencv": "cv2",
    "numpy": "numpy",
    "yt-dlp": "yt_dlp",
    "reportlab": "reportlab",
    "python-docx": "docx",
    "python-pptx": "pptx",
    "pypdf": "pypdf",
    "pypdfium2": "pypdfium2",
    "httpx": "httpx",
    "Pillow": "PIL",
    "SQLAlchemy": "sqlalchemy",
    "psycopg": "psycopg",
    "celery": "celery",
    "redis": "redis",
    "boto3": "boto3",
    "pytest": "pytest",
    "PyYAML": "yaml",
}


def command_output(command: list[str]) -> tuple[bool, str]:
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    output = (completed.stdout or completed.stderr).strip()
    first_line = output.splitlines()[0] if output else f"exit={completed.returncode}"
    return completed.returncode == 0, first_line


def main() -> int:
    checks: list[Check] = []
    checks.append(
        Check(
            "Python",
            sys.version_info >= (3, 12),
            f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        )
    )

    for display_name, module_name in MODULES.items():
        try:
            module = importlib.import_module(module_name)
        except Exception as exc:  # pragma: no cover - diagnostic utility
            checks.append(Check(display_name, False, f"import failed: {exc}"))
            continue
        version = getattr(module, "__version__", "available")
        checks.append(Check(display_name, True, str(version)))

    required_commands = ["ffmpeg", "ffprobe", "tesseract"]
    if os.name == "nt":
        required_commands.append("7z")
    for command in required_commands:
        path = shutil.which(command)
        checks.append(Check(command, bool(path), path or "not found"))

    if shutil.which("ffmpeg"):
        ok, detail = command_output(["ffmpeg", "-version"])
        checks.append(Check("FFmpeg execution", ok, detail))
        encoders = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True,
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
        )
        checks.append(
            Check(
                "NVIDIA FFmpeg encoders",
                True,
                "h264_nvenc + hevc_nvenc" if "h264_nvenc" in encoders.stdout and "hevc_nvenc" in encoders.stdout else "optional encoders unavailable",
            )
        )

    if shutil.which("tesseract"):
        ok, detail = command_output(["tesseract", "--version"])
        checks.append(Check("Tesseract execution", ok, detail))
        completed = subprocess.run(
            ["tesseract", "--list-langs"],
            capture_output=True,
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
        )
        language_output = "\n".join((completed.stdout, completed.stderr))
        languages = {
            line.strip()
            for line in language_output.splitlines()
            if line.strip() and not line.startswith("List of available languages")
        }
        checks.append(
            Check(
                "OCR languages",
                {"eng", "tur"}.issubset(languages),
                f"{len(languages)} detected; eng/tur required",
            )
        )

    width = max(len(check.name) for check in checks)
    for check in checks:
        marker = "OK" if check.ok else "FAIL"
        print(f"[{marker:4}] {check.name:<{width}}  {check.detail}")

    failed = [check for check in checks if not check.ok]
    if failed:
        print(f"\nEnvironment check failed: {len(failed)} item(s).", file=sys.stderr)
        return 1
    print("\nLectureSift local environment is ready.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
