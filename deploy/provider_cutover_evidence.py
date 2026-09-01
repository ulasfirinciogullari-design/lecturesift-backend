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
# Production evidence is always owned by root:root.  Tests may monkeypatch
# these process-local constants to the unprivileged CI runner's effective IDs;
# they are deliberately not configurable through environment variables or CLI.
EVIDENCE_OWNER_UID = 0
EVIDENCE_OWNER_GID = 0
IN_PROGRESS_NAME = "provider-cutover.in-progress"
POSTGRES_PROOF_NAME = "postgres-cutover.ok"
REDIS_PROOF_NAME = "redis-cutover.ok"
SEED_PROOF_NAME = "first-cutover-seed.ok"
FINAL_PROOF_NAME = "provider-cutover.ok"
FIRST_START_IN_PROGRESS_NAME = "provider-first-start.in-progress"
FIRST_START_PROOF_NAME = "provider-first-start.ok"
VERSION = "3"
_CUTOVER_ID = re.compile(r"^[0-9a-f]{32}$")
_REVISION = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]{0,126}$")
_SNAPSHOT_ID = re.compile(r"^[0-9a-f]{64}$")
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
        details.st_uid != EVIDENCE_OWNER_UID
        or details.st_gid != EVIDENCE_OWNER_GID
        or stat.S_IMODE(details.st_mode) & 0o077
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
            os.fchown(descriptor, EVIDENCE_OWNER_UID, EVIDENCE_OWNER_GID)
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
        details.st_uid != EVIDENCE_OWNER_UID
        or details.st_gid != EVIDENCE_OWNER_GID
        or stat.S_IMODE(details.st_mode) != 0o600
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
        details.st_uid != EVIDENCE_OWNER_UID
        or details.st_gid != EVIDENCE_OWNER_GID
        or stat.S_IMODE(details.st_mode) & 0o022
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


def _postgres_digests_valid(fields: dict[str, str]) -> bool:
    return all(
        _SHA256.fullmatch(fields.get(field, ""))
        for field in (
            "source_manifest_sha256",
            "migrated_target_manifest_sha256",
            "postgres_role_login_probe_sha256",
            "postgres_security_manifest_sha256",
            "source_dump_sha256",
            "source_worker_stop_evidence_sha256",
            "target_rollback_dump_sha256",
        )
    )


def _redis_digests_valid(fields: dict[str, str]) -> bool:
    return all(
        _SHA256.fullmatch(fields.get(field, ""))
        for field in (
            "source_worker_stop_evidence_sha256",
            "target_redis_manifest_sha256",
            "target_rollback_sha256",
            "target_state_sha256",
        )
    )


def _seed_digests_valid(fields: dict[str, str]) -> bool:
    return all(
        _SHA256.fullmatch(fields.get(field, ""))
        for field in (
            "backup_set_sha256",
            "configuration_checksums_sha256",
            "database_dump_sha256",
            "migrated_target_manifest_sha256",
            "postgres_role_login_probe_sha256",
            "postgres_security_manifest_sha256",
            "postgres_proof_sha256",
            "redis_dump_sha256",
            "redis_proof_sha256",
            "source_worker_stop_evidence_sha256",
            "target_redis_manifest_sha256",
        )
    )


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
    for name in (
        FINAL_PROOF_NAME,
        POSTGRES_PROOF_NAME,
        REDIS_PROOF_NAME,
        SEED_PROOF_NAME,
        FIRST_START_IN_PROGRESS_NAME,
        FIRST_START_PROOF_NAME,
    ):
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
    migrated_manifest_sha256: str,
    postgres_role_login_probe_sha256: str,
    postgres_security_manifest_sha256: str,
    source_dump_sha256: str,
    source_worker_stop_evidence_sha256: str,
    rollback_dump_sha256: str,
) -> None:
    common = _common_fields(cutover_id=cutover_id, revision=revision, source_fingerprint=source)
    if not _RUN_ID.fullmatch(run_id):
        raise EvidenceError("invalid PostgreSQL cutover run ID")
    for value in (
        manifest_sha256,
        migrated_manifest_sha256,
        postgres_role_login_probe_sha256,
        postgres_security_manifest_sha256,
        source_dump_sha256,
        source_worker_stop_evidence_sha256,
        rollback_dump_sha256,
    ):
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
            "migrated_target_manifest_sha256": migrated_manifest_sha256,
            "postgres_role_login_probe_sha256": postgres_role_login_probe_sha256,
            "postgres_security_manifest_sha256": postgres_security_manifest_sha256,
            "source_dump_sha256": source_dump_sha256,
            "source_frozen": "verified",
            "source_manifest_sha256": manifest_sha256,
            "source_worker_stop_evidence_sha256": source_worker_stop_evidence_sha256,
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
    for name in (FINAL_PROOF_NAME, REDIS_PROOF_NAME, SEED_PROOF_NAME):
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
    source_worker_stop_evidence_sha256: str,
    target_redis_manifest_sha256: str,
) -> None:
    common = _common_fields(cutover_id=cutover_id, revision=revision, source_fingerprint=source)
    if not _RUN_ID.fullmatch(run_id):
        raise EvidenceError("invalid Redis migration run ID")
    for value in (
        state_sha256,
        rollback_sha256,
        source_worker_stop_evidence_sha256,
        target_redis_manifest_sha256,
    ):
        if not _SHA256.fullmatch(value):
            raise EvidenceError("Redis evidence contains an invalid SHA-256 value")
    root = _ensure_evidence_root(root)
    _require_progress(root, common=common, phase="redis-in-progress")
    postgres = _load(root / POSTGRES_PROOF_NAME)
    if (
        not _matches_common(postgres, common)
        or not _postgres_digests_valid(postgres)
        or postgres.get("source_worker_stop_evidence_sha256")
        != source_worker_stop_evidence_sha256
    ):
        raise EvidenceError("PostgreSQL and Redis cutover sessions differ")
    _atomic_write(
        root / REDIS_PROOF_NAME,
        {
            **common,
            "caddy_changed": "false",
            "run_id": run_id,
            "source_frozen": "verified",
            "source_worker_stop_evidence_sha256": source_worker_stop_evidence_sha256,
            "source_worker_queue_zero": "verified",
            "status": "redis-cutover-verified",
            "target_broker_zero": "verified",
            "target_redis_manifest_sha256": target_redis_manifest_sha256,
            "target_rollback_sha256": rollback_sha256,
            "target_state_sha256": state_sha256,
            "verified_at_utc": _now(),
        },
    )
    _atomic_write(
        root / IN_PROGRESS_NAME,
        {**common, "phase": "redis-verified", "updated_at_utc": _now()},
    )


