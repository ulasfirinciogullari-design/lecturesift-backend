"""Reliable duration probing for quota checks and ETA calculations."""

from __future__ import annotations

import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Iterable

import cv2

from .config import DURATION_PROBE_PARALLELISM


def file_duration_seconds(path: Path) -> float:
    try:
        process = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=20,
            check=False,
        )
        if process.returncode == 0:
            value = float(process.stdout.strip() or 0)
            if value > 0:
                return value
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass

    capture = cv2.VideoCapture(str(path))
    try:
        fps = float(capture.get(cv2.CAP_PROP_FPS) or 0)
        frames = float(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        return frames / fps if fps > 0 and frames > 0 else 0.0
    finally:
        capture.release()


def media_duration_seconds(paths: Iterable[Path]) -> float:
    normalized = [Path(path) for path in paths]
    workers = min(DURATION_PROBE_PARALLELISM, len(normalized))
    if workers <= 1:
        durations = [file_duration_seconds(path) for path in normalized]
    else:
        with ThreadPoolExecutor(
            max_workers=workers,
            thread_name_prefix="lecturesift-duration",
        ) as executor:
            durations = list(executor.map(file_duration_seconds, normalized))
    return sum(max(0.0, value) for value in durations)
