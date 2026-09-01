#!/usr/bin/env bash
set -euo pipefail
umask 077
set +x

ROOT_DIR="${LECTURESIFT_ROOT:-/opt/lecturesift}"
ENV_FILE="${LECTURESIFT_ENV_FILE:-/etc/lecturesift/runtime.env}"
API_ENV_FILE="${LECTURESIFT_API_ENV_FILE:-/etc/lecturesift/api.env}"
WORKER_ENV_FILE="${LECTURESIFT_WORKER_ENV_FILE:-/etc/lecturesift/worker.env}"
INSTAGRAM_ENV_FILE="${LECTURESIFT_INSTAGRAM_ENV_FILE:-/etc/lecturesift/instagram.env}"
DB_ENV_FILE="${LECTURESIFT_DB_ENV_FILE:-/etc/lecturesift/postgres.env}"
RESTIC_ENV_FILE="${LECTURESIFT_RESTIC_ENV_FILE:-/etc/lecturesift/restic.env}"
ROLE_ENV_GENERATOR="$ROOT_DIR/deploy/generate_role_envs.py"
CONFIGURATION_SNAPSHOT_TOOL="$ROOT_DIR/deploy/configuration_snapshot.py"
CONFIGURATION_SNAPSHOT_NAME="configuration-snapshot-v1"
RECOVERY_MANIFEST_VERSION=1
RECOVERY_MANIFEST="$ROOT_DIR/deploy/recovery_manifest_v${RECOVERY_MANIFEST_VERSION}.sql"
ALLOWED_BACKUP_ROOT="/var/backups/lecturesift"
REQUESTED_BACKUP_ROOT="${LECTURESIFT_BACKUP_DIR:-$ALLOWED_BACKUP_ROOT}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
RESTIC_HOST="lecturesift-production"
BACKUP_RUNTIME_RECOVERY="$ROOT_DIR/deploy/recover_backup_runtime.sh"

requested_backup_root_normalized="$(realpath -m -- "$REQUESTED_BACKUP_ROOT")"
if [[ "$REQUESTED_BACKUP_ROOT" != "$ALLOWED_BACKUP_ROOT" ||
      "$requested_backup_root_normalized" != "$ALLOWED_BACKUP_ROOT" ]]; then
  echo "Refusing non-canonical backup root." >&2
  exit 1
fi
backup_parent="$(dirname -- "$ALLOWED_BACKUP_ROOT")"
if [[ ! -d "$backup_parent" || -L "$backup_parent" ||
      "$(realpath -e -- "$backup_parent")" != "$backup_parent" ]]; then
  echo "Backup parent must be a canonical real directory." >&2
  exit 1
fi
if [[ -e "$ALLOWED_BACKUP_ROOT" || -L "$ALLOWED_BACKUP_ROOT" ]]; then
  if [[ ! -d "$ALLOWED_BACKUP_ROOT" || -L "$ALLOWED_BACKUP_ROOT" ||
        "$(realpath -e -- "$ALLOWED_BACKUP_ROOT")" != "$ALLOWED_BACKUP_ROOT" ]]; then
    echo "Backup root must be a canonical real directory." >&2
    exit 1
  fi
else
  install -d -o root -g root -m 0700 -- "$ALLOWED_BACKUP_ROOT"
