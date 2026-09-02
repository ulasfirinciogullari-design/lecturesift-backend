#!/usr/bin/env python3
"""Generate a revision-pinned operator wrapper for the fixed stage controller."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import re


HEX40 = re.compile(r"[0-9a-f]{40}\Z")
HEX64 = re.compile(r"[0-9a-f]{64}\Z")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--revision", required=True)
    parser.add_argument("--archive-sha256", required=True)
    parser.add_argument("--bundle-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not HEX40.fullmatch(args.revision):
        parser.error("revision must be 40 lowercase hex characters")
    for label, value in (
        ("archive-sha256", args.archive_sha256),
        ("bundle-sha256", args.bundle_sha256),
    ):
        if not HEX64.fullmatch(value):
            parser.error(f"{label} must be 64 lowercase hex characters")
    if args.output.exists() or args.output.is_symlink():
        parser.error("output must not already exist")

    text = f'''#!/bin/bash -p
set -Eeuo pipefail
set +x
umask 077
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
IFS=$' \\t\\n'
unset CDPATH ENV BASH_ENV
unset -f id realpath stat env 2>/dev/null || true
hash -r
revision={args.revision}
archive_sha256={args.archive_sha256}
bundle_sha256={args.bundle_sha256}
archive="/var/tmp/lecturesift-$revision.tar"
bundle="/var/tmp/lecturesift-$revision.bundle"
controller=/usr/local/sbin/lecturesift-release-stage-controller
fail() {{ echo "EXACT_RELEASE_BOOTSTRAP_FAILED|$*" >&2; exit 1; }}
[[ "$(id -u)" == "0" ]] || fail root-required
[[ "$#" == "0" ]] || fail arguments-forbidden
[[ -f "$controller" && ! -L "$controller" && "$(realpath -e -- "$controller")" == "$controller" && "$(stat -c '%u:%g' -- "$controller")" == "0:0" ]] || fail unsafe-fixed-stage-controller
controller_mode="$(stat -c '%a' -- "$controller")"
(( (8#$controller_mode & 8#022) == 0 )) || fail writable-fixed-stage-controller
env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin LANG=C.UTF-8 \\
  LECTURESIFT_STAGE_REVISION="$revision" \\
  LECTURESIFT_STAGE_ARCHIVE="$archive" \\
  LECTURESIFT_STAGE_BUNDLE="$bundle" \\
  LECTURESIFT_STAGE_ARCHIVE_SHA256="$archive_sha256" \\
  LECTURESIFT_STAGE_BUNDLE_SHA256="$bundle_sha256" \\
  "$controller"
'''
    descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o700)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
        stream.write(text)
        stream.flush()
        os.fsync(stream.fileno())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
