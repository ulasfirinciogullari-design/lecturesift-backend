from __future__ import annotations

import datetime as dt
import importlib.util
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

CUTOVER_ID = "1" * 32
REVISION = "2" * 40
SOURCE = "3" * 64


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


def test_source_fingerprint_omits_secrets_but_binds_every_source_endpoint():
    environment = {
        "SOURCE_DATABASE_URL": (
            "postgresql://owner:db-secret@db.example.render.com:5432/lecturesift"
            "?sslmode=require"
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
    root = tmp_path / "provider-cutover"
    recovery_root = tmp_path / "recovery-drills"
    recovery_root.mkdir(mode=0o700)
    monkeypatch.setattr(evidence, "RECOVERY_EVIDENCE_ROOT", recovery_root)

    evidence.begin_postgres(root, cutover_id=CUTOVER_ID, revision=REVISION, source=SOURCE)
    assert (root / evidence.IN_PROGRESS_NAME).is_file()
    assert not (root / evidence.FINAL_PROOF_NAME).exists()

    evidence.write_postgres(
        root,
        cutover_id=CUTOVER_ID,
        revision=REVISION,
        source=SOURCE,
        run_id="postgres-cutover-20260831T190203Z",
        manifest_sha256="4" * 64,
        source_dump_sha256="5" * 64,
        rollback_dump_sha256="6" * 64,
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
        state_sha256="8" * 64,
        rollback_sha256="9" * 64,
    )

    repository_hash = "a" * 64
    snapshot_id = "b" * 64
    backup_set_hash = "c" * 64
    assert evidence.validate_seed_ready(
        root,
        cutover_id=CUTOVER_ID,
        revision=REVISION,
        source=SOURCE,
    )["redis_proof_sha256"] == evidence.sha256_file(root / evidence.REDIS_PROOF_NAME)
    evidence.write_seed(
        root,
        cutover_id=CUTOVER_ID,
        revision=REVISION,
        source=SOURCE,
        run_id="first-cutover-seed-20260831T190700Z-123",
        snapshot_id=snapshot_id,
        repository_id_sha256=repository_hash,
        backup_set_sha256=backup_set_hash,
        database_dump_sha256="d" * 64,
        redis_dump_sha256="e" * 64,
        configuration_checksums_sha256="f" * 64,
    )
    assert evidence.validate_seed(
        root,
        cutover_id=CUTOVER_ID,
        revision=REVISION,
        source=SOURCE,
        repository_id_sha256=repository_hash,
    )["snapshot_id"] == snapshot_id

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
    )

    final = root / evidence.FINAL_PROOF_NAME
    assert final.is_file()
    if os.name == "posix":
        assert stat.S_IMODE(final.stat().st_mode) == 0o600
    assert not (root / evidence.IN_PROGRESS_NAME).exists()
    assert evidence.validate_final(root, expected_revision=REVISION)["cutover_id"] == CUTOVER_ID
    with pytest.raises(evidence.EvidenceError, match="exact release"):
        evidence.validate_final(root, expected_revision="b" * 40)

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
    assert "validate-seed-snapshot" in seed and "RUN_STARTED_EPOCH" in seed
    assert "age <= 900" not in seed
    assert seed.index("validate-seed-ready") < seed.index("restic backup")
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
