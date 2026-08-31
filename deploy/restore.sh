#!/usr/bin/env bash
set -euo pipefail

if [[ "$(id -u)" != "0" ]]; then
  echo "Run this disaster-recovery restore as root." >&2
  exit 1
fi
if [[ "${LECTURESIFT_RESTORE_CONFIRM:-}" != "YES" ]]; then
  echo "Set LECTURESIFT_RESTORE_CONFIRM=YES to run a destructive restore." >&2
  exit 1
fi
if [[ "${LECTURESIFT_REDIS_RESTORE_SOURCE_VERSION:-}" != "7.4" ]]; then
  echo "Set LECTURESIFT_REDIS_RESTORE_SOURCE_VERSION=7.4 only for a same-version VPS disaster-recovery backup." >&2
  echo "Never use this script to import a Render Valkey/source-migration RDB." >&2
  exit 1
fi

if [[ $# -ne 1 || ! -d "$1" ]]; then
  echo "Usage: LECTURESIFT_RESTORE_CONFIRM=YES $0 /path/to/backup" >&2
  exit 1
fi

ROOT_DIR="${LECTURESIFT_ROOT:-/opt/lecturesift}"
ENV_FILE="${LECTURESIFT_ENV_FILE:-/etc/lecturesift/runtime.env}"
API_ENV_FILE="${LECTURESIFT_API_ENV_FILE:-/etc/lecturesift/api.env}"
WORKER_ENV_FILE="${LECTURESIFT_WORKER_ENV_FILE:-/etc/lecturesift/worker.env}"
INSTAGRAM_ENV_FILE="${LECTURESIFT_INSTAGRAM_ENV_FILE:-/etc/lecturesift/instagram.env}"
DB_ENV_FILE="${LECTURESIFT_DB_ENV_FILE:-/etc/lecturesift/postgres.env}"
RESTIC_ENV_FILE="${LECTURESIFT_RESTIC_ENV_FILE:-/etc/lecturesift/restic.env}"
SOURCE="$(realpath "$1")"
BACKUP_LOCK_ROOT="/var/backups/lecturesift"
ALLOWED_VALIDATION_ROOT="/var/lib/lecturesift/restore-validation"
VALIDATION_RUN_DIR=""
[[ "$SOURCE" != *$'\n'* && "$SOURCE" != *$'\r'* ]] || {
  echo "Backup path contains control characters." >&2
  exit 1
}
case "$SOURCE" in
  "$BACKUP_LOCK_ROOT"/[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]T[0-9][0-9][0-9][0-9][0-9][0-9]Z) ;;
  *)
    echo "Restore source must be one timestamped set directly under $BACKUP_LOCK_ROOT." >&2
    exit 1
    ;;