def validate_seed_ready(
    root: Path,
    *,
    cutover_id: str,
    revision: str,
    source: str,
    source_worker_stop_evidence_sha256: str,
    target_redis_manifest_sha256: str,
) -> dict[str, str]:
    common = _common_fields(cutover_id=cutover_id, revision=revision, source_fingerprint=source)
    if not _SHA256.fullmatch(source_worker_stop_evidence_sha256) or not _SHA256.fullmatch(
        target_redis_manifest_sha256
    ):
        raise EvidenceError("current cutover state contains an invalid SHA-256 value")
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
        or postgres.get("source_frozen") != "verified"
        or postgres.get("source_worker_queue_zero") != "verified"
        or postgres.get("pending_payments_before") != "0"
        or postgres.get("pending_payments_after") != "0"
        or postgres.get("api_worker_started") != "false"
        or not _postgres_digests_valid(postgres)
        or redis.get("status") != "redis-cutover-verified"
        or redis.get("source_frozen") != "verified"
        or redis.get("source_worker_queue_zero") != "verified"
        or redis.get("target_broker_zero") != "verified"
        or not _redis_digests_valid(redis)
        or redis.get("source_worker_stop_evidence_sha256")
        != postgres.get("source_worker_stop_evidence_sha256")
        or source_worker_stop_evidence_sha256
        != postgres.get("source_worker_stop_evidence_sha256")
        or target_redis_manifest_sha256
        != redis.get("target_redis_manifest_sha256")
    ):
        raise EvidenceError("PostgreSQL/Redis proofs are not ready for the first cutover seed")
    return {
        **common,
        "migrated_target_manifest_sha256": postgres[
            "migrated_target_manifest_sha256"
        ],
        "postgres_role_login_probe_sha256": postgres[
            "postgres_role_login_probe_sha256"
        ],
        "postgres_security_manifest_sha256": postgres[
            "postgres_security_manifest_sha256"
        ],
        "postgres_proof_sha256": sha256_file(postgres_path),
        "redis_proof_sha256": sha256_file(redis_path),
        "source_worker_stop_evidence_sha256": postgres[
            "source_worker_stop_evidence_sha256"
        ],
        "target_redis_manifest_sha256": redis[
            "target_redis_manifest_sha256"
        ],
    }


