from __future__ import annotations

import datetime as dt
import hashlib
import importlib.util
import json
import os
import stat
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "provider_cutover_evidence", ROOT / "deploy" / "provider_cutover_evidence.py"
)
assert SPEC and SPEC.loader
evidence = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = evidence
SPEC.loader.exec_module(evidence)
SECURITY_SPEC = importlib.util.spec_from_file_location(
    "validate_postgres_security_manifest",
    ROOT / "deploy" / "validate_postgres_security_manifest.py",
)
assert SECURITY_SPEC and SECURITY_SPEC.loader
security_manifest = importlib.util.module_from_spec(SECURITY_SPEC)
sys.modules[SECURITY_SPEC.name] = security_manifest
SECURITY_SPEC.loader.exec_module(security_manifest)

CUTOVER_ID = "1" * 32
REVISION = "2" * 40
SOURCE = "3" * 64
SOURCE_MANIFEST = "4" * 64
SOURCE_DUMP = "5" * 64
ROLLBACK_DUMP = "6" * 64
MIGRATED_MANIFEST = "7" * 64
REDIS_STATE = "8" * 64
REDIS_ROLLBACK = "9" * 64
SOURCE_WORKER_STOP = "a" * 64
POSTGRES_SECURITY = "b" * 64
TARGET_REDIS_MANIFEST = "c" * 64
POSTGRES_ROLE_LOGIN_PROBE = "d" * 64


