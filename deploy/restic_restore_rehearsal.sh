#!/usr/bin/env bash
set -euo pipefail

# This drill deliberately restores into disposable root-confined storage and a
# network-none PostgreSQL instance only. It must never attach a Compose volume,
# connect to a live database, or stop a running service.

if [[ "$(id -u)" -ne 0 ]]; then
  echo "This restore rehearsal must be run as root." >&2
  exit 1
fi

umask 077
set +x

ENV_FILE="${LECTURESIFT_RESTIC_ENV_FILE:-/etc/lecturesift/restic.env}"
ROOT_DIR="${LECTURESIFT_ROOT:-/opt/lecturesift}"
CONFIGURATION_SNAPSHOT_TOOL="$ROOT_DIR/deploy/configuration_snapshot.py"
CONFIGURATION_SNAPSHOT_NAME="configuration-snapshot-v1"
ALLOWED_RESTORE_ROOT="/var/lib/lecturesift/restic-restore-rehearsal"
REQUESTED_RESTORE_ROOT="${LECTURESIFT_RESTIC_REHEARSAL_ROOT:-$ALLOWED_RESTORE_ROOT}"
EVIDENCE_ROOT="/var/lib/lecturesift/recovery-drills"
BACKUP_LOCK_ROOT="/var/backups/lecturesift"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
RUN_DIR=""
RESTIC_HOST="lecturesift-production"
SNAPSHOT_MAX_AGE_SECONDS=172800
REQUESTED_SNAPSHOT_ID="${LECTURESIFT_RESTIC_REHEARSAL_SNAPSHOT_ID:-}"
RESTORE_HOST_RESERVE_BYTES=5368709120
RESTORE_SIZE_MAX_BYTES=1000000000000

fail() {
  echo "Restic restore rehearsal failed: $*" >&2
  exit 1
}

for command_name in docker restic realpath sha256sum stat mktemp find install grep sed python3 awk df flock; do
  command -v "$command_name" >/dev/null 2>&1 || fail "missing required command: $command_name"
done