fi
[[ "$(stat -c '%u' -- "$ALLOWED_BACKUP_ROOT")" == "0" ]] || {
  echo "Backup root must be owned by root." >&2
  exit 1
}
backup_root_mode="$(stat -c '%a' -- "$ALLOWED_BACKUP_ROOT")"
(( (8#$backup_root_mode & 8#077) == 0 )) || {
  echo "Backup root must not be accessible by group or others." >&2
  exit 1
}
BACKUP_ROOT="$(realpath -e -- "$ALLOWED_BACKUP_ROOT")"
DEST="$BACKUP_ROOT/$STAMP"
STAGING="$BACKUP_ROOT/.incomplete-$STAMP"
RESTIC_STAGE_ROOT="$BACKUP_ROOT/.restic-staging"
RESTIC_STAGE="$RESTIC_STAGE_ROOT/current"
RESTIC_STAGE_NEXT="$RESTIC_STAGE_ROOT/.next-$STAMP"
# Cloudflare protects restic/data and restic/snapshots for 90 days. Keep every
# snapshot for two additional days so object-upload time, snapshot timestamp
# and daily timer jitter can never make forget race the bucket lock. Automated
# prune is deliberately disabled: prune can delete/repack young pack files
# that are still locked even when the snapshots selected for forgetting are
# old enough. Repository compaction therefore requires the separately reviewed
# prefix-rotation/aged-repository procedure documented in VPS_DEPLOYMENT.md.
RESTIC_OBJECT_LOCK_DAYS=90
RESTIC_FORGET_SAFETY_DAYS=2
RESTIC_KEEP_WITHIN_DAYS=$((RESTIC_OBJECT_LOCK_DAYS + RESTIC_FORGET_SAFETY_DAYS))

[[ "$EUID" -eq 0 ]] || {
  echo "Production backups must run as root." >&2
  exit 1
}
check_private_env() {
  local path="$1" label="$2" mode
  if [[ ! -f "$path" || -L "$path" || "$(stat -c '%u' -- "$path")" != "0" ]]; then
    echo "$label must be a root-owned regular non-symlink file." >&2
    exit 1
  fi
  mode="$(stat -c '%a' -- "$path")"
  case "$mode" in
    400|600) ;;
    *)
      echo "$label must have mode 0400 or 0600." >&2
      exit 1
      ;;
  esac
}
check_private_env "$ENV_FILE" "Runtime environment"
check_private_env "$DB_ENV_FILE" "Database environment"
check_private_env "$RESTIC_ENV_FILE" "Restic environment"
[[ -f "$ROLE_ENV_GENERATOR" && ! -L "$ROLE_ENV_GENERATOR" ]] || {
  echo "Role environment generator is missing or unsafe." >&2
  exit 1
}
[[ -f "$CONFIGURATION_SNAPSHOT_TOOL" && ! -L "$CONFIGURATION_SNAPSHOT_TOOL" && \
   "$(stat -c '%u' -- "$CONFIGURATION_SNAPSHOT_TOOL")" == "0" ]] || {
  echo "Configuration snapshot tool is missing or unsafe." >&2
  exit 1
}
configuration_snapshot_tool_mode="$(stat -c '%a' -- "$CONFIGURATION_SNAPSHOT_TOOL")"
(( (8#$configuration_snapshot_tool_mode & 8#022) == 0 )) || {
  echo "Configuration snapshot tool must not be group/other writable." >&2
  exit 1
}
export LECTURESIFT_ENV_FILE="$ENV_FILE"
export LECTURESIFT_API_ENV_FILE="$API_ENV_FILE"
export LECTURESIFT_WORKER_ENV_FILE="$WORKER_ENV_FILE"
export LECTURESIFT_INSTAGRAM_ENV_FILE="$INSTAGRAM_ENV_FILE"
check_private_env "$API_ENV_FILE" "API environment"
check_private_env "$WORKER_ENV_FILE" "Worker environment"
check_private_env "$INSTAGRAM_ENV_FILE" "Instagram environment"
# Scheduled backup is read-only with respect to role configuration. A source
# edit must pass the full production preflight/reload before this unit can use
# it; never publish role files from a partially validated runtime.env here.
python3 "$ROLE_ENV_GENERATOR" --check

set -a
if ! {
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  # shellcheck disable=SC1090
  source "$DB_ENV_FILE"
  # shellcheck disable=SC1090
  source "$RESTIC_ENV_FILE"
} >/dev/null 2>&1; then
  set +a
  set +x
  echo "A required private environment file could not be loaded." >&2
  exit 1
fi
set +a
set +x

if [[ ! -f "$RESTIC_ENV_FILE" || -z "${RESTIC_REPOSITORY:-}" ||
      -z "${RESTIC_PASSWORD:-}" ]]; then
  echo "Off-site restic configuration is mandatory for a production backup." >&2
  exit 1
fi
case "$RESTIC_REPOSITORY" in
  s3:*) ;;
  *)
    echo "Production backups require an off-site S3-compatible restic repository." >&2
    exit 1
    ;;
esac
if [[ -z "${RESTIC_AWS_ACCESS_KEY_ID:-}" ||
      -z "${RESTIC_AWS_SECRET_ACCESS_KEY:-}" ]]; then
  echo "Production S3 restic credentials are required." >&2
  exit 1