esac
[[ ! -L "$SOURCE" && "$(stat -c '%u' -- "$SOURCE")" == "0" ]] || {
  echo "Restore source must be a root-owned real directory." >&2
  exit 1
}
source_mode="$(stat -c '%a' -- "$SOURCE")"
(( (8#$source_mode & 8#022) == 0 )) || {
  echo "Restore source must not be writable by group or others." >&2
  exit 1
}

backup_lock_parent="$(dirname -- "$BACKUP_LOCK_ROOT")"
[[ -d "$backup_lock_parent" && ! -L "$backup_lock_parent" && \
   "$(realpath -e -- "$backup_lock_parent")" == "$backup_lock_parent" ]] || {
  echo "The shared backup/restore lock parent is unsafe." >&2
  exit 1
}
install -d -o root -g root -m 0700 -- "$BACKUP_LOCK_ROOT"
[[ ! -L "$BACKUP_LOCK_ROOT" && "$(realpath -e -- "$BACKUP_LOCK_ROOT")" == "$BACKUP_LOCK_ROOT" && \
   "$(stat -c '%u' -- "$BACKUP_LOCK_ROOT")" == "0" ]] || {
  echo "The shared backup/restore lock directory is unsafe." >&2
  exit 1
}
backup_lock_mode="$(stat -c '%a' -- "$BACKUP_LOCK_ROOT")"
(( (8#$backup_lock_mode & 8#077) == 0 )) || {
  echo "The shared backup/restore lock directory must be private to root." >&2
  exit 1
}
exec 9>"$BACKUP_LOCK_ROOT/.backup.lock"
flock -n 9 || {
  echo "A backup or restore operation is already active." >&2
  exit 1
}

for required_file in SHA256SUMS postgres.dump redis-dump.rdb BACKUP_METADATA; do
  if [[ ! -f "$SOURCE/$required_file" || -L "$SOURCE/$required_file" ]]; then
    echo "Backup is missing $required_file" >&2
    exit 1
  fi
  [[ "$(stat -c '%u' -- "$SOURCE/$required_file")" == "0" ]] || {
    echo "Backup payload $required_file must be owned by root." >&2
    exit 1
  }
  required_mode="$(stat -c '%a' -- "$SOURCE/$required_file")"
  (( (8#$required_mode & 8#022) == 0 )) || {
    echo "Backup payload $required_file must not be writable by group or others." >&2
    exit 1
  }
done
SOURCE_IDENTITY="$({
  stat -c '%d:%i:%s:%Y:%u:%a:%n' -- \
    "$SOURCE/SHA256SUMS" "$SOURCE/postgres.dump" \
    "$SOURCE/redis-dump.rdb" "$SOURCE/BACKUP_METADATA"
} | sha256sum | awk '{print $1}')"
[[ "$SOURCE_IDENTITY" =~ ^[[:xdigit:]]{64}$ ]] || {
  echo "Restore source inode identity could not be recorded." >&2
  exit 1
}

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
    echo "SHA256SUMS contains an unexpected or unsafe entry." >&2
    exit 1
  fi
done <"$SOURCE/SHA256SUMS"
if [[ "$checksum_line_count" -ne 3 || "$postgres_checksum_seen" -ne 1 ||
      "$redis_checksum_seen" -ne 1 || "$metadata_checksum_seen" -ne 1 ]]; then
  echo "SHA256SUMS must contain exactly the three expected backup entries." >&2
  exit 1
fi
(cd "$SOURCE" && sha256sum --check --strict -- SHA256SUMS)
SELECTED_BACKUP_SET_SHA256="$(sha256sum "$SOURCE/SHA256SUMS" | awk '{print $1}')"
[[ "$SELECTED_BACKUP_SET_SHA256" =~ ^[[:xdigit:]]{64}$ ]] || {
  echo "The selected backup-set identity could not be hashed safely." >&2
  exit 1
}

metadata_format="$(sed -n 's/^format=//p' "$SOURCE/BACKUP_METADATA")"
metadata_application="$(sed -n 's/^application_identity=//p' "$SOURCE/BACKUP_METADATA")"
metadata_schema_compatibility="$(sed -n 's/^application_schema_compatibility=//p' "$SOURCE/BACKUP_METADATA")"
metadata_manifest_version="$(sed -n 's/^schema_manifest_version=//p' "$SOURCE/BACKUP_METADATA")"
metadata_manifest_sha256="$(sed -n 's/^schema_manifest_sha256=//p' "$SOURCE/BACKUP_METADATA")"
metadata_database_identity_sha256="$(sed -n 's/^database_identity_sha256=//p' "$SOURCE/BACKUP_METADATA")"
metadata_schema_sha256="$(sed -n 's/^schema_fingerprint_sha256=//p' "$SOURCE/BACKUP_METADATA")"
metadata_data_sha256="$(sed -n 's/^data_fingerprint_sha256=//p' "$SOURCE/BACKUP_METADATA")"
metadata_database_size="$(sed -n 's/^database_size_bytes=//p' "$SOURCE/BACKUP_METADATA")"
metadata_redis_version="$(sed -n 's/^redis_version=//p' "$SOURCE/BACKUP_METADATA")"
metadata_compatibility="$(sed -n 's/^redis_restore_compatibility=//p' "$SOURCE/BACKUP_METADATA")"
case "$metadata_manifest_version" in
  1) RECOVERY_MANIFEST="$ROOT_DIR/deploy/recovery_manifest_v1.sql" ;;
  *)
    echo "The backup references an unsupported recovery manifest version." >&2
    exit 1
    ;;
esac
if [[ "$metadata_format" != "lecturesift-backup-v2" ||
      "$metadata_application" != "lecturesift-production" ||
      "$metadata_schema_compatibility" != "lecturesift-schema-v1" ||
      "$metadata_manifest_version" != "1" ||
      ! "$metadata_manifest_sha256" =~ ^[[:xdigit:]]{64}$ ||
      ! "$metadata_database_identity_sha256" =~ ^[[:xdigit:]]{64}$ ||
      ! "$metadata_schema_sha256" =~ ^[[:xdigit:]]{64}$ ||
      ! "$metadata_data_sha256" =~ ^[[:xdigit:]]{64}$ ||
      ! "$metadata_database_size" =~ ^[1-9][0-9]*$ ||
      ! "$metadata_redis_version" =~ ^7\.4([.]|$) ||
      "$metadata_compatibility" != "redis-7.4-only" ]]; then
  echo "Backup metadata does not prove LectureSift schema identity and Redis 7.4 compatibility." >&2
  exit 1
fi

[[ -f "$RECOVERY_MANIFEST" && ! -L "$RECOVERY_MANIFEST" ]] || {
  echo "The PostgreSQL schema identity manifest is missing or unsafe." >&2
  exit 1
}
current_manifest_sha256="$(sha256sum "$RECOVERY_MANIFEST" | awk '{print $1}')"
if [[ "$current_manifest_sha256" != "$metadata_manifest_sha256" ]]; then
  echo "The backup was created with a different schema identity manifest." >&2
  exit 1
fi

# Use a fixed root-owned workspace only for the small validation report. The
# database pages themselves live in a dynamically sized, memory-capped tmpfs,
# so validation cannot fill the production filesystem.
validation_parent="$(dirname -- "$ALLOWED_VALIDATION_ROOT")"
if [[ ! -d "$validation_parent" || -L "$validation_parent" || \
      "$(realpath -e -- "$validation_parent")" != "$validation_parent" ]]; then
  echo "The restore validation parent must be a canonical real directory." >&2
  exit 1
fi
validation_root_normalized="$(realpath -m -- "$ALLOWED_VALIDATION_ROOT")"
[[ "$validation_root_normalized" == "$ALLOWED_VALIDATION_ROOT" ]] || {
  echo "The restore validation root resolves through an unsafe path." >&2
  exit 1
}
install -d -o root -g root -m 0700 -- "$ALLOWED_VALIDATION_ROOT"
[[ ! -L "$ALLOWED_VALIDATION_ROOT" && \
   "$(realpath -e -- "$ALLOWED_VALIDATION_ROOT")" == "$ALLOWED_VALIDATION_ROOT" ]] || {
  echo "The restore validation root is unsafe." >&2
  exit 1
}
VALIDATION_RUN_DIR="$(mktemp -d -- "$ALLOWED_VALIDATION_ROOT/restore-validation-XXXXXXXX")"
chmod 0700 "$VALIDATION_RUN_DIR"
cleanup_validation() {
  local resolved
  [[ -n "${VALIDATION_RUN_DIR:-}" && -e "$VALIDATION_RUN_DIR" ]] || return 0
  resolved="$(realpath -e -- "$VALIDATION_RUN_DIR")" || return 1
  case "$resolved" in
    "$ALLOWED_VALIDATION_ROOT"/restore-validation-*) ;;
    *)
      echo "Refusing unsafe restore-validation cleanup target." >&2
      return 1
      ;;
  esac
  rm -rf --one-file-system -- "$resolved"
}
trap 'cleanup_validation || true' EXIT