[[ -f "$ENV_FILE" && ! -L "$ENV_FILE" ]] || fail "$ENV_FILE must be a regular, non-symlink file"
[[ "$(stat -c '%u' -- "$ENV_FILE")" == "0" ]] || fail "$ENV_FILE must be owned by root"
env_mode="$(stat -c '%a' -- "$ENV_FILE")"
(( (8#$env_mode & 8#077) == 0 )) || fail "$ENV_FILE must not be readable or writable by group/others"
[[ -f "$CONFIGURATION_SNAPSHOT_TOOL" && ! -L "$CONFIGURATION_SNAPSHOT_TOOL" && \
   "$(stat -c '%u' -- "$CONFIGURATION_SNAPSHOT_TOOL")" == "0" ]] || \
  fail "the configuration snapshot verifier is missing or unsafe"
configuration_tool_mode="$(stat -c '%a' -- "$CONFIGURATION_SNAPSHOT_TOOL")"
(( (8#$configuration_tool_mode & 8#022) == 0 )) || \
  fail "the configuration snapshot verifier must not be group/other writable"

# Reject path traversal, broad directories, and symlinked safety roots before
# creating any restore target.
allowed_root_normalized="$(realpath -m -- "$ALLOWED_RESTORE_ROOT")"
[[ "$allowed_root_normalized" == "$ALLOWED_RESTORE_ROOT" ]] || fail "the allowed restore root resolves through a symlink"
requested_root_normalized="$(realpath -m -- "$REQUESTED_RESTORE_ROOT")"
[[ "$REQUESTED_RESTORE_ROOT" == "$ALLOWED_RESTORE_ROOT" && \
   "$requested_root_normalized" == "$allowed_root_normalized" ]] || \
  fail "the restic rehearsal root is fixed and cannot be overridden"

install -d -o root -g root -m 0700 -- "$allowed_root_normalized" "$requested_root_normalized"
restore_root_real="$(realpath -e -- "$requested_root_normalized")"
case "$restore_root_real" in
  "$allowed_root_normalized"|"$allowed_root_normalized"/*) ;;
  *) fail "restore root escaped the isolated rehearsal directory" ;;
esac

# A SIGKILL or host power loss cannot run EXIT traps. Reconcile only old,
# strictly named drill directories immediately below the fixed root before a
# new restore starts. Symlinks and unexpected names are never followed or
# removed. A root-confined lock proves that no other live drill can own a
# matching directory while reconciliation runs.
exec 8>"$allowed_root_normalized/.rehearsal.lock"
chmod 0600 "$allowed_root_normalized/.rehearsal.lock"
flock -n 8 || fail "another restic restore rehearsal is already running"

# Serialize disk-heavy backup, restore and restic-drill operations. They share
# the host filesystem even though their payload roots are distinct, so
# independent df checks must never race one another.
backup_lock_parent="$(dirname -- "$BACKUP_LOCK_ROOT")"
[[ -d "$backup_lock_parent" && ! -L "$backup_lock_parent" && \
   "$(realpath -e -- "$backup_lock_parent")" == "$backup_lock_parent" ]] || \
  fail "the shared backup/restore lock parent is unsafe"
install -d -o root -g root -m 0700 -- "$BACKUP_LOCK_ROOT"
[[ ! -L "$BACKUP_LOCK_ROOT" && "$(realpath -e -- "$BACKUP_LOCK_ROOT")" == "$BACKUP_LOCK_ROOT" && \
   "$(stat -c '%u' -- "$BACKUP_LOCK_ROOT")" == "0" ]] || \
  fail "the shared backup/restore lock root is unsafe"
shared_lock_mode="$(stat -c '%a' -- "$BACKUP_LOCK_ROOT")"
(( (8#$shared_lock_mode & 8#077) == 0 )) || \
  fail "the shared backup/restore lock root must be private to root"
exec 9>"$BACKUP_LOCK_ROOT/.backup.lock"
flock -n 9 || fail "a backup, restore or restic drill is already active"

reconcile_stale_restore_payloads() {
  if ! find "$restore_root_real" -mindepth 1 -maxdepth 1 -type d \
      -name 'restic-restore-????????T??????Z-????????' -mmin +60 \
      -exec bash -euo pipefail -c '
        root="$1"
        shift
        for candidate in "$@"; do
          basename="${candidate##*/}"
          [[ "$basename" =~ ^restic-restore-[0-9]{8}T[0-9]{6}Z-[[:alnum:]]{8}$ ]]
          [[ ! -L "$candidate" ]]
          resolved="$(realpath -e -- "$candidate")"
          [[ "$resolved" == "$root/$basename" && "$resolved" != "$root" ]]
          rm -rf --one-file-system -- "$resolved"
        done
      ' _ "$restore_root_real" {} +; then
    fail "could not safely reconcile stale restic rehearsal directories"
  fi
}

reconcile_stale_restore_payloads

cleanup_restore_payload() {
  local resolved
  [[ -n "${RUN_DIR:-}" && -e "$RUN_DIR" ]] || return 0
  resolved="$(realpath -e -- "$RUN_DIR")" || return 1
  case "$resolved" in
    "$restore_root_real"/restic-restore-*) ;;
    *)
      echo "Refusing unsafe rehearsal cleanup target: $resolved" >&2
      return 1
      ;;
  esac
  [[ "$resolved" != "$restore_root_real" ]] || return 1
  rm -rf --one-file-system -- "$resolved"
}

trap 'cleanup_restore_payload || true' EXIT
RUN_DIR="$(mktemp -d -- "$restore_root_real/restic-restore-$STAMP-XXXXXXXX")"
chmod 0700 "$RUN_DIR"
PAYLOAD_DIR="$RUN_DIR/payload"
RESTIC_LOG="$RUN_DIR/restic-restore.log"
install -d -o root -g root -m 0700 -- "$PAYLOAD_DIR"

# Load the root-only file without allowing an accidental echo or trace in it to
# expose values on the terminal or create a plaintext diagnostic.
# shellcheck disable=SC1090
if ! source "$ENV_FILE" >/dev/null 2>&1; then
  set +x
  fail "$ENV_FILE could not be loaded"
fi
set +x

[[ -n "${RESTIC_REPOSITORY:-}" ]] || fail "RESTIC_REPOSITORY is not configured"
[[ -n "${RESTIC_PASSWORD:-}" ]] || fail "RESTIC_PASSWORD is not configured"
RESTIC_CACHE_DIR="$RUN_DIR/restic-cache"
export RESTIC_REPOSITORY RESTIC_PASSWORD RESTIC_CACHE_DIR

case "$RESTIC_REPOSITORY" in
  s3:*)
    [[ -n "${RESTIC_AWS_ACCESS_KEY_ID:-}" ]] || fail "RESTIC_AWS_ACCESS_KEY_ID is not configured"
    [[ -n "${RESTIC_AWS_SECRET_ACCESS_KEY:-}" ]] || fail "RESTIC_AWS_SECRET_ACCESS_KEY is not configured"
    export AWS_ACCESS_KEY_ID="$RESTIC_AWS_ACCESS_KEY_ID"
    export AWS_SECRET_ACCESS_KEY="$RESTIC_AWS_SECRET_ACCESS_KEY"
    ;;
  *)
    fail "production restore drills require an off-site S3-compatible restic repository"
    ;;
