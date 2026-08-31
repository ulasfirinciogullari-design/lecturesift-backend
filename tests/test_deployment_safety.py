from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_rehearsal_has_dedicated_queue_bucket_and_consumer_guards():
    script = _read("deploy/rehearsal_stack.sh")

    assert 'rehearsal_redis_container="lecturesift-redis-rehearsal"' in script
    assert 'CELERY_BROKER_URL="redis://$rehearsal_redis_container:6379/0"' in script
    assert 'REDIS_URL="$CELERY_BROKER_URL"' in script
    assert ': "${LECTURESIFT_WORKER_DATABASE_URL:?Missing LECTURESIFT_WORKER_DATABASE_URL}"' in script
    assert 'api_database_url="${DATABASE_URL%/*}/$rehearsal_db"' in script
    assert 'worker_database_url="${LECTURESIFT_WORKER_DATABASE_URL%/*}/$rehearsal_db"' in script
    assert 'DATABASE_URL="$worker_database_url"' in script
    assert 'DATABASE_URL="$api_database_url"' in script
    assert "-e DATABASE_URL -e CELERY_BROKER_URL -e REDIS_URL -e S3_BUCKET" in script
    assert "LECTURESIFT_REHEARSAL_S3_BUCKET" in script
    assert "LECTURESIFT_REHEARSAL_S3_ACCESS_KEY_ID" in script
    assert "LECTURESIFT_REHEARSAL_S3_SECRET_ACCESS_KEY" in script
    assert '[[ "$LECTURESIFT_REHEARSAL_S3_BUCKET" == "$S3_BUCKET" ]]' in script
    assert 'S3_ACCESS_KEY_ID="$LECTURESIFT_REHEARSAL_S3_ACCESS_KEY_ID"' in script
    assert 'S3_SECRET_ACCESS_KEY="$LECTURESIFT_REHEARSAL_S3_SECRET_ACCESS_KEY"' in script
    assert "-e S3_ACCESS_KEY_ID -e S3_SECRET_ACCESS_KEY" in script
    assert "lecturesift-api-1 lecturesift-worker-1" in script
    assert "redis-server --save '' --appendonly no" in script
    assert (
        'REHEARSAL_ENV_FILE="${LECTURESIFT_REHEARSAL_ENV_FILE:-/etc/lecturesift/rehearsal.env}"'
        in script
    )
    rehearsal_check = 'check_private_env "$REHEARSAL_ENV_FILE" "Rehearsal environment"'
    rehearsal_source = 'source "$REHEARSAL_ENV_FILE"'
    assert rehearsal_check in script
    assert rehearsal_source in script
    assert script.index(rehearsal_check) < script.index(rehearsal_source)
    assert "must be a regular non-symlink file" in script
    assert "must be owned by root" in script
    assert "must have mode 0400 or 0600" in script


def test_database_rehearsal_root_and_source_consistency_fail_closed():
    script = _read("deploy/rehearsal_restore.sh")

    assert 'ALLOWED_REHEARSAL_ROOT="/var/backups/lecturesift/rehearsal"' in script
    assert 'realpath -m -- "$REQUESTED_REHEARSAL_ROOT"' in script
    assert 'BACKUP_ROOT="$(realpath -e -- "$requested_root_normalized")"' in script
    assert "Source data changed during the rehearsal" in script
    assert "source_changed_during_rehearsal=false" in script
    assert 'source_changed="true"' not in script
    assert "drop_rehearsal_database()" in script
    assert "cleanup_rehearsal_containers()" in script
    assert "trap cleanup_rehearsal EXIT" in script
    assert "--if-exists --force" in script
    assert '"$ROOT_DIR/deploy/rehearsal_stack.sh" "$rehearsal_db"' in script
    assert "lecturesift-rehearsal-e2e.py" in script
    assert "lecturesift-rehearsal-formats-e2e.py" in script
    assert "database_dropped=true" in script
    assert 'rm -f -- "$run_dir/render.dump"' in script
    assert "-mtime +30" in script
    assert "LECTURESIFT_PROVISION_DATABASE" in script
    assert "safe_pattern='^(DATABASE|SCHEMA|TABLE|ANOMALY|STATUS|UNVALIDATED_FK)" in script
    assert "Source and restored PostgreSQL version/encoding/collation identities differ" in script
    assert "Rehearsal data-integrity anomalies must be resolved before cutover" in script
    assert '$1 == "ANOMALY" && $3 != "0"' in script
    assert script.index('rehearsal_db_created="true"') < script.index(
        'exec -T postgres createdb'
    )
    assert "cleanup_rehearsal_work_volumes()" in script
    assert "lecturesift-api-rehearsal-work" in script
    assert "lecturesift-worker-rehearsal-work" in script
    assert "LECTURESIFT_REHEARSAL_ORCHESTRATED=YES" in script
    assert "reconcile_stale_rehearsal_state()" in script
    assert "^lecturesift_rehearsal_[0-9]{14}$" in script
    assert "created_epoch <= now - 3600" in script
    assert "pg_stat_activity WHERE datname" in script
    assert "Stale rehearsal databases could not be enumerated safely" in script
    assert "-mmin +60 -print" in script
    assert script.index('"$ROOT_DIR/deploy/rehearsal_stack.sh" "$rehearsal_db"') < script.rindex(
        "drop_rehearsal_database"
    )


