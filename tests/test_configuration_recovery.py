from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _snapshot_module():
    spec = importlib.util.spec_from_file_location(
        "lecturesift_configuration_snapshot",
        ROOT / "deploy" / "configuration_snapshot.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_configuration_snapshot_uses_only_exact_allowlists():
    snapshot = _snapshot_module()

    assert snapshot.ENVIRONMENT_ALLOWLIST == (
        "runtime.env",
        "api.env",
        "worker.env",
        "instagram.env",
        "postgres.env",
        "restic.env",
    )
    assert "rehearsal.env" not in snapshot.ENVIRONMENT_ALLOWLIST
    assert snapshot.RELEASE_IDENTITY_ALLOWLIST == (
        "/run/lecturesift/release.env",
    )
    assert snapshot.IDENTITY_ALLOWLIST == (
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
    assert all(".git" not in path and ".docker" not in path for path in snapshot.IDENTITY_ALLOWLIST)


def test_configuration_snapshot_creation_is_root_private_and_fail_closed():
    script = _read("deploy/configuration_snapshot.py")

    assert "os.O_NOFOLLOW" in script
    assert "details.st_uid != 0" in script
    assert "kind == \"environment\" and mode != 0o600" in script
    assert "kind == \"identity\" and mode & 0o022" in script
    assert "kind == \"release_identity\" and mode not in {0o400, 0o600}" in script
    assert "LECTURESIFT_EXPECTED_BUILD_REVISION=[0-9a-f]{40}" in script
    assert "source changed while the snapshot was being created" in script
    assert "source set changed while the snapshot was being created" in script
    assert "source_identities" in script
    assert "os.fsync" in script
    assert "os.chmod(destination, 0o600)" in script
    assert "CONFIGURATION_MANIFEST.json" in script
    assert "CONFIGURATION_SHA256SUMS" in script
    assert "actual_files != expected_files" in script
    assert "snapshot contains a symlink" in script
    assert "print(manifest" not in script and "print(entries" not in script
    assert "read_text" not in script.split("def _copy_source", 1)[1].split(
        "def _hash_file", 1
    )[0]


def test_backup_adds_configuration_only_to_ephemeral_encrypted_stage():
    script = _read("deploy/backup.sh")

    local_checkpoint = script.index('mv "$STAGING" "$DEST"')
    config_create = script.index('"$CONFIGURATION_SNAPSHOT_TOOL" create')
    encrypted_upload = script.index('restic backup "$RESTIC_STAGE"')
    assert local_checkpoint < config_create < encrypted_upload
    assert '--destination "$RESTIC_STAGE_NEXT/$CONFIGURATION_SNAPSHOT_NAME"' in script
    assert "Secrets are never added to the persistent local timestamped checkpoint" in script
    assert script.startswith("#!/usr/bin/env bash\nset -euo pipefail\numask 077\nset +x")
    assert "A required private environment file could not be loaded" in script
    assert "} >/dev/null 2>&1; then" in script
    assert 'rm -rf --one-file-system -- "$STAGING" "$RESTIC_STAGE_NEXT" "$RESTIC_STAGE"' in script
    assert "RESTIC_KEEP_WITHIN_DAYS" in script
    assert 'keep-within "${RESTIC_KEEP_WITHIN_DAYS}d"' in script
    assert "restic forget --prune" not in script
    before_restic_stage = script[: script.index('mkdir -m 0700 -- "$RESTIC_STAGE_NEXT"')]
    assert "CONFIGURATION_SNAPSHOT_NAME" not in before_restic_stage.split(
        "CONFIGURATION_SNAPSHOT_NAME=", 1
    )[-1]


def test_isolated_configuration_recovery_never_overwrites_live_files():
    script = _read("deploy/recover_configuration_snapshot.sh")

    assert 'RECOVERY_ROOT="/var/lib/lecturesift/configuration-recovery"' in script
    assert 'RESTIC_ENV_FILE="/etc/lecturesift/restic.env"' in script
    assert '"$(stat -c \'%a\' -- "$RESTIC_ENV_FILE")" == "600"' in script
    assert "lecturesift-production-backups/restic" in script
    assert 'restic restore "$SNAPSHOT_ID" --target "$EXTRACT_ROOT" --verify' in script
    assert '--include "$RESTIC_CONFIGURATION_PATH/**"' in script
    assert "MAX_CONFIGURATION_BYTES=134217728" in script
    assert 'python3 "$SNAPSHOT_TOOL" verify' in script
    assert 'mv -- "$SNAPSHOT_ROOT" "$FINAL_STAGING/snapshot"' in script
    assert 'mv -- "$FINAL_STAGING" "$FINAL_DIR"' in script
    assert "No file under /etc/lecturesift or /opt/lecturesift was modified" in script
    assert "Never copy the entire snapshot tree over /etc or /opt" in script
    assert 'source "$RESTIC_ENV_FILE" >/dev/null 2>&1' in script
    assert "restic-env-load.log" not in script
    assert "export -n RESTIC_CACHE_DIR" in script
    assert "cp -r" not in script and "cp -a" not in script
    assert 'mv -- "$SNAPSHOT_ROOT" "/etc/' not in script
    assert "install -o root -g root -m 0600" in script
    assert "$FINAL_DIR/snapshot/files/etc/lecturesift/runtime.env" in script


def test_restore_rehearsal_verifies_configuration_before_recording_success():
    script = _read("deploy/restic_restore_rehearsal.sh")

    verify = script.index('python3 "$CONFIGURATION_SNAPSHOT_TOOL" verify')
    payload_cleanup = script.index("cleanup_restore_payload", verify)
    evidence = script.index("configuration_snapshot=verified")
    assert verify < payload_cleanup < evidence
    assert "the restored snapshot is missing its encrypted configuration package" in script
    assert "configuration_snapshot_format=lecturesift-configuration-snapshot-v1" in script
    assert "live_services_touched=false" in script


def test_disaster_restore_does_not_implicitly_install_recovered_configuration():
    destructive_restore = _read("deploy/restore.sh")
    recovery = _read("deploy/recover_configuration_snapshot.sh")

    assert "recover_configuration_snapshot.sh" not in destructive_restore
    assert "configuration-recovery" not in destructive_restore
    assert "/etc/lecturesift/runtime.env" not in recovery.split("cat >", 1)[0]


def test_configuration_recovery_policy_documents_encryption_and_operator_gate():
    policy = _read("BACKUP_POLICY.md")
    runbook = _read("VPS_DEPLOYMENT.md")

    assert "created only in the private ephemeral Restic staging directory" in policy
    assert "never walks `/etc`" in policy
    assert "leaves live\n`/etc/lecturesift` and `/opt/lecturesift` untouched" in policy
    assert "recover_configuration_snapshot.sh <snapshot-id>" in runbook
    assert "install -o root -g root -m 0600" in runbook
    assert "cannot serve as its own only key escrow" in runbook
    assert "`restore.sh` restores application data only" in runbook
