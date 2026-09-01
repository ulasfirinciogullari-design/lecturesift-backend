#!/usr/bin/env bash
set +x
set -euo pipefail
umask 077

[[ "$#" -le 1 ]] || {
  echo "Usage: rehearsal_restore.sh [--reconcile-only|--reconcile-stale]" >&2
  exit 1
}
case "${1:-}" in
  "") rehearsal_mode="run" ;;
  --reconcile-only) rehearsal_mode="reconcile-only" ;;
  --reconcile-stale) rehearsal_mode="reconcile-stale" ;;
  *) echo "Usage: rehearsal_restore.sh [--reconcile-only|--reconcile-stale]" >&2; exit 1 ;;
esac

ROOT_DIR="${LECTURESIFT_ROOT:-/opt/lecturesift}"
SOURCE_ENV="${LECTURESIFT_SOURCE_DB_ENV:-/root/.lecturesift-render-source.env}"
DB_ENV="${LECTURESIFT_DB_ENV_FILE:-/etc/lecturesift/postgres.env}"
ALLOWED_REHEARSAL_ROOT="/var/backups/lecturesift/rehearsal"
REQUESTED_REHEARSAL_ROOT="${LECTURESIFT_REHEARSAL_ROOT:-$ALLOWED_REHEARSAL_ROOT}"
PROVENANCE_ROOT="/var/lib/lecturesift/rehearsal-provenance"
SOURCE_POSTGRES_TRANSPORT="$ROOT_DIR/deploy/source_postgres_transport.py"

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
  "$ROOT_DIR/deploy/generate_rehearsal_envs.py" \
  "$ROOT_DIR/deploy/prove_rehearsal_r2_isolation.py" \
  "$ROOT_DIR/deploy/rehearsal_synthetic_audio.py" \
  "$ROOT_DIR/deploy/rehearsal_e2e.py" \
  "$ROOT_DIR/deploy/rehearsal_formats_e2e.py" \
  "$ROOT_DIR/deploy/rehearsal_purge_e2e.py" \
  "$SOURCE_POSTGRES_TRANSPORT" \
  "$ROOT_DIR/deploy/verify_schema_transition.py" \
  "$ROOT_DIR/deploy/schema_contract_payment_provider_sessions_v1.txt"; do
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

# This fixed root-only registry closes the CREATE DATABASE -> COMMENT crash
# window. A marker is atomically installed before createdb and is removed only
# after both the database and its three cluster-global roles are proven absent.
provenance_root_normalized="$(realpath -m -- "$PROVENANCE_ROOT")"
[[ "$provenance_root_normalized" == "$PROVENANCE_ROOT" ]] || {
  echo "The rehearsal provenance root resolves through a symlink." >&2
  exit 1
}
if [[ -e "$PROVENANCE_ROOT" || -L "$PROVENANCE_ROOT" ]]; then
  [[ -d "$PROVENANCE_ROOT" && ! -L "$PROVENANCE_ROOT" ]] || {
    echo "The rehearsal provenance root is not a safe directory." >&2
    exit 1
  }
else
  install -d -o root -g root -m 0700 -- "$PROVENANCE_ROOT"
fi
[[ "$(realpath -e -- "$PROVENANCE_ROOT")" == "$PROVENANCE_ROOT" &&
   "$(stat -c '%u:%g' -- "$PROVENANCE_ROOT")" == "0:0" &&
   "$(stat -c '%a' -- "$PROVENANCE_ROOT")" == "700" ]] || {
  echo "The rehearsal provenance root must be fixed, root-owned and mode 0700." >&2
  exit 1
}

source_pg_exec() {
  python3 "$SOURCE_POSTGRES_TRANSPORT" exec-libpq-docker \
    --source-env "$SOURCE_ENV" -- "$@"
}

SOURCE_PG_DOCKER_ENV=(
  --env PGHOST --env PGPORT --env PGDATABASE --env PGUSER
  --env PGSSLMODE --env PGSSLROOTCERT
  --env PGCONNECT_TIMEOUT
)
python3 "$SOURCE_POSTGRES_TRANSPORT" validate --source-env "$SOURCE_ENV" \
  >/dev/null || {
  echo "The Render source transport contract is invalid." >&2
  exit 1
}

set -a
# shellcheck disable=SC1090
source "$DB_ENV"
set +a
: "${POSTGRES_USER:?Missing POSTGRES_USER}"
: "${POSTGRES_DB:?Missing POSTGRES_DB}"

stamp="${LECTURESIFT_EXPECTED_REHEARSAL_RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"
[[ "$stamp" =~ ^[0-9]{8}T[0-9]{6}Z$ ]] || {
  echo "The exact rehearsal run identity is invalid." >&2
  exit 1
}
run_dir="$BACKUP_ROOT/$stamp"
rehearsal_db="lecturesift_rehearsal_${stamp//[^0-9]/}"
rehearsal_suffix="${rehearsal_db#lecturesift_rehearsal_}"
rehearsal_owner_role="lecturesift_rehearsal_owner_$rehearsal_suffix"
rehearsal_api_role="lecturesift_rehearsal_api_$rehearsal_suffix"
rehearsal_worker_role="lecturesift_rehearsal_worker_$rehearsal_suffix"
rehearsal_database_comment="lecturesift.rehearsal-db:v2:$rehearsal_db"
rehearsal_role_comment="lecturesift.rehearsal-role:v2:$rehearsal_db"
rehearsal_provenance_marker="$PROVENANCE_ROOT/$rehearsal_db.provenance"
rehearsal_db_created="false"

rehearsal_provenance_payload() {
  local database="$1" suffix run_id
  [[ "$database" =~ ^lecturesift_rehearsal_([0-9]{14})$ ]] || return 1
  suffix="${BASH_REMATCH[1]}"
  run_id="${suffix:0:8}T${suffix:8:6}Z"
  printf '%s\n' \
    'format=lecturesift-rehearsal-provenance-v2' \
    "run_id=$run_id" \
    "database=$database" \
    "database_comment=lecturesift.rehearsal-db:v2:$database" \
    "owner_role=lecturesift_rehearsal_owner_$suffix" \
    "api_role=lecturesift_rehearsal_api_$suffix" \
    "worker_role=lecturesift_rehearsal_worker_$suffix" \
    "role_comment=lecturesift.rehearsal-role:v2:$database"
}

validate_rehearsal_provenance_marker() {
  local database="$1" marker mode
  [[ "$database" =~ ^lecturesift_rehearsal_[0-9]{14}$ ]] || {
    echo "Refusing an unsafe rehearsal provenance identity." >&2
    return 1
  }
  marker="$PROVENANCE_ROOT/$database.provenance"
  [[ -f "$marker" && ! -L "$marker" &&
     "$(realpath -e -- "$marker")" == "$marker" &&
     "$(stat -c '%u:%g' -- "$marker")" == "0:0" &&
     "$(stat -c '%h' -- "$marker")" == "1" ]] || {
    echo "The rehearsal provenance marker is missing or unsafe: $database" >&2
    return 1
  }
  mode="$(stat -c '%a' -- "$marker")"
  [[ "$mode" == "600" ]] || {
    echo "The rehearsal provenance marker must have mode 0600: $database" >&2
    return 1
  }
  cmp --silent <(rehearsal_provenance_payload "$database") "$marker" || {
    echo "The rehearsal provenance marker content does not match its identity." >&2
    return 1
  }
}