def test_production_preflight_requires_recent_real_recovery_evidence():
    script = _read("deploy/preflight.sh")

    assert "BILLING_SESSION_SECRET PAYMENT_TOKEN_BINDING_SECRET ADMIN_ADMIN" in script
    assert "PAYMENT_TOKEN_BINDING_SECRET must be at least 32 characters." in script
    assert "PAYMENT_TOKEN_BINDING_LEGACY_SECRET must be empty or at least 32 characters." in script
    assert 'RECOVERY_EVIDENCE_ROOT="/var/lib/lecturesift/recovery-drills"' in script
    assert "RECOVERY_DRILL_MAX_AGE_DAYS=90" in script
    assert "RECOVERY_SNAPSHOT_MAX_AGE_SECONDS=172800" in script
    assert "latest off-site production snapshot is missing or older than 48 hours" in script
    assert "--latest 1 --host lecturesift-production" in script
    assert "snapshot_time_epoch >= completed_time_epoch - RECOVERY_SNAPSHOT_MAX_AGE_SECONDS" in script
    assert 'drill_scope="$(sed -n' in script
    assert 'drill_scope" != "current-latest"' in script
    assert '"$drill_scope" == "explicit-backup"' in script
    assert "snapshot_oldest_allowed" not in script
    assert '[[ -f "$marker" && ! -L "$marker" ]]' in script
    assert "stat -c '%u' -- \"$marker\"" in script
    assert "marker_mtime >= oldest_allowed" in script
    assert "status=success" in script
    assert "postgres_restore=verified" in script
    assert "redis_restore=verified" in script
    assert "restic cat config" in script
    assert "off-site S3-compatible restic repository" in script
    assert "s3:*/lecturesift-production-backups/restic" in script
    assert "RESTIC_AWS_ACCESS_KEY_ID is required" in script
    assert "RESTIC_AWS_SECRET_ACCESS_KEY is required" in script
    assert "CURRENT_REPOSITORY_ID_SHA256" in script
    assert "repository_id_sha256=$CURRENT_REPOSITORY_ID_SHA256" in script
    assert "host-lecturesift-production-tags-lecturesift-production" in script
    assert "backup_set_sha256=" in script
    assert "LECTURESIFT_REQUIRED_BACKUP_SET_SHA256" in script
    assert "validate_escrow_marker" in script
    assert "restic-password-escrow.ok" in script
    assert "encrypted-off-host" in script
    assert "CURRENT_RESTIC_KEY_ID" in script
    assert "restic key list --json" in script
    assert "restic list keys --quiet" in script
    assert "restic_key_id=$CURRENT_RESTIC_KEY_ID" in script
    assert 'REDIS_FAIL_STOP_MARKER="/var/lib/lecturesift/migration-fail-stop/redis-state-unproven"' in script
    assert "manual recovery and fail-stop marker clearance are required" in script
    for confirmation in (
        "LECTURESIFT_DATABASE_RECOVERY_CONFIRMED",
        "LECTURESIFT_OBJECT_RETENTION_CONFIRMED",
        "LECTURESIFT_RECOVERY_DRILL_CONFIRMED",
    ):
        assert confirmation in script
    assert "LECTURESIFT_RECOVERY_BOOTSTRAP_OVERRIDE" in script
    assert "must not be stored in the runtime environment file" in script
    assert "off-site restic backup is not configured" not in script