fi
command -v restic >/dev/null 2>&1 || {
  echo "restic is not installed." >&2
  exit 1
}
[[ -f "$RECOVERY_MANIFEST" && ! -L "$RECOVERY_MANIFEST" ]] || {
  echo "The backup schema identity manifest is missing or unsafe." >&2
  exit 1
}
[[ -f "$BACKUP_RUNTIME_RECOVERY" && ! -L "$BACKUP_RUNTIME_RECOVERY" ]] || {
  echo "The backup runtime recovery helper is missing or unsafe." >&2
  exit 1
}

install -d -m 0700 "$STAGING" "$RESTIC_STAGE_ROOT"
exec 9>"$BACKUP_ROOT/.backup.lock"
if ! flock -n 9; then
  echo "Another LectureSift backup is already running." >&2
  exit 1
fi

# Reconcile only strictly named, old incomplete work left by SIGKILL/power loss.
# Complete timestamped backups and the current restic stage are never matched.
find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d \
  -name '.incomplete-????????T??????Z' -mmin +60 \
  -exec rm -rf --one-file-system -- {} +
find "$RESTIC_STAGE_ROOT" -mindepth 1 -maxdepth 1 -type d \
  -name '.next-????????T??????Z' -mmin +60 \
  -exec rm -rf --one-file-system -- {} +

cleanup_staging() {
  rm -rf --one-file-system -- "$STAGING" "$RESTIC_STAGE_NEXT" "$RESTIC_STAGE"
}
cd "$ROOT_DIR"

fence_active="false"
worker_was_running="false"
worker_stopped="false"
snapshot_export_pid=""
snapshot_app_name="lecturesift-backup-snapshot-$STAMP"
snapshot_info_file="/tmp/lecturesift-backup-snapshot-$STAMP"

service_is_running() {
  local service="$1" container_id
  while IFS= read -r container_id; do
    [[ -n "$container_id" ]] || continue
    if [[ "$(docker inspect -f '{{.State.Running}}' "$container_id")" == "true" ]]; then
      return 0
    fi
  done < <(docker compose ps -q "$service")
  return 1
}

queue_is_idle() {
  local state_lock pending_tasks unacked_tasks
  state_lock="$(docker compose exec -T redis redis-cli --raw \
    EXISTS lecturesift:jobs:v2:write-lock | tr -d '\r')" || return 1
  [[ "$state_lock" == "0" ]] || return 1
  if docker compose exec -T redis redis-cli --scan \
    --pattern 'lecturesift:job:*:processing' | grep -q .; then
    return 1
  fi
  pending_tasks="$(docker compose exec -T redis redis-cli --raw LLEN celery | tr -d '\r')"
  unacked_tasks="$(docker compose exec -T redis redis-cli --raw HLEN unacked | tr -d '\r')"
  [[ "$pending_tasks" == "0" && "$unacked_tasks" == "0" ]] || return 1
  docker compose exec -T redis redis-cli --raw GET lecturesift:jobs:v2 \
    | python3 -c 'import json,sys; raw=sys.stdin.read().strip(); payload=json.loads(raw) if raw else {"jobs": {}}; jobs=payload.get("jobs", {}); active=[key for key,value in jobs.items() if str(value.get("status", "")) in {"queued", "working"}]; raise SystemExit(1 if active else 0)'
}

restore_runtime() {
  local original_status="$?"
  local restore_failed="false"
  trap - EXIT
  set +e

  close_backup_snapshot || restore_failed="true"
  restore_service_availability || restore_failed="true"
  cleanup_staging
  if [[ "$restore_failed" == "true" ]]; then
    echo "Backup cleanup could not fully restore the API/worker runtime." >&2
    original_status=1
  fi
  exit "$original_status"
}
trap restore_runtime EXIT

