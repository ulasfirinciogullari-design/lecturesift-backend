#!/usr/bin/env python3
"""Release the pinned frozen Render PostgreSQL dump to the exact rehearsal.

Render has served no traffic since the provider cutover and its database is
frozen. The exact rehearsal therefore no longer reads it over the network: it
restores the verified final custom-format dump of that database from one fixed
root-only path into a disposable, network-less PostgreSQL container.

This helper is the only reader of that path. It accepts only a canonical,
single-link, root-owned regular file with mode 0400 or 0600 in a private
root-owned directory, checks the descriptor it actually reads, and releases
the bytes only after their SHA-256 equals the pinned digest file beside the
dump. That pin must also equal the `source_dump_sha256` that the finalized
provider cutover recorded, so only the exact dump the cutover restored into
OVH is accepted. Nothing is written anywhere except stdout.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import importlib.util
import os
from pathlib import Path
import re
import stat
import sys
from types import ModuleType
from typing import Final


FROZEN_SOURCE_DIRECTORY: Final = Path("/var/backups/lecturesift/frozen-source")
FROZEN_SOURCE_OWNER: Final = (0, 0)
DUMP_NAME: Final = "render-postgres.dump"
DIGEST_NAME: Final = "render-postgres.dump.sha256"
# The pinned dump is about 155 KB. The bound only keeps a wrong file from
# being buffered; the digest pins the exact content.
MAX_DUMP_BYTES: Final = 256 * 1024 * 1024
MAX_DIGEST_BYTES: Final = 4096
# pg_dump custom-format archives start with this magic.
CUSTOM_ARCHIVE_MAGIC: Final = b"PGDMP"
_DIGEST_LINE: Final = re.compile(r"([0-9a-f]{64})  render-postgres\.dump\n")
_SHA256: Final = re.compile(r"[0-9a-f]{64}")
CUTOVER_EVIDENCE_TOOL: Final = Path(__file__).resolve().with_name(
    "provider_cutover_evidence.py"
)


class FrozenSourceError(RuntimeError):
    """The frozen source dump is missing, unsafe or does not match its pin."""


def _check_directory(path: Path, *, owner: tuple[int, int], private: bool) -> None:
    try:
        details = path.lstat()
    except OSError as exc:
        raise FrozenSourceError(f"{path} is missing or inaccessible") from exc
    if (
        not path.is_absolute()
        or path != Path(os.path.realpath(path))
        or not stat.S_ISDIR(details.st_mode)
        or details.st_uid != owner[0]
        or (private and details.st_gid != owner[1])
        or stat.S_IMODE(details.st_mode) & (0o077 if private else 0o022)
    ):
        raise FrozenSourceError(
            f"{path} must be a canonical root-owned directory"
            + (" with mode 0700" if private else " that only root can write")
        )


def _read_private_file(path: Path, *, owner: tuple[int, int], limit: int) -> bytes:
    if path != Path(os.path.realpath(path)):
        raise FrozenSourceError(f"{path} must be canonical and not a symlink")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise FrozenSourceError(f"{path} is missing, a symlink or unreadable") from exc
    with os.fdopen(descriptor, "rb") as stream:
        details = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(details.st_mode)
            or details.st_nlink != 1
            or (details.st_uid, details.st_gid) != owner
            or stat.S_IMODE(details.st_mode) not in {0o400, 0o600}
        ):
            raise FrozenSourceError(
                f"{path} must be a single-link root-owned regular file "
                "with mode 0400 or 0600"
            )
        if details.st_size > limit:
            raise FrozenSourceError(f"{path} exceeds the supported size")
        payload = stream.read(limit + 1)
    try:
        current = path.lstat()
    except OSError as exc:
        raise FrozenSourceError(f"{path} disappeared while it was read") from exc
    if (
        len(payload) != details.st_size
        or (current.st_dev, current.st_ino) != (details.st_dev, details.st_ino)
    ):
        raise FrozenSourceError(f"{path} changed while it was read")
    return payload


def read_verified_dump(directory: Path, *, owner: tuple[int, int]) -> tuple[bytes, str]:
    """Return the dump bytes and their digest once both match the pin."""

    _check_directory(directory.parent, owner=owner, private=False)
    _check_directory(directory, owner=owner, private=True)
    digest_text = _read_private_file(
        directory / DIGEST_NAME, owner=owner, limit=MAX_DIGEST_BYTES
    )
    match = _DIGEST_LINE.fullmatch(digest_text.decode("ascii", errors="replace"))
    if match is None:
        raise FrozenSourceError(
            f"{directory / DIGEST_NAME} must hold exactly one line "
            f"'<sha256>  {DUMP_NAME}'"
        )
    payload = _read_private_file(
        directory / DUMP_NAME, owner=owner, limit=MAX_DUMP_BYTES
    )
    if not payload.startswith(CUSTOM_ARCHIVE_MAGIC):
        raise FrozenSourceError(f"{directory / DUMP_NAME} is not a pg_dump custom archive")
    digest = hashlib.sha256(payload).hexdigest()
    if not hmac.compare_digest(digest, match.group(1)):
        raise FrozenSourceError(f"{directory / DUMP_NAME} does not match its pinned SHA-256")
    return payload, digest


def _cutover_evidence() -> ModuleType:
    # Load the evidence tool next to this file by path, as the release gates
    # run it, so the finalized proof is validated by the same code.
    spec = importlib.util.spec_from_file_location(
        "lecturesift_frozen_source_cutover_evidence", CUTOVER_EVIDENCE_TOOL
    )
    if spec is None or spec.loader is None:
        raise FrozenSourceError("the provider cutover evidence tool is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def finalized_source_dump_sha256() -> str:
    """Return the Render dump digest bound into the finalized provider cutover."""

    evidence = _cutover_evidence()
    root = evidence.EVIDENCE_ROOT
    postgres_path = root / evidence.POSTGRES_PROOF_NAME
    try:
        final_proof = evidence._load(root / evidence.FINAL_PROOF_NAME)
        final = evidence.validate_final(
            root, expected_revision=final_proof.get("release_revision", "")
        )
        postgres = evidence._load(postgres_path)
        if final.get("postgres_proof_sha256") != evidence.sha256_file(postgres_path):
            raise evidence.EvidenceError("PostgreSQL cutover proof changed after finalization")
    except evidence.EvidenceError as exc:
        raise FrozenSourceError(f"the finalized provider cutover evidence is invalid: {exc}") from exc
    digest = postgres.get("source_dump_sha256", "")
    if not _SHA256.fullmatch(digest):
        raise FrozenSourceError("the finalized provider cutover names no valid source dump digest")
    return digest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("verify", "stream"))
    arguments = parser.parse_args(argv)
    try:
        payload, digest = read_verified_dump(
            FROZEN_SOURCE_DIRECTORY, owner=FROZEN_SOURCE_OWNER
        )
        if not hmac.compare_digest(digest, finalized_source_dump_sha256()):
            raise FrozenSourceError(
                "the pinned dump is not the Render dump the finalized provider cutover restored"
            )
    except FrozenSourceError as exc:
        print(f"Frozen source dump rejected: {exc}", file=sys.stderr)
        return 1
    if arguments.mode == "verify":
        print(f"FROZEN_SOURCE_DUMP_OK|sha256={digest}|bytes={len(payload)}")
    else:
        sys.stdout.buffer.write(payload)
        sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
