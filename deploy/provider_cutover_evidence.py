#!/usr/bin/env python3
"""Atomic, fail-closed evidence state machine for the Render-to-OVH cutover."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import stat
import tempfile
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlsplit


EVIDENCE_ROOT = Path("/var/lib/lecturesift/provider-cutover")
RECOVERY_EVIDENCE_ROOT = Path("/var/lib/lecturesift/recovery-drills")
IN_PROGRESS_NAME = "provider-cutover.in-progress"
POSTGRES_PROOF_NAME = "postgres-cutover.ok"
REDIS_PROOF_NAME = "redis-cutover.ok"
FINAL_PROOF_NAME = "provider-cutover.ok"
VERSION = "1"
_CUTOVER_ID = re.compile(r"^[0-9a-f]{32}$")
_REVISION = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]{0,126}$")
_SAFE_VALUE = re.compile(r"^[A-Za-z0-9_.:@/+,-]{1,512}$")


class EvidenceError(RuntimeError):
    pass


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_common(cutover_id: str, revision: str, source_fingerprint: str) -> None:
    if not _CUTOVER_ID.fullmatch(cutover_id):
        raise EvidenceError("cutover ID must be exactly 32 lowercase hex characters")
    if not _REVISION.fullmatch(revision):
        raise EvidenceError("release revision must be exactly 40 lowercase hex characters")
    if not _SHA256.fullmatch(source_fingerprint):
        raise EvidenceError("source fingerprint must be one lowercase SHA-256 value")


def _canonical_endpoint(value: str, *, kind: str) -> dict[str, object]:
    parsed = urlsplit(value)
    host = (parsed.hostname or "").lower().rstrip(".")
    user = unquote(parsed.username or "")
    if not host or not parsed.password or (kind == "database" and not user):
        raise EvidenceError(f"{kind} source endpoint is incomplete")
    if parsed.fragment:
        raise EvidenceError(f"{kind} source endpoint must not contain a fragment")

    if kind == "database":
        if parsed.scheme not in {"postgres", "postgresql"}:
            raise EvidenceError("database source endpoint must use PostgreSQL")
        if not host.endswith(".render.com") or not parsed.path.strip("/"):
            raise EvidenceError("database source endpoint must be the external Render database")
        try:
            port = parsed.port or 5432
        except ValueError as exc:
            raise EvidenceError("database source endpoint has an invalid port") from exc
    else:
        if parsed.scheme not in {"redis", "rediss"}:
            raise EvidenceError(f"{kind} source endpoint must use Redis")
        if host in {"redis", "localhost", "127.0.0.1", "::1"}:
            raise EvidenceError(f"{kind} source endpoint must be external")
        try:
            port = parsed.port or 6379
        except ValueError as exc:
            raise EvidenceError(f"{kind} source endpoint has an invalid port") from exc

    return {
        "scheme": parsed.scheme,
        "host": host,
        "port": port,
        "user": user,
        "path": parsed.path or "/0",
        "query": sorted(parse_qsl(parsed.query, keep_blank_values=True)),
    }


def source_fingerprint_from_environment(environment: dict[str, str]) -> str:
    health = urlsplit(environment.get("SOURCE_HEALTH_URL", ""))
    health_host = (health.hostname or "").lower().rstrip(".")
    if (
        health.scheme != "https"
        or not health_host.endswith(".onrender.com")
        or not health.path.rstrip("/").endswith("/health")
        or health.query
        or health.fragment
    ):
        raise EvidenceError("source health endpoint must be the direct HTTPS Render health URL")
    canonical = {
        "database": _canonical_endpoint(
            environment.get("SOURCE_DATABASE_URL", ""), kind="database"
        ),
        "health": {
            "scheme": "https",
            "host": health_host,
            "port": health.port or 443,
            "path": health.path,
        },
        "redis": _canonical_endpoint(environment.get("SOURCE_REDIS_URL", ""), kind="redis"),
        "broker": _canonical_endpoint(
            environment.get("SOURCE_CELERY_BROKER_URL", ""), kind="broker"
        ),
    }
    serialized = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    return sha256_bytes(serialized)


def _ensure_evidence_root(root: Path) -> Path:
    if not root.parent.is_dir() or root.parent.is_symlink():
        raise EvidenceError("provider-cutover evidence parent is missing or unsafe")
    root.mkdir(mode=0o700, exist_ok=True)
    resolved = root.resolve(strict=True)
    if resolved != root or root.is_symlink():
        raise EvidenceError("provider-cutover evidence root escaped its fixed path")
    details = root.stat()
    if os.name == "posix" and (
        details.st_uid != 0 or stat.S_IMODE(details.st_mode) & 0o077
    ):
        raise EvidenceError("provider-cutover evidence root must be root-owned mode 0700")
    return resolved


def _validate_fields(fields: dict[str, str]) -> None:
    if not fields or any(not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", key) for key in fields):
        raise EvidenceError("evidence contains an invalid field name")
    if any(not _SAFE_VALUE.fullmatch(str(value)) for value in fields.values()):
        raise EvidenceError("evidence contains an unsafe field value")


def _atomic_write(path: Path, fields: dict[str, str]) -> None:
    _validate_fields(fields)
    root = _ensure_evidence_root(path.parent)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}-", dir=root)
    temporary = Path(temporary_name)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, 0o600)
        else:
            os.chmod(temporary, 0o600)
        if hasattr(os, "fchown"):
            os.fchown(descriptor, 0, 0)
        payload = "".join(f"{key}={fields[key]}\n" for key in sorted(fields)).encode()
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            descriptor = -1
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        if os.name == "posix":
            directory_fd = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _unlink(path: Path) -> None:
    if path.is_symlink():
        raise EvidenceError(f"refusing symlinked evidence path: {path.name}")
    try:
        path.unlink()
    except FileNotFoundError:
        return
    if os.name == "posix":
        directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)


def _load(path: Path) -> dict[str, str]:
    if not path.is_file() or path.is_symlink():
        raise EvidenceError(f"missing or unsafe evidence: {path.name}")
    details = path.stat()
    if os.name == "posix" and (
        details.st_uid != 0 or stat.S_IMODE(details.st_mode) != 0o600
    ):
        raise EvidenceError(f"evidence must be root-owned mode 0600: {path.name}")
    fields: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition("=")
        if not separator or key in fields:
            raise EvidenceError(f"malformed evidence: {path.name}")
        fields[key] = value
    _validate_fields(fields)
    return fields


def _load_recovery_evidence(path: Path) -> dict[str, str]:
    if not path.is_file() or path.is_symlink():
        raise EvidenceError(f"missing or unsafe recovery evidence: {path.name}")
    details = path.stat()
    if os.name == "posix" and (
        details.st_uid != 0 or stat.S_IMODE(details.st_mode) & 0o022
    ):
        raise EvidenceError(f"recovery evidence must be root-owned and immutable to others: {path.name}")
    fields: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition("=")
        if not separator or key in fields:
            raise EvidenceError(f"malformed recovery evidence: {path.name}")
        fields[key] = value
    _validate_fields(fields)
    return fields


def _common_fields(
    *, cutover_id: str, revision: str, source_fingerprint: str
) -> dict[str, str]:
    _validate_common(cutover_id, revision, source_fingerprint)
    return {
        "cutover_id": cutover_id,
        "release_revision": revision,
        "source_fingerprint_sha256": source_fingerprint,
        "version": VERSION,
    }


def _matches_common(fields: dict[str, str], common: dict[str, str]) -> bool:
    return all(fields.get(key) == value for key, value in common.items())


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def begin_postgres(root: Path, *, cutover_id: str, revision: str, source: str) -> None:
    common = _common_fields(cutover_id=cutover_id, revision=revision, source_fingerprint=source)
    root = _ensure_evidence_root(root)
    progress = root / IN_PROGRESS_NAME
    if progress.exists() or progress.is_symlink():
        raise EvidenceError("an earlier provider cutover is still in progress")
    # Write the global stop fence first. A crash before old evidence removal is
    # safe because production preflight rejects this marker unconditionally.
    _atomic_write(
        progress,
        {**common, "phase": "postgres-in-progress", "started_at_utc": _now()},
    )
    for name in (FINAL_PROOF_NAME, POSTGRES_PROOF_NAME, REDIS_PROOF_NAME):
        _unlink(root / name)


def _require_progress(root: Path, *, common: dict[str, str], phase: str) -> dict[str, str]:
    progress = _load(root / IN_PROGRESS_NAME)
    if not _matches_common(progress, common) or progress.get("phase") != phase:
        raise EvidenceError("provider cutover phase/session evidence does not match")
    return progress


def write_postgres(
    root: Path,
    *,
    cutover_id: str,
    revision: str,
    source: str,
    run_id: str,
    manifest_sha256: str,
    source_dump_sha256: str,
    rollback_dump_sha256: str,
) -> None:
    common = _common_fields(cutover_id=cutover_id, revision=revision, source_fingerprint=source)
    if not _RUN_ID.fullmatch(run_id):
        raise EvidenceError("invalid PostgreSQL cutover run ID")
    for value in (manifest_sha256, source_dump_sha256, rollback_dump_sha256):
        if not _SHA256.fullmatch(value):
            raise EvidenceError("PostgreSQL evidence contains an invalid SHA-256 value")
    root = _ensure_evidence_root(root)
    _require_progress(root, common=common, phase="postgres-in-progress")
    _atomic_write(
        root / POSTGRES_PROOF_NAME,
        {
            **common,
            "api_role_probe": "verified",
            "api_worker_started": "false",
            "caddy_changed": "false",
            "pending_payments_after": "0",
            "pending_payments_before": "0",
            "run_id": run_id,
            "source_dump_sha256": source_dump_sha256,
            "source_frozen": "verified",
            "source_manifest_sha256": manifest_sha256,
            "source_worker_queue_zero": "verified",
            "status": "postgres-cutover-verified",
            "target_rollback_dump_sha256": rollback_dump_sha256,
            "verified_at_utc": _now(),
            "worker_role_probe": "verified",
        },
    )
    _atomic_write(
        root / IN_PROGRESS_NAME,
        {**common, "phase": "postgres-verified", "updated_at_utc": _now()},
    )


def begin_redis(root: Path, *, cutover_id: str, revision: str, source: str) -> None:
    common = _common_fields(cutover_id=cutover_id, revision=revision, source_fingerprint=source)
    root = _ensure_evidence_root(root)
    _require_progress(root, common=common, phase="postgres-verified")
    postgres = _load(root / POSTGRES_PROOF_NAME)
    if not _matches_common(postgres, common) or postgres.get("status") != "postgres-cutover-verified":
        raise EvidenceError("PostgreSQL success proof does not match this Redis cutover")
    _atomic_write(
        root / IN_PROGRESS_NAME,
        {**common, "phase": "redis-in-progress", "updated_at_utc": _now()},
    )
    for name in (FINAL_PROOF_NAME, REDIS_PROOF_NAME):
        _unlink(root / name)


def write_redis(
    root: Path,
    *,
    cutover_id: str,
    revision: str,
    source: str,
    run_id: str,
    state_sha256: str,
    rollback_sha256: str,
) -> None:
    common = _common_fields(cutover_id=cutover_id, revision=revision, source_fingerprint=source)
    if not _RUN_ID.fullmatch(run_id):
        raise EvidenceError("invalid Redis migration run ID")
    for value in (state_sha256, rollback_sha256):
        if not _SHA256.fullmatch(value):
            raise EvidenceError("Redis evidence contains an invalid SHA-256 value")
    root = _ensure_evidence_root(root)
    _require_progress(root, common=common, phase="redis-in-progress")
    postgres = _load(root / POSTGRES_PROOF_NAME)
    if not _matches_common(postgres, common):
        raise EvidenceError("PostgreSQL and Redis cutover sessions differ")
    _atomic_write(
        root / REDIS_PROOF_NAME,
        {
            **common,
            "caddy_changed": "false",
            "run_id": run_id,
            "source_frozen": "verified",
            "source_worker_queue_zero": "verified",
            "status": "redis-cutover-verified",
            "target_broker_zero": "verified",
            "target_rollback_sha256": rollback_sha256,
            "target_state_sha256": state_sha256,
            "verified_at_utc": _now(),
        },
    )
    _atomic_write(
        root / IN_PROGRESS_NAME,
        {**common, "phase": "redis-verified", "updated_at_utc": _now()},
    )


def finalize(
    root: Path,
    *,
    cutover_id: str,
    revision: str,
    source: str,
    recovery_marker: Path,
    recovery_sha256: str,
    retention_marker: Path,
    retention_sha256: str,
    repository_id_sha256: str,
) -> None:
    common = _common_fields(cutover_id=cutover_id, revision=revision, source_fingerprint=source)
    for value in (recovery_sha256, retention_sha256, repository_id_sha256):
        if not _SHA256.fullmatch(value):
            raise EvidenceError("final cutover evidence contains an invalid SHA-256 value")
    if recovery_marker.name != recovery_marker.as_posix() or retention_marker.name != retention_marker.as_posix():
        raise EvidenceError("final evidence records marker basenames only")
    root = _ensure_evidence_root(root)
    _require_progress(root, common=common, phase="redis-verified")
    postgres_path = root / POSTGRES_PROOF_NAME
    redis_path = root / REDIS_PROOF_NAME
    postgres = _load(postgres_path)
    redis = _load(redis_path)
    if (
        not _matches_common(postgres, common)
        or not _matches_common(redis, common)
        or postgres.get("status") != "postgres-cutover-verified"
        or redis.get("status") != "redis-cutover-verified"
    ):
        raise EvidenceError("PostgreSQL and Redis proofs do not exactly match the final session")
    _atomic_write(
        root / FINAL_PROOF_NAME,
        {
            **common,
            "caddy_changed": "false",
            "dns_changed": "false",
            "finalized_at_utc": _now(),
            "pending_payments": "0",
            "postgres_proof_sha256": sha256_file(postgres_path),
            "r2_recovery_marker": recovery_marker.name,
            "r2_recovery_marker_sha256": recovery_sha256,
            "r2_repository_id_sha256": repository_id_sha256,
            "r2_retention_marker": retention_marker.name,
            "r2_retention_marker_sha256": retention_sha256,
            "redis_proof_sha256": sha256_file(redis_path),
            "source_freeze_revalidated": "true",
            "source_worker_queue_zero": "verified",
            "status": "provider-cutover-verified",
            "target_api_worker_started": "false",
            "target_queue_zero": "verified",
        },
    )
    # The final proof becomes usable only when the stronger in-progress fence
    # is durably removed. A crash before this unlink remains fail-closed.
    _unlink(root / IN_PROGRESS_NAME)


def validate_final(root: Path, *, expected_revision: str) -> dict[str, str]:
    if not _REVISION.fullmatch(expected_revision):
        raise EvidenceError("expected release revision is invalid")
    root = _ensure_evidence_root(root)
    if (root / IN_PROGRESS_NAME).exists() or (root / IN_PROGRESS_NAME).is_symlink():
        raise EvidenceError("provider cutover is still in progress")
    final = _load(root / FINAL_PROOF_NAME)
    if (
        final.get("version") != VERSION
        or final.get("status") != "provider-cutover-verified"
        or final.get("release_revision") != expected_revision
        or final.get("caddy_changed") != "false"
        or final.get("dns_changed") != "false"
    ):
        raise EvidenceError("provider cutover final proof is not valid for this exact release")
    postgres = root / POSTGRES_PROOF_NAME
    redis = root / REDIS_PROOF_NAME
    postgres_fields = _load(postgres)
    redis_fields = _load(redis)
    if final.get("postgres_proof_sha256") != sha256_file(postgres):
        raise EvidenceError("PostgreSQL cutover proof changed after finalization")
    if final.get("redis_proof_sha256") != sha256_file(redis):
        raise EvidenceError("Redis cutover proof changed after finalization")
    for step in (postgres_fields, redis_fields):
        for key in ("cutover_id", "release_revision", "source_fingerprint_sha256"):
            if step.get(key) != final.get(key):
                raise EvidenceError("step proof does not match provider final proof")
    if not RECOVERY_EVIDENCE_ROOT.is_dir() or RECOVERY_EVIDENCE_ROOT.is_symlink():
        raise EvidenceError("recovery evidence root is missing or unsafe")
    recovery_name = final.get("r2_recovery_marker", "")
    retention_name = final.get("r2_retention_marker", "")
    if not re.fullmatch(r"restic-restore-[A-Za-z0-9.-]{1,180}[.]ok", recovery_name):
        raise EvidenceError("provider proof names an invalid Restic recovery marker")
    if retention_name != "r2-retention-lock.ok":
        raise EvidenceError("provider proof names an invalid R2 retention marker")
    recovery_path = RECOVERY_EVIDENCE_ROOT / recovery_name
    retention_path = RECOVERY_EVIDENCE_ROOT / retention_name
    recovery_fields = _load_recovery_evidence(recovery_path)
    retention_fields = _load_recovery_evidence(retention_path)
    if final.get("r2_recovery_marker_sha256") != sha256_file(recovery_path):
        raise EvidenceError("Restic recovery evidence changed after finalization")
    if final.get("r2_retention_marker_sha256") != sha256_file(retention_path):
        raise EvidenceError("R2 retention evidence changed after finalization")
    repository_hash = final.get("r2_repository_id_sha256", "")
    if (
        not _SHA256.fullmatch(repository_hash)
        or recovery_fields.get("repository_id_sha256") != repository_hash
        or retention_fields.get("repository_id_sha256") != repository_hash
        or recovery_fields.get("status") != "success"
        or retention_fields.get("status") != "immutable-retention-verified"
    ):
        raise EvidenceError("R2 recovery/retention evidence does not match the finalized repository")
    return final


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("source-fingerprint")

    def common(subparser: argparse.ArgumentParser) -> None:
        subparser.add_argument("--cutover-id", required=True)
        subparser.add_argument("--revision", required=True)
        subparser.add_argument("--source-fingerprint", required=True)

    common(commands.add_parser("begin-postgres"))
    postgres = commands.add_parser("write-postgres")
    common(postgres)
    postgres.add_argument("--run-id", required=True)
    postgres.add_argument("--manifest-sha256", required=True)
    postgres.add_argument("--source-dump-sha256", required=True)
    postgres.add_argument("--rollback-dump-sha256", required=True)
    common(commands.add_parser("begin-redis"))
    redis = commands.add_parser("write-redis")
    common(redis)
    redis.add_argument("--run-id", required=True)
    redis.add_argument("--state-sha256", required=True)
    redis.add_argument("--rollback-sha256", required=True)
    final = commands.add_parser("finalize")
    common(final)
    final.add_argument("--recovery-marker", required=True)
    final.add_argument("--recovery-sha256", required=True)
    final.add_argument("--retention-marker", required=True)
    final.add_argument("--retention-sha256", required=True)
    final.add_argument("--repository-id-sha256", required=True)
    validate = commands.add_parser("validate-final")
    validate.add_argument("--expected-revision", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if not hasattr(os, "geteuid") or os.geteuid() != 0:
            raise EvidenceError("provider cutover evidence must be managed as root")
        if args.command == "source-fingerprint":
            print(source_fingerprint_from_environment(dict(os.environ)))
            return 0
        kwargs = {
            "cutover_id": getattr(args, "cutover_id", ""),
            "revision": getattr(args, "revision", ""),
            "source": getattr(args, "source_fingerprint", ""),
        }
        if args.command == "begin-postgres":
            begin_postgres(EVIDENCE_ROOT, **kwargs)
        elif args.command == "write-postgres":
            write_postgres(
                EVIDENCE_ROOT,
                **kwargs,
                run_id=args.run_id,
                manifest_sha256=args.manifest_sha256,
                source_dump_sha256=args.source_dump_sha256,
                rollback_dump_sha256=args.rollback_dump_sha256,
            )
        elif args.command == "begin-redis":
            begin_redis(EVIDENCE_ROOT, **kwargs)
        elif args.command == "write-redis":
            write_redis(
                EVIDENCE_ROOT,
                **kwargs,
                run_id=args.run_id,
                state_sha256=args.state_sha256,
                rollback_sha256=args.rollback_sha256,
            )
        elif args.command == "finalize":
            finalize(
                EVIDENCE_ROOT,
                **kwargs,
                recovery_marker=Path(args.recovery_marker),
                recovery_sha256=args.recovery_sha256,
                retention_marker=Path(args.retention_marker),
                retention_sha256=args.retention_sha256,
                repository_id_sha256=args.repository_id_sha256,
            )
        elif args.command == "validate-final":
            validate_final(EVIDENCE_ROOT, expected_revision=args.expected_revision)
        return 0
    except EvidenceError as exc:
        print(f"Provider cutover evidence rejected: {exc}", file=os.sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