close_backup_snapshot() {
  local remaining="" close_failed="false"
  if [[ -n "${snapshot_app_name:-}" ]]; then
    docker compose exec -T postgres psql --no-psqlrc --quiet \
      --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
      --variable=backup_app="$snapshot_app_name" <<'SQL' >/dev/null 2>&1 || true
SELECT pg_terminate_backend(pid)
FROM pg_stat_activity
WHERE application_name = :'backup_app' AND pid <> pg_backend_pid();
SQL
    remaining="$(docker compose exec -T postgres psql --no-psqlrc --quiet \
      --tuples-only --no-align --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
      --variable=backup_app="$snapshot_app_name" <<'SQL' 2>/dev/null || true
SELECT count(*) FROM pg_stat_activity WHERE application_name = :'backup_app';
SQL
)"
    [[ "$(printf '%s' "$remaining" | tr -d '\r[:space:]')" == "0" ]] || close_failed="true"
  fi
  if [[ -n "${snapshot_export_pid:-}" ]]; then
    for _ in $(seq 1 50); do
      kill -0 "$snapshot_export_pid" >/dev/null 2>&1 || break
      sleep 0.1
    done
    if kill -0 "$snapshot_export_pid" >/dev/null 2>&1; then
      kill "$snapshot_export_pid" >/dev/null 2>&1 || true
    fi
    wait "$snapshot_export_pid" >/dev/null 2>&1 || true
    snapshot_export_pid=""
  fi
  docker compose exec -T postgres rm -f "$snapshot_info_file" >/dev/null 2>&1 || true
  [[ "$close_failed" == "false" ]]
}

restore_service_availability() {
  "$BACKUP_RUNTIME_RECOVERY" || return 1
  worker_stopped="false"
  fence_active="false"
}

activate_backup_fence() {
  local idle="false"
  service_is_running api || {
    echo "The API must be running before the automatic backup fence is applied." >&2
    return 1
  }
  service_is_running worker || {
    echo "The worker must be healthy/running before an automatic backup." >&2
    return 1
  }
  worker_was_running="true"

  # Install an atomic, same-boot, two-hour runtime fence inside the API work
  # volume. The middleware reads it per request, so payment callbacks remain
  # reachable and no API/Caddy container is recreated. A stale fence expires
  # automatically and is ignored after a host reboot.
  fence_active="true"
  docker compose exec -T api python - <<'PY'
import json
import os
import time
from pathlib import Path

work_dir = Path(os.environ.get("LECTURESIFT_WORK_DIR", "/var/lib/lecturesift")).resolve()
marker = (work_dir / ".runtime-maintenance.json").resolve(strict=False)
if marker.parent != work_dir or marker.name != ".runtime-maintenance.json":
    raise SystemExit("Unsafe runtime maintenance marker path")
boot_id = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip().lower()
payload = {
    "version": 1,
    "mode": "drain",
    "expires_at": int(time.time()) + 7200,
    "boot_id": boot_id,
}
temporary = marker.with_name(f"{marker.name}.{os.getpid()}.tmp")
temporary.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
temporary.chmod(0o600)
os.replace(temporary, marker)
PY

  maintenance_mode="$(docker compose exec -T api python -c \
    "import json,urllib.request; print(json.load(urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=5)).get('maintenance_mode',''))" \
    | tr -d '\r')"
  [[ "$maintenance_mode" == "drain" ]] || {
    echo "The live API did not acknowledge the backup drain fence." >&2
    return 1
  }

  for _ in $(seq 1 240); do
    if queue_is_idle; then
      idle="true"
      break
    fi
    sleep 5
  done
  [[ "$idle" == "true" ]] || {
    echo "The queue did not drain within 20 minutes; backup aborted safely." >&2
    return 1
  }

  if [[ "$worker_was_running" == "true" ]]; then
    worker_stopped="true"
    docker compose stop --timeout 600 worker >/dev/null
  fi
  queue_is_idle || {
    echo "The job system changed while the worker was stopping." >&2
    return 1
  }
}

assert_quiescent() {
  local maintenance_mode bgsave_active

  maintenance_mode="$(docker compose exec -T api python -c \
    "import json,urllib.request; print(json.load(urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=5)).get('maintenance_mode',''))" \
    | tr -d '\r')"
  [[ "$maintenance_mode" == "drain" ]] || {
    echo "The automatic backup API fence is not active." >&2
    return 1
  }
  if ! queue_is_idle; then
    echo "Backup refused while queued/working jobs exist or job state is invalid." >&2
    return 1
  fi

  bgsave_active="$(docker compose exec -T redis redis-cli --raw INFO persistence \
    | awk -F: '$1 == "rdb_bgsave_in_progress" {gsub("\\r", "", $2); print $2}')"
  [[ "$bgsave_active" == "0" ]] || {
    echo "Backup refused because another Redis BGSAVE is active." >&2
    return 1
  }
}