@pytest.fixture(autouse=True)
def _use_ci_process_as_evidence_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercise ownership checks without requiring the Linux CI user to be root."""
    if os.name == "posix":
        monkeypatch.setattr(evidence, "EVIDENCE_OWNER_UID", os.geteuid())
        monkeypatch.setattr(evidence, "EVIDENCE_OWNER_GID", os.getegid())


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _chmod(path: Path, mode: int) -> None:
    path.chmod(mode)
    if os.name == "posix":
        assert stat.S_IMODE(path.stat().st_mode) == mode


def _write_external(path: Path, fields: dict[str, str], mode: int = 0o600) -> None:
    path.write_text(
        "".join(f"{key}={value}\n" for key, value in sorted(fields.items())),
        encoding="utf-8",
    )
    _chmod(path, mode)


def _security_manifest_text() -> str:
    families = (
        "column_acl",
        "database",
        "database_acl",
        "default_acl",
        "extension",
        "relation",
        "relation_acl",
        "role",
        "role_setting",
        "schema",
        "schema_acl",
        "tablespace",
        "tablespace_acl",
        "type",
        "type_acl",
        "view_definition",
    )
    records = [
        {"family": family, "name": family}
        for family in families
    ]
    records.extend(
        {"family": "expected_role", "kind": kind, "name": name, "present": True}
        for kind, name in (
            ("owner", "lecturesift_owner"),
            ("api", "lecturesift_api"),
            ("worker", "lecturesift_worker"),
        )
    )
    records.append(
        {
            "contract": "postgres-security-v1",
            "family": "security_coverage",
            "large_objects": 0,
            "parameter_acl_rows": 0,
            "tablespaces": 2,
            "types": 4,
        }
    )
    payloads = sorted(
        json.dumps(record, sort_keys=True) for record in records
    )
    digest = hashlib.md5(
        "\n".join(payloads).encode(), usedforsecurity=False
    ).hexdigest()
    return (
        "SECURITY_MANIFEST|v1\n"
        + "".join(f"SECURITY_OBJECT|{payload}\n" for payload in payloads)
        + f"SECURITY_COMPLETE|v1|{len(payloads)}|{digest}\n"
    )


def test_postgres_security_manifest_is_complete_canonical_and_fail_closed(
    tmp_path: Path,
):
    manifest = tmp_path / "postgres-security.raw"
    manifest.write_text(_security_manifest_text(), encoding="utf-8")
    assert security_manifest.canonicalize(manifest) == _security_manifest_text()

    lines = _security_manifest_text().splitlines()
    manifest.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")
    with pytest.raises(security_manifest.ManifestError, match="sentinel"):
        security_manifest.canonicalize(manifest)

    tampered = lines.copy()
    database_line = next(
        index
        for index, line in enumerate(tampered)
        if '"family": "database"' in line and '"name": "database"' in line
    )
    tampered[database_line] = tampered[database_line].replace(
        '"name": "database"', '"name": "changed"'
    )
    manifest.write_text("\n".join(tampered) + "\n", encoding="utf-8")
    with pytest.raises(security_manifest.ManifestError, match="digest"):
        security_manifest.canonicalize(manifest)

    reordered = lines.copy()
    reordered[1], reordered[2] = reordered[2], reordered[1]
    manifest.write_text("\n".join(reordered) + "\n", encoding="utf-8")
    with pytest.raises(security_manifest.ManifestError, match="canonical"):
        security_manifest.canonicalize(manifest)


def test_source_fingerprint_omits_secrets_but_binds_every_source_endpoint():
    environment = {
        "SOURCE_DATABASE_URL": (
            "postgresql://owner:db-secret@db.example.render.com:5432/lecturesift"
            "?sslmode=verify-full"
        ),
        "SOURCE_HEALTH_URL": "https://lecturesift-backend.onrender.com/health",
        "SOURCE_REDIS_URL": "rediss://default:redis-secret@oregon-keyvalue.render.com:6379/0",
        "SOURCE_CELERY_BROKER_URL": (
            "rediss://default:redis-secret@oregon-keyvalue.render.com:6379/0"
        ),
    }
    first = evidence.source_fingerprint_from_environment(environment)
    environment["SOURCE_DATABASE_URL"] = environment["SOURCE_DATABASE_URL"].replace(
        "db-secret", "rotated-secret"
    )
    environment["SOURCE_REDIS_URL"] = environment["SOURCE_REDIS_URL"].replace(
        "redis-secret", "rotated-secret"
    )
    environment["SOURCE_CELERY_BROKER_URL"] = environment[
        "SOURCE_CELERY_BROKER_URL"
    ].replace("redis-secret", "rotated-secret")
    assert evidence.source_fingerprint_from_environment(environment) == first

    environment["SOURCE_HEALTH_URL"] = "https://other-backend.onrender.com/health"
    assert evidence.source_fingerprint_from_environment(environment) != first


def test_atomic_state_machine_requires_matching_postgres_redis_r2_and_revision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    assert evidence.VERSION == "3"
    root = tmp_path / "provider-cutover"
    recovery_root = tmp_path / "recovery-drills"
    recovery_root.mkdir(mode=0o700)
    monkeypatch.setattr(evidence, "RECOVERY_EVIDENCE_ROOT", recovery_root)

    evidence.begin_postgres(root, cutover_id=CUTOVER_ID, revision=REVISION, source=SOURCE)
    assert (root / evidence.IN_PROGRESS_NAME).is_file()
    assert not (root / evidence.FINAL_PROOF_NAME).exists()

    with pytest.raises(evidence.EvidenceError, match="invalid SHA-256"):
        evidence.write_postgres(
            root,
            cutover_id=CUTOVER_ID,
            revision=REVISION,
            source=SOURCE,
            run_id="postgres-cutover-20260831T190203Z",
            manifest_sha256=SOURCE_MANIFEST,
            migrated_manifest_sha256="not-a-sha256",
            postgres_role_login_probe_sha256=POSTGRES_ROLE_LOGIN_PROBE,
            postgres_security_manifest_sha256=POSTGRES_SECURITY,
            source_dump_sha256=SOURCE_DUMP,
            source_worker_stop_evidence_sha256=SOURCE_WORKER_STOP,
            rollback_dump_sha256=ROLLBACK_DUMP,
        )
    evidence.write_postgres(
        root,
        cutover_id=CUTOVER_ID,
        revision=REVISION,
        source=SOURCE,
        run_id="postgres-cutover-20260831T190203Z",
        manifest_sha256=SOURCE_MANIFEST,
        migrated_manifest_sha256=MIGRATED_MANIFEST,
        postgres_role_login_probe_sha256=POSTGRES_ROLE_LOGIN_PROBE,
        postgres_security_manifest_sha256=POSTGRES_SECURITY,
        source_dump_sha256=SOURCE_DUMP,
        source_worker_stop_evidence_sha256=SOURCE_WORKER_STOP,
        rollback_dump_sha256=ROLLBACK_DUMP,
    )
    postgres_fields = dict(
        line.split("=", 1)
        for line in (root / evidence.POSTGRES_PROOF_NAME).read_text(encoding="utf-8").splitlines()
    )
    assert postgres_fields["source_manifest_sha256"] == SOURCE_MANIFEST
    assert postgres_fields["migrated_target_manifest_sha256"] == MIGRATED_MANIFEST
    assert postgres_fields["postgres_security_manifest_sha256"] == POSTGRES_SECURITY
    assert (
        postgres_fields["postgres_role_login_probe_sha256"]
        == POSTGRES_ROLE_LOGIN_PROBE
    )
    assert (
        postgres_fields["source_worker_stop_evidence_sha256"]
        == SOURCE_WORKER_STOP
    )
    with pytest.raises(evidence.EvidenceError, match="phase/session"):
        evidence.begin_redis(
            root,
            cutover_id=CUTOVER_ID,
            revision="7" * 40,
            source=SOURCE,
        )

    evidence.begin_redis(root, cutover_id=CUTOVER_ID, revision=REVISION, source=SOURCE)
    evidence.write_redis(
        root,
        cutover_id=CUTOVER_ID,
        revision=REVISION,
        source=SOURCE,
        run_id="redis-migration-20260831T190503Z",
        state_sha256=REDIS_STATE,
        rollback_sha256=REDIS_ROLLBACK,
        source_worker_stop_evidence_sha256=SOURCE_WORKER_STOP,
        target_redis_manifest_sha256=TARGET_REDIS_MANIFEST,
    )

    # Version 2 did not bind the security, worker-stop, or full Redis state.
    # It must not be accepted even when every other field is preserved.
    postgres_path = root / evidence.POSTGRES_PROOF_NAME
    postgres_fields = evidence._load(postgres_path)
    old_postgres_fields = {**postgres_fields, "version": "2"}
    old_postgres_fields.pop("postgres_security_manifest_sha256")
    evidence._atomic_write(postgres_path, old_postgres_fields)
    with pytest.raises(evidence.EvidenceError, match="not ready"):
        evidence.validate_seed_ready(
            root,
            cutover_id=CUTOVER_ID,
            revision=REVISION,
            source=SOURCE,
            source_worker_stop_evidence_sha256=SOURCE_WORKER_STOP,
            target_redis_manifest_sha256=TARGET_REDIS_MANIFEST,
        )
    evidence._atomic_write(postgres_path, postgres_fields)

    repository_hash = "a" * 64
    snapshot_id = "b" * 64
    backup_set_hash = "c" * 64
    assert evidence.validate_seed_ready(
        root,
        cutover_id=CUTOVER_ID,
        revision=REVISION,
        source=SOURCE,
        source_worker_stop_evidence_sha256=SOURCE_WORKER_STOP,
        target_redis_manifest_sha256=TARGET_REDIS_MANIFEST,
    )["redis_proof_sha256"] == evidence.sha256_file(root / evidence.REDIS_PROOF_NAME)
    seed_kwargs = {
        "cutover_id": CUTOVER_ID,
        "revision": REVISION,
        "source": SOURCE,
        "run_id": "first-cutover-seed-20260831T190700Z-123",
        "snapshot_id": snapshot_id,
        "repository_id_sha256": repository_hash,
        "backup_set_sha256": backup_set_hash,
        "database_dump_sha256": "d" * 64,
        "postgres_role_login_probe_sha256": POSTGRES_ROLE_LOGIN_PROBE,
        "postgres_security_manifest_sha256": POSTGRES_SECURITY,
        "redis_dump_sha256": "e" * 64,
        "source_worker_stop_evidence_sha256": SOURCE_WORKER_STOP,
        "target_redis_manifest_sha256": TARGET_REDIS_MANIFEST,
        "configuration_checksums_sha256": "f" * 64,
    }
    with pytest.raises(evidence.EvidenceError, match="target manifest does not match"):
        evidence.write_seed(
            root,
            **seed_kwargs,
            migrated_manifest_sha256="0" * 64,
        )
    evidence.write_seed(
        root,
        **seed_kwargs,
        migrated_manifest_sha256=MIGRATED_MANIFEST,
    )
    seed_fields = evidence.validate_seed(
        root,
        cutover_id=CUTOVER_ID,
        revision=REVISION,
        source=SOURCE,
        repository_id_sha256=repository_hash,
        source_worker_stop_evidence_sha256=SOURCE_WORKER_STOP,
        target_redis_manifest_sha256=TARGET_REDIS_MANIFEST,
    )
    assert seed_fields["snapshot_id"] == snapshot_id
    assert seed_fields["migrated_target_manifest_sha256"] == MIGRATED_MANIFEST

    recovery = recovery_root / "restic-restore-20260831T180000Z-123.ok"
    retention = recovery_root / "r2-retention-lock.ok"
    recovery_fields = {
        "backup_set_sha256": backup_set_hash,
        "drill_scope": "current-latest",
        "live_services_touched": "false",
        "postgres_restore": "verified",
        "repository_id_sha256": repository_hash,
        "redis_restore": "verified",
        "restored_payload_removed": "true",
        "snapshot_id": snapshot_id,
        "status": "success",
    }
    _write_external(recovery, {**recovery_fields, "snapshot_id": "0" * 64})
    _write_external(
        retention,
        {
            "repository_id_sha256": repository_hash,
            "status": "immutable-retention-verified",
        },
    )
    with pytest.raises(evidence.EvidenceError, match="exactly match"):
        evidence.finalize(
            root,
            cutover_id=CUTOVER_ID,
            revision=REVISION,
            source=SOURCE,
            recovery_marker=Path(recovery.name),
            recovery_sha256=evidence.sha256_file(recovery),
            retention_marker=Path(retention.name),
            retention_sha256=evidence.sha256_file(retention),
            repository_id_sha256=repository_hash,
            migrated_target_manifest_sha256=MIGRATED_MANIFEST,
            postgres_role_login_probe_sha256=POSTGRES_ROLE_LOGIN_PROBE,
            postgres_security_manifest_sha256=POSTGRES_SECURITY,
            source_worker_stop_evidence_sha256=SOURCE_WORKER_STOP,
            target_redis_manifest_sha256=TARGET_REDIS_MANIFEST,
        )
    assert (root / evidence.IN_PROGRESS_NAME).is_file()
    assert not (root / evidence.FINAL_PROOF_NAME).exists()

    _write_external(recovery, recovery_fields)
    evidence.finalize(
        root,
        cutover_id=CUTOVER_ID,
        revision=REVISION,
        source=SOURCE,
        recovery_marker=Path(recovery.name),
        recovery_sha256=evidence.sha256_file(recovery),
        retention_marker=Path(retention.name),
        retention_sha256=evidence.sha256_file(retention),
        repository_id_sha256=repository_hash,
        migrated_target_manifest_sha256=MIGRATED_MANIFEST,
        postgres_role_login_probe_sha256=POSTGRES_ROLE_LOGIN_PROBE,
        postgres_security_manifest_sha256=POSTGRES_SECURITY,
        source_worker_stop_evidence_sha256=SOURCE_WORKER_STOP,
        target_redis_manifest_sha256=TARGET_REDIS_MANIFEST,
    )

    final = root / evidence.FINAL_PROOF_NAME
    assert final.is_file()
    if os.name == "posix":
        assert stat.S_IMODE(final.stat().st_mode) == 0o600
    assert not (root / evidence.IN_PROGRESS_NAME).exists()
    assert evidence.validate_final(root, expected_revision=REVISION)["cutover_id"] == CUTOVER_ID
    with pytest.raises(evidence.EvidenceError, match="exact release"):
        evidence.validate_final(root, expected_revision="b" * 40)

    assert evidence.first_start_status(root, expected_revision=REVISION) == "required"
    with pytest.raises(evidence.EvidenceError, match="does not match"):
        evidence.arm_first_start(
            root,
            expected_revision=REVISION,
            migrated_target_manifest_sha256="0" * 64,
            postgres_role_login_probe_sha256=POSTGRES_ROLE_LOGIN_PROBE,
            postgres_security_manifest_sha256=POSTGRES_SECURITY,
            target_redis_manifest_sha256=TARGET_REDIS_MANIFEST,
        )
    assert not (root / evidence.FIRST_START_IN_PROGRESS_NAME).exists()
    assert (
        evidence.arm_first_start(
            root,
            expected_revision=REVISION,
            migrated_target_manifest_sha256=MIGRATED_MANIFEST,
            postgres_role_login_probe_sha256=POSTGRES_ROLE_LOGIN_PROBE,
            postgres_security_manifest_sha256=POSTGRES_SECURITY,
            target_redis_manifest_sha256=TARGET_REDIS_MANIFEST,
        )
        == "armed"
    )
    with pytest.raises(evidence.EvidenceError, match="manual review"):
        evidence.first_start_status(root, expected_revision=REVISION)
    assert evidence.complete_first_start(root, expected_revision=REVISION) == "consumed"
    assert not (root / evidence.FIRST_START_IN_PROGRESS_NAME).exists()
    assert evidence.first_start_status(root, expected_revision=REVISION) == "consumed"
    first_start_path = root / evidence.FIRST_START_PROOF_NAME
    first_start_fields = evidence._load(first_start_path)
    assert first_start_fields["final_proof_sha256"] == evidence.sha256_file(final)
    assert first_start_fields["release_revision"] == REVISION

    evidence._atomic_write(
        first_start_path,
        {**first_start_fields, "final_proof_sha256": "0" * 64},
    )
    with pytest.raises(evidence.EvidenceError, match="does not match"):
        evidence.first_start_status(root, expected_revision=REVISION)
    evidence._atomic_write(first_start_path, first_start_fields)

    evidence._atomic_write(
        root / evidence.FIRST_START_IN_PROGRESS_NAME,
        {
            "armed_at_utc": "2026-08-31T19:30:00Z",
            "cutover_id": CUTOVER_ID,
            "final_proof_sha256": evidence.sha256_file(final),
            "migrated_target_manifest_sha256": MIGRATED_MANIFEST,
            "postgres_role_login_probe_sha256": POSTGRES_ROLE_LOGIN_PROBE,
            "postgres_security_manifest_sha256": POSTGRES_SECURITY,
            "release_revision": REVISION,
            "source_fingerprint_sha256": SOURCE,
            "status": "provider-first-start-armed",
            "target_redis_manifest_sha256": TARGET_REDIS_MANIFEST,
            "version": evidence.VERSION,
        },
    )
    with pytest.raises(evidence.EvidenceError, match="manual review"):
        evidence.first_start_status(root, expected_revision=REVISION)
    with pytest.raises(evidence.EvidenceError, match="ambiguous"):
        evidence.complete_first_start(root, expected_revision=REVISION)
    evidence._unlink(root / evidence.FIRST_START_IN_PROGRESS_NAME)

    # Old final evidence is rejected even if it otherwise names the current
    # release and still hashes all current step proofs.
    final_fields = evidence._load(final)
    evidence._atomic_write(final, {**final_fields, "version": "2"})
    with pytest.raises(evidence.EvidenceError, match="exact release"):
        evidence.validate_final(root, expected_revision=REVISION)
    evidence._atomic_write(final, final_fields)

    # Updating the final proof hash cannot disguise a v3 PostgreSQL proof that
    # has lost a required target-state digest.
    postgres_fields = evidence._load(postgres_path)
    postgres_without_migrated_digest = dict(postgres_fields)
    postgres_without_migrated_digest.pop("postgres_security_manifest_sha256")
    evidence._atomic_write(postgres_path, postgres_without_migrated_digest)
    evidence._atomic_write(
        final,
        {
            **final_fields,
            "postgres_proof_sha256": evidence.sha256_file(postgres_path),
        },
    )
    with pytest.raises(evidence.EvidenceError, match="invalid digest evidence"):
        evidence.validate_final(root, expected_revision=REVISION)
    evidence._atomic_write(postgres_path, postgres_fields)
    evidence._atomic_write(final, final_fields)

    seed_path = root / evidence.SEED_PROOF_NAME
    seed_fields = evidence._load(seed_path)
    seed_without_migrated_digest = dict(seed_fields)
    seed_without_migrated_digest.pop("target_redis_manifest_sha256")
    evidence._atomic_write(seed_path, seed_without_migrated_digest)
    evidence._atomic_write(
        final,
        {
            **final_fields,
            "seed_proof_sha256": evidence.sha256_file(seed_path),
        },
    )
    with pytest.raises(evidence.EvidenceError, match="invalid digest evidence"):
        evidence.validate_final(root, expected_revision=REVISION)
    evidence._atomic_write(seed_path, seed_fields)
    evidence._atomic_write(final, final_fields)

    with (root / evidence.REDIS_PROOF_NAME).open("a", encoding="utf-8") as stream:
        stream.write("tampered=true\n")
    with pytest.raises(evidence.EvidenceError, match="changed after finalization"):
        evidence.validate_final(root, expected_revision=REVISION)


def test_postgres_and_redis_steps_leave_global_fence_until_finalizer():
    postgres = _read("deploy/migrate_postgres.sh")
    redis = _read("deploy/migrate_redis_state.sh")

    assert "provider_cutover_evidence.py" in postgres
    assert "begin-postgres" in postgres and "write-postgres" in postgres
    assert postgres.index("begin-postgres") < postgres.index("postgres-cutover-in-progress")
    assert postgres.index('migration_verified="true"') < postgres.index("write-postgres")
    assert postgres.index('target_mutated="false"', postgres.index("CUTOVER_VERIFIED")) < postgres.index(
        "write-postgres"
    )
    assert "lecturesift-api-rehearsal lecturesift-worker-rehearsal" in postgres
    assert "LECTURESIFT_PROVIDER_CUTOVER_ID" in postgres
    assert "LECTURESIFT_EXPECTED_BUILD_REVISION" in postgres
    assert "up -d caddy" not in postgres and "stop caddy" not in postgres

    assert "provider_cutover_evidence.py" in redis
    assert "begin-redis" in redis and "write-redis" in redis
    assert "POSTGRES_FAIL_STOP_MARKER" in redis
    assert redis.index("begin-redis") < redis.index('check_source_frozen()')
    assert redis.index('migration_committed="true"') < redis.index("write-redis")
    assert redis.index("write-redis") < redis.rindex('rm -f -- "$FAIL_STOP_MARKER"')
    assert "up -d caddy" not in redis and "stop caddy" not in redis


def test_finalizer_rechecks_volatile_state_and_never_switches_traffic():
    finalizer = _read("deploy/finalize_provider_cutover.sh")

    assert "LECTURESIFT_PROVIDER_CUTOVER_FINALIZE_CONFIRM" in finalizer
    assert finalizer.count("assert_source_frozen_and_idle") >= 3  # definition + two checks
    assert finalizer.count("source_pending_count") >= 3
    assert finalizer.count("target_pending_count") >= 3
    assert finalizer.count("assert_target_queue_idle") >= 3
    assert "lecturesift-api-rehearsal lecturesift-worker-rehearsal" in finalizer
    assert "r2-retention-lock.ok" in finalizer
    assert "drill_scope=current-latest" in finalizer
    assert "first-cutover-seed.ok" in finalizer
    assert "validate-seed" in finalizer
    assert 'snapshot_id=$SEED_SNAPSHOT_ID' in finalizer
    assert 'backup_set_sha256=$SEED_BACKUP_SET_SHA256' in finalizer
    assert "release.sh" in finalizer and 'bash "$RELEASE_TOOL" prepare' in finalizer
    assert "provider_cutover_evidence.py" in finalizer and " finalize" in finalizer
    assert "Redis/R2 rollback was not asserted or performed" in finalizer
    assert "up -d caddy" not in finalizer
    assert "stop caddy" not in finalizer
    assert "api.lecturesift.com" not in finalizer


def test_seed_snapshot_start_time_remains_valid_after_a_long_r2_upload():
    run_started_epoch = 2_000_000_000
    snapshot_id = "a" * 64
    snapshot_started = dt.datetime.fromtimestamp(
        run_started_epoch + 2, tz=dt.timezone.utc
    )
    upload_completed = dt.datetime.fromtimestamp(
        run_started_epoch + 6 * 60 * 60, tz=dt.timezone.utc
    )
    document = [
        {
            "hostname": "lecturesift-production",
            "id": snapshot_id,
            "tags": ["lecturesift", "production", "first-cutover-seed"],
            "time": snapshot_started.isoformat().replace("+00:00", "Z"),
        }
    ]

    assert evidence.validate_seed_snapshot_document(
        document,
        expected_snapshot_id=snapshot_id,
        run_started_epoch=run_started_epoch,
        now=upload_completed,
    )["id"] == snapshot_id

    document[0]["time"] = dt.datetime.fromtimestamp(
        run_started_epoch - 6, tz=dt.timezone.utc
    ).isoformat()
    with pytest.raises(evidence.EvidenceError, match="did not begin during"):
        evidence.validate_seed_snapshot_document(
            document,
            expected_snapshot_id=snapshot_id,
            run_started_epoch=run_started_epoch,
            now=upload_completed,
        )


def test_first_cutover_seed_is_exact_format_fenced_and_never_changes_runtime():
    seed = _read("deploy/seed_first_cutover_backup.sh")

    for confirmation in (
        "LECTURESIFT_FIRST_CUTOVER_SEED_CONFIRM",
        "LECTURESIFT_SOURCE_FROZEN",
        "LECTURESIFT_SOURCE_WORKER_STOPPED",
        "LECTURESIFT_PROVIDER_RECONCILED",
    ):
        assert confirmation in seed
    assert "validate-seed-ready" in seed and "write-seed" in seed
    assert (
        'POSTGRES_CUTOVER_PROOF="/var/lib/lecturesift/provider-cutover/postgres-cutover.ok"'
        in seed
    )
    assert "migrated_target_manifest_sha256=" in seed
    assert 'TARGET_BEFORE_MANIFEST_SHA256="$(' in seed
    assert 'sha256sum "$RUN_DIR/target-cutover-snapshot.safe"' in seed
    assert '--migrated-manifest-sha256 "$TARGET_BEFORE_MANIFEST_SHA256"' in seed
    assert 'CUTOVER_MANIFEST="$ROOT_DIR/deploy/rehearsal_manifest.sql"' in seed
    assert "SCHEMA_OBJECT" in seed
    assert 'cat "$CUTOVER_MANIFEST"' in seed
    assert 'SET TRANSACTION SNAPSHOT' in seed
    assert "validate-seed-snapshot" in seed and "RUN_STARTED_EPOCH" in seed
    assert "age <= 900" not in seed
    assert seed.index("validate-seed-ready") < seed.index("restic backup")
    assert seed.index("validate-seed-ready") < seed.index("target-cutover-snapshot.safe")
    exported_snapshot = seed.index('SNAPSHOT_ID="${snapshot_info#*|}"')
    strict_snapshot_manifest = seed.index('cat "$CUTOVER_MANIFEST"')
    manifest_comparison = seed.index(
        "target PostgreSQL changed after the verified schema migration"
    )
    postgres_dump = seed.index('${compose[@]}" exec -T postgres pg_dump')
    assert (
        exported_snapshot
        < strict_snapshot_manifest
        < seed.index("target-cutover-snapshot.safe")
        < manifest_comparison
        < postgres_dump
    )
    assert seed.index("restic backup") < seed.index("write-seed")
    assert "provider-cutover.in-progress" not in seed  # helper owns the state machine
    assert "lecturesift-production-backups/restic" in seed
    assert "eu[.]r2[.]cloudflarestorage[.]com" in seed
    assert 'RESTIC_HOST="lecturesift-production"' in seed
    assert "--tag lecturesift --tag production --tag first-cutover-seed" in seed

    # PostgreSQL dump and metadata are bound to one exported MVCC snapshot.
    assert "pg_export_snapshot()" in seed
    assert '--snapshot "$SNAPSHOT_ID"' in seed
    assert "format=lecturesift-backup-v2" in seed
    assert "application_identity=lecturesift-production" in seed
    assert "postgres.dump redis-dump.rdb BACKUP_METADATA >SHA256SUMS" in seed
    assert "configuration-snapshot-v1" in seed
    assert '"$CONFIGURATION_SNAPSHOT_TOOL" create' in seed
    assert '"$CONFIGURATION_SNAPSHOT_TOOL" verify' in seed
    assert '.release.lock' in seed and seed.count("assert_release_checkout_unchanged") >= 4

    # Redis is forced durable and the copied RDB is validated before upload.
    assert "WAITAOF 1 0 5000" in seed
    assert "BGSAVE" in seed and "redis-check-rdb" in seed
    assert "rdb_changes_since_last_save" in seed
    assert seed.count("assert_seed_state_unchanged") == 3  # definition + before/after upload
    assert seed.count("assert_source_frozen_and_idle") >= 4  # definition + capture gates

    # The seed is a data-only cutover bridge. It cannot make runtime or traffic
    # transitions and does not perform retention deletion/compaction.
    assert "up -d" not in seed
    assert "compose stop" not in seed and "compose start" not in seed
    assert "up -d caddy" not in seed and "stop caddy" not in seed
    assert "api.lecturesift.com" not in seed
    assert "restic forget" not in seed and "restic prune" not in seed
    assert seed.index('rm -rf --one-file-system -- "$payload_real"') < seed.index(
        "write-seed"
    )


def test_normal_preflight_requires_exact_final_gate_and_nonproduction_is_explicit():
    preflight = _read("deploy/preflight.sh")
    restore = _read("deploy/restore.sh")
    service = _read("deploy/lecturesift.service")

    assert 'PREFLIGHT_CONTEXT="${LECTURESIFT_PREFLIGHT_CONTEXT:-production}"' in preflight
    assert "provider-cutover.in-progress" in preflight
    assert "provider-cutover.ok" in preflight
    assert "validate-final" in preflight
    assert "--expected-revision" in preflight
    assert "bootstrap-infrastructure requires both explicit one-time confirmations" in preflight
    assert "bootstrap-infrastructure cannot run from a persistent systemd service" in preflight
    assert "disaster restore validation requires one exact selected backup set" in preflight
    assert "LECTURESIFT_PREFLIGHT_CONTEXT=disaster-restore-validation" in restore
    assert "LECTURESIFT_PREFLIGHT_CONTEXT" not in service
    assert "LECTURESIFT_RECOVERY_BOOTSTRAP_OVERRIDE" not in service


def test_r2_probe_records_repository_bound_root_only_evidence():
    probe = _read("deploy/r2_retention_probe.py")
    service = _read("deploy/lecturesift-r2-retention-probe.service")

    assert 'EVIDENCE_NAME = "r2-retention-lock.ok"' in probe
    assert '[restic, "cat", "config"]' in probe
    assert '"repository_id_sha256"' in probe
    assert '"repository_target_sha256"' in probe
    assert 'os.fchmod(descriptor, 0o600)' in probe
    assert "RESTIC_AWS_SECRET_ACCESS_KEY" in probe
    assert "StateDirectory=lecturesift/recovery-drills" in service
    assert "StateDirectoryMode=0700" in service
    assert "ReadWritePaths=/var/lib/lecturesift/recovery-drills" in service