# Validate both archives before stopping a service or dropping the live
# database. These containers are network-isolated and mount the backup read-only.
docker image inspect postgres:18-bookworm >/dev/null 2>&1 || {
  echo "postgres:18-bookworm must already be present for restore validation." >&2
  exit 1
}
docker image inspect redis:7.4-alpine >/dev/null 2>&1 || {
  echo "redis:7.4-alpine must already be present for restore validation." >&2
  exit 1
}
docker run --rm --pull=never --network none --read-only --cap-drop ALL \
  --security-opt no-new-privileges --pids-limit 64 \
  -v "$SOURCE:/backup:ro" --entrypoint pg_restore \
  postgres:18-bookworm --list /backup/postgres.dump >/dev/null
postgres_uid="$(docker run --rm --pull=never --network none --read-only \
  --entrypoint id postgres:18-bookworm -u postgres)"
postgres_gid="$(docker run --rm --pull=never --network none --read-only \
  --entrypoint id postgres:18-bookworm -g postgres)"
[[ "$postgres_uid" =~ ^[0-9]+$ && "$postgres_gid" =~ ^[0-9]+$ ]] || {
  echo "Could not resolve the disposable PostgreSQL image user." >&2
  exit 1
}
if (( metadata_database_size > 1000000000000 )); then
  echo "The recorded database size exceeds the supported validation bound." >&2
  exit 1