# PostgreSQL and Redis represent one durable job system. Hold the application
# in drain with its worker stopped for the entire two-snapshot interval.
activate_backup_fence
assert_quiescent

# Export one MVCC snapshot and keep its read-only transaction open while both
# pg_dump and the identity/data manifest run. Billing callbacks may remain
# reachable in drain mode, but their later commits cannot make metadata describe
# a different database state than the archive.
docker compose exec -T postgres rm -f "$snapshot_info_file" >/dev/null 2>&1 || true
docker compose exec -T -e "PGAPPNAME=$snapshot_app_name" postgres \
  psql --no-psqlrc --quiet --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  >"$STAGING/snapshot-export.log" 2>&1 <<SQL &
\set ON_ERROR_STOP on
BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;
\pset tuples_only on
\pset format unaligned
\o $snapshot_info_file
SELECT pg_backend_pid() || '|' || pg_export_snapshot();
\o
SELECT pg_sleep(21600);
ROLLBACK;
SQL
snapshot_export_pid="$!"
snapshot_info=""
for _ in $(seq 1 100); do
  if docker compose exec -T postgres test -s "$snapshot_info_file" >/dev/null 2>&1; then
    snapshot_info="$(docker compose exec -T postgres cat "$snapshot_info_file" | tr -d '\r\n')"
    break
  fi
  kill -0 "$snapshot_export_pid" >/dev/null 2>&1 || break
  sleep 0.1
done
if [[ ! "$snapshot_info" =~ ^[0-9]+\|[0-9A-Fa-f]+-[0-9A-Fa-f]+-[0-9A-Fa-f]+$ ]]; then
  echo "Could not establish the database backup snapshot." >&2
  exit 1
fi
snapshot_id="${snapshot_info#*|}"

# Reserve enough shared-root capacity for an incompressible dump, Redis data,
# the off-site staging copy and 5 GiB of OS/PostgreSQL/Docker headroom. Existing
# retained backups are already reflected in df's available-byte value.
snapshot_database_size="$(docker compose exec -T postgres psql --no-psqlrc \
  --quiet --tuples-only --no-align --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -c 'SELECT pg_database_size(current_database())' | tr -d '\r[:space:]')"
redis_used_memory="$(docker compose exec -T redis redis-cli --raw INFO memory \
  | awk -F: '$1 == "used_memory" {gsub("\\r", "", $2); print $2}')"
backup_available_bytes="$(df --output=avail -B1 "$BACKUP_ROOT" | awk 'NR == 2 {print $1}')"
[[ "$snapshot_database_size" =~ ^[1-9][0-9]*$ && \
   "$redis_used_memory" =~ ^[0-9]+$ && "$backup_available_bytes" =~ ^[0-9]+$ ]] || {
  echo "Could not determine backup capacity safely." >&2
  exit 1
}
(( snapshot_database_size <= 1000000000000 && redis_used_memory <= 1000000000000 )) || {
  echo "Backup source size exceeds the supported safety bound." >&2
  exit 1
}
backup_required_bytes=$((snapshot_database_size * 5 + redis_used_memory * 3 + 5368709120))
(( backup_available_bytes >= backup_required_bytes )) || {
  echo "Insufficient backup filesystem capacity while preserving 5 GiB of host reserve." >&2
  exit 1
}

docker compose exec -T postgres pg_dump \
  --username "$POSTGRES_USER" \
  --dbname "$POSTGRES_DB" \
  --format custom \
  --no-owner \
  --no-acl \
  --snapshot "$snapshot_id" >"$STAGING/postgres.dump"

# Bind this archive to the LectureSift application schema while the same
# write fence used for pg_dump is still active. The transient report contains
# only aggregate fingerprints/counts and is removed after the identity values
# are written to BACKUP_METADATA.
{
  printf '\\set ON_ERROR_STOP on\n'
  printf 'BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;\n'
  printf "SET TRANSACTION SNAPSHOT '%s';\n" "$snapshot_id"
  cat "$RECOVERY_MANIFEST"
  printf '\nCOMMIT;\n'
} | docker compose exec -T postgres psql --no-psqlrc --quiet \
  --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  >"$STAGING/rehearsal-manifest.txt"
close_backup_snapshot
tr -d '\r' <"$STAGING/rehearsal-manifest.txt" \
  >"$STAGING/rehearsal-manifest.normalized"
