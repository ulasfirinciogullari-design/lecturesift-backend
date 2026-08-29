from __future__ import annotations

import argparse
import ctypes
import os
import tempfile
from pathlib import Path

import pytest

from check_local_environment import main as check_environment


def _short_windows_path(path: str) -> str:
    if os.name != "nt":
        return path
    buffer = ctypes.create_unicode_buffer(32768)
    length = ctypes.windll.kernel32.GetShortPathNameW(path, buffer, len(buffer))
    return buffer.value if length else path


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the LectureSift Windows environment.")
    parser.add_argument("--skip-tests", action="store_true")
    args = parser.parse_args()

    tool_path = os.environ.get("LECTURESIFT_TOOL_PATH", "").strip()
    if tool_path:
        os.environ["PATH"] = tool_path + os.pathsep + os.environ.get("PATH", "")

    if args.skip_tests:
        return check_environment()

    configured_temp = os.environ.get("LECTURESIFT_TEST_TEMP_ROOT", "").strip()
    temp_parent = Path(configured_temp or _short_windows_path(tempfile.gettempdir()))
    with tempfile.TemporaryDirectory(prefix="lecturesift-tests-", dir=temp_parent) as temporary:
        base_temp = Path(temporary) / "pytest"
        test_result = pytest.main(["-q", f"--basetemp={base_temp}"])
    check_result = check_environment()
    return test_result or check_result


if __name__ == "__main__":
    raise SystemExit(main())