def write_seed(
    root: Path,
    *,
    cutover_id: str,
    revision: str,
    source: str,
    run_id: str,
    snapshot_id: str,
    repository_id_sha256: str,
    backup_set_sha256: str,
    database_dump_sha256: str,
    migrated_manifest_sha256: str,
    postgres_role_login_probe_sha256: str,
    postgres_security_manifest_sha256: str,
    redis_dump_sha256: str,
    source_worker_stop_evidence_sha256: str,
    target_redis_manifest_sha256: str,
    configuration_checksums_sha256: str,
) -> None:
    if not _RUN_ID.fullmatch(run_id):
        raise EvidenceError("invalid first-cutover seed run ID")
    if not _SNAPSHOT_ID.fullmatch(snapshot_id):
        raise EvidenceError("first-cutover seed snapshot ID must be one full lowercase Restic ID")
    for value in (
        repository_id_sha256,
        backup_set_sha256,
        database_dump_sha256,
        migrated_manifest_sha256,
        postgres_role_login_probe_sha256,
        postgres_security_manifest_sha256,
        redis_dump_sha256,
        source_worker_stop_evidence_sha256,
        target_redis_manifest_sha256,
        configuration_checksums_sha256,
    ):
        if not _SHA256.fullmatch(value):
            raise EvidenceError("first-cutover seed contains an invalid SHA-256 value")
    common = _common_fields(cutover_id=cutover_id, revision=revision, source_fingerprint=source)
    root = _ensure_evidence_root(root)
    step_hashes = validate_seed_ready(
        root,
        cutover_id=cutover_id,
        revision=revision,
        source=source,
        source_worker_stop_evidence_sha256=source_worker_stop_evidence_sha256,
        target_redis_manifest_sha256=target_redis_manifest_sha256,
    )
    if migrated_manifest_sha256 != step_hashes[
        "migrated_target_manifest_sha256"
    ]:
        raise EvidenceError(
            "first-cutover target manifest does not match the PostgreSQL cutover proof"
        )
    if postgres_security_manifest_sha256 != step_hashes[
        "postgres_security_manifest_sha256"
    ]:
        raise EvidenceError(
            "first-cutover PostgreSQL security manifest does not match the cutover proof"
        )
    if postgres_role_login_probe_sha256 != step_hashes[
        "postgres_role_login_probe_sha256"
    ]:
        raise EvidenceError(
            "first-cutover PostgreSQL login probe does not match the cutover proof"
        )
    if target_redis_manifest_sha256 != step_hashes[
        "target_redis_manifest_sha256"
    ]:
        raise EvidenceError(
            "first-cutover Redis manifest does not match the Redis cutover proof"
        )
    if source_worker_stop_evidence_sha256 != step_hashes[
        "source_worker_stop_evidence_sha256"
    ]:
        raise EvidenceError(
            "first-cutover worker-stop evidence does not match the cutover proofs"
        )
    seed_path = root / SEED_PROOF_NAME
    _atomic_write(
        seed_path,
        {
            **common,
            "api_worker_started": "false",
            "backup_set_sha256": backup_set_sha256,
            "caddy_changed": "false",
            "configuration_checksums_sha256": configuration_checksums_sha256,
            "database_dump_sha256": database_dump_sha256,
            "dns_changed": "false",
            "migrated_target_manifest_sha256": migrated_manifest_sha256,
            "postgres_role_login_probe_sha256": postgres_role_login_probe_sha256,
            "postgres_security_manifest_sha256": postgres_security_manifest_sha256,
            "postgres_proof_sha256": step_hashes["postgres_proof_sha256"],
            "redis_dump_sha256": redis_dump_sha256,
            "redis_proof_sha256": step_hashes["redis_proof_sha256"],
            "repository_id_sha256": repository_id_sha256,
            "run_id": run_id,
            "seeded_at_utc": _now(),
            "snapshot_id": snapshot_id,
            "source_worker_stop_evidence_sha256": source_worker_stop_evidence_sha256,
            "status": "first-cutover-seed-verified",
            "target_redis_manifest_sha256": target_redis_manifest_sha256,
        },
    )
    # The global stop fence already blocks production.  Publish the seed proof
    # first and only then advance its phase, so a crash is either retryable
    # from redis-verified or fail-closed at seed-verified.
    _atomic_write(
        root / IN_PROGRESS_NAME,
        {**common, "phase": "seed-verified", "updated_at_utc": _now()},
    )


def validate_seed(
    root: Path,
    *,
    cutover_id: str,
    revision: str,
    source: str,
    repository_id_sha256: str,
    source_worker_stop_evidence_sha256: str,
    target_redis_manifest_sha256: str,
) -> dict[str, str]:
    common = _common_fields(cutover_id=cutover_id, revision=revision, source_fingerprint=source)
    if not _SHA256.fullmatch(repository_id_sha256):
        raise EvidenceError("first-cutover seed repository identity is invalid")
    if not _SHA256.fullmatch(source_worker_stop_evidence_sha256) or not _SHA256.fullmatch(
        target_redis_manifest_sha256
    ):
        raise EvidenceError("current cutover state contains an invalid SHA-256 value")
    root = _ensure_evidence_root(root)
    _require_progress(root, common=common, phase="seed-verified")
    postgres_path = root / POSTGRES_PROOF_NAME
    redis_path = root / REDIS_PROOF_NAME
    postgres = _load(postgres_path)
    redis = _load(redis_path)
    seed = _load(root / SEED_PROOF_NAME)
    if (
        not _matches_common(seed, common)
        or seed.get("status") != "first-cutover-seed-verified"
        or seed.get("repository_id_sha256") != repository_id_sha256
        or not _RUN_ID.fullmatch(seed.get("run_id", ""))
        or not _SNAPSHOT_ID.fullmatch(seed.get("snapshot_id", ""))
        or not _postgres_digests_valid(postgres)
        or not _redis_digests_valid(redis)
        or not _seed_digests_valid(seed)
        or seed.get("migrated_target_manifest_sha256")
        != postgres.get("migrated_target_manifest_sha256")
        or seed.get("postgres_proof_sha256") != sha256_file(postgres_path)
        or seed.get("redis_proof_sha256") != sha256_file(redis_path)
        or seed.get("postgres_security_manifest_sha256")
        != postgres.get("postgres_security_manifest_sha256")
        or seed.get("postgres_role_login_probe_sha256")
        != postgres.get("postgres_role_login_probe_sha256")
        or seed.get("target_redis_manifest_sha256")
        != redis.get("target_redis_manifest_sha256")
        or seed.get("source_worker_stop_evidence_sha256")
        != postgres.get("source_worker_stop_evidence_sha256")
        or seed.get("source_worker_stop_evidence_sha256")
        != redis.get("source_worker_stop_evidence_sha256")
        or source_worker_stop_evidence_sha256
        != seed.get("source_worker_stop_evidence_sha256")
        or target_redis_manifest_sha256
        != seed.get("target_redis_manifest_sha256")
        or seed.get("api_worker_started") != "false"
        or seed.get("caddy_changed") != "false"
        or seed.get("dns_changed") != "false"
    ):
        raise EvidenceError("first-cutover seed proof does not match this provider cutover")
    return seed