def test_backup_is_quiescent_async_versioned_and_retention_scoped():
    script = _read("deploy/backup.sh")
    runtime_recovery = _read("deploy/recover_backup_runtime.sh")

    assert 'RESTIC_STAGE="$RESTIC_STAGE_ROOT/current"' in script
    assert 'restic backup "$RESTIC_STAGE" --host "$RESTIC_HOST"' in script
    assert '--tag lecturesift --tag production' in script
    assert 'restic forget --host "$RESTIC_HOST" --tag lecturesift' in script
    assert "RESTIC_OBJECT_LOCK_DAYS=90" in script
    assert "RESTIC_FORGET_SAFETY_DAYS=2" in script
    assert 'keep-within "${RESTIC_KEEP_WITHIN_DAYS}d"' in script
    assert "--keep-daily 365 --keep-weekly 104 --keep-monthly 60 --keep-yearly 10" in script
    assert "restic forget --prune" not in script
    assert "restic prune" not in script
    assert "--group-by host,tags" in script
    assert "redis-cli SAVE" not in script
    assert "redis-cli --raw BGSAVE" in script
    assert "LASTSAVE" in script
    assert "rdb_bgsave_in_progress" in script
    assert "rdb_last_bgsave_status" in script
    assert '"mode": "drain"' in script
    assert "The live API did not acknowledge the backup drain fence." in script
    assert "docker compose stop --timeout 600 worker" in script
    assert "trap restore_runtime EXIT" in script
    assert "--force-recreate" not in script
    assert '"$BACKUP_RUNTIME_RECOVERY"' in script
    assert 'docker start "$worker_container"' in runtime_recovery
    assert "existing API/worker runtime did not become healthy" in runtime_recovery
    assert "docker compose stop" in script
    assert "docker compose stop caddy" not in script
    assert script.count("assert_quiescent") >= 3  # definition + before/after capture
    assert script.index("assert_quiescent\n\n# Export one MVCC snapshot") < script.index(
        "pg_export_snapshot()"
    ) < script.index("docker compose exec -T postgres pg_dump") < script.index(
        "redis-cli --raw BGSAVE"
    )
    assert script.rindex("assert_quiescent") > script.index(
        'docker compose cp redis:/data/dump.rdb'
    )
    assert "BACKUP_METADATA" in script
    assert "format=lecturesift-backup-v2" in script
    assert "application_identity=lecturesift-production" in script
    assert "application_schema_compatibility=lecturesift-schema-v1" in script
    assert "schema_manifest_sha256=" in script
    assert "schema_fingerprint_sha256=" in script
    assert "database_identity_sha256=" in script
    assert "data_fingerprint_sha256=" in script
    assert "grep '^TABLE|'" in script
    assert "LC_ALL=C sort | sha256sum" in script
    assert "database_size_bytes=" in script
    assert "pg_export_snapshot()" in script
    assert '--snapshot "$snapshot_id"' in script
    assert "SET TRANSACTION SNAPSHOT" in script
    assert script.index("pg_export_snapshot()") < script.index(
        '--snapshot "$snapshot_id"'
    ) < script.index("SET TRANSACTION SNAPSHOT")
    assert "close_backup_snapshot" in script
    assert "backup_required_bytes" in script
    assert "backup_available_bytes" in script
    assert "preserving 5 GiB of host reserve" in script
    assert "recovery_manifest_v${RECOVERY_MANIFEST_VERSION}.sql" in script
    assert "rehearsal_manifest.sql" not in script
    assert "TABLE_DIFF|UNVALIDATED_FK" in script
    assert "redis_restore_compatibility=redis-7.4-only" in script
    assert "Off-site restic configuration is mandatory" in script
    assert 'ALLOWED_BACKUP_ROOT="/var/backups/lecturesift"' in script
    assert 'realpath -m -- "$REQUESTED_BACKUP_ROOT"' in script
    assert 'realpath -e -- "$ALLOWED_BACKUP_ROOT"' in script
    assert "Refusing non-canonical backup root" in script
    assert "-name '.incomplete-????????T??????Z' -mmin +60" in script
    assert "-name '.next-????????T??????Z' -mmin +60" in script
    assert "off-site S3-compatible restic repository" in script
    assert "Production S3 restic credentials are required" in script
    assert 'check_private_env "$ENV_FILE" "Runtime environment"' in script
    assert 'check_private_env "$DB_ENV_FILE" "Database environment"' in script
    assert 'python3 "$ROLE_ENV_GENERATOR" --check' in script
    assert "local-only" not in script
    assert "/var/lib/lecturesift/backup-alerts/latest-failure" in script


def test_backup_failure_records_evidence_and_sends_an_operations_alert():
    service = _read("deploy/lecturesift-backup.service")
    alert_service = _read("deploy/lecturesift-backup-alert@.service")
    alert_script = _read("deploy/backup_failure_alert.sh")
    preflight = _read("deploy/preflight.sh")

    assert "OnFailure=lecturesift-backup-alert@%n.service" in service
    assert "backup_failure_alert.sh %i" in alert_service
    assert 'ALERT_MARKER="$ALERT_ROOT/latest-failure"' in alert_script
    assert "status=backup-failed" in alert_script
    assert "https://api.resend.com/emails" in alert_script
    assert "smtplib.SMTP" in alert_script
    assert "LECTURESIFT_OPS_ALERT_EMAIL" in preflight


def test_restic_retention_matches_cloudflare_object_lock_contract():
    script = _read("deploy/backup.sh")
    preflight = _read("deploy/preflight.sh")
    docs = _read("VPS_DEPLOYMENT.md")
    policy = _read("BACKUP_POLICY.md")

    assert "RESTIC_OBJECT_LOCK_DAYS=90" in script
    assert "RESTIC_FORGET_SAFETY_DAYS=2" in script
    assert 'keep-within "${RESTIC_KEEP_WITHIN_DAYS}d"' in script
    assert "restic forget --prune" not in script
    assert "restic prune" not in script
    assert "lecturesift-production-backups/restic" in preflight
    assert "lecturesift-production-backups" in docs
    assert "restic/data/" in docs and "restic/snapshots/" in docs
    assert "restic/config" in docs and "restic/keys/" in docs
    assert "restic/locks/" in docs and "restic/index/" in docs
    assert "keeps **every** snapshot for at least 92 days" in docs
    assert "Automated prune is disabled" in policy


