#!/usr/bin/env bash
set -euo pipefail
umask 077

ROOT_DIR="${LECTURESIFT_ROOT:-/opt/lecturesift}"
SOURCE_ENV="${LECTURESIFT_SOURCE_DB_ENV:-/root/.lecturesift-render-source.env}"
DB_ENV="${LECTURESIFT_DB_ENV_FILE:-/etc/lecturesift/postgres.env}"
ALLOWED_REHEARSAL_ROOT="/var/backups/lecturesift/rehearsal"
REQUESTED_REHEARSAL_ROOT="${LECTURESIFT_REHEARSAL_ROOT:-$ALLOWED_REHEARSAL_ROOT}"

if [[ "$(id -u)" != "0" ]]; then
  echo "Run this rehearsal as root." >&2
  exit 1
fi
check_private_env() {
  local path="$1" label="$2" mode
  [[ -f "$path" && ! -L "$path" && "$(stat -c '%u' -- "$path")" == "0" ]] || {
    echo "$label must be a root-owned regular non-symlink file." >&2
    exit 1
  }
  mode="$(stat -c '%a' -- "$path")"
  case "$mode" in
    400|600) ;;
    *) echo "$label must have mode 0400 or 0600." >&2; exit 1 ;;
  esac
}
check_private_env "$SOURCE_ENV" "Source database environment"
check_private_env "$DB_ENV" "Target database environment"
for path in "$SOURCE_ENV" "$DB_ENV" \
  "$ROOT_DIR/deploy/rehearsal_manifest.sql" \
  "$ROOT_DIR/deploy/provision_database_role.sh" \
  "$ROOT_DIR/deploy/release.sh" \
  "$ROOT_DIR/deploy/rehearsal_stack.sh" \
  "$ROOT_DIR/deploy/rehearsal_e2e.py" \
  "$ROOT_DIR/deploy/rehearsal_formats_e2e.py"; do
  if [[ ! -f "$path" || -L "$path" ]]; then
    echo "Missing rehearsal input: $path" >&2
    exit 1
  fi
done

# Canonicalize before mkdir/chmod so an override cannot redirect root-owned
# output through traversal or a symlink into an arbitrary filesystem path.
allowed_root_normalized="$(realpath -m -- "$ALLOWED_REHEARSAL_ROOT")"
[[ "$allowed_root_normalized" == "$ALLOWED_REHEARSAL_ROOT" ]] || {
  echo "The allowed rehearsal root resolves through a symlink." >&2
  exit 1
}
requested_root_normalized="$(realpath -m -- "$REQUESTED_REHEARSAL_ROOT")"
[[ "$REQUESTED_REHEARSAL_ROOT" == "$ALLOWED_REHEARSAL_ROOT" && \
   "$requested_root_normalized" == "$allowed_root_normalized" ]] || {
  echo "The rehearsal root is fixed and cannot be overridden." >&2
  exit 1
}
install -d -o root -g root -m 0700 -- \
  "$allowed_root_normalized" "$requested_root_normalized"
