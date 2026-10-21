"""Disk-backed temporary directory for the API process.

Starlette spools every multipart upload part over 1 MB to ``tempfile`` before
the route runs. The API container's /tmp is a small memory-backed tmpfs, so
production points TMPDIR at a private directory on the API work volume.
"""

from __future__ import annotations

import logging
import os
import shutil
import stat
import tempfile
import time
from pathlib import Path

from . import config

LOGGER = logging.getLogger(__name__)
# Upload spools are anonymous files that vanish with their request. Named
# entries come from renders or document checks that a crash interrupted.
STALE_TEMP_SECONDS = 6 * 60 * 60


def api_temp_dir() -> Path | None:
    """TMPDIR when it names a dedicated directory inside the work volume.

    A developer's own TMPDIR (macOS always sets one) is left untouched.
    """
    value = os.environ.get("TMPDIR", "").strip()
    if not value or not os.path.isabs(value):
        return None
    path = Path(os.path.abspath(value))
    work_root = Path(os.path.abspath(config.WORK_DIR))
    if work_root not in path.parents:
        return None
    return path


def prepare_api_temp_dir() -> Path | None:
    """Create the private temp directory, use it for ``tempfile`` and prune it."""
    path = api_temp_dir()
    if path is None:
        return None
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    details = path.lstat()
    if not stat.S_ISDIR(details.st_mode) or details.st_uid != os.geteuid():
        raise RuntimeError("API temporary directory is not a private directory owned by the API user")
    os.chmod(path, 0o700)
    # tempfile caches its directory on first use; it may have fallen back to
    # /tmp if something asked before this directory existed.
    tempfile.tempdir = str(path)
    clean_stale_temp_entries(path)
    return path


def clean_stale_temp_entries(path: Path | None = None, *, max_age_seconds: int = STALE_TEMP_SECONDS) -> int:
    """Remove top-level temp entries untouched for longer than ``max_age_seconds``."""
    path = path or api_temp_dir()
    if path is None:
        return 0
    cutoff = time.time() - max_age_seconds
    removed = 0
    try:
        entries = list(os.scandir(path))
    except FileNotFoundError:
        return 0
    for entry in entries:
        try:
            if entry.stat(follow_symlinks=False).st_mtime > cutoff:
                continue
            if entry.is_dir(follow_symlinks=False):
                shutil.rmtree(entry.path)
            else:
                os.unlink(entry.path)
            removed += 1
        except OSError:
            continue
    if removed:
        LOGGER.info("Removed %d stale API temporary entries", removed)
    return removed