def test_logical_redis_migration_proves_freeze_and_final_state():
    script = _read("deploy/migrate_redis_state.sh")

    assert 'ALLOWED_BACKUP_ROOT="/var/backups/lecturesift"' in script
    assert '[[ "$REQUESTED_BACKUP_ROOT" == "$ALLOWED_BACKUP_ROOT" ]]' in script
    assert 'realpath -m -- "$ALLOWED_BACKUP_ROOT"' in script
    assert 'realpath -m -- "$REQUESTED_BACKUP_ROOT"' in script
    assert 'BACKUP_ROOT="$allowed_backup_root_normalized"' in script
    assert 'BACKUP_ROOT="$(realpath -e -- "$BACKUP_ROOT")"' in script
    assert '[[ "$BACKUP_ROOT" == "$ALLOWED_BACKUP_ROOT" && ! -L "$BACKUP_ROOT" ]]' in script
    assert script.count('RUN_DIR="$BACKUP_ROOT/redis-migration-$STAMP"') == 1
    assert script.index('BACKUP_ROOT="$allowed_backup_root_normalized"') < script.index(
        'install -d -o root -g root -m 0700 -- "$BACKUP_ROOT"'
    )
    assert script.index('BACKUP_ROOT="$(realpath -e -- "$BACKUP_ROOT")"') < script.index(
        'RUN_DIR="$BACKUP_ROOT/redis-migration-$STAMP"'
    )
    assert "LECTURESIFT_SOURCE_WORKER_STOPPED" in script
    assert 'SOURCE_REDIS_URL="${REDIS_URL:-}"' in script
    assert 'SOURCE_CELERY_BROKER_URL="${CELERY_BROKER_URL:-}"' in script
    assert 'payload.get("maintenance_mode") == "freeze"' in script
    assert 'Celery(broker=os.environ["SOURCE_CELERY_BROKER_URL"])' in script
    assert "app.control.ping(timeout=8)" in script
    assert "lecturesift-api-1 lecturesift-worker-1" in script
    assert 'MIGRATION_LOCK_KEY="lecturesift:jobs:v2:write-lock"' in script
    assert 'NX EX 3600' in script
    assert "assert_target_broker_empty()" in script
    assert 'redis.call("SCAN", cursor, "COUNT", 250)' in script
    assert 'priority_separator = string.char(6) .. string.char(22)' in script
    assert 'key_type == "list"' in script
    assert 'redis.call("LLEN", key)' in script
    assert 'checked_size(KEYS[2], "hash", "HLEN")' in script
    assert 'checked_size(KEYS[3], "zset", "ZCARD")' in script
    assert "3 celery unacked unacked_index" in script
    assert script.index('assert_target_broker_empty "before-import"') < script.index(
        'target_write_resp="$RUN_DIR/target-write.resp"'
    )
    assert 'command(b"SET", b"lecturesift:jobs:v2", source)' in script
    assert 'command(b"WAITAOF", b"1", b"0", b"0")' in script
    assert "redis-cli --pipe" in script
    assert script.index('assert_target_broker_empty "before-commit"') > script.index(
        'before != after or after != final'
    )
    assert "source-final.json" in script
    assert "target-final.json" in script
    assert script.index("/migration/source-after.json") < script.rindex(
        '>"$RUN_DIR/target-final.json"'
    )
    assert "before != after or after != final" in script
    assert "final != target_final" in script
    assert "release_target_lock_best_effort" in script
    assert "release_target_lock_strict" in script
    assert '[[ "$delete_reply" == "1" ]]' in script
    assert '[[ "$remaining" == "0" ]]' in script
    assert script.index("release_target_lock_strict\nmigration_committed=\"true\"") > script.index(
        'assert_target_broker_empty "before-commit"'
    )
    assert 'command(b"WAITAOF", b"1", b"0", b"0")' in script
    assert "timeout 35s docker compose exec -T redis redis-cli --pipe" in script
    assert "errors:[[:space:]]*0" in script
    assert "target-rollback.resp" in script
    assert "Failed to durably restore target Redis" in script
    assert "Redis migration cleanup could not prove a durable rollback" in script
    assert 'FAIL_STOP_MARKER="$FAIL_STOP_ROOT/redis-state-unproven"' in script
    assert "status=redis-state-unproven" in script
    assert "status=redis-migration-in-progress" in script
    assert "do-not-start-production-until-migration-exits-cleanly" in script
    assert "production preflight is blocked for manual recovery" in script
    assert "previous Redis migration left unproven target state" in script
    assert 'cmp --silent "$RUN_DIR/target-before.json" "$rollback_current"' in script
    assert script.count('command(b"WAITAOF", b"1", b"0", b"0")') >= 2
    forward_attempt = script.index('target_updated="true"')
    forward_pipe = script.index(
        "if ! timeout 35s docker compose exec -T redis redis-cli --pipe",
        forward_attempt,
    )
    assert forward_attempt < forward_pipe
    assert script.index('command(b"WAITAOF"') < script.index(
        'assert_target_broker_empty "before-commit"'
    )