esac

# Keep repository diagnostics private because the repository URL can contain
# account-specific information. The log is deleted with the isolated payload.
RESTIC_CONFIG_JSON="$RUN_DIR/restic-config.json"
if ! restic cat config >"$RESTIC_CONFIG_JSON" 2>"$RESTIC_LOG"; then
  fail "the configured restic repository is inaccessible or uninitialized"
fi
repository_id_sha256="$(python3 - "$RESTIC_CONFIG_JSON" <<'PY'
import hashlib
import json
from pathlib import Path
import sys

config = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
repository_id = str(config.get("id") or "")
if not repository_id:
    raise SystemExit(1)
print(hashlib.sha256(repository_id.encode("ascii")).hexdigest())
PY
)" || fail "the restic repository config has no stable id"

SNAPSHOTS_JSON="$RUN_DIR/restic-snapshots.json"
if [[ -n "$REQUESTED_SNAPSHOT_ID" ]]; then
  [[ "$REQUESTED_SNAPSHOT_ID" =~ ^[[:xdigit:]]{8,64}$ ]] || \
    fail "LECTURESIFT_RESTIC_REHEARSAL_SNAPSHOT_ID must be a restic snapshot id"
  drill_scope="explicit-backup"
  snapshot_query=(restic snapshots --json --host "$RESTIC_HOST" \
    --tag lecturesift --tag production "$REQUESTED_SNAPSHOT_ID")
else
  drill_scope="current-latest"
  snapshot_query=(restic snapshots --json --latest 1 --host "$RESTIC_HOST" \
    --tag lecturesift --tag production)
fi
if ! "${snapshot_query[@]}" >"$SNAPSHOTS_JSON" 2>"$RESTIC_LOG"; then
  fail "the latest production LectureSift snapshot could not be selected"
fi
snapshot_selection="$(python3 - "$SNAPSHOTS_JSON" "$SNAPSHOT_MAX_AGE_SECONDS" "$drill_scope" <<'PY'
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

snapshots = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if not isinstance(snapshots, list) or len(snapshots) != 1:
    raise SystemExit(1)
snapshot = snapshots[0]
snapshot_id = str(snapshot.get("id") or "")
snapshot_time_text = str(snapshot.get("time") or "")
tags = set(snapshot.get("tags") or [])
if (
    not snapshot_id
    or snapshot.get("hostname") != "lecturesift-production"
    or not {"lecturesift", "production"}.issubset(tags)
):
    raise SystemExit(1)
snapshot_time = datetime.fromisoformat(snapshot_time_text.replace("Z", "+00:00"))
now = datetime.now(timezone.utc)
age = (now - snapshot_time.astimezone(timezone.utc)).total_seconds()
scope = sys.argv[3]
if age < -300 or (scope == "current-latest" and age > int(sys.argv[2])):
    raise SystemExit(1)
print(snapshot_id)
print(snapshot_time.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"))
PY
)" || fail "the selected production snapshot is missing, ambiguous, or older than 48 hours"
SNAPSHOT_ID="$(printf '%s\n' "$snapshot_selection" | sed -n '1p')"
SNAPSHOT_TIME_UTC="$(printf '%s\n' "$snapshot_selection" | sed -n '2p')"
[[ "$SNAPSHOT_ID" =~ ^[[:xdigit:]]{8,64}$ && -n "$SNAPSHOT_TIME_UTC" ]] || \
  fail "the selected snapshot identity is invalid"

