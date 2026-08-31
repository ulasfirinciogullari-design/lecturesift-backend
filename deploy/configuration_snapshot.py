#!/usr/bin/env python3
"""Create and verify the root-only configuration payload stored by Restic.

The payload is intentionally separate from the unencrypted local database
checkpoint.  It contains only the exact allowlists below and is staged only
for the duration of a client-side encrypted Restic backup.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import sys
from typing import Final


SNAPSHOT_FORMAT: Final = "lecturesift-configuration-snapshot-v1"
APPLICATION_IDENTITY: Final = "lecturesift-production"
MANIFEST_NAME: Final = "CONFIGURATION_MANIFEST.json"
CHECKSUM_NAME: Final = "CONFIGURATION_SHA256SUMS"

# This is the complete production configuration allowlist.  In particular,
# rehearsal.env is a temporary provider-migration input and Docker credentials
# are deliberately excluded.
ENVIRONMENT_ALLOWLIST: Final = (
    "runtime.env",
    "api.env",
    "worker.env",
    "instagram.env",
    "postgres.env",
    "restic.env",
)

RELEASE_IDENTITY_ALLOWLIST: Final = (
    "/run/lecturesift/release.env",
)

# These files identify the exact deploy/recovery contract without copying an
# arbitrary source tree, .git, Docker credentials, caches, or user content.
IDENTITY_ALLOWLIST: Final = (
    "compose.yaml",
    "Caddyfile",
    "Dockerfile",
    "requirements.txt",
    "deploy/00-lecturesift-sshd.conf",
    "deploy/99-lecturesift-sysctl.conf",
    "deploy/docker-daemon.json",
    "deploy/backup.sh",
    "deploy/backup_failure_alert.sh",
    "deploy/restore.sh",
    "deploy/restic_restore_rehearsal.sh",
    "deploy/recover_configuration_snapshot.sh",
    "deploy/recover_backup_runtime.sh",
    "deploy/record_restic_escrow.sh",
    "deploy/configuration_snapshot.py",
    "deploy/preflight.sh",
    "deploy/resource_guard.sh",
    "deploy/generate_role_envs.py",
    "deploy/postgres-app-role.sh",
    "deploy/provision_database_role.sh",
    "deploy/release.sh",
    "deploy/image_smoke.py",
    "deploy/lecturesift.service",
    "deploy/lecturesift-backup.service",
    "deploy/lecturesift-backup.timer",
    "deploy/lecturesift-backup-alert@.service",
    "deploy/lecturesift-instagram.service",
    "deploy/lecturesift-instagram.timer",
    "deploy/lecturesift-r2-retention-probe.service",
    "deploy/r2_retention_probe.py",
    "deploy/recovery_manifest_v1.sql",
    "deploy/redis_rdb_to_aof.sh",
    "deploy/redis.conf",
)


class SnapshotError(RuntimeError):
    """A fail-closed snapshot validation error."""


def _archive_path(kind: str, relative_name: str) -> str:
    if kind == "environment":
        return f"files/etc/lecturesift/{relative_name}"
    if kind == "release_identity":
        return "files/run/lecturesift/release.env"
    return f"files/opt/lecturesift/{relative_name}"


def _source_path(kind: str, relative_name: str, deploy_root: Path) -> Path:
    if kind == "environment":
        return Path("/etc/lecturesift") / relative_name
    return deploy_root / relative_name


def _expected_entries(deploy_root: Path) -> list[tuple[str, str, str, str]]:
    entries: list[tuple[str, str, str, str]] = []
    for name in ENVIRONMENT_ALLOWLIST:
        entries.append(
            (
                "environment",
                str(Path("/etc/lecturesift") / name),
                _archive_path("environment", name),
                "0600",
            )
        )
    for name in IDENTITY_ALLOWLIST:
        entries.append(
            (
                "identity",
                str(deploy_root / name),
                _archive_path("identity", name),
                "",
            )
        )
    for source_path in RELEASE_IDENTITY_ALLOWLIST:
        entries.append(
            (
                "release_identity",
                source_path,
                _archive_path("release_identity", source_path),
                "",
            )
        )
    return entries


def _safe_deploy_root(value: str) -> Path:
    root = Path(value)
    if not root.is_absolute() or root != Path(os.path.realpath(root)):
        raise SnapshotError("the deploy root must be an absolute canonical directory")
    details = os.lstat(root)
    if not stat.S_ISDIR(details.st_mode) or stat.S_ISLNK(details.st_mode):
        raise SnapshotError("the deploy root must be a real directory")
    if details.st_uid != 0 or stat.S_IMODE(details.st_mode) & 0o022:
        raise SnapshotError("the deploy root must be root-owned and not group/other writable")
    return root


def _open_validated_source(path: Path, kind: str) -> tuple[int, os.stat_result]:
    if Path(os.path.abspath(path)) != Path(os.path.realpath(path)):
        raise SnapshotError(f"required {kind} path must not traverse a symlink: {path}")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise SnapshotError(f"required {kind} file is missing or unsafe: {path}") from exc
    details = os.fstat(descriptor)
    mode = stat.S_IMODE(details.st_mode)
    if not stat.S_ISREG(details.st_mode) or details.st_uid != 0:
        os.close(descriptor)
        raise SnapshotError(f"required {kind} file must be root-owned and regular: {path}")
    if kind == "environment" and mode != 0o600:
        os.close(descriptor)
        raise SnapshotError(f"required environment file must have mode 0600: {path}")
    if kind == "identity" and mode & 0o022:
        os.close(descriptor)
        raise SnapshotError(f"identity file must not be group/other writable: {path}")
    if kind == "release_identity" and mode not in {0o400, 0o600}:
        os.close(descriptor)
        raise SnapshotError(f"release identity must have mode 0400 or 0600: {path}")
    return descriptor, details


def _same_identity(before: os.stat_result, after: os.stat_result) -> bool:
    fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns", "st_uid", "st_gid", "st_mode")
    return all(getattr(before, field) == getattr(after, field) for field in fields)


def _validated_identity(path: Path, kind: str) -> os.stat_result:
    descriptor, details = _open_validated_source(path, kind)
    os.close(descriptor)
    return details


def _copy_source(path: Path, destination: Path, kind: str) -> tuple[str, str]:
    descriptor, before = _open_validated_source(path, kind)
    digest = hashlib.sha256()
    try:
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with os.fdopen(descriptor, "rb", closefd=True) as source, destination.open("xb") as target:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
                target.write(chunk)
            target.flush()
            os.fsync(target.fileno())
            after = os.fstat(source.fileno())
        if not _same_identity(before, after):
            raise SnapshotError(f"source changed while the snapshot was being created: {path}")
        os.chown(destination, 0, 0)
        os.chmod(destination, 0o600)
        if kind == "release_identity":
            release_identity = destination.read_bytes()
            if not re.fullmatch(
                rb"LECTURESIFT_EXPECTED_BUILD_REVISION=[0-9a-f]{40}\n",
                release_identity,
            ):
                raise SnapshotError("release identity does not contain one exact commit")
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return digest.hexdigest(), f"{stat.S_IMODE(before.st_mode):04o}"


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def create_snapshot(destination: Path, deploy_root_value: str) -> int:
    if os.geteuid() != 0:
        raise SnapshotError("configuration snapshots must be created as root")
    deploy_root = _safe_deploy_root(deploy_root_value)
    if destination.exists() or destination.is_symlink():
        raise SnapshotError("configuration snapshot destination must not already exist")
    parent = destination.parent
    if Path(os.path.abspath(parent)) != Path(os.path.realpath(parent)):
        raise SnapshotError("configuration snapshot parent must not traverse a symlink")
    parent_details = os.lstat(parent)
    if (
        not stat.S_ISDIR(parent_details.st_mode)
        or stat.S_ISLNK(parent_details.st_mode)
        or parent_details.st_uid != 0
        or stat.S_IMODE(parent_details.st_mode) & 0o077
    ):
        raise SnapshotError("configuration snapshot parent must be a private root-owned directory")

    destination.mkdir(mode=0o700)
    os.chown(destination, 0, 0)
    entries: list[dict[str, str]] = []
    try:
        expected_entries = _expected_entries(deploy_root)
        source_identities = {
            source_text: _validated_identity(Path(source_text), kind)
            for kind, source_text, _archive_path_value, _required_mode in expected_entries
        }
        for kind, source_text, archive_path, required_mode in expected_entries:
            source = Path(source_text)
            digest, source_mode = _copy_source(source, destination / archive_path, kind)
            if required_mode and source_mode != required_mode:
                raise SnapshotError(f"environment mode changed during snapshot: {source}")
            entries.append(
                {
                    "kind": kind,
                    "source_path": source_text,
                    "archive_path": archive_path,
                    "source_mode": source_mode,
                    "sha256": digest,
                }
            )

        manifest = {
            "format": SNAPSHOT_FORMAT,
            "application_identity": APPLICATION_IDENTITY,
            "created_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "deploy_root": str(deploy_root),
            "files": entries,
        }
        manifest_path = destination / MANIFEST_NAME
        with manifest_path.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(manifest, handle, ensure_ascii=True, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chown(manifest_path, 0, 0)
        os.chmod(manifest_path, 0o600)

        checksums = [(MANIFEST_NAME, _hash_file(manifest_path))]
        checksums.extend((entry["archive_path"], entry["sha256"]) for entry in entries)
        checksum_path = destination / CHECKSUM_NAME
        with checksum_path.open("x", encoding="ascii", newline="\n") as handle:
            for relative_path, digest in checksums:
                handle.write(f"{digest}  {relative_path}\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chown(checksum_path, 0, 0)
        os.chmod(checksum_path, 0o600)
        for kind, source_text, _archive_path_value, _required_mode in expected_entries:
            current_identity = _validated_identity(Path(source_text), kind)
            if not _same_identity(source_identities[source_text], current_identity):
                raise SnapshotError(
                    f"source set changed while the snapshot was being created: {source_text}"
                )
        verify_snapshot(destination, str(deploy_root), quiet=True)
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
        raise
    return len(entries)


def _regular_root_private(path: Path, *, directory: bool = False) -> os.stat_result:
    details = os.lstat(path)
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if not expected(details.st_mode) or stat.S_ISLNK(details.st_mode) or details.st_uid != 0:
        raise SnapshotError(f"snapshot entry is not a root-owned regular {'directory' if directory else 'file'}: {path}")
    if stat.S_IMODE(details.st_mode) & 0o077:
        raise SnapshotError(f"snapshot entry is accessible by group or others: {path}")
    return details


def verify_snapshot(snapshot_root: Path, deploy_root_value: str, *, quiet: bool) -> int:
    if os.geteuid() != 0:
        raise SnapshotError("configuration snapshots must be verified as root")
    deploy_root = Path(deploy_root_value)
    if not deploy_root.is_absolute() or str(deploy_root) != os.path.normpath(str(deploy_root)):
        raise SnapshotError("expected deploy root must be an absolute normalized path")
    requested_snapshot_root = Path(os.path.abspath(snapshot_root))
    if requested_snapshot_root.is_symlink():
        raise SnapshotError("configuration snapshot root must not be a symlink")
    snapshot_root = Path(os.path.realpath(requested_snapshot_root))
    if snapshot_root != requested_snapshot_root:
        raise SnapshotError("configuration snapshot root must not traverse a symlink")
    _regular_root_private(snapshot_root, directory=True)

    manifest_path = snapshot_root / MANIFEST_NAME
    checksum_path = snapshot_root / CHECKSUM_NAME
    _regular_root_private(manifest_path)
    _regular_root_private(checksum_path)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SnapshotError("configuration snapshot manifest is unreadable") from exc
    if not isinstance(manifest, dict) or set(manifest) != {
        "format", "application_identity", "created_at_utc", "deploy_root", "files"
    }:
        raise SnapshotError("configuration snapshot manifest fields are invalid")
    if (
        manifest["format"] != SNAPSHOT_FORMAT
        or manifest["application_identity"] != APPLICATION_IDENTITY
        or manifest["deploy_root"] != str(deploy_root)
        or not isinstance(manifest["created_at_utc"], str)
    ):
        raise SnapshotError("configuration snapshot identity is invalid")

    expected = _expected_entries(deploy_root)
    files = manifest.get("files")
    if not isinstance(files, list) or len(files) != len(expected):
        raise SnapshotError("configuration snapshot file count is invalid")
    expected_archive_paths: set[str] = set()
    manifest_hashes: dict[str, str] = {}
    for item, (kind, source_path, archive_path, required_mode) in zip(files, expected, strict=True):
        if not isinstance(item, dict) or set(item) != {
            "kind", "source_path", "archive_path", "source_mode", "sha256"
        }:
            raise SnapshotError("configuration snapshot file manifest is invalid")
        digest = item["sha256"]
        source_mode = item["source_mode"]
        if (
            item["kind"] != kind
            or item["source_path"] != source_path
            or item["archive_path"] != archive_path
            or not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
            or not isinstance(source_mode, str)
            or len(source_mode) != 4
            or any(character not in "01234567" for character in source_mode)
            or (required_mode and source_mode != required_mode)
            or (kind == "identity" and int(source_mode, 8) & 0o022)
            or (kind == "release_identity" and source_mode not in {"0400", "0600"})
        ):
            raise SnapshotError("configuration snapshot file metadata is invalid")
        relative = PurePosixPath(archive_path)
        if relative.is_absolute() or ".." in relative.parts:
            raise SnapshotError("configuration snapshot contains an unsafe archive path")
        expected_archive_paths.add(archive_path)
        manifest_hashes[archive_path] = digest

    checksum_lines = checksum_path.read_text(encoding="ascii").splitlines()
    expected_checksum_paths = [MANIFEST_NAME, *[entry[2] for entry in expected]]
    if len(checksum_lines) != len(expected_checksum_paths):
        raise SnapshotError("configuration checksum manifest has an invalid entry count")
    checksum_hashes: dict[str, str] = {}
    for line, expected_path in zip(checksum_lines, expected_checksum_paths, strict=True):
        parts = line.split("  ", 1)
        if len(parts) != 2 or parts[1] != expected_path:
            raise SnapshotError("configuration checksum manifest contains an unexpected path")
        digest = parts[0]
        if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            raise SnapshotError("configuration checksum manifest contains an invalid digest")
        checksum_hashes[expected_path] = digest

    if checksum_hashes[MANIFEST_NAME] != _hash_file(manifest_path):
        raise SnapshotError("configuration manifest checksum does not match")
    for archive_path in expected_archive_paths:
        candidate = snapshot_root.joinpath(*PurePosixPath(archive_path).parts)
        _regular_root_private(candidate)
        digest = _hash_file(candidate)
        if digest != manifest_hashes[archive_path] or digest != checksum_hashes[archive_path]:
            raise SnapshotError("configuration snapshot file checksum does not match")

    expected_files = {MANIFEST_NAME, CHECKSUM_NAME, *expected_archive_paths}
    actual_files: set[str] = set()
    for candidate in snapshot_root.rglob("*"):
        relative = candidate.relative_to(snapshot_root).as_posix()
        if candidate.is_symlink():
            raise SnapshotError("configuration snapshot contains a symlink")
        if candidate.is_dir():
            _regular_root_private(candidate, directory=True)
        elif candidate.is_file():
            actual_files.add(relative)
        else:
            raise SnapshotError("configuration snapshot contains a non-regular entry")
    if actual_files != expected_files:
        raise SnapshotError("configuration snapshot contains missing or unexpected files")
    if not quiet:
        print(f"Configuration snapshot verified ({len(expected)} allowlisted files).")
    return len(expected)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create")
    create.add_argument("--destination", required=True, type=Path)
    create.add_argument("--deploy-root", default="/opt/lecturesift")
    verify = subparsers.add_parser("verify")
    verify.add_argument("--snapshot-root", required=True, type=Path)
    verify.add_argument("--deploy-root", default="/opt/lecturesift")
    verify.add_argument("--quiet", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        if args.command == "create":
            count = create_snapshot(args.destination, args.deploy_root)
            print(f"Configuration snapshot created ({count} allowlisted files).")
        else:
            verify_snapshot(args.snapshot_root, args.deploy_root, quiet=args.quiet)
    except (OSError, SnapshotError) as exc:
        print(f"Configuration snapshot failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