def test_restore_drill_performs_disposable_postgres_restore_and_manifest():
    script = _read("deploy/restic_restore_rehearsal.sh")
    recovery_manifest = _read("deploy/recovery_manifest_v1.sql")

    assert "--network none" in script
    assert "initdb --no-sync" in script
    assert "pg_restore --host=/tmp --exit-on-error --single-transaction" in script
    assert "/probe/recovery_manifest.sql" in script
    assert 'grep -q "^SCHEMA|"' in script
    assert "postgres_restore=verified" in script
    assert "redis_restore=verified" in script
    assert "postgres-18-bookworm-isolated-restore-manifest" in script
    assert '--host "$RESTIC_HOST"' in script
    assert "--tag lecturesift --tag production" in script
    assert "SNAPSHOT_MAX_AGE_SECONDS=172800" in script
    assert "LECTURESIFT_RESTIC_REHEARSAL_SNAPSHOT_ID" in script
    assert 'drill_scope="explicit-backup"' in script
    assert 'drill_scope="current-latest"' in script
    assert "drill_scope=%s" in script
    assert 'restic restore "$SNAPSHOT_ID"' in script
    assert "reconcile_stale_restore_payloads()" in script
    assert 'exec 8>"$allowed_root_normalized/.rehearsal.lock"' in script
    assert 'flock -n 8 || fail "another restic restore rehearsal is already running"' in script
    assert "-name 'restic-restore-????????T??????Z-????????' -mmin +60" in script
    assert 'restic stats --mode restore-size --json "$SNAPSHOT_ID"' in script
    assert "restore_size_bytes" in script
    assert "restore_required_bytes=$((restore_size_bytes * 2 + RESTORE_HOST_RESERVE_BYTES))" in script
    assert "preserving 5 GiB of host reserve" in script
    assert script.index('restic stats --mode restore-size --json "$SNAPSHOT_ID"') < script.index(
        'restic restore "$SNAPSHOT_ID"'
    )
    assert "repository_id_sha256=" in script
    assert "off-site S3-compatible restic repository" in script
    assert "size=2g" not in script
    assert "metadata_database_size" in script
    assert "validation_tmpfs_bytes=$((metadata_database_size * 2 + 536870912))" in script
    assert "validation_host_required_bytes" in script
    assert "MemAvailable" in script
    assert '--memory-swap "${validation_container_memory_bytes}b"' in script
    assert "database_identity_sha256" in script
    assert "backup_set_sha256=" in script
    assert "lecturesift-backup-v2" in script
    assert "application_schema_compatibility" in script
    assert "lecturesift-schema-v1" in script
    assert "schema_fingerprint_sha256" in script
    assert "data_fingerprint_sha256" in script
    assert "restored_data_sha256" in script
    assert 'RECOVERY_MANIFEST="$ROOT_DIR/deploy/recovery_manifest_v1.sql"' in script
    assert "('billing_payment_provider_sessions')" in recovery_manifest


def test_recovery_escrow_evidence_is_non_secret_and_repository_bound():
    script = _read("deploy/record_restic_escrow.sh")

    assert "LECTURESIFT_RESTIC_ESCROW_CONFIRM" in script
    assert "LECTURESIFT_RESTIC_ESCROW_RECOVERY_TESTED" in script
    assert "LECTURESIFT_RESTIC_ESCROW_CIPHERTEXT_SHA256" in script
    assert "LECTURESIFT_RESTIC_ESCROW_KEY_ID" in script
    assert "restic cat config" in script
    assert "restic key list --json" in script
    assert "repository_id_sha256=" in script
    assert "restic_key_id=" in script
    assert "ciphertext_sha256=" in script
    assert "recovery_test=decrypt-and-repository-opened" in script
    assert "RESTIC_PASSWORD" not in script.split("marker_tmp=", 1)[1]


