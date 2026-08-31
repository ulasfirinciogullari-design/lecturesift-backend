"""Fail-closed disk budgets for transient per-job workspaces."""

from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path

from .config import WORK_DIR
from .errors import LectureSiftError


# These gates activate only where the deployment explicitly supplies them.
# That keeps an older Render rollback environment compatible while the OVH
# runtime.env turns both values on before cutover.
HOST_DISK_RESERVE_BYTES = max(
    0,
    int(os.getenv("LECTURESIFT_HOST_DISK_RESERVE_BYTES", "0")),
)
MAX_JOB_WORK_BYTES = max(
    0,
    int(os.getenv("LECTURESIFT_MAX_JOB_WORK_BYTES", "0")),
)


def _safe_job_dir(job_dir: Path, work_root: Path | None = None) -> tuple[Path, Path]:
    root = Path(work_root if work_root is not None else WORK_DIR).resolve()
    candidate = Path(job_dir).resolve(strict=False)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise LectureSiftError(
            "LS-STORAGE-02",
            "Geçici iş alanı güvenli biçimde hazırlanamadı.",
            "Job workspace escaped the configured work root",
            507,
        ) from exc
    if candidate == root:
        raise LectureSiftError(
            "LS-STORAGE-02",
            "Geçici iş alanı güvenli biçimde hazırlanamadı.",
            "The work root itself cannot be used as one job workspace",
            507,
        )
    return candidate, root


def job_workspace_bytes(job_dir: Path, *, work_root: Path | None = None) -> int:
    """Measure regular files without following links outside one job."""
    candidate, _root = _safe_job_dir(job_dir, work_root)
    if not candidate.exists():
        return 0
    total = 0
    for directory, directories, files in os.walk(candidate, followlinks=False):
        directory_path = Path(directory)
        for name in list(directories):
            child = directory_path / name
            if child.is_symlink():
                raise LectureSiftError(
                    "LS-STORAGE-02",
                    "Geçici iş alanı güvenli biçimde denetlenemedi.",
                    "Symlink found in job workspace",
                    507,
                )
        for name in files:
            child = directory_path / name
            details = child.lstat()
            if stat.S_ISLNK(details.st_mode):
                raise LectureSiftError(
                    "LS-STORAGE-02",
                    "Geçici iş alanı güvenli biçimde denetlenemedi.",
                    "Symlink found in job workspace",
                    507,
                )
            if stat.S_ISREG(details.st_mode):
                total += details.st_size
                if MAX_JOB_WORK_BYTES and total > MAX_JOB_WORK_BYTES:
                    break
        if MAX_JOB_WORK_BYTES and total > MAX_JOB_WORK_BYTES:
            break
    return total


def enforce_job_workspace(
    job_dir: Path,
    *,
    additional_bytes: int = 0,
    known_usage_bytes: int | None = None,
    reserve_full_budget: bool = False,
    work_root: Path | None = None,
) -> int:
    """Require both the per-job ceiling and the emergency host reserve.

    ``known_usage_bytes`` is used by the streamed upload loop, which already
    tracks every byte written for the job. Worker stage boundaries omit it and
    receive a symlink-safe scan instead. ``reserve_full_budget`` ensures one
    admitted long job can grow to its ceiling while preserving emergency disk.
    """
    additional = max(0, int(additional_bytes))
    if not MAX_JOB_WORK_BYTES and not HOST_DISK_RESERVE_BYTES:
        # Compatibility path for a still-live provider that predates these
        # explicit deployment values. OVH runtime.env enables both gates.
        return max(0, int(known_usage_bytes or 0)) + additional
    candidate, root = _safe_job_dir(job_dir, work_root)
    used = (
        max(0, int(known_usage_bytes))
        if known_usage_bytes is not None
        else job_workspace_bytes(candidate, work_root=root)
    )
    projected = used + additional
    if MAX_JOB_WORK_BYTES and projected > MAX_JOB_WORK_BYTES:
        raise LectureSiftError(
            "LS-STORAGE-03",
            "Bu işin geçici dosyaları güvenli iş alanı sınırını aşıyor.",
            f"Job workspace limit exceeded: {projected} > {MAX_JOB_WORK_BYTES}",
            507,
        )
    try:
        free = shutil.disk_usage(root).free
    except OSError as exc:
        raise LectureSiftError(
            "LS-STORAGE-01",
            "Sunucu depolama kapasitesi şu anda doğrulanamıyor. Lütfen biraz sonra yeniden dene.",
            f"Work filesystem capacity probe failed: {type(exc).__name__}",
            507,
        ) from exc
    growth = (
        max(0, MAX_JOB_WORK_BYTES - projected)
        if reserve_full_budget and MAX_JOB_WORK_BYTES
        else 0
    )
    required = additional + growth + HOST_DISK_RESERVE_BYTES
    if free < required:
        raise LectureSiftError(
            "LS-STORAGE-01",
            "Sunucuda güvenli işlem için yeterli geçici alan yok. Lütfen biraz sonra yeniden dene.",
            f"Work filesystem reserve would be crossed: free={free}, required={required}",
            507,
        )
    return projected