create_rehearsal_provenance_marker() {
  local temporary
  [[ ! -e "$rehearsal_provenance_marker" &&
     ! -L "$rehearsal_provenance_marker" ]] || {
    echo "A rehearsal provenance marker already exists for this run." >&2
    return 1
  }
  temporary="$(mktemp -- "$PROVENANCE_ROOT/.provenance-XXXXXXXX")"
  rehearsal_provenance_payload "$rehearsal_db" >"$temporary"
  chmod 0600 "$temporary"
  chown root:root "$temporary"
  mv -T -- "$temporary" "$rehearsal_provenance_marker"
  validate_rehearsal_provenance_marker "$rehearsal_db"
  sync -f -- "$rehearsal_provenance_marker"
  sync -f -- "$PROVENANCE_ROOT"
}

cleanup_rehearsal_roles_for_database() {
  local database="$1" suffix owner_role api_role worker_role expected_comment role_output
  local role_name role_description
  [[ "$database" =~ ^lecturesift_rehearsal_([0-9]{14})$ ]] || {
    echo "Refusing an unsafe rehearsal role cleanup target." >&2
    return 1
  }
  suffix="${BASH_REMATCH[1]}"
  owner_role="lecturesift_rehearsal_owner_$suffix"
  api_role="lecturesift_rehearsal_api_$suffix"
  worker_role="lecturesift_rehearsal_worker_$suffix"
  expected_comment="lecturesift.rehearsal-role:v2:$database"
  role_output="$(
    docker compose --project-directory "$ROOT_DIR" --file "$ROOT_DIR/compose.yaml" \
      exec -T postgres psql --no-psqlrc --quiet --tuples-only --no-align \
      --username "$POSTGRES_USER" --dbname postgres \
      --variable=owner_role="$owner_role" --variable=api_role="$api_role" \
      --variable=worker_role="$worker_role" <<'SQL'
SELECT rolname || '|' || coalesce(shobj_description(oid, 'pg_authid'), '')
FROM pg_roles
WHERE rolname IN (:'owner_role', :'api_role', :'worker_role')
ORDER BY rolname;
SQL
  )" || return 1
  if [[ -n "$(printf '%s' "$role_output" | tr -d '\r[:space:]')" ]]; then
    validate_rehearsal_provenance_marker "$database" || return 1
  fi
  while IFS='|' read -r role_name role_description; do
    [[ -n "$role_name" ]] || continue
    [[ ( "$role_name" == "$owner_role" || "$role_name" == "$api_role" ||
         "$role_name" == "$worker_role" ) &&
       "$role_description" == "$expected_comment" ]] || {
      echo "A rehearsal-like role lacks matching ownership provenance." >&2
      return 1
    }
  done <<<"$(printf '%s' "$role_output" | tr -d '\r')"
  docker compose --project-directory "$ROOT_DIR" --file "$ROOT_DIR/compose.yaml" \
    exec -T postgres psql --no-psqlrc --quiet --set=ON_ERROR_STOP=1 \
    --username "$POSTGRES_USER" --dbname postgres \
    --variable=owner_role="$owner_role" --variable=api_role="$api_role" \
    --variable=worker_role="$worker_role" \
    --variable=expected_comment="$expected_comment" <<'SQL'
BEGIN;
SELECT EXISTS (
  SELECT 1
  FROM pg_roles
  WHERE rolname IN (:'owner_role', :'api_role', :'worker_role')
    AND coalesce(shobj_description(oid, 'pg_authid'), '') <> :'expected_comment'
) AS unsafe_role
\gset
\if :unsafe_role
  \warn 'A rehearsal-like role lacks matching ownership provenance.'
  \quit 3
\endif
SELECT format('DROP ROLE %I', :'api_role')
WHERE EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'api_role')
\gexec
SELECT format('DROP ROLE %I', :'worker_role')
WHERE EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'worker_role')
\gexec
SELECT format('DROP ROLE %I', :'owner_role')
WHERE EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'owner_role')
\gexec
COMMIT;
SQL
}

drop_rehearsal_database() {
  local database_exists database_comment database_owner database_record
  [[ "$rehearsal_db_created" == "true" ]] || return 0
  database_record="$(
    docker compose --project-directory "$ROOT_DIR" --file "$ROOT_DIR/compose.yaml" \
      exec -T postgres psql --no-psqlrc --quiet --tuples-only --no-align \
      --username "$POSTGRES_USER" --dbname postgres \
      --variable=database_name="$rehearsal_db" <<'SQL'
SELECT count(*)::text || '|' ||
       coalesce(max(shobj_description(d.oid, 'pg_database')), '') || '|' ||
       coalesce(max(r.rolname), '')
FROM pg_database d
JOIN pg_roles r ON r.oid = d.datdba
WHERE d.datname = :'database_name';
SQL
  )" || return 1
  IFS='|' read -r database_exists database_comment database_owner \
    <<<"$(printf '%s' "$database_record" | tr -d '\r\n')"
  if [[ "$database_exists" == "0" ]]; then
    rehearsal_db_created="false"
    return 0
  fi
  [[ "$database_exists" == "1" ]] || return 1
  validate_rehearsal_provenance_marker "$rehearsal_db" || return 1
  [[ -z "$database_comment" ||
     "$database_comment" == "$rehearsal_database_comment" ]] || {
    echo "The rehearsal database lacks matching ownership provenance; refusing deletion." >&2
    return 1
  }
  [[ "$database_owner" == "$rehearsal_owner_role" ]] || {
    echo "The rehearsal database is not owned by its bound clone owner; refusing deletion." >&2
    return 1
  }
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