def test_recovery_escrow_repository_id_hash_accepts_a_valid_repository():
    script = _read("deploy/record_restic_escrow.sh")
    embedded_python = re.search(
        r"repository_id_sha256=.*?python3 -c '\n(?P<code>.*?)\n'\)\" \|\|",
        script,
        flags=re.DOTALL,
    )
    assert embedded_python is not None

    repository_id = "ab" * 32
    result = subprocess.run(
        [sys.executable, "-c", embedded_python.group("code")],
        input=json.dumps({"id": repository_id}),
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == hashlib.sha256(repository_id.encode("ascii")).hexdigest()


def test_staging_ingress_and_secret_ignores_match_production_contract():
    staging = _read("deploy/Caddyfile.staging")
    production = _read("Caddyfile")
    gitignore = _read(".gitignore")
    dockerignore = _read(".dockerignore")
    manifest = _read("deploy/rehearsal_manifest.sql")

    assert "max_size 1100MB" in production
    assert "max_size 1100MB" in staging
    assert "deploy/rehearsal.env" in gitignore
    assert "deploy/rehearsal.env" in dockerignore
    assert "('billing_payment_provider_sessions')" in manifest
    assert "invalid_payment_provider_token_digest" in manifest
    assert "payment_provider_session_mismatch" in manifest
    assert "server_version_num')::integer / 10000" in manifest
    assert "DATABASE_SIZE|" in manifest


def test_shared_image_smoke_is_build_time_and_health_is_role_specific():
    dockerfile = _read("Dockerfile")
    compose = _read("compose.yaml")

    assert "COPY deploy/image_smoke.py /usr/local/bin/lecturesift-image-smoke.py" in dockerfile
    assert "RUN python /usr/local/bin/lecturesift-image-smoke.py" in dockerfile
    assert "HEALTHCHECK" not in dockerfile
    assert "healthcheck:" in compose
    assert "celery_app.control.ping" in compose
    assert "disable: true" in compose
    assert "unless-stopped" not in compose
    assert compose.count('restart: "on-failure:5"') == 5
    service = _read("deploy/lecturesift.service")
    assert "BindsTo=docker.service" in service
    assert "PartOf=docker.service" in service


def test_disaster_rdb_restore_is_version_guarded_and_docs_split_paths():
    restore = _read("deploy/restore.sh")
    docs = _read("VPS_DEPLOYMENT.md")

    assert "LECTURESIFT_REDIS_RESTORE_SOURCE_VERSION" in restore
    assert "BACKUP_METADATA" in restore
    assert "redis-7.4-only" in restore
    assert "SHA256SUMS must contain exactly the three expected backup entries" in restore
    assert "sha256sum --check --strict" in restore
    assert "postgres:18-bookworm --list /backup/postgres.dump" in restore
    assert "redis:7.4-alpine /backup/redis-dump.rdb" in restore
    assert "size=2g" not in restore
    assert "restore-validation-XXXXXXXX" in restore
    assert "validation_tmpfs_bytes=$((metadata_database_size * 2 + 536870912))" in restore
    assert "validation_host_required_bytes" in restore
    assert "MemAvailable" in restore
    assert '--memory-swap "${validation_container_memory_bytes}b"' in restore
    assert "recovery_manifest_v1.sql" in restore
    assert "schema_fingerprint_sha256" in restore
    assert "data_fingerprint_sha256" in restore
    assert "restored_data_sha256" in restore
    assert "lecturesift-backup-v2" in restore
    assert "application_schema_compatibility" in restore
    assert "lecturesift-schema-v1" in restore
    assert "LECTURESIFT_REQUIRED_BACKUP_SET_SHA256" in restore
    assert 'BACKUP_LOCK_ROOT="/var/backups/lecturesift"' in restore
    assert 'exec 9>"$BACKUP_LOCK_ROOT/.backup.lock"' in restore
    assert "A backup or restore operation is already active" in restore
    assert '"$ROOT_DIR/deploy/provision_database_role.sh"' in restore
    assert restore.index("postgres:18-bookworm --list") < restore.index(
        "docker compose stop caddy api worker redis"
    )
    assert '"$ROOT_DIR/deploy/preflight.sh"' in restore
    assert "A destructive restore refuses the recovery bootstrap override" in restore
    assert restore.index('"$ROOT_DIR/deploy/preflight.sh"') < restore.index(
        "docker compose stop caddy api worker redis"
    )
    database_start = "docker compose up -d --wait --wait-timeout 600 postgres redis"
    application_start = (
        "docker compose up -d --no-deps --wait --wait-timeout 600 api worker"
    )
    caddy_start = "docker compose up -d --no-deps --wait --wait-timeout 180 caddy"
    assert database_start in restore
    assert application_start in restore
    assert caddy_start in restore
    assert restore.index(database_start) < restore.index(application_start)
    assert restore.index(application_start) < restore.index(caddy_start)
    assert "docker compose stop caddy api worker >/dev/null 2>&1 || true" in restore
    assert "Caddy, API and worker are confirmed stopped" in restore
    assert "could not be confirmed" in restore
    assert "Database services may remain running for recovery" in restore
    assert "Restore both into the VPS" not in docs
    assert "Do not" in docs and "restore.sh" in docs and "during provider migration" in docs
    assert "Post-cutover disaster recovery" in docs
    assert "/etc/lecturesift/rehearsal.env" in docs
    assert "starts Caddy last" in docs
    assert "logical Redis JSON migration" in docs


def test_postgres_runtime_uses_a_distinct_least_privilege_role():
    database_example = _read("deploy/database.env.example")
    runtime_example = _read("deploy/env.example")
    compose = _read("compose.yaml")
    preflight = _read("deploy/preflight.sh")
    provision = _read("deploy/postgres-app-role.sh")
    service = _read("deploy/lecturesift.service")

    assert "POSTGRES_USER=lecturesift_owner" in database_example
    assert "LECTURESIFT_APP_DB_USER=lecturesift_app" in database_example
    assert "LECTURESIFT_APP_DB_PASSWORD=" in database_example
    assert "LECTURESIFT_WORKER_DB_USER=lecturesift_worker" in database_example
    assert "LECTURESIFT_WORKER_DB_PASSWORD=" in database_example
    assert "postgresql+psycopg://lecturesift_app:" in runtime_example
    assert "LECTURESIFT_WORKER_DATABASE_URL=postgresql+psycopg://lecturesift_worker:" in runtime_example
    assert "LECTURESIFT_APP_DB_USER LECTURESIFT_APP_DB_PASSWORD" in preflight
    assert "LECTURESIFT_WORKER_DB_USER LECTURESIFT_WORKER_DB_PASSWORD" in preflight
    assert "len({owner_user, api_user, worker_user}) == 3" in preflight
    assert "NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS" in provision
    assert "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES" in provision
    assert "GRANT USAGE, CREATE ON SCHEMA public" not in provision
    assert "lecturesift_worker.billing_usage_events" in provision
    assert "lecturesift_worker.lecturesift_runtime_metrics" in provision
    assert "public.billing_auth_tokens" in provision
    assert "public.lecturesift_admin_account_events" in provision
    assert "public.lecturesift_contact_messages" in provision
    assert "NOT has_table_privilege(:'worker_user', 'public.billing_users', 'SELECT')" in provision
    assert "/docker-entrypoint-initdb.d/10-lecturesift-app-role.sh:ro" in compose
    migration = compose.split("  migration:", 1)[1].split("  postgres:", 1)[0]
    assert "${LECTURESIFT_DB_ENV_FILE:-/etc/lecturesift/postgres.env}" in migration
    assert 'profiles: ["maintenance"]' in migration
    assert "init_rollout_database" in migration
    assert "${LECTURESIFT_API_ENV_FILE" not in migration
    assert "${LECTURESIFT_WORKER_ENV_FILE" not in migration
    assert service.index("up -d --wait --wait-timeout 300 postgres redis") < service.index(
        "provision_database_role.sh"
    ) < service.index("up -d --remove-orphans --wait --wait-timeout 600")


def test_rehearsal_stack_replaces_production_work_volumes():
    script = _read("deploy/rehearsal_stack.sh")

    assert "LECTURESIFT_REHEARSAL_ORCHESTRATED" in script
    assert 'rehearsal_api_work_volume="lecturesift-api-rehearsal-work"' in script
    assert 'rehearsal_worker_work_volume="lecturesift-worker-rehearsal-work"' in script
    assert "source: rehearsal_api_work" in script
    assert "source: rehearsal_worker_work" in script
    assert "target: /var/lib/lecturesift" in script
    assert 'docker volume create --label lecturesift.rehearsal=true "$volume"' in script
    assert '--file "$ROOT_DIR/compose.yaml" --file "$rehearsal_compose_file"' in script
    assert '"${compose[@]}" run -d --name lecturesift-api-rehearsal' in script
    assert '"${compose[@]}" run -d --name lecturesift-worker-rehearsal' in script


def test_recovery_runbooks_describe_the_vps_target_not_stale_render_services():
    policy = _read("BACKUP_POLICY.md")
    durable = _read("DURABLE_PROCESSING.md")

    assert "private Compose" in policy and "PostgreSQL service is the source of" in policy
    assert "latest off-site production snapshot" in policy
    assert "deploy/restic_restore_rehearsal.sh" in policy
    assert "deploy/record_restic_escrow.sh" in policy
    assert "paid managed Render Postgres database is the source of truth" not in policy
    assert "persistent Render Key Value instance" not in durable
    assert "private Compose Redis service" in durable
    assert "generate_role_envs.py" in durable


def test_postgres_cutover_is_snapshot_consistent_reversible_and_fail_stopped():
    script = _read("deploy/migrate_postgres.sh")
    docs = _read("VPS_DEPLOYMENT.md")

    assert "LECTURESIFT_POSTGRES_CUTOVER_CONFIRM" in script
    assert "LECTURESIFT_SOURCE_FROZEN" in script
    assert "LECTURESIFT_SOURCE_WORKER_STOPPED" in script
    assert "LECTURESIFT_PROVIDER_RECONCILED" in script
    assert 'ALLOWED_SOURCE_ENV_FILE="/root/.lecturesift-render-source.env"' in script
    assert 'ALLOWED_DB_ENV_FILE="/etc/lecturesift/postgres.env"' in script
    assert 'ALLOWED_RUNTIME_ENV_FILE="/etc/lecturesift/runtime.env"' in script
    assert 'ALLOWED_BACKUP_ROOT="/var/backups/lecturesift/postgres-cutover"' in script
    assert "must have mode 0400 or 0600" in script
    assert 'host.endswith(".render.com")' in script
    assert 'host.endswith(".onrender.com")' in script
    assert 'payload.get("maintenance_mode") == "freeze"' in script
    assert "billing_manual_orders WHERE status = 'pending'" in script
    assert "billing_payment_orders WHERE status IN ('created', 'pending')" in script
    assert script.count('[[ "$pending_') >= 2
    assert "assert_render_worker_and_queue_stopped" in script
    assert "connection.ensure_connection" in script
    assert "app.control.ping(timeout=8)" in script
    assert 'client.scan_iter(match=b"celery*"' in script
    assert 'client.hlen("unacked")' in script
    assert 'client.zcard("unacked_index")' in script
    assert "queued\", \"working" in script
    assert "LECTURESIFT_WORKER_DB_USER" in script
    assert "LECTURESIFT_WORKER_DB_PASSWORD" in script
    assert "LECTURESIFT_WORKER_DATABASE_URL" in script
    assert "api.username != worker.username" in script

    assert "pg_export_snapshot()" in script
    assert '--snapshot "$SNAPSHOT_ID"' in script
    assert "source-before.safe" in script
    assert "source-snapshot.safe" in script
    assert "source-after.safe" in script
    assert 'cmp --silent "$RUN_DIR/source-before.safe" "$RUN_DIR/source-snapshot.safe"' in script
    assert 'cmp --silent "$RUN_DIR/source-snapshot.safe" "$RUN_DIR/source-after.safe"' in script
    assert "target_size" in script
    assert script.index(
        "required_bytes=$((source_size * 2 + target_size * 2 + 5368709120))"
    ) < script.index(
        'pg_dump "$SOURCE_DATABASE_URL"'
    )
    assert "preserving 5 GiB of host reserve" in script

    writer_stop = '"${compose[@]}" stop --timeout 600 api worker'
    rollback_dump = '>"$RUN_DIR/target-before.dump"'
    mutation = 'target_mutated="true"'
    assert writer_stop in script
    assert script.index(writer_stop) < script.index(rollback_dump) < script.rindex(mutation)
    assert "target_stopped" in script
    assert 'reset_and_restore_target "$RUN_DIR/render-final.dump"' in script
    assert 'reset_and_restore_target "$RUN_DIR/target-before.dump"' in script
    assert '"$PROVISION_ROLE"' in script
    assert "probe_application_role api" in script
    assert "probe_application_role worker" in script
    assert "NOSUPERUSER" not in script  # the probe reads PostgreSQL flags directly
    assert "rolsuper, rolcreatedb, rolcreaterole" in script
    assert "CREATE TEMP TABLE lecturesift_forbidden_temp" in script
    assert "CREATE TABLE public.lecturesift_forbidden_ddl" in script
    assert "lecturesift_worker,public" in script
    assert "password_salt <> '' OR password_hash <> ''" in script
    assert "public.billing_auth_tokens" in script
    assert "public.lecturesift_admin_account_events" in script
    assert "public.lecturesift_contact_messages" in script
    assert "INSERT INTO lecturesift_runtime_metrics" in script
    assert "INSERT INTO billing_usage_events" in script
    assert "transaction.rollback()" in script
    assert 'cmp --silent "$RUN_DIR/source-snapshot.safe" "$RUN_DIR/target-restored.safe"' in script
    assert "postgres-cutover-target-unproven" in script
    assert 'FAIL_STOP_MARKER="$FAIL_STOP_ROOT/postgres-cutover-unproven"' in script
    assert "api_worker_started=false" in script
    assert "caddy_changed=false" in script
    assert "stop caddy" not in script
    assert "up -d caddy" not in script

    assert "deploy/migrate_postgres.sh" in docs
    assert "/var/backups/lecturesift/postgres-cutover" in docs
    assert "Caddy/DNS is the last" in docs and "gate, not part" in docs


def test_postgres_reverse_reconciliation_never_guesses_a_merge_or_traffic_flip():
    script = _read("deploy/rollback_postgres_to_render.sh")
    docs = _read("VPS_DEPLOYMENT.md")

    for flag in (
        "LECTURESIFT_POSTGRES_ROLLBACK_CONFIRM",
        "LECTURESIFT_OVH_FROZEN",
        "LECTURESIFT_OVH_WORKER_STOPPED",
        "LECTURESIFT_RENDER_FROZEN",
        "LECTURESIFT_RENDER_WORKER_STOPPED",
        "LECTURESIFT_PROVIDER_RECONCILED",
    ):
        assert flag in script
    assert 'ALLOWED_BACKUP_ROOT="/var/backups/lecturesift/postgres-rollback"' in script
    assert 'host.endswith(".onrender.com")' in script
    assert "payload.get(\"maintenance_mode\") == \"freeze\"" in script
    assert 'check_health_freeze "$OVH_HEALTH_URL" "OVH"' in script
    assert 'check_health_freeze "$SOURCE_HEALTH_URL" "Render"' in script
    assert "assert_render_worker_stopped" in script
    assert "connection.ensure_connection" in script
    assert "app.control.ping(timeout=8)" in script
    assert '"${compose[@]}" stop --timeout 600 api worker' in script
    assert "queue_idle" in script
    assert '[[ "$(local_pending)" == "0" ]]' in script
    assert "billing_payment_orders WHERE status IN ('created', 'pending')" in script

    assert "pg_export_snapshot()" in script
    assert '--snapshot "$snapshot_id"' in script
    assert "ovh-before.safe" in script
    assert "ovh-final.safe" in script
    assert "ovh-after.safe" in script
    assert "render-before.dump" in script
    assert "render-after-capture.safe" in script
    assert 'cmp --silent "$RUN_DIR/render-before.safe" "$RUN_DIR/render-after-capture.safe"' in script
    assert "RECONCILIATION_REQUIRED" in script
    assert 'LECTURESIFT_RENDER_REPLACE_CONFIRM:-}" != "REPLACE_STILL_FENCED_RENDER"' in script
    assert "DROP SCHEMA public CASCADE; CREATE SCHEMA public;" in script
    assert "automatic_row_merge=false" in script
    assert "whole_database_replacement=true" in script
    assert 'render_command reset-restore render-before.dump' in script
    assert 'cmp --silent "$RUN_DIR/ovh-final.safe" "$RUN_DIR/render-restored.safe"' in script
    assert "postgres-rollback-render-unproven" in script
    assert "redis_reconciliation_complete=false" in script
    assert "r2_reconciliation_complete=false" in script
    assert "traffic_changed=false" in script
    assert "stop caddy" not in script

    assert "deploy/rollback_postgres_to_render.sh" in docs
    assert "There is no safe automatic row merge" in docs
    assert "REPLACE_STILL_FENCED_RENDER" in docs
    assert "Reconcile the logical Redis job" in docs