# `restic restore` materializes the complete snapshot before backup metadata
# can be inspected. Ask restic for the selected snapshot's restore size first
# and preserve 5 GiB of host free space. A second payload-sized allowance
# covers the restored files plus the isolated restic cache. Missing/ambiguous
# size data fails closed instead of risking PostgreSQL/Redis on the same disk.
RESTORE_STATS_JSON="$RUN_DIR/restic-restore-stats.json"
if ! restic stats --mode restore-size --json "$SNAPSHOT_ID" \
    >"$RESTORE_STATS_JSON" 2>"$RESTIC_LOG"; then
  fail "the selected snapshot restore size could not be determined"
fi
restore_size_bytes="$(python3 - "$RESTORE_STATS_JSON" <<'PY'
import json
from pathlib import Path
import sys

stats = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
size = stats.get("total_size")
if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
    raise SystemExit(1)
print(size)
PY
)" || fail "restic returned an invalid or empty restore size"
[[ "$restore_size_bytes" =~ ^[1-9][0-9]*$ ]] || \
  fail "restic returned an invalid restore-size value"
(( restore_size_bytes <= RESTORE_SIZE_MAX_BYTES )) || \
  fail "the selected snapshot exceeds the supported rehearsal restore-size bound"
restore_available_bytes="$(df --output=avail -B1 -- "$restore_root_real" | awk 'NR == 2 {print $1}')"
[[ "$restore_available_bytes" =~ ^[0-9]+$ ]] || \
  fail "could not determine free space for the isolated restic rehearsal root"
restore_required_bytes=$((restore_size_bytes * 2 + RESTORE_HOST_RESERVE_BYTES))
(( restore_available_bytes >= restore_required_bytes )) || \
  fail "insufficient disk space for the selected restic snapshot while preserving 5 GiB of host reserve"

if ! restic restore "$SNAPSHOT_ID" --target "$PAYLOAD_DIR" --verify >"$RESTIC_LOG" 2>&1; then
  fail "the selected production LectureSift snapshot could not be restored and verified"
fi

# Credentials are no longer needed after the restore and must not appear in
# evidence files or subsequent child-process environments.
unset RESTIC_REPOSITORY RESTIC_PASSWORD RESTIC_CACHE_DIR
unset RESTIC_AWS_ACCESS_KEY_ID RESTIC_AWS_SECRET_ACCESS_KEY
unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY

mapfile -d '' manifest_files < <(find "$PAYLOAD_DIR" -type f -name SHA256SUMS -print0)
candidate_dirs=()
for manifest_file in "${manifest_files[@]}"; do
  [[ "$manifest_file" != *$'\n'* && "$manifest_file" != *$'\r'* ]] || \
    fail "refusing a restored manifest path containing control characters"
  candidate_dir="${manifest_file%/*}"
  if [[ "$candidate_dir/SHA256SUMS" == "$manifest_file" && \
        -f "$candidate_dir/SHA256SUMS" && ! -L "$candidate_dir/SHA256SUMS" && \
        -f "$candidate_dir/postgres.dump" && ! -L "$candidate_dir/postgres.dump" && \
        -f "$candidate_dir/redis-dump.rdb" && ! -L "$candidate_dir/redis-dump.rdb" && \
        -f "$candidate_dir/BACKUP_METADATA" && ! -L "$candidate_dir/BACKUP_METADATA" ]]; then
    candidate_dirs+=("$candidate_dir")
  fi
done
[[ "${#candidate_dirs[@]}" -eq 1 ]] || fail "the restored snapshot must contain exactly one complete LectureSift backup"

