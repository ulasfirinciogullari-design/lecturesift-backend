from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import pytest


ROOT = Path(__file__).resolve().parents[1]
CONTROLLERS = (
    "deploy/trusted_stage_release_controller.sh",
    "deploy/trusted_exact_rehearsal_controller.sh",
)


def _reconciler(relative: str) -> str:
    script = (ROOT / relative).read_text(encoding="utf-8")
    start = script.index("reconcile_stale_controller_state() {")
    marker = "\n}\nreconcile_stale_controller_state\n"
    end = script.index(marker, start) + 2
    return script[start:end]


def _harness(relative: str) -> str:
    return (
        r'''#!/usr/bin/env bash
set -Eeuo pipefail
STATE_ROOT=$1
COUNTER=$2
controller_lock="$STATE_ROOT/.controller.lock"
real_stat=$(command -v stat)

stat() {
  local format path device value count
  if [[ "$#" -ge 4 && "$1" == "-c" ]]; then
    format=$2
    path="${@: -1}"
    case "$format" in
      %u:%g:%a:%d)
        device="$("$real_stat" -c '%d' -- "$path")"
        printf '0:0:700:%s\n' "$device"
        return 0
        ;;
      %d:%i)
        value="$("$real_stat" -c '%d:%i' -- "$path")"
        if [[ "${MOCK_IDENTITY_CHANGE:-false}" == "true" && "$path" != "$STATE_ROOT" ]]; then
          count=0
          [[ ! -f "$COUNTER" ]] || count="$(wc -l <"$COUNTER")"
          printf 'x\n' >>"$COUNTER"
          if (( count >= 1 )); then
            printf '999999:999999\n'
            return 0
          fi
        fi
        printf '%s\n' "$value"
        return 0
        ;;
    esac
  fi
  "$real_stat" "$@"
}

find() {
  local status inventory=false argument
  [[ "${1:-}" != "$STATE_ROOT" ]] || inventory=true
  command find "$@"
  status=$?
  if [[ "$inventory" == "true" && "${MOCK_INVENTORY_FAILURE:-false}" == "true" ]]; then
    return 55
  fi
  return "$status"
}

findmnt() {
  [[ "${MOCK_FINDMNT_FAILURE:-false}" != "true" ]] || return 56
  [[ -z "${MOCK_MOUNT_TARGETS:-}" ]] || printf '%s\n' "$MOCK_MOUNT_TARGETS"
}

sync() { :; }
fail() { printf 'HARNESS_FAIL|%s\n' "$1" >&2; exit 97; }

'''
        + _reconciler(relative)
        + "\nreconcile_stale_controller_state\n"
    )


def _directory(path: Path, *, age_seconds: int) -> None:
    path.mkdir(mode=0o700)
    path.chmod(0o700)
    timestamp = time.time() - age_seconds
    os.utime(path, (timestamp, timestamp))


@pytest.mark.skipif(sys.platform != "linux", reason="requires Linux Bash semantics")
@pytest.mark.parametrize("relative", CONTROLLERS)
@pytest.mark.parametrize(
    "scenario",
    (
        "safe_old",
        "recent_atomic",
        "malformed_atomic",
        "symlink_atomic",
        "nested_mount",
        "findmnt_failure",
        "identity_change",
        "inventory_failure",
    ),
)
def test_controller_state_reconciler_matrix_without_root(
    tmp_path: Path,
    relative: str,
    scenario: str,
) -> None:
    bash = shutil.which("bash")
    assert bash
    state_root = tmp_path / "state"
    state_root.mkdir(mode=0o700)
    state_root.chmod(0o700)
    lock = state_root / ".controller.lock"
    lock.write_bytes(b"")
    lock.chmod(0o600)

    old = state_root / ("a" * 40 + ".Ab12Cd34")
    _directory(old, age_seconds=62 * 60)
    protected: list[Path] = [old]
    environment = os.environ.copy()

    if scenario == "recent_atomic":
        recent = state_root / ("b" * 40 + ".Ef56Gh78")
        _directory(recent, age_seconds=5 * 60)
        protected.append(recent)
    elif scenario == "malformed_atomic":
        malformed = state_root / "unexpected-entry"
        malformed.write_text("do-not-delete", encoding="utf-8")
        protected.append(malformed)
    elif scenario == "symlink_atomic":
        outside = tmp_path / "outside"
        outside.mkdir()
        marker = outside / "marker"
        marker.write_text("preserve", encoding="utf-8")
        link = state_root / ("b" * 40 + ".Ef56Gh78")
        link.symlink_to(outside, target_is_directory=True)
        protected.extend((link, marker))
    elif scenario == "nested_mount":
        environment["MOCK_MOUNT_TARGETS"] = str(old / "nested")
    elif scenario == "findmnt_failure":
        environment["MOCK_FINDMNT_FAILURE"] = "true"
    elif scenario == "identity_change":
        environment["MOCK_IDENTITY_CHANGE"] = "true"
    elif scenario == "inventory_failure":
        environment["MOCK_INVENTORY_FAILURE"] = "true"

    harness = tmp_path / "harness.sh"
    harness.write_text(_harness(relative), encoding="utf-8", newline="\n")
    harness.chmod(0o700)
    completed = subprocess.run(
        [bash, str(harness), str(state_root), str(tmp_path / "identity-counter")],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=15,
        env=environment,
    )

    if scenario == "safe_old":
        assert completed.returncode == 0, completed.stderr
        assert not old.exists()
        assert lock.is_file()
    else:
        assert completed.returncode == 97, completed.stderr
        for path in protected:
            assert path.exists() or path.is_symlink()