BACKUP_ROOT="$(realpath -e -- "$requested_root_normalized")"
case "$BACKUP_ROOT" in
  "$allowed_root_normalized"|"$allowed_root_normalized"/*) ;;
  *)
    echo "The rehearsal root escaped its fixed safety boundary." >&2
    exit 1
    ;;
esac
exec 8>"$allowed_root_normalized/.rehearsal.lock"
if ! flock -n 8; then
  echo "Another database rehearsal is already running." >&2
  exit 1
fi

# Serialize every operation that allocates backup/clone space or touches the
# live PostgreSQL service. This is the same fixed lock used by backup,
# destructive restore and the restic drill.
SHARED_BACKUP_LOCK_ROOT="/var/backups/lecturesift"
[[ ! -L "$SHARED_BACKUP_LOCK_ROOT" && \
   "$(realpath -e -- "$SHARED_BACKUP_LOCK_ROOT")" == "$SHARED_BACKUP_LOCK_ROOT" && \
   "$(stat -c '%u' -- "$SHARED_BACKUP_LOCK_ROOT")" == "0" ]] || {
  echo "The shared backup/restore lock root is unsafe." >&2
  exit 1
}
shared_lock_mode="$(stat -c '%a' -- "$SHARED_BACKUP_LOCK_ROOT")"
(( (8#$shared_lock_mode & 8#077) == 0 )) || {
  echo "The shared backup/restore lock root must be private to root." >&2
  exit 1
}
exec 9>"$SHARED_BACKUP_LOCK_ROOT/.backup.lock"
if ! flock -n 9; then
  echo "A backup, restore or restic drill is already active." >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$SOURCE_ENV"
# shellcheck disable=SC1090
source "$DB_ENV"
set +a
: "${SOURCE_DATABASE_URL:?Missing SOURCE_DATABASE_URL}"
: "${POSTGRES_USER:?Missing POSTGRES_USER}"
: "${POSTGRES_DB:?Missing POSTGRES_DB}"
if ! python3 - <<'PY'
import os
from urllib.parse import parse_qs, urlsplit

url = urlsplit(os.environ["SOURCE_DATABASE_URL"])
host = (url.hostname or "").lower().rstrip(".")
query = parse_qs(url.query)
valid = (
    url.scheme in {"postgres", "postgresql"}
    and bool(url.username)
    and bool(url.password)
    and bool(url.path.strip("/"))
    and host.endswith(".render.com")
    and host not in {"localhost", "postgres", "127.0.0.1", "::1"}
    and query.get("sslmode", [""])[0] in {"require", "verify-ca", "verify-full"}
)
raise SystemExit(0 if valid else 1)
PY
then
  echo "SOURCE_DATABASE_URL must be the distinct TLS-protected external Render PostgreSQL source." >&2
  exit 1
fi

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
run_dir="$BACKUP_ROOT/$stamp"
rehearsal_db="lecturesift_rehearsal_${stamp//[^0-9]/}"
mkdir -m 0700 -- "$run_dir"
rehearsal_db_created="false"

drop_rehearsal_database() {
  [[ "$rehearsal_db_created" == "true" ]] || return 0
  docker compose --project-directory "$ROOT_DIR" --file "$ROOT_DIR/compose.yaml" \
    exec -T postgres dropdb -U "$POSTGRES_USER" --maintenance-db postgres \
    --if-exists --force "$rehearsal_db" || return 1
  if docker compose --project-directory "$ROOT_DIR" --file "$ROOT_DIR/compose.yaml" \
    exec -T postgres psql -U "$POSTGRES_USER" -d postgres -Atqc \
    "SELECT 1 FROM pg_database WHERE datname = '$rehearsal_db'" | grep -q '^1$'; then
    echo "The disposable rehearsal database still exists after cleanup." >&2
    return 1
  fi
  rehearsal_db_created="false"
}

cleanup_rehearsal_containers() {
  local container
  for container in lecturesift-api-rehearsal lecturesift-worker-rehearsal \
    lecturesift-redis-rehearsal lecturesift-egress-proxy-rehearsal; do
    if docker container inspect "$container" >/dev/null 2>&1; then
      docker rm -f "$container" >/dev/null || return 1
    fi
  done
}

cleanup_rehearsal_work_volumes() {
  local volume
  for volume in lecturesift-api-rehearsal-work lecturesift-worker-rehearsal-work; do
    if docker volume inspect "$volume" >/dev/null 2>&1; then
      docker volume rm "$volume" >/dev/null || return 1
    fi
  done
}

cleanup_rehearsal() {
  local original_status="$?" cleanup_failed="false"
  trap - EXIT
  set +e
  docker exec lecturesift-postgres-1 rm -f /tmp/rehearsal_manifest.sql >/dev/null 2>&1 || true
  rm -f -- "$run_dir/render.dump"
  cleanup_rehearsal_containers || cleanup_failed="true"
  cleanup_rehearsal_work_volumes || cleanup_failed="true"
  drop_rehearsal_database || cleanup_failed="true"
  find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d \
    -name '????????T??????Z' -mtime +30 -exec rm -rf -- {} + || cleanup_failed="true"
  if [[ "$cleanup_failed" == "true" ]]; then
    echo "Rehearsal cleanup failed; the isolated database must be removed before another run." >&2
    original_status=1
  fi
  exit "$original_status"
}
trap cleanup_rehearsal EXIT

reconcile_stale_rehearsal_state() {
  local database suffix created_epoch now active_count dump_path dump_name run_path
  local stale_database_output stale_dump_output
  now="$(date -u +%s)"

  # Holding .rehearsal.lock proves no healthy orchestrator is active. Remove
  # only the fixed rehearsal containers/volumes, then inspect every database
  # name and age before destructive cleanup of power-loss residue.
  cleanup_rehearsal_containers
  cleanup_rehearsal_work_volumes
  stale_database_output="$(
    docker compose --project-directory "$ROOT_DIR" --file "$ROOT_DIR/compose.yaml" \
      exec -T postgres psql -U "$POSTGRES_USER" -d postgres -Atqc \
      "SELECT datname FROM pg_database WHERE datname ~ '^lecturesift_rehearsal_[0-9]{14}$' ORDER BY datname" \
      | tr -d '\r'
  )" || {
    echo "Stale rehearsal databases could not be enumerated safely." >&2
    return 1
  }
  stale_databases=()
  if [[ -n "$stale_database_output" ]]; then
    mapfile -t stale_databases <<<"$stale_database_output"
  fi
  for database in "${stale_databases[@]}"; do
    [[ "$database" =~ ^lecturesift_rehearsal_([0-9]{14})$ ]] || {
      echo "Refusing an unsafe stale rehearsal database name." >&2
      return 1
    }
    suffix="${BASH_REMATCH[1]}"
    created_epoch="$(date -u -d \
      "${suffix:0:8} ${suffix:8:2}:${suffix:10:2}:${suffix:12:2}" +%s 2>/dev/null)" || {
      echo "A stale rehearsal database has an invalid timestamp." >&2
      return 1
    }
    (( created_epoch <= now - 3600 )) || {
      echo "A recent rehearsal database exists without its orchestrator; inspect it before retrying." >&2
      return 1
    }
    active_count="$(docker compose --project-directory "$ROOT_DIR" --file "$ROOT_DIR/compose.yaml" \
      exec -T postgres psql -U "$POSTGRES_USER" -d postgres -Atqc \
      "SELECT count(*) FROM pg_stat_activity WHERE datname = '$database'" | tr -d '\r[:space:]')"
    [[ "$active_count" == "0" ]] || {
      echo "A stale rehearsal database still has an attached client; refusing cleanup." >&2
      return 1
    }
    docker compose --project-directory "$ROOT_DIR" --file "$ROOT_DIR/compose.yaml" \
      exec -T postgres dropdb -U "$POSTGRES_USER" --maintenance-db postgres \
      --if-exists "$database"
  done

  stale_dump_output="$(docker compose --project-directory "$ROOT_DIR" --file "$ROOT_DIR/compose.yaml" \
    exec -T postgres find /tmp -maxdepth 1 -type f \
    -name 'lecturesift_rehearsal_*.dump' -mmin +60 -print | tr -d '\r')" || {
    echo "Stale rehearsal dump files could not be enumerated safely." >&2
    return 1
  }
  if [[ -n "$stale_dump_output" ]]; then
    while IFS= read -r dump_path; do
      dump_name="${dump_path##*/}"
      [[ "$dump_name" =~ ^lecturesift_rehearsal_[0-9]{14}[.]dump$ ]] || {
        echo "Refusing an unsafe stale rehearsal dump path." >&2
        return 1
      }
      docker compose --project-directory "$ROOT_DIR" --file "$ROOT_DIR/compose.yaml" \
        exec -T postgres rm -f -- "$dump_path"
    done <<<"$stale_dump_output"
  fi

  shopt -s nullglob
  for run_path in "$BACKUP_ROOT"/????????T??????Z; do
    [[ -d "$run_path" && ! -L "$run_path" ]] || continue
    if [[ -f "$run_path/render.dump" && \
          "$(stat -c '%Y' -- "$run_path/render.dump")" -le $((now - 3600)) ]]; then
      rm -f -- "$run_path/render.dump"
    fi
  done
  shopt -u nullglob
}