BACKUP_DIR="$(realpath -e -- "${candidate_dirs[0]}")"
case "$BACKUP_DIR" in
  "$PAYLOAD_DIR"/*) ;;
  *) fail "restored backup escaped the isolated payload directory" ;;
esac

# The production snapshot is incomplete unless it also carries the exact
# root-only host configuration package.  Verify the package while it remains
# inside this disposable root-confined restore tree; no live configuration is
# read, written, or replaced here.
CONFIGURATION_SNAPSHOT_DIR="$BACKUP_DIR/$CONFIGURATION_SNAPSHOT_NAME"
[[ -d "$CONFIGURATION_SNAPSHOT_DIR" && ! -L "$CONFIGURATION_SNAPSHOT_DIR" ]] || \
  fail "the restored snapshot is missing its encrypted configuration package"
python3 "$CONFIGURATION_SNAPSHOT_TOOL" verify \
  --snapshot-root "$CONFIGURATION_SNAPSHOT_DIR" \
  --deploy-root "$ROOT_DIR" --quiet || \
  fail "the restored configuration package failed its manifest verification"

# Only the three known, relative payload names are allowed in the checksum file;
# this prevents a crafted manifest from reading paths outside the rehearsal.
postgres_checksum_seen=0
redis_checksum_seen=0
metadata_checksum_seen=0
checksum_line_count=0
while IFS= read -r checksum_line || [[ -n "$checksum_line" ]]; do
  ((checksum_line_count += 1))
  if [[ "$checksum_line" =~ ^([[:xdigit:]]{64})[[:space:]]+(\*)?(postgres\.dump|redis-dump\.rdb|BACKUP_METADATA)$ ]]; then
    case "${BASH_REMATCH[3]}" in
      postgres.dump) ((postgres_checksum_seen += 1)) ;;
      redis-dump.rdb) ((redis_checksum_seen += 1)) ;;
      BACKUP_METADATA) ((metadata_checksum_seen += 1)) ;;
    esac
  else
    fail "SHA256SUMS contains an unexpected or unsafe entry"
  fi
done <"$BACKUP_DIR/SHA256SUMS"
[[ "$checksum_line_count" -eq 3 && "$postgres_checksum_seen" -eq 1 && \
   "$redis_checksum_seen" -eq 1 && "$metadata_checksum_seen" -eq 1 ]] || \
  fail "SHA256SUMS must contain exactly one entry for each backup payload"

(cd -- "$BACKUP_DIR" && sha256sum --check --strict -- SHA256SUMS >/dev/null)
backup_set_sha256="$(sha256sum "$BACKUP_DIR/SHA256SUMS" | awk '{print $1}')"
[[ "$backup_set_sha256" =~ ^[[:xdigit:]]{64}$ ]] || \
  fail "the restored backup-set identity could not be hashed"
metadata_format="$(sed -n 's/^format=//p' "$BACKUP_DIR/BACKUP_METADATA")"
metadata_application="$(sed -n 's/^application_identity=//p' "$BACKUP_DIR/BACKUP_METADATA")"
metadata_schema_compatibility="$(sed -n 's/^application_schema_compatibility=//p' "$BACKUP_DIR/BACKUP_METADATA")"
metadata_manifest_version="$(sed -n 's/^schema_manifest_version=//p' "$BACKUP_DIR/BACKUP_METADATA")"
metadata_manifest_sha256="$(sed -n 's/^schema_manifest_sha256=//p' "$BACKUP_DIR/BACKUP_METADATA")"
metadata_database_identity_sha256="$(sed -n 's/^database_identity_sha256=//p' "$BACKUP_DIR/BACKUP_METADATA")"
metadata_schema_sha256="$(sed -n 's/^schema_fingerprint_sha256=//p' "$BACKUP_DIR/BACKUP_METADATA")"
metadata_data_sha256="$(sed -n 's/^data_fingerprint_sha256=//p' "$BACKUP_DIR/BACKUP_METADATA")"
metadata_database_size="$(sed -n 's/^database_size_bytes=//p' "$BACKUP_DIR/BACKUP_METADATA")"
metadata_redis_version="$(sed -n 's/^redis_version=//p' "$BACKUP_DIR/BACKUP_METADATA")"
metadata_compatibility="$(sed -n 's/^redis_restore_compatibility=//p' "$BACKUP_DIR/BACKUP_METADATA")"
case "$metadata_manifest_version" in
  1) RECOVERY_MANIFEST="$ROOT_DIR/deploy/recovery_manifest_v1.sql" ;;
  *) fail "the backup references an unsupported recovery manifest version" ;;
esac
[[ "$metadata_format" == "lecturesift-backup-v2" && \
   "$metadata_application" == "lecturesift-production" && \
   "$metadata_schema_compatibility" == "lecturesift-schema-v1" && \
   "$metadata_manifest_version" == "1" && \
   "$metadata_manifest_sha256" =~ ^[[:xdigit:]]{64}$ && \
   "$metadata_database_identity_sha256" =~ ^[[:xdigit:]]{64}$ && \
   "$metadata_schema_sha256" =~ ^[[:xdigit:]]{64}$ && \
   "$metadata_data_sha256" =~ ^[[:xdigit:]]{64}$ && \
   "$metadata_database_size" =~ ^[1-9][0-9]*$ && \
   "$metadata_redis_version" =~ ^7\.4([.]|$) && \
   "$metadata_compatibility" == "redis-7.4-only" ]] || \
  fail "backup metadata does not prove LectureSift schema identity and Redis 7.4 compatibility"

# Validators run in short-lived, network-isolated containers with the backup
# directory mounted read-only. No named/live volume or service is referenced.
docker image inspect postgres:18-bookworm@sha256:1c59e2c3c818eaa0f0628f695b36e7c9e362d6b219b36a54a32df645cbd7e1af >/dev/null 2>&1 || \
  fail "pinned PostgreSQL image must already be present before the drill"
docker image inspect redis:7.4-alpine@sha256:ff02b58f971e7d7d156a1267e283fcbbeee91773b6aa36c49dac28ecfe28eadf >/dev/null 2>&1 || \
  fail "pinned Redis image must already be present before the drill"
[[ -f "$RECOVERY_MANIFEST" && ! -L "$RECOVERY_MANIFEST" ]] || \
  fail "the PostgreSQL rehearsal manifest is missing"
[[ -f "$ROOT_DIR/deploy/redis_rdb_to_aof.sh" && \
   ! -L "$ROOT_DIR/deploy/redis_rdb_to_aof.sh" && \
   -f "$ROOT_DIR/deploy/redis.conf" && ! -L "$ROOT_DIR/deploy/redis.conf" ]] || \
  fail "the Redis RDB-to-AOF recovery assets are missing or unsafe"
current_manifest_sha256="$(sha256sum "$RECOVERY_MANIFEST" | awk '{print $1}')"
[[ "$current_manifest_sha256" == "$metadata_manifest_sha256" ]] || \
  fail "the backup was created with a different schema identity manifest"

# Listing an archive proves only that its table of contents is readable. Start
# PostgreSQL inside the same network-none container, restore the dump into a
# database-size-bound tmpfs, run the production migration manifest, and let
# container removal discard every database page after validation.
postgres_uid="$(docker run --rm --pull=never --network none --read-only \
  --entrypoint id postgres:18-bookworm@sha256:1c59e2c3c818eaa0f0628f695b36e7c9e362d6b219b36a54a32df645cbd7e1af -u postgres)"
postgres_gid="$(docker run --rm --pull=never --network none --read-only \
  --entrypoint id postgres:18-bookworm@sha256:1c59e2c3c818eaa0f0628f695b36e7c9e362d6b219b36a54a32df645cbd7e1af -g postgres)"
[[ "$postgres_uid" =~ ^[0-9]+$ && "$postgres_gid" =~ ^[0-9]+$ ]] || \
  fail "could not resolve the disposable PostgreSQL image user"

# The backup records live pg_database_size(), avoiding unsafe guesses from the
# compressed dump ratio. Size both tmpfs and the container dynamically, disable
# swap, and reserve 2 GiB for the host. Insufficient RAM fails before a
# disposable database starts and cannot fill the production filesystem.
(( metadata_database_size <= 1000000000000 )) || \
  fail "the recorded database size exceeds the supported validation bound"
validation_tmpfs_bytes=$((metadata_database_size * 2 + 536870912))
validation_container_memory_bytes=$((validation_tmpfs_bytes + 1073741824))
validation_host_required_bytes=$((validation_container_memory_bytes + 2147483648))
validation_host_available_bytes="$(awk '/^MemAvailable:/ {printf "%.0f\n", $2 * 1024}' /proc/meminfo)"
[[ "$validation_host_available_bytes" =~ ^[0-9]+$ ]] || \
  fail "could not determine host memory capacity for isolated PostgreSQL validation"
(( validation_host_available_bytes >= validation_host_required_bytes )) || \
  fail "insufficient host memory for isolated PostgreSQL validation with a 2 GiB reserve"

docker run --rm --pull=never --network none --read-only \
  --user "$postgres_uid:$postgres_gid" --cap-drop ALL \
  --security-opt no-new-privileges --pids-limit 256 \
  --memory "${validation_container_memory_bytes}b" \
  --memory-swap "${validation_container_memory_bytes}b" \
  --tmpfs "/var/lib/postgresql:rw,nosuid,nodev,size=$validation_tmpfs_bytes,uid=$postgres_uid,gid=$postgres_gid,mode=0700" \
  --tmpfs "/tmp:rw,nosuid,nodev,size=128m,uid=$postgres_uid,gid=$postgres_gid,mode=0700" \
  -v "$BACKUP_DIR:/backup:ro" \
  -v "$RECOVERY_MANIFEST:/probe/recovery_manifest.sql:ro" \
  --entrypoint bash postgres:18-bookworm@sha256:1c59e2c3c818eaa0f0628f695b36e7c9e362d6b219b36a54a32df645cbd7e1af -euo pipefail -c '
    export PGDATA=/var/lib/postgresql/data
    initdb --no-sync --auth-local=trust --auth-host=reject \
      --locale=en_US.UTF8 --encoding=UTF8 >/dev/null
    pg_ctl -o "-c listen_addresses= -c unix_socket_directories=/tmp -c fsync=off" \
      -w start >/dev/null
    trap '\''pg_ctl -m immediate -w stop >/dev/null 2>&1 || true'\'' EXIT
    createdb --host=/tmp --template=template0 --encoding=UTF8 lecturesift_rehearsal
    pg_restore --host=/tmp --exit-on-error --single-transaction \
      --no-owner --no-acl --dbname=lecturesift_rehearsal /backup/postgres.dump
    psql --host=/tmp --dbname=lecturesift_rehearsal -v ON_ERROR_STOP=1 \
      -f /probe/recovery_manifest.sql > /tmp/rehearsal-manifest.out
    grep -q "^SCHEMA|" /tmp/rehearsal-manifest.out
    grep -q "^TABLE|" /tmp/rehearsal-manifest.out
    ! grep -Eq "^(TABLE_DIFF|UNVALIDATED_FK)\\|" /tmp/rehearsal-manifest.out
    awk -F"|" '\''$1 == "ANOMALY" && $3 != "0" { invalid=1 } END { exit invalid }'\'' \
      /tmp/rehearsal-manifest.out
    cat /tmp/rehearsal-manifest.out
  ' >"$RUN_DIR/rehearsal-manifest.out"

restored_database_line="$(tr -d '\r' <"$RUN_DIR/rehearsal-manifest.out" | grep '^DATABASE|')"
restored_schema_line="$(tr -d '\r' <"$RUN_DIR/rehearsal-manifest.out" | grep '^SCHEMA|')"
restored_table_count="$(tr -d '\r' <"$RUN_DIR/rehearsal-manifest.out" | grep -c '^TABLE|')"
restored_data_sha256="$(tr -d '\r' <"$RUN_DIR/rehearsal-manifest.out" \
  | grep '^TABLE|' | LC_ALL=C sort | sha256sum | awk '{print $1}')"
[[ "$(tr -d '\r' <"$RUN_DIR/rehearsal-manifest.out" | grep -c '^DATABASE|')" == "1" && \
   "$(tr -d '\r' <"$RUN_DIR/rehearsal-manifest.out" | grep -c '^SCHEMA|')" == "1" && \
   "$restored_table_count" =~ ^[1-9][0-9]*$ ]] || \
  fail "the restored database did not produce exactly one database/schema fingerprint"
[[ "$(printf '%s\n' "$restored_database_line" | sha256sum | awk '{print $1}')" == \
   "$metadata_database_identity_sha256" ]] || \
  fail "the restored PostgreSQL runtime/collation identity does not match backup metadata"
[[ "$(printf '%s\n' "$restored_schema_line" | sha256sum | awk '{print $1}')" == \
   "$metadata_schema_sha256" ]] || \
  fail "the restored database schema fingerprint does not match backup metadata"
[[ "$restored_data_sha256" == "$metadata_data_sha256" ]] || \
  fail "the restored database data fingerprint does not match backup metadata"

docker run --rm --pull=never --network none \
  --read-only --cap-drop ALL --security-opt no-new-privileges --pids-limit 64 \
  -v "$BACKUP_DIR:/backup:ro" \
  --entrypoint redis-check-rdb \
  redis:7.4-alpine@sha256:ff02b58f971e7d7d156a1267e283fcbbeee91773b6aa36c49dac28ecfe28eadf /backup/redis-dump.rdb >/dev/null

# Exercise the real Redis 7 startup semantics, not only the RDB checksum. The
# production config has AOF enabled, so the drill must preload RDB with AOF
# disabled, convert it through a durable rewrite, then prove a second normal
# appendonly boot preserves the same dataset. Redis maxmemory is 512 MiB; the
# bounded tmpfs/container allow its conversion without touching host disk.
redis_validation_host_available="$(awk '/^MemAvailable:/ {printf "%.0f\n", $2 * 1024}' /proc/meminfo)"
[[ "$redis_validation_host_available" =~ ^[0-9]+$ ]] || \
  fail "could not determine host memory capacity for Redis startup validation"
(( redis_validation_host_available >= 3758096384 )) || \
  fail "insufficient host memory for Redis RDB-to-AOF validation with a 2 GiB reserve"
redis_conversion_output="$(docker run --rm --pull=never --network none --user 0 \
  --security-opt no-new-privileges --pids-limit 128 \
  --memory 1536m --memory-swap 1536m \
  --tmpfs /data:rw,nosuid,nodev,size=1g,uid=0,gid=0,mode=0700 \
  -v "$BACKUP_DIR:/restore:ro" \
  -v "$ROOT_DIR/deploy:/probe:ro" \
  --entrypoint sh redis:7.4-alpine@sha256:ff02b58f971e7d7d156a1267e283fcbbeee91773b6aa36c49dac28ecfe28eadf /probe/redis_rdb_to_aof.sh)" || \
  fail "Redis RDB-to-AOF startup validation failed"
[[ "$(printf '%s\n' "$redis_conversion_output" | grep -c '^RESTORED_DBSIZE=')" == "1" && \
   "$(printf '%s\n' "$redis_conversion_output" | sed -n 's/^RESTORED_DBSIZE=//p')" =~ ^[0-9]+$ ]] || \
  fail "Redis startup validation did not return one verified key count"

# Delete every restored byte before recording success. The evidence contains
# validation facts only and no repository URL, object path, credentials or data.
cleanup_restore_payload
RUN_DIR=""

evidence_root_normalized="$(realpath -m -- "$EVIDENCE_ROOT")"
[[ "$evidence_root_normalized" == "$EVIDENCE_ROOT" ]] || fail "the evidence root resolves through a symlink"
install -d -o root -g root -m 0750 -- "$evidence_root_normalized"
[[ "$(realpath -e -- "$evidence_root_normalized")" == "$EVIDENCE_ROOT" ]] || fail "unsafe evidence root"
MARKER="$EVIDENCE_ROOT/restic-restore-$STAMP-${BASHPID}.ok"
MARKER_TMP="$(mktemp -- "$EVIDENCE_ROOT/.restic-restore-$STAMP-XXXXXXXX")"
{
  printf 'status=success\n'
  printf 'completed_at_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf 'snapshot_selector=host-lecturesift-production-tags-lecturesift-production\n'
  printf 'drill_scope=%s\n' "$drill_scope"
  printf 'snapshot_id=%s\n' "$SNAPSHOT_ID"
  printf 'snapshot_time_utc=%s\n' "$SNAPSHOT_TIME_UTC"
  printf 'backup_set_sha256=%s\n' "$backup_set_sha256"
  printf 'repository_id_sha256=%s\n' "$repository_id_sha256"
  printf 'checksums=verified\n'
  printf 'postgres_restore=verified\n'
  printf 'redis_restore=verified\n'
  printf 'redis_aof_startup=verified\n'
  printf 'configuration_snapshot=verified\n'
  printf 'configuration_snapshot_format=lecturesift-configuration-snapshot-v1\n'
  printf 'postgres_validator=postgres-18-bookworm-isolated-restore-manifest\n'
  printf 'redis_validator=redis-7.4-alpine-redis-check-rdb\n'
  printf 'restored_payload_removed=true\n'
  printf 'live_services_touched=false\n'
} >"$MARKER_TMP"
chmod 0644 "$MARKER_TMP"
mv -- "$MARKER_TMP" "$MARKER"

echo "Restic restore rehearsal passed. Evidence: $MARKER"