fi
validation_tmpfs_bytes=$((metadata_database_size * 2 + 536870912))
validation_container_memory_bytes=$((validation_tmpfs_bytes + 1073741824))
validation_host_required_bytes=$((validation_container_memory_bytes + 2147483648))
validation_host_available_bytes="$(awk '/^MemAvailable:/ {printf "%.0f\n", $2 * 1024}' /proc/meminfo)"
if [[ ! "$validation_host_available_bytes" =~ ^[0-9]+$ ]] ||
   (( validation_host_available_bytes < validation_host_required_bytes )); then
  echo "Insufficient host memory for isolated PostgreSQL validation with a 2 GiB reserve." >&2
  exit 1
fi
docker run --rm --pull=never --network none --read-only \
  --user "$postgres_uid:$postgres_gid" --cap-drop ALL \
  --security-opt no-new-privileges --pids-limit 256 \
  --memory "${validation_container_memory_bytes}b" \
  --memory-swap "${validation_container_memory_bytes}b" \
  --tmpfs "/var/lib/postgresql:rw,nosuid,nodev,size=$validation_tmpfs_bytes,uid=$postgres_uid,gid=$postgres_gid,mode=0700" \
  --tmpfs "/tmp:rw,nosuid,nodev,size=128m,uid=$postgres_uid,gid=$postgres_gid,mode=0700" \
  -v "$SOURCE:/backup:ro" \
  -v "$RECOVERY_MANIFEST:/probe/recovery_manifest.sql:ro" \
  --entrypoint bash postgres:18-bookworm \
  -euo pipefail -c '
    export PGDATA=/var/lib/postgresql/data
    initdb --no-sync --auth-local=trust --auth-host=reject \
      --locale=en_US.UTF-8 --encoding=UTF8 >/dev/null
    pg_ctl -o "-c listen_addresses= -c unix_socket_directories=/tmp -c fsync=off" \
      -w start >/dev/null
    trap '\''pg_ctl -m immediate -w stop >/dev/null 2>&1 || true'\'' EXIT
    createdb --host=/tmp --template=template0 --encoding=UTF8 restore_validation
    pg_restore --host=/tmp --exit-on-error --single-transaction \
      --no-owner --no-acl --dbname=restore_validation /backup/postgres.dump
    psql --host=/tmp --dbname=restore_validation -v ON_ERROR_STOP=1 \
      -f /probe/recovery_manifest.sql > /tmp/rehearsal-manifest.out
    grep -q "^SCHEMA|" /tmp/rehearsal-manifest.out
    grep -q "^TABLE|" /tmp/rehearsal-manifest.out
    ! grep -Eq "^(TABLE_DIFF|UNVALIDATED_FK)\\|" /tmp/rehearsal-manifest.out
    awk -F"|" '\''$1 == "ANOMALY" && $3 != "0" { invalid=1 } END { exit invalid }'\'' \
      /tmp/rehearsal-manifest.out
    cat /tmp/rehearsal-manifest.out
  ' >"$VALIDATION_RUN_DIR/rehearsal-manifest.out"
restored_database_count="$(tr -d '\r' <"$VALIDATION_RUN_DIR/rehearsal-manifest.out" | grep -c '^DATABASE|')"
restored_database_line="$(tr -d '\r' <"$VALIDATION_RUN_DIR/rehearsal-manifest.out" | grep '^DATABASE|')"
restored_schema_count="$(tr -d '\r' <"$VALIDATION_RUN_DIR/rehearsal-manifest.out" | grep -c '^SCHEMA|')"
restored_schema_line="$(tr -d '\r' <"$VALIDATION_RUN_DIR/rehearsal-manifest.out" | grep '^SCHEMA|')"
restored_table_count="$(tr -d '\r' <"$VALIDATION_RUN_DIR/rehearsal-manifest.out" | grep -c '^TABLE|')"
restored_data_sha256="$(tr -d '\r' <"$VALIDATION_RUN_DIR/rehearsal-manifest.out" \
  | grep '^TABLE|' | LC_ALL=C sort | sha256sum | awk '{print $1}')"