reconcile_stale_rehearsal_state

docker run --rm --user 0:0 \
  --env SOURCE_DATABASE_URL \
  --volume "$run_dir:/backup" \
  --volume "$ROOT_DIR/deploy:/probe:ro" \
  postgres:18-bookworm \
  sh -ec '
    psql "$SOURCE_DATABASE_URL" -v ON_ERROR_STOP=1 \
      -f /probe/rehearsal_manifest.sql > /backup/source-before.txt
  '

source_database_size="$(tr -d '\r' <"$run_dir/source-before.txt" \
  | sed -n 's/^DATABASE_SIZE|//p')"
[[ "$(tr -d '\r' <"$run_dir/source-before.txt" | grep -c '^DATABASE_SIZE|')" == "1" && \
   "$source_database_size" =~ ^[1-9][0-9]*$ ]] || {
  echo "The source manifest did not provide one valid database size." >&2
  exit 1
}
(( source_database_size <= 1000000000000 )) || {
  echo "The source database exceeds the supported rehearsal-size bound." >&2
  exit 1
}
rehearsal_required_bytes=$((source_database_size * 2 + 5368709120))
rehearsal_root_available_bytes="$(df --output=avail -B1 -- "$BACKUP_ROOT" | awk 'NR == 2 {print $1}')"
postgres_volume_available_bytes="$(docker compose --project-directory "$ROOT_DIR" \
  --file "$ROOT_DIR/compose.yaml" exec -T postgres \
  df --output=avail -B1 /var/lib/postgresql | awk 'NR == 2 {print $1}' | tr -d '\r')"
