#!/usr/bin/env python3
"""Fail closed unless every OVH Instagram publisher path is disabled and stopped."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys
from typing import Callable


RUNTIME_ENV = Path("/etc/lecturesift/runtime.env")
INSTAGRAM_ENV = Path("/etc/lecturesift/instagram.env")
MAX_ENV_BYTES = 64 * 1024


class PublisherStateError(RuntimeError):
    pass


def _automation_disabled(path: Path) -> None:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise PublisherStateError("an Instagram environment file is unavailable") from exc
    if not raw or len(raw) > MAX_ENV_BYTES or b"\0" in raw:
        raise PublisherStateError("an Instagram environment file is invalid")
    try:
        lines = raw.decode("utf-8", errors="strict").splitlines()
    except UnicodeError as exc:
        raise PublisherStateError("an Instagram environment file is not UTF-8") from exc
    assignments = [line for line in lines if line.startswith("INSTAGRAM_DAILY_AUTOMATION_ENABLED=")]
    if assignments != ["INSTAGRAM_DAILY_AUTOMATION_ENABLED=false"]:
        raise PublisherStateError(
            "daily Instagram automation must be exactly false in runtime and generated configuration"
        )


def verify_publishers_stopped(
    runtime_env: Path,
    instagram_env: Path,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> None:
    _automation_disabled(runtime_env)
    _automation_disabled(instagram_env)
    for unit, allowed_enablement in (
        ("lecturesift-instagram.timer", "disabled"),
        ("lecturesift-instagram.service", "static"),
    ):
        active = runner(
            ["systemctl", "show", "--property=ActiveState", "--value", unit],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
        enabled = runner(
            ["systemctl", "is-enabled", unit],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
        if active.returncode != 0 or active.stdout.strip() != "inactive":
            raise PublisherStateError("an Instagram systemd unit is active or unprovable")
        if enabled.stdout.strip() != allowed_enablement:
            raise PublisherStateError("an Instagram systemd unit has unsafe boot enablement")
    running = runner(
        ["docker", "ps", "--quiet", "--filter", "label=com.docker.compose.service=instagram"],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    if running.returncode != 0 or running.stdout.strip():
        raise PublisherStateError("a Compose Instagram publisher container is running or unprovable")


def main() -> int:
    try:
        verify_publishers_stopped(RUNTIME_ENV, INSTAGRAM_ENV)
        print("INSTAGRAM_PUBLISHERS_STOPPED")
        return 0
    except PublisherStateError as exc:
        print(f"Instagram publisher verification failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