if [[ "$restored_database_count" != "1" || "$restored_schema_count" != "1" || \
      ! "$restored_table_count" =~ ^[1-9][0-9]*$ || \
      "$(printf '%s\n' "$restored_database_line" | sha256sum | awk '{print $1}')" != \
      "$metadata_database_identity_sha256" || \
      "$(printf '%s\n' "$restored_schema_line" | sha256sum | awk '{print $1}')" != \
      "$metadata_schema_sha256" || "$restored_data_sha256" != "$metadata_data_sha256" ]]; then
  echo "The restored database identity/schema/data fingerprints do not match backup metadata." >&2
  exit 1
fi
docker run --rm --pull=never --network none --read-only --cap-drop ALL \
  --security-opt no-new-privileges --pids-limit 64 \
  -v "$SOURCE:/backup:ro" --entrypoint redis-check-rdb \
  redis:7.4-alpine /backup/redis-dump.rdb >/dev/null

cleanup_validation
VALIDATION_RUN_DIR=""
trap - EXIT

# A restore must not discover stale or over-privileged role files after it has
# already stopped traffic or replaced data. Run the complete production gate,
# including exact role-environment derivation, restic reachability and recovery
# evidence validation, before the first service or database mutation.
if [[ ! -f "$ROOT_DIR/deploy/preflight.sh" || -L "$ROOT_DIR/deploy/preflight.sh" ]]; then
  echo "The production preflight script is missing or unsafe." >&2
  exit 1
fi
if [[ ! -f "$ROOT_DIR/deploy/provision_database_role.sh" || \
      -L "$ROOT_DIR/deploy/provision_database_role.sh" ]]; then
  echo "The PostgreSQL application-role provisioning script is missing or unsafe." >&2
  exit 1
fi
if [[ ! -f "$ROOT_DIR/deploy/redis_rdb_to_aof.sh" || \
      -L "$ROOT_DIR/deploy/redis_rdb_to_aof.sh" || \
      ! -f "$ROOT_DIR/deploy/redis.conf" || -L "$ROOT_DIR/deploy/redis.conf" ]]; then
  echo "The Redis RDB-to-AOF recovery assets are missing or unsafe." >&2
  exit 1
fi
if [[ "${LECTURESIFT_RECOVERY_BOOTSTRAP_OVERRIDE:-}" == "YES" ]]; then
  echo "A destructive restore refuses the recovery bootstrap override." >&2
  exit 1
fi
export LECTURESIFT_ROOT="$ROOT_DIR"
export LECTURESIFT_ENV_FILE="$ENV_FILE"
export LECTURESIFT_API_ENV_FILE="$API_ENV_FILE"
export LECTURESIFT_WORKER_ENV_FILE="$WORKER_ENV_FILE"
export LECTURESIFT_INSTAGRAM_ENV_FILE="$INSTAGRAM_ENV_FILE"
export LECTURESIFT_DB_ENV_FILE="$DB_ENV_FILE"
export LECTURESIFT_RESTIC_ENV_FILE="$RESTIC_ENV_FILE"
export LECTURESIFT_REQUIRED_BACKUP_SET_SHA256="$SELECTED_BACKUP_SET_SHA256"
export LECTURESIFT_PREFLIGHT_CONTEXT=disaster-restore-validation
"$ROOT_DIR/deploy/preflight.sh"
bash "$ROOT_DIR/deploy/release.sh" build
unset LECTURESIFT_REQUIRED_BACKUP_SET_SHA256
unset LECTURESIFT_PREFLIGHT_CONTEXT

# Preflight can involve remote repository checks. Re-prove the exact immutable
# payload set immediately before the first destructive boundary so an
# unprivileged local process cannot replace a previously validated archive.
CURRENT_SOURCE_IDENTITY="$({
  stat -c '%d:%i:%s:%Y:%u:%a:%n' -- \
    "$SOURCE/SHA256SUMS" "$SOURCE/postgres.dump" \
    "$SOURCE/redis-dump.rdb" "$SOURCE/BACKUP_METADATA"
} | sha256sum | awk '{print $1}')"
[[ "$CURRENT_SOURCE_IDENTITY" == "$SOURCE_IDENTITY" ]] || {
  echo "Restore source changed after validation; no production data was mutated." >&2
  exit 1
}
(cd "$SOURCE" && sha256sum --check --strict -- SHA256SUMS >/dev/null) || {
  echo "Restore source checksums changed after validation; no production data was mutated." >&2
  exit 1
}
[[ "$(sha256sum "$SOURCE/SHA256SUMS" | awk '{print $1}')" == "$SELECTED_BACKUP_SET_SHA256" ]] || {
  echo "Restore source identity changed after validation; no production data was mutated." >&2
  exit 1
}

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
# shellcheck disable=SC1090
source "$DB_ENV_FILE"
set +a