[[ "$rehearsal_root_available_bytes" =~ ^[0-9]+$ && \
   "$postgres_volume_available_bytes" =~ ^[0-9]+$ ]] || {
  echo "Could not prove rehearsal filesystem capacity." >&2
  exit 1
}
(( rehearsal_root_available_bytes >= rehearsal_required_bytes )) || {
  echo "Insufficient rehearsal-root capacity while preserving 5 GiB of host reserve." >&2
  exit 1
}
(( postgres_volume_available_bytes >= rehearsal_required_bytes )) || {
  echo "Insufficient PostgreSQL-volume capacity for a disposable clone plus 5 GiB reserve." >&2
  exit 1
}

docker run --rm --user 0:0 \
  --env SOURCE_DATABASE_URL \
  --volume "$run_dir:/backup" \
  postgres:18-bookworm \
  sh -ec '
    pg_dump "$SOURCE_DATABASE_URL" --format=custom --no-owner --no-acl \
      --serializable-deferrable --file=/backup/render.dump
  '

docker run --rm --user 0:0 \
  --env SOURCE_DATABASE_URL \
  --volume "$run_dir:/backup" \
  --volume "$ROOT_DIR/deploy:/probe:ro" \
  postgres:18-bookworm \
  sh -ec '
    psql "$SOURCE_DATABASE_URL" -v ON_ERROR_STOP=1 \
      -f /probe/rehearsal_manifest.sql > /backup/source-after.txt
  '

sha256sum "$run_dir/render.dump" > "$run_dir/render.dump.sha256"
pg_dump_size="$(stat -c '%s' "$run_dir/render.dump")"
docker run --rm --user 0:0 \
  --volume "$run_dir:/backup:ro" postgres:18-bookworm \
  pg_restore --list /backup/render.dump > "$run_dir/render.dump.list"

if docker compose --project-directory "$ROOT_DIR" --file "$ROOT_DIR/compose.yaml" \
  exec -T postgres psql -U "$POSTGRES_USER" -d postgres -Atqc \
  "SELECT 1 FROM pg_database WHERE datname = '$rehearsal_db'" | grep -q '^1$'; then
  echo "Refusing to overwrite existing rehearsal database." >&2
  exit 1
fi

rehearsal_db_created="true"
docker compose --project-directory "$ROOT_DIR" --file "$ROOT_DIR/compose.yaml" \
  exec -T postgres createdb -U "$POSTGRES_USER" -T template0 \
  --encoding=UTF8 --locale=en_US.UTF-8 "$rehearsal_db"
docker compose --project-directory "$ROOT_DIR" --file "$ROOT_DIR/compose.yaml" \
  exec -T postgres pg_restore -U "$POSTGRES_USER" --exit-on-error \
  --single-transaction --no-owner --no-acl --dbname="$rehearsal_db" \
  <"$run_dir/render.dump"
rm -f -- "$run_dir/render.dump"

docker cp "$ROOT_DIR/deploy/rehearsal_manifest.sql" \
  lecturesift-postgres-1:/tmp/rehearsal_manifest.sql
docker compose --project-directory "$ROOT_DIR" --file "$ROOT_DIR/compose.yaml" \
  exec -T postgres psql -U "$POSTGRES_USER" -d "$rehearsal_db" \
  -v ON_ERROR_STOP=1 -f /tmp/rehearsal_manifest.sql > "$run_dir/target.txt"