remove_rehearsal_provenance_if_clear() {
  local database="$1" suffix owner_role api_role worker_role identity_counts
  local database_count role_count
  [[ "$database" =~ ^lecturesift_rehearsal_([0-9]{14})$ ]] || return 1
  suffix="${BASH_REMATCH[1]}"
  [[ -e "$PROVENANCE_ROOT/$database.provenance" ||
     -L "$PROVENANCE_ROOT/$database.provenance" ]] || return 0
  validate_rehearsal_provenance_marker "$database" || return 1
  owner_role="lecturesift_rehearsal_owner_$suffix"
  api_role="lecturesift_rehearsal_api_$suffix"
  worker_role="lecturesift_rehearsal_worker_$suffix"
  identity_counts="$(
    docker compose --project-directory "$ROOT_DIR" --file "$ROOT_DIR/compose.yaml" \
      exec -T postgres psql --no-psqlrc --quiet --tuples-only --no-align \
      --username "$POSTGRES_USER" --dbname postgres \
      --variable=database_name="$database" \
      --variable=owner_role="$owner_role" --variable=api_role="$api_role" \
      --variable=worker_role="$worker_role" <<'SQL'
SELECT (SELECT count(*) FROM pg_database WHERE datname = :'database_name')::text || '|' ||
       (SELECT count(*) FROM pg_roles
        WHERE rolname IN (:'owner_role', :'api_role', :'worker_role'))::text;
SQL
  )" || return 1
  IFS='|' read -r database_count role_count \
    <<<"$(printf '%s' "$identity_counts" | tr -d '\r\n')"
  [[ "$database_count" == "0" && "$role_count" == "0" ]] || {
    echo "Rehearsal provenance cannot be removed while its database or roles exist." >&2
    return 1
  }
  rm -f -- "$PROVENANCE_ROOT/$database.provenance"
  sync -f -- "$PROVENANCE_ROOT"
}

cleanup_rehearsal_containers() {
  local expected_run="${1:-}" container labels run_label rehearsal_label purpose_label
  for container in lecturesift-api-rehearsal lecturesift-worker-rehearsal \
    lecturesift-redis-rehearsal lecturesift-egress-proxy-api-rehearsal \
    lecturesift-egress-proxy-worker-rehearsal \
    lecturesift-migration-rehearsal lecturesift-source-postgres-rehearsal; do
    if docker container inspect "$container" >/dev/null 2>&1; then
      labels="$(docker container inspect --format \
        '{{ index .Config.Labels "lecturesift.rehearsal" }}|{{ index .Config.Labels "lecturesift.rehearsal.run" }}|{{ index .Config.Labels "lecturesift.rehearsal.purpose" }}' \
        "$container")" || return 1
      IFS='|' read -r rehearsal_label run_label purpose_label <<<"$labels"
      [[ "$rehearsal_label" == "true" && "$run_label" =~ ^[0-9]{14}$ && \
         ( -z "$expected_run" || "$run_label" == "$expected_run" ) ]] || {
        echo "Refusing to delete an unlabeled or foreign fixed-name rehearsal container." >&2
        return 1
      }
      [[ "$container" != "lecturesift-migration-rehearsal" || \
         "$purpose_label" == "candidate-database-migration" ]] || {
        echo "Refusing to delete a candidate migration container with the wrong purpose." >&2
        return 1
      }
      [[ "$container" != "lecturesift-source-postgres-rehearsal" || \
         "$purpose_label" == "source-postgres-client" ]] || {
        echo "Refusing to delete a source PostgreSQL client with the wrong purpose." >&2
        return 1
      }
      docker rm -f "$container" >/dev/null || return 1
    fi
  done
}

cleanup_rehearsal_work_volumes() {
  local expected_run="${1:-}" volume labels run_label rehearsal_label
  for volume in lecturesift-api-rehearsal-work lecturesift-worker-rehearsal-work; do
    if docker volume inspect "$volume" >/dev/null 2>&1; then
      labels="$(docker volume inspect --format \
        '{{ index .Labels "lecturesift.rehearsal" }}|{{ index .Labels "lecturesift.rehearsal.run" }}' \
        "$volume")" || return 1
      IFS='|' read -r rehearsal_label run_label <<<"$labels"
      [[ "$rehearsal_label" == "true" && "$run_label" =~ ^[0-9]{14}$ && \
         ( -z "$expected_run" || "$run_label" == "$expected_run" ) ]] || {
        echo "Refusing to delete an unlabeled or foreign fixed-name rehearsal volume." >&2
        return 1
      }
      docker volume rm "$volume" >/dev/null || return 1
    fi
  done
}

cleanup_rehearsal_proxy_networks() {
  local expected_run="${1:-}" network labels run_label rehearsal_label purpose_label internal driver scope endpoints postgres_identity
  for network in lecturesift_rehearsal_backend lecturesift_rehearsal_api_proxy \
    lecturesift_rehearsal_worker_proxy lecturesift_rehearsal_migration; do
    if docker network inspect "$network" >/dev/null 2>&1; then
      labels="$(docker network inspect --format \
        '{{ index .Labels "lecturesift.rehearsal" }}|{{ index .Labels "lecturesift.rehearsal.run" }}|{{ index .Labels "lecturesift.rehearsal.purpose" }}' \
        "$network")" || return 1
      IFS='|' read -r rehearsal_label run_label purpose_label <<<"$labels"
      internal="$(docker network inspect --format '{{.Internal}}' "$network")" || return 1
      driver="$(docker network inspect --format '{{.Driver}}' "$network")" || return 1
      scope="$(docker network inspect --format '{{.Scope}}' "$network")" || return 1
      [[ "$rehearsal_label" == "true" && "$run_label" =~ ^[0-9]{14}$ && \
         "$internal" == "true" && "$driver" == "bridge" && "$scope" == "local" && \
         ( -z "$expected_run" || "$run_label" == "$expected_run" ) ]] || {
        echo "Refusing to delete an unlabeled, non-local or foreign rehearsal network." >&2
        return 1
      }
      [[ "$network" != "lecturesift_rehearsal_migration" || \
         "$purpose_label" == "candidate-database-migration" ]] || {
        echo "Refusing to delete a candidate migration network with the wrong purpose." >&2
        return 1
      }
      if [[ "$network" == "lecturesift_rehearsal_backend" || \
            "$network" == "lecturesift_rehearsal_migration" ]]; then
        endpoints="$(docker network inspect --format \
          '{{range .Containers}}{{println .Name}}{{end}}' "$network")" || return 1
        [[ -z "$endpoints" || "$endpoints" == "lecturesift-postgres-1" ]] || {
          echo "Refusing to detach an unknown rehearsal-backend endpoint." >&2
          return 1
        }
        if [[ "$endpoints" == "lecturesift-postgres-1" ]]; then
          postgres_identity="$(docker container inspect --format \
            '{{ index .Config.Labels "com.docker.compose.project" }}|{{ index .Config.Labels "com.docker.compose.service" }}|{{.State.Running}}' \
            lecturesift-postgres-1 2>/dev/null || true)"
          [[ "$postgres_identity" == "lecturesift|postgres|true" ]] || return 1
          docker network disconnect "$network" lecturesift-postgres-1 >/dev/null || return 1
        fi
      fi
      docker network rm "$network" >/dev/null || return 1
    fi
  done
}