cd "$ROOT_DIR"

fail_stop_required="false"
restore_failed() {
  local status="$?"
  local running_services=""
  trap - ERR
  if [[ "$fail_stop_required" == "true" ]]; then
    # A partially successful Compose operation can leave one application
    # container running. Always repeat the stop, then report only what an
    # explicit state query proves.
    docker compose stop caddy api worker >/dev/null 2>&1 || true
    if running_services="$(docker compose ps --status running --services \
      caddy api worker 2>/dev/null)" && [[ -z "$running_services" ]]; then
      echo "Restore failed after the fail-stop boundary; Caddy, API and worker are confirmed stopped. Database services may remain running for recovery." >&2
    else
      echo "Restore failed after the fail-stop boundary, and the stopped state of Caddy/API/worker could not be confirmed. Inspect the host immediately and keep traffic fenced." >&2
    fi
  else
    echo "Restore validation failed before any service or live-data mutation." >&2
  fi
  exit "$status"
}
trap restore_failed ERR

fail_stop_required="true"
docker compose stop caddy api worker redis
docker compose cp "$SOURCE/postgres.dump" postgres:/tmp/lecturesift.dump
docker compose exec -T postgres dropdb \
  --if-exists \
  --username "$POSTGRES_USER" \
  --maintenance-db postgres \
  "$POSTGRES_DB"
docker compose exec -T postgres createdb \
  --username "$POSTGRES_USER" \
  --maintenance-db postgres \
  "$POSTGRES_DB"
docker compose exec -T postgres pg_restore \
  --username "$POSTGRES_USER" \
  --dbname "$POSTGRES_DB" \
  --clean --if-exists --no-owner --no-acl --exit-on-error --single-transaction \
  /tmp/lecturesift.dump
docker compose exec -T postgres rm -f /tmp/lecturesift.dump

# pg_restore intentionally ignores source ACLs. Recreate and verify the
# least-privilege application grants on the new database before any API or
# worker container is allowed to connect.
"$ROOT_DIR/deploy/provision_database_role.sh"

redis_conversion_output="$(docker run --rm --pull=never --network none --user 0 \
  -v lecturesift-redis-data:/data \
  -v "$SOURCE:/restore:ro" \
  -v "$ROOT_DIR/deploy:/probe:ro" \
  --entrypoint sh redis:7.4-alpine \
  /probe/redis_rdb_to_aof.sh)"
restored_redis_dbsize="$(printf '%s\n' "$redis_conversion_output" \
  | sed -n 's/^RESTORED_DBSIZE=//p')"
[[ "$(printf '%s\n' "$redis_conversion_output" | grep -c '^RESTORED_DBSIZE=')" == "1" && \
   "$restored_redis_dbsize" =~ ^[0-9]+$ ]] || {
  echo "Redis RDB-to-AOF conversion did not produce one verified key count." >&2
  exit 1
}

# API/worker files are disposable caches; R2 and Redis are authoritative.
# Clear them so an older local artifact can never shadow the restored state.
docker run --rm --user 0 \
  -v lecturesift-api-work:/api-work \
  -v lecturesift-worker-work:/worker-work \
  --entrypoint sh redis:7.4-alpine \
  -c 'find /api-work /worker-work -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +'

# Recover private state first. Public ingress stays stopped until both the API
# and its consumer are independently healthy.
docker compose up -d --wait --wait-timeout 600 postgres redis
live_redis_dbsize="$(docker compose exec -T redis redis-cli --raw DBSIZE | tr -d '\r')"
[[ "$live_redis_dbsize" == "$restored_redis_dbsize" ]] || {
  echo "Normal Redis startup did not preserve the converted RDB dataset; ingress remains stopped." >&2
  exit 1
}
docker compose up -d --no-deps --wait --wait-timeout 600 api worker
docker compose up -d --no-deps --wait --wait-timeout 180 caddy
fail_stop_required="false"
trap - ERR
echo "Restore completed from $SOURCE"