docker exec lecturesift-postgres-1 rm -f /tmp/rehearsal_manifest.sql

printf '%s\n' "$rehearsal_db" > "$run_dir/database-name.txt"
safe_pattern='^(DATABASE|SCHEMA|TABLE|ANOMALY|STATUS|UNVALIDATED_FK)\|'
grep -E "$safe_pattern" "$run_dir/source-before.txt" | sort > "$run_dir/source-before.safe"
grep -E "$safe_pattern" "$run_dir/source-after.txt" | sort > "$run_dir/source-after.safe"
grep -E "$safe_pattern" "$run_dir/target.txt" | sort > "$run_dir/target.safe"
if ! diff -u "$run_dir/source-before.safe" "$run_dir/source-after.safe" \
  > "$run_dir/source-live.diff"; then
  echo "Source data changed during the rehearsal; discard this run and retry from a stable source." >&2
  exit 1
fi
if ! diff -u "$run_dir/source-before.safe" "$run_dir/target.safe" \
  > "$run_dir/source-target.diff"; then
  echo "Stable source and restored target manifests differ." >&2
  exit 1
fi
if awk -F'|' '$1 == "ANOMALY" && $3 != "0" { invalid=1 } END { exit(invalid ? 0 : 1) }' \
  "$run_dir/target.txt"; then
  echo "Rehearsal data-integrity anomalies must be resolved before cutover." >&2
  exit 1
fi

printf 'rehearsal_database=%s\n' "$rehearsal_db"
printf 'dump_bytes=%s\n' "$pg_dump_size"
printf 'dump_sha256=%s\n' "$(cut -d' ' -f1 "$run_dir/render.dump.sha256")"
printf 'source_changed_during_rehearsal=false\n'
source_schema="$(grep '^SCHEMA|' "$run_dir/source-before.txt")"
target_schema="$(grep '^SCHEMA|' "$run_dir/target.txt")"
if [[ "$source_schema" != "$target_schema" ]]; then
  echo "Source and restored schema fingerprints differ." >&2
  exit 1
fi
source_database="$(grep '^DATABASE|' "$run_dir/source-before.txt")"
target_database="$(grep '^DATABASE|' "$run_dir/target.txt")"
if [[ "$source_database" != "$target_database" ]]; then
  echo "Source and restored PostgreSQL version/encoding/collation identities differ." >&2
  exit 1
fi
grep '^DATABASE|' "$run_dir/target.txt"
grep '^SCHEMA|' "$run_dir/target.txt"
grep '^TABLE|' "$run_dir/target.txt"
grep '^ANOMALY|' "$run_dir/target.txt"
if grep -Eq '^(TABLE_DIFF|UNVALIDATED_FK)\|' "$run_dir/target.txt"; then
  echo "Rehearsal integrity gate failed; inspect the root-only manifest." >&2
  exit 1
fi

# Keep the full clone alive only inside this orchestrated command. The stack
# and a real account/R2/worker/result E2E run must complete before success; the
# EXIT trap removes every rehearsal container and force-drops the clone on
# both success and failure, so no production-data clone is left indefinitely.
LECTURESIFT_PROVISION_DATABASE="$rehearsal_db" \
  "$ROOT_DIR/deploy/provision_database_role.sh"
LECTURESIFT_REHEARSAL_ORCHESTRATED=YES \
  "$ROOT_DIR/deploy/rehearsal_stack.sh" "$rehearsal_db"
docker cp "$ROOT_DIR/deploy/rehearsal_e2e.py" \
  lecturesift-api-rehearsal:/tmp/lecturesift-rehearsal-e2e.py
docker exec lecturesift-api-rehearsal \
  python /tmp/lecturesift-rehearsal-e2e.py >"$run_dir/application-e2e.json"
docker cp "$ROOT_DIR/deploy/rehearsal_formats_e2e.py" \
  lecturesift-api-rehearsal:/tmp/lecturesift-rehearsal-formats-e2e.py
docker exec lecturesift-api-rehearsal \
  python /tmp/lecturesift-rehearsal-formats-e2e.py >"$run_dir/formats-e2e.json"
cleanup_rehearsal_containers
cleanup_rehearsal_work_volumes
drop_rehearsal_database
printf 'database_dropped=true\n' >> "$run_dir/database-name.txt"
echo "PostgreSQL and application rehearsal completed; the disposable database and containers were removed without opening public traffic."