def validate_seed_snapshot_document(
    document: object,
    *,
    expected_snapshot_id: str,
    run_started_epoch: int,
    now: dt.datetime | None = None,
) -> dict[str, object]:
    """Validate the Restic snapshot returned by this seed invocation.

    Restic records a snapshot's start time, not its upload completion time.
    Therefore the lower bound is the start of this script invocation while the
    upper bound only rejects a clock-skewed future timestamp. There is
    deliberately no maximum upload-duration bound.
    """

    if not _SNAPSHOT_ID.fullmatch(expected_snapshot_id):
        raise EvidenceError("expected first-cutover snapshot ID is invalid")
    if (
        isinstance(run_started_epoch, bool)
        or not isinstance(run_started_epoch, int)
        or run_started_epoch <= 0
    ):
        raise EvidenceError("first-cutover run start time is invalid")
    if not isinstance(document, list) or len(document) != 1:
        raise EvidenceError("Restic must return exactly one first-cutover snapshot")
    snapshot = document[0]
    if not isinstance(snapshot, dict):
        raise EvidenceError("Restic returned malformed first-cutover snapshot metadata")
    tags_value = snapshot.get("tags")
    if not isinstance(tags_value, list) or any(
        not isinstance(tag, str) for tag in tags_value
    ):
        raise EvidenceError("Restic returned malformed first-cutover snapshot tags")
    snapshot_id = str(snapshot.get("id") or "")
    if (
        snapshot_id != expected_snapshot_id
        or snapshot.get("hostname") != "lecturesift-production"
        or not {"lecturesift", "production", "first-cutover-seed"}.issubset(
            set(tags_value)
        )
    ):
        raise EvidenceError("Restic snapshot identity, host or tags do not match the seed")
    created_text = str(snapshot.get("time") or "")
    try:
        created = dt.datetime.fromisoformat(created_text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise EvidenceError("Restic snapshot start time is invalid") from exc
    if created.tzinfo is None:
        raise EvidenceError("Restic snapshot start time must include a timezone")
    current = now or dt.datetime.now(dt.timezone.utc)
    if current.tzinfo is None:
        raise EvidenceError("snapshot validation time must include a timezone")
    created_epoch = created.astimezone(dt.timezone.utc).timestamp()
    current_epoch = current.astimezone(dt.timezone.utc).timestamp()
    if created_epoch < run_started_epoch - 5 or created_epoch > current_epoch + 300:
        raise EvidenceError("Restic snapshot did not begin during this seed invocation")
    return snapshot


def validate_seed_snapshot_file(
    path: Path, *, expected_snapshot_id: str, run_started_epoch: int
) -> dict[str, object]:
    if not path.is_absolute() or not path.is_file() or path.is_symlink():
        raise EvidenceError("first-cutover snapshot metadata file is missing or unsafe")
    if path.resolve(strict=True) != path:
        raise EvidenceError("first-cutover snapshot metadata path is not canonical")
    details = path.stat()
    if os.name == "posix" and (
        details.st_uid != EVIDENCE_OWNER_UID
        or details.st_gid != EVIDENCE_OWNER_GID
        or stat.S_IMODE(details.st_mode) & 0o077
    ):
        raise EvidenceError("first-cutover snapshot metadata must be root-private")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise EvidenceError("first-cutover snapshot metadata is unreadable") from exc
    return validate_seed_snapshot_document(
        document,
        expected_snapshot_id=expected_snapshot_id,
        run_started_epoch=run_started_epoch,
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
    migrated_target_manifest_sha256: str,
    postgres_role_login_probe_sha256: str,
    postgres_security_manifest_sha256: str,
    source_worker_stop_evidence_sha256: str,
    target_redis_manifest_sha256: str,
) -> None:
    common = _common_fields(cutover_id=cutover_id, revision=revision, source_fingerprint=source)
    for value in (
        recovery_sha256,
        retention_sha256,
        repository_id_sha256,
        migrated_target_manifest_sha256,
        postgres_role_login_probe_sha256,
        postgres_security_manifest_sha256,
        source_worker_stop_evidence_sha256,
        target_redis_manifest_sha256,
    ):
        if not _SHA256.fullmatch(value):
            raise EvidenceError("final cutover evidence contains an invalid SHA-256 value")
    if recovery_marker.name != recovery_marker.as_posix() or retention_marker.name != retention_marker.as_posix():
        raise EvidenceError("final evidence records marker basenames only")
    root = _ensure_evidence_root(root)
    _require_progress(root, common=common, phase="seed-verified")
    postgres_path = root / POSTGRES_PROOF_NAME
    redis_path = root / REDIS_PROOF_NAME
    seed_path = root / SEED_PROOF_NAME
    postgres = _load(postgres_path)
    redis = _load(redis_path)
    seed = validate_seed(
        root,
        cutover_id=cutover_id,
        revision=revision,
        source=source,
        repository_id_sha256=repository_id_sha256,
        source_worker_stop_evidence_sha256=source_worker_stop_evidence_sha256,
        target_redis_manifest_sha256=target_redis_manifest_sha256,
    )
    recovery_path = RECOVERY_EVIDENCE_ROOT / recovery_marker.name
    retention_path = RECOVERY_EVIDENCE_ROOT / retention_marker.name
    recovery = _load_recovery_evidence(recovery_path)
    retention = _load_recovery_evidence(retention_path)
    if (
        sha256_file(recovery_path) != recovery_sha256
        or sha256_file(retention_path) != retention_sha256
        or recovery.get("status") != "success"
        or recovery.get("drill_scope") != "current-latest"
        or recovery.get("postgres_restore") != "verified"
        or recovery.get("redis_restore") != "verified"
        or recovery.get("restored_payload_removed") != "true"
        or recovery.get("live_services_touched") != "false"
        or recovery.get("repository_id_sha256") != repository_id_sha256
        or recovery.get("snapshot_id") != seed.get("snapshot_id")
        or recovery.get("backup_set_sha256") != seed.get("backup_set_sha256")
        or retention.get("status") != "immutable-retention-verified"
        or retention.get("repository_id_sha256") != repository_id_sha256
    ):
        raise EvidenceError(
            "recovery/retention evidence does not exactly match the first-cutover seed"
        )
    if (
        not _matches_common(postgres, common)
        or not _matches_common(redis, common)
        or postgres.get("status") != "postgres-cutover-verified"
        or redis.get("status") != "redis-cutover-verified"
        or not _postgres_digests_valid(postgres)
        or not _redis_digests_valid(redis)
        or migrated_target_manifest_sha256
        != postgres.get("migrated_target_manifest_sha256")
        or migrated_target_manifest_sha256
        != seed.get("migrated_target_manifest_sha256")
        or postgres_security_manifest_sha256
        != postgres.get("postgres_security_manifest_sha256")
        or postgres_security_manifest_sha256
        != seed.get("postgres_security_manifest_sha256")
        or postgres_role_login_probe_sha256
        != postgres.get("postgres_role_login_probe_sha256")
        or postgres_role_login_probe_sha256
        != seed.get("postgres_role_login_probe_sha256")
        or target_redis_manifest_sha256
        != redis.get("target_redis_manifest_sha256")
        or target_redis_manifest_sha256
        != seed.get("target_redis_manifest_sha256")
        or source_worker_stop_evidence_sha256
        != postgres.get("source_worker_stop_evidence_sha256")
        or source_worker_stop_evidence_sha256
        != redis.get("source_worker_stop_evidence_sha256")
        or source_worker_stop_evidence_sha256
        != seed.get("source_worker_stop_evidence_sha256")
    ):
        raise EvidenceError("PostgreSQL and Redis proofs do not exactly match the final session")
    _atomic_write(
        root / FINAL_PROOF_NAME,
        {
            **common,
            "caddy_changed": "false",
            "dns_changed": "false",
            "finalized_at_utc": _now(),
            "migrated_target_manifest_sha256": migrated_target_manifest_sha256,
            "pending_payments": "0",
            "postgres_role_login_probe_sha256": postgres_role_login_probe_sha256,
            "postgres_security_manifest_sha256": postgres_security_manifest_sha256,
            "postgres_proof_sha256": sha256_file(postgres_path),
            "r2_recovery_marker": recovery_marker.name,
            "r2_recovery_marker_sha256": recovery_sha256,
            "r2_repository_id_sha256": repository_id_sha256,
            "r2_retention_marker": retention_marker.name,
            "r2_retention_marker_sha256": retention_sha256,
            "redis_proof_sha256": sha256_file(redis_path),
            "seed_backup_set_sha256": seed["backup_set_sha256"],
            "seed_proof_sha256": sha256_file(seed_path),
            "seed_snapshot_id": seed["snapshot_id"],
            "source_freeze_revalidated": "true",
            "source_worker_stop_evidence_sha256": source_worker_stop_evidence_sha256,
            "source_worker_queue_zero": "verified",
            "status": "provider-cutover-verified",
            "target_api_worker_started": "false",
            "target_queue_zero": "verified",
            "target_redis_manifest_sha256": target_redis_manifest_sha256,
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
    seed = root / SEED_PROOF_NAME
    postgres_fields = _load(postgres)
    redis_fields = _load(redis)
    seed_fields = _load(seed)
    if final.get("postgres_proof_sha256") != sha256_file(postgres):
        raise EvidenceError("PostgreSQL cutover proof changed after finalization")
    if final.get("redis_proof_sha256") != sha256_file(redis):
        raise EvidenceError("Redis cutover proof changed after finalization")
    if final.get("seed_proof_sha256") != sha256_file(seed):
        raise EvidenceError("first-cutover seed proof changed after finalization")
    for step in (postgres_fields, redis_fields, seed_fields):
        for key in (
            "cutover_id",
            "release_revision",
            "source_fingerprint_sha256",
            "version",
        ):
            if step.get(key) != final.get(key):
                raise EvidenceError("step proof does not match provider final proof")
    if not _postgres_digests_valid(postgres_fields):
        raise EvidenceError("PostgreSQL cutover proof contains invalid digest evidence")
    if (
        not _seed_digests_valid(seed_fields)
        or not _redis_digests_valid(redis_fields)
        or seed_fields.get("migrated_target_manifest_sha256")
        != postgres_fields.get("migrated_target_manifest_sha256")
        or seed_fields.get("postgres_proof_sha256") != sha256_file(postgres)
        or seed_fields.get("redis_proof_sha256") != sha256_file(redis)
        or seed_fields.get("postgres_security_manifest_sha256")
        != postgres_fields.get("postgres_security_manifest_sha256")
        or seed_fields.get("postgres_role_login_probe_sha256")
        != postgres_fields.get("postgres_role_login_probe_sha256")
        or seed_fields.get("target_redis_manifest_sha256")
        != redis_fields.get("target_redis_manifest_sha256")
        or seed_fields.get("source_worker_stop_evidence_sha256")
        != postgres_fields.get("source_worker_stop_evidence_sha256")
        or seed_fields.get("source_worker_stop_evidence_sha256")
        != redis_fields.get("source_worker_stop_evidence_sha256")
        or final.get("migrated_target_manifest_sha256")
        != seed_fields.get("migrated_target_manifest_sha256")
        or final.get("postgres_security_manifest_sha256")
        != seed_fields.get("postgres_security_manifest_sha256")
        or final.get("postgres_role_login_probe_sha256")
        != seed_fields.get("postgres_role_login_probe_sha256")
        or final.get("target_redis_manifest_sha256")
        != seed_fields.get("target_redis_manifest_sha256")
        or final.get("source_worker_stop_evidence_sha256")
        != seed_fields.get("source_worker_stop_evidence_sha256")
    ):
        raise EvidenceError("first-cutover seed contains invalid digest evidence")
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
        or seed_fields.get("repository_id_sha256") != repository_hash
        or seed_fields.get("status") != "first-cutover-seed-verified"
        or recovery_fields.get("snapshot_id") != seed_fields.get("snapshot_id")
        or recovery_fields.get("backup_set_sha256") != seed_fields.get("backup_set_sha256")
        or final.get("seed_snapshot_id") != seed_fields.get("snapshot_id")
        or final.get("seed_backup_set_sha256") != seed_fields.get("backup_set_sha256")
        or recovery_fields.get("status") != "success"
        or retention_fields.get("status") != "immutable-retention-verified"
    ):
        raise EvidenceError("R2 recovery/retention evidence does not match the finalized repository")
    return final


def _first_start_identity(
    root: Path, *, expected_revision: str
) -> tuple[dict[str, str], str]:
    final = validate_final(root, expected_revision=expected_revision)
    return final, sha256_file(root / FINAL_PROOF_NAME)


def _consumed_first_start_valid(
    fields: dict[str, str], *, final: dict[str, str], final_sha256: str
) -> bool:
    expected = {
        "consumed_at_utc",
        "cutover_id",
        "final_proof_sha256",
        "migrated_target_manifest_sha256",
        "postgres_role_login_probe_sha256",
        "postgres_security_manifest_sha256",
        "release_revision",
        "source_fingerprint_sha256",
        "status",
        "target_redis_manifest_sha256",
        "version",
    }
    return (
        set(fields) == expected
        and fields.get("version") == VERSION
        and fields.get("status") == "provider-first-start-consumed"
        and fields.get("cutover_id") == final.get("cutover_id")
        and fields.get("release_revision") == final.get("release_revision")
        and fields.get("source_fingerprint_sha256")
        == final.get("source_fingerprint_sha256")
        and fields.get("final_proof_sha256") == final_sha256
        and fields.get("migrated_target_manifest_sha256")
        == final.get("migrated_target_manifest_sha256")
        and fields.get("postgres_role_login_probe_sha256")
        == final.get("postgres_role_login_probe_sha256")
        and fields.get("postgres_security_manifest_sha256")
        == final.get("postgres_security_manifest_sha256")
        and fields.get("target_redis_manifest_sha256")
        == final.get("target_redis_manifest_sha256")
    )


def first_start_status(root: Path, *, expected_revision: str) -> str:
    root = _ensure_evidence_root(root)
    final, final_sha256 = _first_start_identity(
        root, expected_revision=expected_revision
    )
    progress = root / FIRST_START_IN_PROGRESS_NAME
    consumed = root / FIRST_START_PROOF_NAME
    if progress.exists() or progress.is_symlink():
        raise EvidenceError(
            "provider first start is in progress; manual review is required"
        )
    if consumed.exists() or consumed.is_symlink():
        fields = _load(consumed)
        if not _consumed_first_start_valid(
            fields, final=final, final_sha256=final_sha256
        ):
            raise EvidenceError("provider first-start proof does not match final evidence")
        return "consumed"
    return "required"


def arm_first_start(
    root: Path,
    *,
    expected_revision: str,
    migrated_target_manifest_sha256: str,
    postgres_role_login_probe_sha256: str,
    postgres_security_manifest_sha256: str,
    target_redis_manifest_sha256: str,
) -> str:
    for value in (
        migrated_target_manifest_sha256,
        postgres_role_login_probe_sha256,
        postgres_security_manifest_sha256,
        target_redis_manifest_sha256,
    ):
        if not _SHA256.fullmatch(value):
            raise EvidenceError("provider first-start state digest is invalid")
    status = first_start_status(root, expected_revision=expected_revision)
    if status == "consumed":
        return status
    root = _ensure_evidence_root(root)
    final, final_sha256 = _first_start_identity(
        root, expected_revision=expected_revision
    )
    if (
        migrated_target_manifest_sha256
        != final.get("migrated_target_manifest_sha256")
        or postgres_role_login_probe_sha256
        != final.get("postgres_role_login_probe_sha256")
        or postgres_security_manifest_sha256
        != final.get("postgres_security_manifest_sha256")
        or target_redis_manifest_sha256
        != final.get("target_redis_manifest_sha256")
    ):
        raise EvidenceError(
            "provider first-start target state does not match finalized evidence"
        )
    _atomic_write(
        root / FIRST_START_IN_PROGRESS_NAME,
        {
            "armed_at_utc": _now(),
            "cutover_id": final["cutover_id"],
            "final_proof_sha256": final_sha256,
            "migrated_target_manifest_sha256": migrated_target_manifest_sha256,
            "postgres_role_login_probe_sha256": postgres_role_login_probe_sha256,
            "postgres_security_manifest_sha256": postgres_security_manifest_sha256,
            "release_revision": final["release_revision"],
            "source_fingerprint_sha256": final["source_fingerprint_sha256"],
            "status": "provider-first-start-armed",
            "target_redis_manifest_sha256": target_redis_manifest_sha256,
            "version": VERSION,
        },
    )
    return "armed"


def complete_first_start(root: Path, *, expected_revision: str) -> str:
    root = _ensure_evidence_root(root)
    final, final_sha256 = _first_start_identity(
        root, expected_revision=expected_revision
    )
    progress_path = root / FIRST_START_IN_PROGRESS_NAME
    consumed_path = root / FIRST_START_PROOF_NAME
    if consumed_path.exists() or consumed_path.is_symlink():
        if progress_path.exists() or progress_path.is_symlink():
            raise EvidenceError(
                "provider first-start completion is ambiguous; manual review is required"
            )
        consumed = _load(consumed_path)
        if not _consumed_first_start_valid(
            consumed, final=final, final_sha256=final_sha256
        ):
            raise EvidenceError("provider first-start proof does not match final evidence")
        return "consumed"
    progress = _load(progress_path)
    expected_progress = {
        "armed_at_utc",
        "cutover_id",
        "final_proof_sha256",
        "migrated_target_manifest_sha256",
        "postgres_role_login_probe_sha256",
        "postgres_security_manifest_sha256",
        "release_revision",
        "source_fingerprint_sha256",
        "status",
        "target_redis_manifest_sha256",
        "version",
    }
    if (
        set(progress) != expected_progress
        or progress.get("version") != VERSION
        or progress.get("status") != "provider-first-start-armed"
        or progress.get("cutover_id") != final.get("cutover_id")
        or progress.get("release_revision") != final.get("release_revision")
        or progress.get("source_fingerprint_sha256")
        != final.get("source_fingerprint_sha256")
        or progress.get("final_proof_sha256") != final_sha256
        or progress.get("migrated_target_manifest_sha256")
        != final.get("migrated_target_manifest_sha256")
        or progress.get("postgres_role_login_probe_sha256")
        != final.get("postgres_role_login_probe_sha256")
        or progress.get("postgres_security_manifest_sha256")
        != final.get("postgres_security_manifest_sha256")
        or progress.get("target_redis_manifest_sha256")
        != final.get("target_redis_manifest_sha256")
    ):
        raise EvidenceError(
            "provider first-start progress does not match finalized evidence"
        )
    _atomic_write(
        consumed_path,
        {
            "consumed_at_utc": _now(),
            "cutover_id": final["cutover_id"],
            "final_proof_sha256": final_sha256,
            "migrated_target_manifest_sha256": final[
                "migrated_target_manifest_sha256"
            ],
            "postgres_role_login_probe_sha256": final[
                "postgres_role_login_probe_sha256"
            ],
            "postgres_security_manifest_sha256": final[
                "postgres_security_manifest_sha256"
            ],
            "release_revision": final["release_revision"],
            "source_fingerprint_sha256": final["source_fingerprint_sha256"],
            "status": "provider-first-start-consumed",
            "target_redis_manifest_sha256": final[
                "target_redis_manifest_sha256"
            ],
            "version": VERSION,
        },
    )
    # The consumed proof is durable before the stronger crash fence is removed.
    # If power is lost between these operations both files remain and the next
    # preflight fails closed for operator review.
    _unlink(progress_path)
    return "consumed"


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
    postgres.add_argument("--migrated-manifest-sha256", required=True)
    postgres.add_argument("--postgres-role-login-probe-sha256", required=True)
    postgres.add_argument("--postgres-security-manifest-sha256", required=True)
    postgres.add_argument("--source-dump-sha256", required=True)
    postgres.add_argument("--source-worker-stop-evidence-sha256", required=True)
    postgres.add_argument("--rollback-dump-sha256", required=True)
    common(commands.add_parser("begin-redis"))
    redis = commands.add_parser("write-redis")
    common(redis)
    redis.add_argument("--run-id", required=True)
    redis.add_argument("--state-sha256", required=True)
    redis.add_argument("--rollback-sha256", required=True)
    redis.add_argument("--source-worker-stop-evidence-sha256", required=True)
    redis.add_argument("--target-redis-manifest-sha256", required=True)
    seed_ready = commands.add_parser("validate-seed-ready")
    common(seed_ready)
    seed_ready.add_argument("--source-worker-stop-evidence-sha256", required=True)
    seed_ready.add_argument("--target-redis-manifest-sha256", required=True)
    seed = commands.add_parser("write-seed")
    common(seed)
    seed.add_argument("--run-id", required=True)
    seed.add_argument("--snapshot-id", required=True)
    seed.add_argument("--repository-id-sha256", required=True)
    seed.add_argument("--backup-set-sha256", required=True)
    seed.add_argument("--database-dump-sha256", required=True)
    seed.add_argument("--migrated-manifest-sha256", required=True)
    seed.add_argument("--postgres-role-login-probe-sha256", required=True)
    seed.add_argument("--postgres-security-manifest-sha256", required=True)
    seed.add_argument("--redis-dump-sha256", required=True)
    seed.add_argument("--source-worker-stop-evidence-sha256", required=True)
    seed.add_argument("--target-redis-manifest-sha256", required=True)
    seed.add_argument("--configuration-checksums-sha256", required=True)
    validate_seed_parser = commands.add_parser("validate-seed")
    common(validate_seed_parser)
    validate_seed_parser.add_argument("--repository-id-sha256", required=True)
    validate_seed_parser.add_argument(
        "--source-worker-stop-evidence-sha256", required=True
    )
    validate_seed_parser.add_argument("--target-redis-manifest-sha256", required=True)
    snapshot = commands.add_parser("validate-seed-snapshot")
    snapshot.add_argument("--snapshot-json", required=True)
    snapshot.add_argument("--expected-snapshot-id", required=True)
    snapshot.add_argument("--run-started-epoch", required=True, type=int)
    final = commands.add_parser("finalize")
    common(final)
    final.add_argument("--recovery-marker", required=True)
    final.add_argument("--recovery-sha256", required=True)
    final.add_argument("--retention-marker", required=True)
    final.add_argument("--retention-sha256", required=True)
    final.add_argument("--repository-id-sha256", required=True)
    final.add_argument("--migrated-target-manifest-sha256", required=True)
    final.add_argument("--postgres-role-login-probe-sha256", required=True)
    final.add_argument("--postgres-security-manifest-sha256", required=True)
    final.add_argument("--source-worker-stop-evidence-sha256", required=True)
    final.add_argument("--target-redis-manifest-sha256", required=True)
    validate = commands.add_parser("validate-final")
    validate.add_argument("--expected-revision", required=True)
    first_start_status_parser = commands.add_parser("first-start-status")
    first_start_status_parser.add_argument("--expected-revision", required=True)
    first_start_arm = commands.add_parser("arm-first-start")
    first_start_arm.add_argument("--expected-revision", required=True)
    first_start_arm.add_argument("--migrated-target-manifest-sha256", required=True)
    first_start_arm.add_argument("--postgres-role-login-probe-sha256", required=True)
    first_start_arm.add_argument("--postgres-security-manifest-sha256", required=True)
    first_start_arm.add_argument("--target-redis-manifest-sha256", required=True)
    first_start_complete = commands.add_parser("complete-first-start")
    first_start_complete.add_argument("--expected-revision", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if (EVIDENCE_OWNER_UID, EVIDENCE_OWNER_GID) != (0, 0):
            raise EvidenceError("production evidence ownership must remain root:root")
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
        if args.command == "validate-seed-snapshot":
            validate_seed_snapshot_file(
                Path(args.snapshot_json),
                expected_snapshot_id=args.expected_snapshot_id,
                run_started_epoch=args.run_started_epoch,
            )
        elif args.command == "begin-postgres":
            begin_postgres(EVIDENCE_ROOT, **kwargs)
        elif args.command == "write-postgres":
            write_postgres(
                EVIDENCE_ROOT,
                **kwargs,
                run_id=args.run_id,
                manifest_sha256=args.manifest_sha256,
                migrated_manifest_sha256=args.migrated_manifest_sha256,
                postgres_role_login_probe_sha256=args.postgres_role_login_probe_sha256,
                postgres_security_manifest_sha256=args.postgres_security_manifest_sha256,
                source_dump_sha256=args.source_dump_sha256,
                source_worker_stop_evidence_sha256=args.source_worker_stop_evidence_sha256,
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
                source_worker_stop_evidence_sha256=args.source_worker_stop_evidence_sha256,
                target_redis_manifest_sha256=args.target_redis_manifest_sha256,
            )
        elif args.command == "validate-seed-ready":
            validate_seed_ready(
                EVIDENCE_ROOT,
                **kwargs,
                source_worker_stop_evidence_sha256=args.source_worker_stop_evidence_sha256,
                target_redis_manifest_sha256=args.target_redis_manifest_sha256,
            )
        elif args.command == "write-seed":
            write_seed(
                EVIDENCE_ROOT,
                **kwargs,
                run_id=args.run_id,
                snapshot_id=args.snapshot_id,
                repository_id_sha256=args.repository_id_sha256,
                backup_set_sha256=args.backup_set_sha256,
                database_dump_sha256=args.database_dump_sha256,
                migrated_manifest_sha256=args.migrated_manifest_sha256,
                postgres_role_login_probe_sha256=args.postgres_role_login_probe_sha256,
                postgres_security_manifest_sha256=args.postgres_security_manifest_sha256,
                redis_dump_sha256=args.redis_dump_sha256,
                source_worker_stop_evidence_sha256=args.source_worker_stop_evidence_sha256,
                target_redis_manifest_sha256=args.target_redis_manifest_sha256,
                configuration_checksums_sha256=args.configuration_checksums_sha256,
            )
        elif args.command == "validate-seed":
            validate_seed(
                EVIDENCE_ROOT,
                **kwargs,
                repository_id_sha256=args.repository_id_sha256,
                source_worker_stop_evidence_sha256=args.source_worker_stop_evidence_sha256,
                target_redis_manifest_sha256=args.target_redis_manifest_sha256,
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
                migrated_target_manifest_sha256=args.migrated_target_manifest_sha256,
                postgres_role_login_probe_sha256=args.postgres_role_login_probe_sha256,
                postgres_security_manifest_sha256=args.postgres_security_manifest_sha256,
                source_worker_stop_evidence_sha256=args.source_worker_stop_evidence_sha256,
                target_redis_manifest_sha256=args.target_redis_manifest_sha256,
            )
        elif args.command == "validate-final":
            validate_final(EVIDENCE_ROOT, expected_revision=args.expected_revision)
        elif args.command == "first-start-status":
            print(
                first_start_status(
                    EVIDENCE_ROOT, expected_revision=args.expected_revision
                )
            )
        elif args.command == "arm-first-start":
            print(
                arm_first_start(
                    EVIDENCE_ROOT,
                    expected_revision=args.expected_revision,
                    migrated_target_manifest_sha256=args.migrated_target_manifest_sha256,
                    postgres_role_login_probe_sha256=args.postgres_role_login_probe_sha256,
                    postgres_security_manifest_sha256=args.postgres_security_manifest_sha256,
                    target_redis_manifest_sha256=args.target_redis_manifest_sha256,
                )
            )
        elif args.command == "complete-first-start":
            print(
                complete_first_start(
                    EVIDENCE_ROOT, expected_revision=args.expected_revision
                )
            )
        return 0
    except EvidenceError as exc:
        print(f"Provider cutover evidence rejected: {exc}", file=os.sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