cleanup_expired_rehearsal_runs() {
  local listing candidate resolved name mounts target mode
  local -a candidates=()
  listing="$(find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d \
    -name '????????T??????Z' -mtime +30 -print)" || return 1
  [[ -n "$listing" ]] || return 0
  mapfile -t candidates <<<"$listing"
  mounts="$(findmnt -rn -o TARGET)" || return 1
  for candidate in "${candidates[@]}"; do
    [[ -n "$candidate" && -d "$candidate" && ! -L "$candidate" ]] || return 1
    name="${candidate##*/}"
    [[ "$name" =~ ^[0-9]{8}T[0-9]{6}Z$ && \
       "$candidate" == "$BACKUP_ROOT/$name" && "$candidate" != "$run_dir" ]] || \
      return 1
    resolved="$(realpath -e -- "$candidate")" || return 1
    mode="$(stat -c '%u:%g:%a' -- "$candidate")" || return 1
    [[ "$resolved" == "$candidate" && "$mode" == "0:0:700" ]] || return 1
    while IFS= read -r target; do
      [[ "$target" == "$resolved" || "$target" == "$resolved/"* ]] && return 1
    done <<<"$mounts"
  done
  for candidate in "${candidates[@]}"; do
    rm -rf --one-file-system -- "$candidate" || return 1
  done
}

cleanup_rehearsal() {
  local original_status="$?" cleanup_failed="false"
  trap - EXIT
  set +e
  docker exec lecturesift-postgres-1 rm -f /tmp/rehearsal_manifest.sql >/dev/null 2>&1 || true
  rm -f -- "$run_dir/render.dump" \
    "$run_dir/rehearsal-api.env" "$run_dir/rehearsal-worker.env" \
    "$run_dir/rehearsal-api-squid.conf" \
    "$run_dir/rehearsal-worker-squid.conf" \
    "$run_dir/rehearsal-synthetic-lecture.mp3"
  cleanup_rehearsal_containers "$rehearsal_suffix" || cleanup_failed="true"
  cleanup_rehearsal_proxy_networks "$rehearsal_suffix" || cleanup_failed="true"
  cleanup_rehearsal_work_volumes "$rehearsal_suffix" || cleanup_failed="true"
  drop_rehearsal_database || cleanup_failed="true"
  cleanup_rehearsal_roles_for_database "$rehearsal_db" || cleanup_failed="true"
  remove_rehearsal_provenance_if_clear "$rehearsal_db" || cleanup_failed="true"
  cleanup_expired_rehearsal_runs || cleanup_failed="true"
  if [[ "$cleanup_failed" == "true" ]]; then
    echo "Rehearsal cleanup failed; the isolated database must be removed before another run." >&2
    original_status=1
  fi
  unset rehearsal_owner_password rehearsal_api_password rehearsal_worker_password
  unset rehearsal_owner_database_url rehearsal_api_database_url rehearsal_worker_database_url
  exit "$original_status"
}

reconcile_orphaned_provenance_markers() {
  local now="$1" database suffix created_epoch provenance_path provenance_name
  local identity_counts database_count role_count
  local -a provenance_entries=() provenance_databases=()

  # A crash can leave only the pre-created marker (before createdb), or the
  # marker after both database and roles were removed. Accept no unknown entry
  # in this fixed directory, and retire an old valid marker only after absence
  # of all three bound cluster identities is proven again at removal time.
  shopt -s nullglob dotglob
  provenance_entries=("$PROVENANCE_ROOT"/*)
  shopt -u nullglob dotglob
  for provenance_path in "${provenance_entries[@]}"; do
    provenance_name="${provenance_path##*/}"
    [[ "$provenance_name" =~ ^(lecturesift_rehearsal_[0-9]{14})[.]provenance$ &&
       -f "$provenance_path" && ! -L "$provenance_path" ]] || {
      echo "The rehearsal provenance registry contains an unknown entry." >&2
      return 1
    }
    database="${BASH_REMATCH[1]}"
    validate_rehearsal_provenance_marker "$database" || return 1
    suffix="${database#lecturesift_rehearsal_}"
    created_epoch="$(date -u -d \
      "${suffix:0:8} ${suffix:8:2}:${suffix:10:2}:${suffix:12:2}" +%s 2>/dev/null)" || {
      echo "A rehearsal provenance marker has an invalid timestamp." >&2
      return 1
    }
    (( created_epoch <= now - 3600 )) || {
      echo "A recent rehearsal provenance marker requires operator inspection." >&2
      return 1
    }
    identity_counts="$(
      docker compose --project-directory "$ROOT_DIR" --file "$ROOT_DIR/compose.yaml" \
        exec -T postgres psql --no-psqlrc --quiet --tuples-only --no-align \
        --username "$POSTGRES_USER" --dbname postgres \
        --variable=database_name="$database" \
        --variable=owner_role="lecturesift_rehearsal_owner_$suffix" \
        --variable=api_role="lecturesift_rehearsal_api_$suffix" \
        --variable=worker_role="lecturesift_rehearsal_worker_$suffix" <<'SQL'
SELECT (SELECT count(*) FROM pg_database WHERE datname = :'database_name')::text || '|' ||
       (SELECT count(*) FROM pg_roles
        WHERE rolname IN (:'owner_role', :'api_role', :'worker_role'))::text;
SQL
    )" || return 1
    IFS='|' read -r database_count role_count \
      <<<"$(printf '%s' "$identity_counts" | tr -d '\r\n')"
    [[ "$database_count" == "0" && "$role_count" == "0" ]] || {
      echo "A registered rehearsal database or role escaped stale inventory cleanup." >&2
      return 1
    }
    provenance_databases+=("$database")
  done

  # Validation is deliberately two-phase: a later recent or malformed entry
  # must block before any earlier safe marker is removed.
  for database in "${provenance_databases[@]}"; do
    remove_rehearsal_provenance_if_clear "$database"
  done
}

reconcile_rehearsal_provenance_only() {
  local identity_count now residue

  # This mode is an outer-gate primitive, not stale database cleanup. It may
  # remove only an old marker whose exact database and roles are already gone.
  # Any cluster identity must be inspected by an operator or the normal fully
  # audited cleanup path; reconcile-only never drops databases or roles.
  identity_count="$(
    docker compose --project-directory "$ROOT_DIR" --file "$ROOT_DIR/compose.yaml" \
      exec -T postgres psql --no-psqlrc --quiet --tuples-only --no-align \
      --username "$POSTGRES_USER" --dbname postgres <<'SQL'
SELECT
  (SELECT count(*) FROM pg_database
   WHERE datname ~ '^lecturesift_rehearsal_[0-9]{14}$') +
  (SELECT count(*) FROM pg_roles
    WHERE rolname ~ '^lecturesift_rehearsal_(owner|api|worker)_[0-9]{14}$');
SQL
  )" || {
    echo "Rehearsal identities could not be enumerated safely." >&2
    return 1
  }
  [[ "$(printf '%s' "$identity_count" | tr -d '\r[:space:]')" == "0" ]] || {
    echo "A rehearsal database or role exists; reconcile-only will not modify it." >&2
    return 1
  }
  now="$(date -u +%s)"
  reconcile_orphaned_provenance_markers "$now"
  residue="$(find "$PROVENANCE_ROOT" -mindepth 1 -maxdepth 1 -print -quit)"
  [[ -z "$residue" ]] || {
    echo "The rehearsal provenance registry is not empty after reconciliation." >&2
    return 1
  }
}