mv -- "$STAGING/rehearsal-manifest.normalized" "$STAGING/rehearsal-manifest.txt"
if [[ "$(grep -c '^DATABASE|' "$STAGING/rehearsal-manifest.txt")" != "1" || \
      "$(grep -c '^SCHEMA|' "$STAGING/rehearsal-manifest.txt")" != "1" ]]; then
  echo "The backup manifest did not produce one database and schema identity." >&2
  exit 1
fi
if grep -Eq '^(TABLE_DIFF|UNVALIDATED_FK)\|' "$STAGING/rehearsal-manifest.txt" || \
   awk -F'|' '$1 == "ANOMALY" && $3 != "0" { invalid=1 } END { exit(invalid ? 0 : 1) }' \
     "$STAGING/rehearsal-manifest.txt"; then
  echo "The backup schema/data integrity manifest failed." >&2
  exit 1
fi
schema_line="$(grep '^SCHEMA|' "$STAGING/rehearsal-manifest.txt")"
database_identity_line="$(grep '^DATABASE|' "$STAGING/rehearsal-manifest.txt")"
database_size_line="$(grep '^DATABASE_SIZE|' "$STAGING/rehearsal-manifest.txt")"
database_size_bytes="${database_size_line#DATABASE_SIZE|}"
table_line_count="$(grep -c '^TABLE|' "$STAGING/rehearsal-manifest.txt")"
[[ "$table_line_count" =~ ^[1-9][0-9]*$ ]] || {
  echo "The backup manifest did not produce a data fingerprint." >&2
  exit 1
}
schema_fingerprint_sha256="$(printf '%s\n' "$schema_line" | sha256sum | awk '{print $1}')"
database_identity_sha256="$(printf '%s\n' "$database_identity_line" | sha256sum | awk '{print $1}')"
data_fingerprint_sha256="$(grep '^TABLE|' "$STAGING/rehearsal-manifest.txt" \
  | LC_ALL=C sort | sha256sum | awk '{print $1}')"
schema_manifest_sha256="$(sha256sum "$RECOVERY_MANIFEST" | awk '{print $1}')"
[[ "$schema_fingerprint_sha256" =~ ^[[:xdigit:]]{64}$ && \
   "$database_identity_sha256" =~ ^[[:xdigit:]]{64}$ && \
   "$data_fingerprint_sha256" =~ ^[[:xdigit:]]{64}$ && \
   "$schema_manifest_sha256" =~ ^[[:xdigit:]]{64}$ && \
   "$database_size_bytes" =~ ^[1-9][0-9]*$ ]] || {
  echo "The backup schema identity could not be hashed safely." >&2
  exit 1
}
rm -f -- "$STAGING/rehearsal-manifest.txt"

lastsave_before="$(docker compose exec -T redis redis-cli --raw LASTSAVE | tr -d '\r')"
if (( $(date -u +%s) <= lastsave_before )); then
  sleep 1
fi
bgsave_reply="$(docker compose exec -T redis redis-cli --raw BGSAVE | tr -d '\r')"
[[ "$bgsave_reply" == "Background saving started" ]] || {
  echo "Redis did not start the requested BGSAVE." >&2
  exit 1
}

bgsave_complete="false"
for _ in $(seq 1 180); do
  persistence_info="$(docker compose exec -T redis redis-cli --raw INFO persistence)"
  bgsave_active="$(printf '%s\n' "$persistence_info" \
    | awk -F: '$1 == "rdb_bgsave_in_progress" {gsub("\\r", "", $2); print $2}')"
  bgsave_status="$(printf '%s\n' "$persistence_info" \
    | awk -F: '$1 == "rdb_last_bgsave_status" {gsub("\\r", "", $2); print $2}')"
  lastsave_after="$(docker compose exec -T redis redis-cli --raw LASTSAVE | tr -d '\r')"
  if [[ "$bgsave_active" == "0" ]]; then
    [[ "$bgsave_status" == "ok" ]] || {
      echo "Redis reported a failed background save." >&2
      exit 1
    }
    if (( lastsave_after > lastsave_before )); then
      bgsave_complete="true"
      break
    fi
  fi
  sleep 1
done
[[ "$bgsave_complete" == "true" ]] || {
  echo "Redis BGSAVE did not complete before the safety timeout." >&2
  exit 1
}

docker compose cp redis:/data/dump.rdb "$STAGING/redis-dump.rdb"
redis_version="$(docker compose exec -T redis redis-cli --raw INFO server \
  | awk -F: '$1 == "redis_version" {gsub("\\r", "", $2); print $2}')"
[[ "$redis_version" =~ ^7\.4([.]|$) ]] || {
  echo "Refusing to label a disaster-recovery RDB from an unexpected Redis version." >&2
  exit 1
}
{
  printf 'format=lecturesift-backup-v2\n'
  printf 'application_identity=lecturesift-production\n'
  printf 'application_schema_compatibility=lecturesift-schema-v1\n'
  printf 'schema_manifest_version=%s\n' "$RECOVERY_MANIFEST_VERSION"
  printf 'schema_manifest_sha256=%s\n' "$schema_manifest_sha256"
  printf 'database_identity_sha256=%s\n' "$database_identity_sha256"
  printf 'schema_fingerprint_sha256=%s\n' "$schema_fingerprint_sha256"
  printf 'data_fingerprint_sha256=%s\n' "$data_fingerprint_sha256"
  printf 'database_size_bytes=%s\n' "$database_size_bytes"
  printf 'redis_version=%s\n' "$redis_version"
  printf 'redis_restore_compatibility=redis-7.4-only\n'
} >"$STAGING/BACKUP_METADATA"

assert_quiescent

(cd "$STAGING" && sha256sum postgres.dump redis-dump.rdb BACKUP_METADATA >SHA256SUMS)
chmod 0600 "$STAGING/postgres.dump" "$STAGING/redis-dump.rdb" \
  "$STAGING/BACKUP_METADATA" "$STAGING/SHA256SUMS"
mv "$STAGING" "$DEST"

# The immutable local checkpoint is complete. Restore the worker first and
# only then reopen the API, before the potentially slower off-site upload and
# retention pass. The EXIT trap repeats this safely if either step fails.
restore_service_availability || {
  echo "The checkpoint is local, but worker/API availability could not be safely restored." >&2
  exit 1
}

export AWS_ACCESS_KEY_ID="${RESTIC_AWS_ACCESS_KEY_ID:-}"
export AWS_SECRET_ACCESS_KEY="${RESTIC_AWS_SECRET_ACCESS_KEY:-}"
if ! restic cat config >/dev/null 2>&1; then
  echo "The configured restic repository is not initialized or is inaccessible." >&2
  exit 1
fi
mkdir -m 0700 -- "$RESTIC_STAGE_NEXT"
cp --reflink=auto --preserve=mode,timestamps \
  "$DEST/postgres.dump" "$DEST/redis-dump.rdb" \
  "$DEST/BACKUP_METADATA" "$DEST/SHA256SUMS" "$RESTIC_STAGE_NEXT/"
# Secrets are never added to the persistent local timestamped checkpoint.
# The exact root-only configuration allowlist is copied only into this
# ephemeral private stage, verified, encrypted client-side by Restic below,
# and removed by the EXIT trap on success or failure.
python3 "$CONFIGURATION_SNAPSHOT_TOOL" create \
  --destination "$RESTIC_STAGE_NEXT/$CONFIGURATION_SNAPSHOT_NAME" \
  --deploy-root "$ROOT_DIR"
python3 "$CONFIGURATION_SNAPSHOT_TOOL" verify \
  --snapshot-root "$RESTIC_STAGE_NEXT/$CONFIGURATION_SNAPSHOT_NAME" \
  --deploy-root "$ROOT_DIR" --quiet
rm -rf --one-file-system -- "$RESTIC_STAGE"
mv -- "$RESTIC_STAGE_NEXT" "$RESTIC_STAGE"
restic backup "$RESTIC_STAGE" --host "$RESTIC_HOST" \
  --tag lecturesift --tag production
restic forget --host "$RESTIC_HOST" --tag lecturesift \
  --group-by host,tags --keep-within "${RESTIC_KEEP_WITHIN_DAYS}d" \
  --keep-daily 365 --keep-weekly 104 --keep-monthly 60 --keep-yearly 10

# Clear an earlier alert only after both the new off-site snapshot and its
# retention pass have succeeded. A failed run leaves the marker intact.
rm -f -- /var/lib/lecturesift/backup-alerts/latest-failure

find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d -name '????????T??????Z' -mtime +7 -exec rm -rf -- {} +
echo "Backup completed: $DEST"