reconcile_stale_rehearsal_state() {
  local database database_description database_owner suffix created_epoch now active_count
  local dump_path dump_name run_path role_name role_description expected_role_comment
  local stale_database_output stale_dump_output stale_role_output database_count
  local stale_database
  local -a stale_databases=() validated_databases=() validated_role_only_databases=()
  local -a validated_stale_dumps=()
  local -A processed_role_databases=()
  local -A validated_database_names=()
  now="$(date -u +%s)"

  # Holding .rehearsal.lock proves no healthy orchestrator is active. Remove
  # only the fixed rehearsal containers/volumes, then inspect every database
  # name and age before destructive cleanup of power-loss residue.
  cleanup_rehearsal_containers
  cleanup_rehearsal_proxy_networks
  cleanup_rehearsal_work_volumes
  stale_database_output="$(
    docker compose --project-directory "$ROOT_DIR" --file "$ROOT_DIR/compose.yaml" \
      exec -T postgres psql -U "$POSTGRES_USER" -d postgres -Atqc \
      "SELECT d.datname || '|' || coalesce(shobj_description(d.oid, 'pg_database'), '') || '|' || r.rolname FROM pg_database d JOIN pg_roles r ON r.oid = d.datdba WHERE d.datname ~ '^lecturesift_rehearsal_[0-9]{14}$' ORDER BY d.datname" \
      | tr -d '\r'
  )" || {
    echo "Stale rehearsal databases could not be enumerated safely." >&2
    return 1
  }
  if [[ -n "$stale_database_output" ]]; then
    mapfile -t stale_databases <<<"$stale_database_output"
  fi
  for stale_database in "${stale_databases[@]}"; do
    IFS='|' read -r database database_description database_owner <<<"$stale_database"
    [[ "$database" =~ ^lecturesift_rehearsal_([0-9]{14})$ ]] || {
      echo "Refusing an unsafe stale rehearsal database name." >&2
      return 1
    }
    suffix="${BASH_REMATCH[1]}"
    validate_rehearsal_provenance_marker "$database" || return 1
    [[ -z "$database_description" ||
       "$database_description" == "lecturesift.rehearsal-db:v2:$database" ]] || {
      echo "A rehearsal-like database lacks matching ownership provenance; refusing deletion." >&2
      return 1
    }
    [[ "$database_owner" == "lecturesift_rehearsal_owner_$suffix" ]] || {
      echo "A rehearsal-like database has the wrong owner; refusing deletion." >&2
      return 1
    }
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
    validated_databases+=("$database")
    validated_database_names["$database"]=1
  done

  # A power loss can occur after the database drop but before its three derived
  # roles are removed. Delete only old roles whose exact comment binds them to
  # the absent timestamped rehearsal database.
  stale_role_output="$(
    docker compose --project-directory "$ROOT_DIR" --file "$ROOT_DIR/compose.yaml" \
      exec -T postgres psql -U "$POSTGRES_USER" -d postgres -Atqc \
      "SELECT rolname || '|' || coalesce(shobj_description(oid, 'pg_authid'), '') FROM pg_roles WHERE rolname ~ '^lecturesift_rehearsal_(owner|api|worker)_[0-9]{14}$' ORDER BY rolname" \
      | tr -d '\r'
  )" || {
    echo "Stale rehearsal roles could not be enumerated safely." >&2
    return 1
  }
  if [[ -n "$stale_role_output" ]]; then
    while IFS='|' read -r role_name role_description; do
      [[ "$role_name" =~ ^lecturesift_rehearsal_(owner|api|worker)_([0-9]{14})$ ]] || {
        echo "Refusing an unsafe stale rehearsal role name." >&2
        return 1
      }
      suffix="${BASH_REMATCH[2]}"
      database="lecturesift_rehearsal_$suffix"
      if [[ -n "${processed_role_databases[$database]:-}" ]]; then
        continue
      fi
      processed_role_databases["$database"]=1
      validate_rehearsal_provenance_marker "$database" || return 1
      expected_role_comment="lecturesift.rehearsal-role:v2:$database"
      [[ "$role_description" == "$expected_role_comment" ]] || {
        echo "A rehearsal-like role lacks matching ownership provenance; refusing deletion." >&2
        return 1
      }
      created_epoch="$(date -u -d \
        "${suffix:0:8} ${suffix:8:2}:${suffix:10:2}:${suffix:12:2}" +%s 2>/dev/null)" || {
        echo "A stale rehearsal role has an invalid timestamp." >&2
        return 1
      }
      (( created_epoch <= now - 3600 )) || {
        echo "A recent rehearsal role exists without its orchestrator; inspect it before retrying." >&2
        return 1
      }
      database_count="$(
        docker compose --project-directory "$ROOT_DIR" --file "$ROOT_DIR/compose.yaml" \
          exec -T postgres psql -U "$POSTGRES_USER" -d postgres -Atqc \
          "SELECT count(*) FROM pg_database WHERE datname = '$database'" \
          | tr -d '\r[:space:]'
      )"
      [[ "$database_count" == "0" || \
         ( "$database_count" == "1" && \
           -n "${validated_database_names[$database]:-}" ) ]] || {
        echo "A rehearsal role still belongs to an existing database; refusing orphan cleanup." >&2
        return 1
      }
      if [[ "$database_count" == "0" ]]; then
        validated_role_only_databases+=("$database")
      fi
    done <<<"$stale_role_output"
  fi

  # Two-phase destructive step. Every database, role, marker, age, owner,
  # comment and client-count check above completed before the first DROP.
  for database in "${validated_databases[@]}"; do
    docker compose --project-directory "$ROOT_DIR" --file "$ROOT_DIR/compose.yaml" \
      exec -T postgres dropdb -U "$POSTGRES_USER" --maintenance-db postgres \
      --if-exists "$database"
    cleanup_rehearsal_roles_for_database "$database"
    remove_rehearsal_provenance_if_clear "$database"
  done
  for database in "${validated_role_only_databases[@]}"; do
    cleanup_rehearsal_roles_for_database "$database"
    remove_rehearsal_provenance_if_clear "$database"
  done

  reconcile_orphaned_provenance_markers "$now"

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
      validated_stale_dumps+=("$dump_path")
    done <<<"$stale_dump_output"
    for dump_path in "${validated_stale_dumps[@]}"; do
      docker compose --project-directory "$ROOT_DIR" --file "$ROOT_DIR/compose.yaml" \
        exec -T postgres rm -f -- "$dump_path"
    done
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

if [[ "$rehearsal_mode" == "reconcile-stale" ]]; then
  reconcile_stale_rehearsal_state
  reconcile_rehearsal_provenance_only
  echo "REHEARSAL_STALE_RECONCILE_OK|validated_old_database_and_role_cleanup=true|provenance_empty=true"
  exit 0
fi

if [[ "$rehearsal_mode" == "reconcile-only" ]]; then
  reconcile_rehearsal_provenance_only
  echo "REHEARSAL_RECONCILE_OK|database_or_role_modified=false|provenance_empty=true"
  exit 0
fi

mapfile -t rehearsal_passwords < <(
  python3 -c 'import secrets; [print(secrets.token_urlsafe(48)) for _ in range(3)]'
)
[[ "${#rehearsal_passwords[@]}" == "3" &&
   ${#rehearsal_passwords[0]} -ge 32 &&
   ${#rehearsal_passwords[1]} -ge 32 &&
   ${#rehearsal_passwords[2]} -ge 32 &&
   "${rehearsal_passwords[0]}" != "${rehearsal_passwords[1]}" &&
   "${rehearsal_passwords[0]}" != "${rehearsal_passwords[2]}" &&
   "${rehearsal_passwords[1]}" != "${rehearsal_passwords[2]}" ]] || {
  echo "Could not create isolated rehearsal role credentials." >&2
  exit 1
}
rehearsal_owner_password="${rehearsal_passwords[0]}"
rehearsal_api_password="${rehearsal_passwords[1]}"
rehearsal_worker_password="${rehearsal_passwords[2]}"
unset rehearsal_passwords
rehearsal_owner_database_url="postgresql+psycopg://${rehearsal_owner_role}:${rehearsal_owner_password}@postgres:5432/${rehearsal_db}"
rehearsal_api_database_url="postgresql+psycopg://${rehearsal_api_role}:${rehearsal_api_password}@postgres:5432/${rehearsal_db}"
rehearsal_worker_database_url="postgresql+psycopg://${rehearsal_worker_role}:${rehearsal_worker_password}@postgres:5432/${rehearsal_db}"
mkdir -m 0700 -- "$run_dir"
trap cleanup_rehearsal EXIT

reconcile_stale_rehearsal_state

source_pg_exec docker run --rm --user 0:0 \
  --name lecturesift-source-postgres-rehearsal \
  --label lecturesift.rehearsal=true \
  --label "lecturesift.rehearsal.run=$rehearsal_suffix" \
  --label lecturesift.rehearsal.purpose=source-postgres-client \
  "${SOURCE_PG_DOCKER_ENV[@]}" \
  --env "PGOPTIONS=-c default_transaction_read_only=on" \
  --volume "$run_dir:/backup" \
  --volume "$ROOT_DIR/deploy:/probe:ro" \
  postgres:18-bookworm@sha256:1c59e2c3c818eaa0f0628f695b36e7c9e362d6b219b36a54a32df645cbd7e1af \
  sh -ec '
    set +x
    psql --no-psqlrc --quiet -v ON_ERROR_STOP=1 \
      -v LECTURESIFT_ALLOW_LEGACY_PROVIDER_SESSIONS=on \
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

source_pg_exec docker run --rm --user 0:0 \
  --name lecturesift-source-postgres-rehearsal \
  --label lecturesift.rehearsal=true \
  --label "lecturesift.rehearsal.run=$rehearsal_suffix" \
  --label lecturesift.rehearsal.purpose=source-postgres-client \
  "${SOURCE_PG_DOCKER_ENV[@]}" \
  --env "PGOPTIONS=-c default_transaction_read_only=on" \
  --volume "$run_dir:/backup" \
  postgres:18-bookworm@sha256:1c59e2c3c818eaa0f0628f695b36e7c9e362d6b219b36a54a32df645cbd7e1af \
  sh -ec '
    set +x
    pg_dump --format=custom --no-owner --no-acl \
      --serializable-deferrable --file=/backup/render.dump
  '

source_pg_exec docker run --rm --user 0:0 \
  --name lecturesift-source-postgres-rehearsal \
  --label lecturesift.rehearsal=true \
  --label "lecturesift.rehearsal.run=$rehearsal_suffix" \
  --label lecturesift.rehearsal.purpose=source-postgres-client \
  "${SOURCE_PG_DOCKER_ENV[@]}" \
  --env "PGOPTIONS=-c default_transaction_read_only=on" \
  --volume "$run_dir:/backup" \
  --volume "$ROOT_DIR/deploy:/probe:ro" \
  postgres:18-bookworm@sha256:1c59e2c3c818eaa0f0628f695b36e7c9e362d6b219b36a54a32df645cbd7e1af \
  sh -ec '
    set +x
    psql --no-psqlrc --quiet -v ON_ERROR_STOP=1 \
      -v LECTURESIFT_ALLOW_LEGACY_PROVIDER_SESSIONS=on \
      -f /probe/rehearsal_manifest.sql > /backup/source-after.txt
  '

sha256sum "$run_dir/render.dump" > "$run_dir/render.dump.sha256"
pg_dump_size="$(stat -c '%s' "$run_dir/render.dump")"
docker run --rm --user 0:0 \
  --name lecturesift-source-postgres-rehearsal \
  --label lecturesift.rehearsal=true \
  --label "lecturesift.rehearsal.run=$rehearsal_suffix" \
  --label lecturesift.rehearsal.purpose=source-postgres-client \
  --volume "$run_dir:/backup:ro" postgres:18-bookworm@sha256:1c59e2c3c818eaa0f0628f695b36e7c9e362d6b219b36a54a32df645cbd7e1af \
  pg_restore --list /backup/render.dump > "$run_dir/render.dump.list"

if docker compose --project-directory "$ROOT_DIR" --file "$ROOT_DIR/compose.yaml" \
  exec -T postgres psql -U "$POSTGRES_USER" -d postgres -Atqc \
  "SELECT 1 FROM pg_database WHERE datname = '$rehearsal_db'" | grep -q '^1$'; then
  echo "Refusing to overwrite existing rehearsal database." >&2
  exit 1
fi

create_rehearsal_provenance_marker
# The disposable owner is created only after the durable marker is fsynced.
# It is timestamp-bound, unprivileged, and cannot collide with the production
# owner.  The production owner is used only by this trusted postgres client to
# create the isolated identity; candidate code never receives its credential.
docker compose --project-directory "$ROOT_DIR" --file "$ROOT_DIR/compose.yaml" \
  exec -T postgres psql --no-psqlrc --quiet --set=ON_ERROR_STOP=1 \
  --username "$POSTGRES_USER" --dbname postgres \
  --variable=owner_role="$rehearsal_owner_role" \
  --variable=owner_password="$rehearsal_owner_password" \
  --variable=role_comment="$rehearsal_role_comment" <<'SQL'
BEGIN;
SELECT EXISTS (
  SELECT 1 FROM pg_roles WHERE rolname = :'owner_role'
) AS owner_exists
\gset
\if :owner_exists
  \warn 'The derived rehearsal owner role already exists.'
  \quit 3
\endif
SELECT format(
  'CREATE ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD %L',
  :'owner_role', :'owner_password'
)
\gexec
SELECT format('COMMENT ON ROLE %I IS %L', :'owner_role', :'role_comment')
\gexec
COMMIT;
SQL
rehearsal_db_created="true"
docker compose --project-directory "$ROOT_DIR" --file "$ROOT_DIR/compose.yaml" \
  exec -T postgres createdb -U "$POSTGRES_USER" -T template0 \
  --owner="$rehearsal_owner_role" --encoding=UTF8 --locale=en_US.UTF-8 \
  "$rehearsal_db"
docker compose --project-directory "$ROOT_DIR" --file "$ROOT_DIR/compose.yaml" \
  exec -T postgres psql --no-psqlrc --quiet --set=ON_ERROR_STOP=1 \
  --username "$POSTGRES_USER" --dbname postgres \
  --variable=database_name="$rehearsal_db" \
  --variable=database_comment="$rehearsal_database_comment" <<'SQL'
SELECT format('COMMENT ON DATABASE %I IS %L', :'database_name', :'database_comment')
\gexec
SQL
docker compose --project-directory "$ROOT_DIR" --file "$ROOT_DIR/compose.yaml" \
  exec -T postgres pg_restore -U "$POSTGRES_USER" --exit-on-error \
  --single-transaction --role="$rehearsal_owner_role" --no-owner --no-acl \
  --dbname="$rehearsal_db" \
  <"$run_dir/render.dump"
docker compose --project-directory "$ROOT_DIR" --file "$ROOT_DIR/compose.yaml" \
  exec -T postgres psql --no-psqlrc --quiet --set=ON_ERROR_STOP=1 \
  --username "$POSTGRES_USER" --dbname postgres \
  --variable=database_name="$rehearsal_db" \
  --variable=database_comment="$rehearsal_database_comment" <<'SQL'
SELECT format('COMMENT ON DATABASE %I IS %L', :'database_name', :'database_comment')
\gexec
SQL
rm -f -- "$run_dir/render.dump"

docker cp "$ROOT_DIR/deploy/rehearsal_manifest.sql" \
  lecturesift-postgres-1:/tmp/rehearsal_manifest.sql
docker compose --project-directory "$ROOT_DIR" --file "$ROOT_DIR/compose.yaml" \
  exec -T postgres psql --no-psqlrc --quiet -U "$POSTGRES_USER" -d "$rehearsal_db" \
  -v ON_ERROR_STOP=1 -v LECTURESIFT_ALLOW_LEGACY_PROVIDER_SESSIONS=on \
  -f /tmp/rehearsal_manifest.sql > "$run_dir/target.txt"

printf '%s\n' "$rehearsal_db" > "$run_dir/database-name.txt"
safe_pattern='^(DATABASE|SCHEMA|SCHEMA_OBJECT|TABLE|TABLE_DIFF|ANOMALY|STATUS|SCHEMA_COMPAT|UNVALIDATED_FK|MANIFEST_COMPLETE)\|'
python3 "$ROOT_DIR/deploy/verify_schema_transition.py" legacy --manifest "$run_dir/source-before.txt" --contract "$ROOT_DIR/deploy/schema_contract_payment_provider_sessions_v1.txt" > "$run_dir/source-before.schema-contract.txt"
python3 "$ROOT_DIR/deploy/verify_schema_transition.py" legacy --manifest "$run_dir/source-after.txt" --contract "$ROOT_DIR/deploy/schema_contract_payment_provider_sessions_v1.txt" > "$run_dir/source-after.schema-contract.txt"
python3 "$ROOT_DIR/deploy/verify_schema_transition.py" legacy --manifest "$run_dir/target.txt" --contract "$ROOT_DIR/deploy/schema_contract_payment_provider_sessions_v1.txt" > "$run_dir/target.schema-contract.txt"
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
legacy_compat_marker='SCHEMA_COMPAT|legacy_missing_table|billing_payment_provider_sessions|integrity_checks_deferred_to_current_schema_migration'
legacy_provider_sessions_missing="false"
for manifest_path in \
  "$run_dir/source-before.txt" "$run_dir/source-after.txt" "$run_dir/target.txt"; do
  compat_count="$(grep -c '^SCHEMA_COMPAT|' "$manifest_path" || true)"
  if [[ "$compat_count" -gt 1 ]] || \
     grep '^SCHEMA_COMPAT|' "$manifest_path" | grep -Fvxq "$legacy_compat_marker"; then
    echo "The legacy schema compatibility evidence is not the one permitted migration." >&2
    exit 1
  fi
done
if grep -Fxq "$legacy_compat_marker" "$run_dir/target.txt"; then
  legacy_provider_sessions_missing="true"
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

# The raw restored clone now exactly matches the stable source. Apply the
# current owner-only schema migration on that disposable clone, then switch
# back to the strict manifest. This proves that the one permitted legacy gap is
# upgraded rather than silently accepted.
LECTURESIFT_PROVISION_DATABASE="$rehearsal_db" \
  LECTURESIFT_REHEARSAL_ROLE_MODE=YES \
  LECTURESIFT_REHEARSAL_OWNER_DB_USER="$rehearsal_owner_role" \
  LECTURESIFT_REHEARSAL_OWNER_DB_PASSWORD="$rehearsal_owner_password" \
  LECTURESIFT_REHEARSAL_OWNER_DATABASE_URL="$rehearsal_owner_database_url" \
  LECTURESIFT_REHEARSAL_APP_DB_USER="$rehearsal_api_role" \
  LECTURESIFT_REHEARSAL_APP_DB_PASSWORD="$rehearsal_api_password" \
  LECTURESIFT_REHEARSAL_WORKER_DB_USER="$rehearsal_worker_role" \
  LECTURESIFT_REHEARSAL_WORKER_DB_PASSWORD="$rehearsal_worker_password" \
  "$ROOT_DIR/deploy/provision_database_role.sh"
docker compose --project-directory "$ROOT_DIR" --file "$ROOT_DIR/compose.yaml" \
  exec -T postgres psql --no-psqlrc --quiet -U "$POSTGRES_USER" -d "$rehearsal_db" \
  -v ON_ERROR_STOP=1 -f /tmp/rehearsal_manifest.sql > "$run_dir/target-migrated.txt"
if grep -Eq '^(TABLE_DIFF|SCHEMA_COMPAT|UNVALIDATED_FK)\|' \
  "$run_dir/target-migrated.txt" || \
   awk -F'|' '$1 == "ANOMALY" && $3 != "0" { invalid=1 } END { exit(invalid ? 0 : 1) }' \
   "$run_dir/target-migrated.txt"; then
  echo "The current schema migration did not produce a strict, anomaly-free clone." >&2
  exit 1
fi
python3 "$ROOT_DIR/deploy/verify_schema_transition.py" current --manifest "$run_dir/target-migrated.txt" --contract "$ROOT_DIR/deploy/schema_contract_payment_provider_sessions_v1.txt" > "$run_dir/schema-migrated-current.txt"
grep -E '^(DATABASE|TABLE|STATUS)\|' "$run_dir/target.txt" \
  | sort > "$run_dir/target-before-migration.data"
grep -E '^(DATABASE|TABLE|STATUS)\|' "$run_dir/target-migrated.txt" \
  | sort > "$run_dir/target-after-migration.data"
if [[ "$legacy_provider_sessions_missing" == "true" ]]; then
  if ! grep -Fxq 'TABLE|billing_payment_provider_sessions|0|0|0' \
    "$run_dir/target-migrated.txt"; then
    echo "The migrated payment-provider session table is missing or unexpectedly non-empty." >&2
    exit 1
  fi
  grep -Fv 'TABLE|billing_payment_provider_sessions|' \
    "$run_dir/target-after-migration.data" \
    > "$run_dir/target-after-migration.comparable"
else
  cp -- "$run_dir/target-after-migration.data" \
    "$run_dir/target-after-migration.comparable"
fi
if ! diff -u "$run_dir/target-before-migration.data" \
  "$run_dir/target-after-migration.comparable" \
  > "$run_dir/schema-migration-data.diff"; then
  echo "The schema migration changed pre-existing table data." >&2
  exit 1
fi
python3 "$ROOT_DIR/deploy/verify_schema_transition.py" transition \
  --before "$run_dir/target.txt" \
  --after "$run_dir/target-migrated.txt" \
  --contract "$ROOT_DIR/deploy/schema_contract_payment_provider_sessions_v1.txt" \
  > "$run_dir/schema-transition.txt"
grep -E "$safe_pattern" "$run_dir/target-migrated.txt" \
  | LC_ALL=C sort > "$run_dir/target-migrated.safe"

grep '^DATABASE|' "$run_dir/target-migrated.txt"
grep '^SCHEMA|' "$run_dir/target-migrated.txt"
grep '^TABLE|' "$run_dir/target-migrated.txt"
grep '^ANOMALY|' "$run_dir/target-migrated.txt"

# Keep the full clone alive only inside this orchestrated command. The stack
# and a real account/R2/worker/result E2E run must complete before success; the
# EXIT trap removes every rehearsal container and force-drops the clone on
# both success and failure, so no production-data clone is left indefinitely.
LECTURESIFT_REHEARSAL_ORCHESTRATED=YES \
  LECTURESIFT_REHEARSAL_RUN_DIR="$run_dir" \
  LECTURESIFT_REHEARSAL_API_DATABASE_URL="$rehearsal_api_database_url" \
  LECTURESIFT_REHEARSAL_WORKER_DATABASE_URL="$rehearsal_worker_database_url" \
  "$ROOT_DIR/deploy/rehearsal_stack.sh" "$rehearsal_db"
docker cp "$ROOT_DIR/deploy/rehearsal_e2e.py" \
  lecturesift-api-rehearsal:/tmp/lecturesift-rehearsal-e2e.py
docker exec lecturesift-api-rehearsal \
  python /tmp/lecturesift-rehearsal-e2e.py >"$run_dir/application-e2e.json"
docker cp "$ROOT_DIR/deploy/rehearsal_formats_e2e.py" \
  lecturesift-api-rehearsal:/tmp/lecturesift-rehearsal-formats-e2e.py
docker exec lecturesift-api-rehearsal \
  python /tmp/lecturesift-rehearsal-formats-e2e.py >"$run_dir/formats-e2e.json"
docker cp "$ROOT_DIR/deploy/rehearsal_purge_e2e.py" \
  lecturesift-api-rehearsal:/tmp/lecturesift-rehearsal-purge-e2e.py
docker cp "$run_dir/application-e2e.json" \
  lecturesift-api-rehearsal:/tmp/lecturesift-application-e2e.json
docker cp "$run_dir/formats-e2e.json" \
  lecturesift-api-rehearsal:/tmp/lecturesift-formats-e2e.json
docker exec --user 0:0 lecturesift-api-rehearsal \
  chown 10001:10001 \
    /tmp/lecturesift-rehearsal-purge-e2e.py \
    /tmp/lecturesift-application-e2e.json \
    /tmp/lecturesift-formats-e2e.json
docker exec --user 0:0 lecturesift-api-rehearsal \
  chmod 0400 \
    /tmp/lecturesift-rehearsal-purge-e2e.py \
    /tmp/lecturesift-application-e2e.json \
    /tmp/lecturesift-formats-e2e.json
docker exec --user 10001:10001 lecturesift-api-rehearsal \
  python /tmp/lecturesift-rehearsal-purge-e2e.py \
    --application-result /tmp/lecturesift-application-e2e.json \
    --formats-result /tmp/lecturesift-formats-e2e.json \
  >"$run_dir/e2e-purge.json"
docker compose --project-directory "$ROOT_DIR" --file "$ROOT_DIR/compose.yaml" \
  exec -T postgres psql --no-psqlrc --quiet -U "$POSTGRES_USER" -d "$rehearsal_db" \
  -v ON_ERROR_STOP=1 -f /tmp/rehearsal_manifest.sql > "$run_dir/target-after-e2e.txt"
if grep -Eq '^(TABLE_DIFF|SCHEMA_COMPAT|UNVALIDATED_FK)\|' \
  "$run_dir/target-after-e2e.txt" || \
   awk -F'|' '$1 == "ANOMALY" && $3 != "0" { invalid=1 } END { exit(invalid ? 0 : 1) }' \
   "$run_dir/target-after-e2e.txt"; then
  echo "The full application rehearsal left a schema or data-integrity anomaly." >&2
  exit 1
fi
python3 "$ROOT_DIR/deploy/verify_schema_transition.py" current \
  --manifest "$run_dir/target-after-e2e.txt" \
  --contract "$ROOT_DIR/deploy/schema_contract_payment_provider_sessions_v1.txt" \
  > "$run_dir/schema-after-e2e.txt"
grep -E "$safe_pattern" "$run_dir/target-after-e2e.txt" \
  | LC_ALL=C sort > "$run_dir/target-after-e2e.safe"
if ! diff -u "$run_dir/target-migrated.safe" \
  "$run_dir/target-after-e2e.safe" \
  > "$run_dir/e2e-cleanup.diff"; then
  echo "The full application rehearsal did not return to its exact pre-E2E database state." >&2
  exit 1
fi
docker exec lecturesift-postgres-1 rm -f /tmp/rehearsal_manifest.sql
cleanup_rehearsal_containers "$rehearsal_suffix"
cleanup_rehearsal_proxy_networks "$rehearsal_suffix"
cleanup_rehearsal_work_volumes "$rehearsal_suffix"
drop_rehearsal_database
cleanup_rehearsal_roles_for_database "$rehearsal_db"
remove_rehearsal_provenance_if_clear "$rehearsal_db"
printf 'database_dropped=true\n' >> "$run_dir/database-name.txt"
echo "PostgreSQL and application rehearsal completed; the disposable database and containers were removed without opening public traffic."
