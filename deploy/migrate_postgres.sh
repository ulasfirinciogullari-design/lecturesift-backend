#!/usr/bin/env bash
set +x
set -euo pipefail
umask 077

# Final, fail-stop PostgreSQL provider cutover: frozen Render -> private OVH.
# This command deliberately never starts API/worker and never changes Caddy,
# DNS, frontend configuration, or a payment-provider callback URL.

if [[ "$(id -u)" != "0" ]]; then
  echo "Run the PostgreSQL cutover as root." >&2
  exit 1
fi
if [[ "${LECTURESIFT_POSTGRES_CUTOVER_CONFIRM:-}" != "YES" ||
      "${LECTURESIFT_SOURCE_FROZEN:-}" != "YES" ||
      "${LECTURESIFT_SOURCE_WORKER_STOPPED:-}" != "YES" ||
      "${LECTURESIFT_PROVIDER_RECONCILED:-}" != "YES" ]]; then
  echo "Set every PostgreSQL cutover confirmation only after Render is frozen, drained and provider payments are reconciled." >&2
  exit 1
fi

ROOT_DIR="${LECTURESIFT_ROOT:-/opt/lecturesift}"
ALLOWED_SOURCE_ENV_FILE="/root/.lecturesift-render-source.env"
SOURCE_ENV_FILE="${LECTURESIFT_SOURCE_ENV_FILE:-$ALLOWED_SOURCE_ENV_FILE}"
ALLOWED_DB_ENV_FILE="/etc/lecturesift/postgres.env"
DB_ENV_FILE="${LECTURESIFT_DB_ENV_FILE:-$ALLOWED_DB_ENV_FILE}"
ALLOWED_RUNTIME_ENV_FILE="/etc/lecturesift/runtime.env"
RUNTIME_ENV_FILE="${LECTURESIFT_ENV_FILE:-$ALLOWED_RUNTIME_ENV_FILE}"
MANIFEST="$ROOT_DIR/deploy/rehearsal_manifest.sql"
SCHEMA_CONTRACT="$ROOT_DIR/deploy/schema_contract_payment_provider_sessions_v1.txt"
PRESERVED_SCHEMA_CONTRACT="$ROOT_DIR/deploy/schema_contract_billing_email_verifications_v1.txt"
SCHEMA_VERIFIER="$ROOT_DIR/deploy/verify_schema_transition.py"
POSTGRES_SECURITY_MANIFEST="$ROOT_DIR/deploy/postgres_security_manifest.sql"
POSTGRES_SECURITY_VALIDATOR="$ROOT_DIR/deploy/validate_postgres_security_manifest.py"
POSTGRES_ROLE_LOGIN_PROBE="$ROOT_DIR/deploy/postgres_role_login_probe.sh"
PROVISION_ROLE="$ROOT_DIR/deploy/provision_database_role.sh"
CUTOVER_EVIDENCE_TOOL="$ROOT_DIR/deploy/provider_cutover_evidence.py"
RENDER_WORKER_STOP_TOOL="$ROOT_DIR/deploy/render_worker_stop_evidence.py"
SOURCE_REDIS_GUARD="$ROOT_DIR/deploy/source_redis_guard.py"
SOURCE_POSTGRES_TRANSPORT="$ROOT_DIR/deploy/source_postgres_transport.py"
ALLOWED_BACKUP_ROOT="/var/backups/lecturesift/postgres-cutover"
REQUESTED_BACKUP_ROOT="${LECTURESIFT_POSTGRES_CUTOVER_ROOT:-$ALLOWED_BACKUP_ROOT}"
FAIL_STOP_ROOT="/var/lib/lecturesift/migration-fail-stop"
FAIL_STOP_MARKER="$FAIL_STOP_ROOT/postgres-cutover-unproven"
PENDING_SQL="SELECT (SELECT count(*) FROM billing_manual_orders WHERE status = 'pending') + (SELECT count(*) FROM billing_payment_orders WHERE status IN ('created', 'pending'));"
CUTOVER_ID="${LECTURESIFT_PROVIDER_CUTOVER_ID:-}"
EXPECTED_BUILD_REVISION="${LECTURESIFT_EXPECTED_BUILD_REVISION:-}"

fail() {
  echo "PostgreSQL cutover failed: $*" >&2
  exit 1
}

check_private_file() {
  local path="$1" label="$2" mode
  [[ -f "$path" && ! -L "$path" && "$(stat -c '%u' -- "$path")" == "0" ]] ||
    fail "$label must be a root-owned regular non-symlink file"
  mode="$(stat -c '%a' -- "$path")"
  case "$mode" in
    400|600) ;;
    *) fail "$label must have mode 0400 or 0600" ;;
  esac
}

[[ "$SOURCE_ENV_FILE" == "$ALLOWED_SOURCE_ENV_FILE" ]] ||
  fail "the Render source environment path is fixed"
[[ "$DB_ENV_FILE" == "$ALLOWED_DB_ENV_FILE" ]] ||
  fail "the target database environment path is fixed"
[[ "$RUNTIME_ENV_FILE" == "$ALLOWED_RUNTIME_ENV_FILE" ]] ||
  fail "the runtime environment path is fixed"
[[ "$REQUESTED_BACKUP_ROOT" == "$ALLOWED_BACKUP_ROOT" ]] ||
  fail "the PostgreSQL cutover backup root is fixed"
check_private_file "$SOURCE_ENV_FILE" "Render source environment"
check_private_file "$DB_ENV_FILE" "Target database environment"
check_private_file "$RUNTIME_ENV_FILE" "Runtime environment"
for path in "$MANIFEST" "$SCHEMA_CONTRACT" "$PRESERVED_SCHEMA_CONTRACT" "$SCHEMA_VERIFIER" \
  "$POSTGRES_SECURITY_MANIFEST" "$POSTGRES_SECURITY_VALIDATOR" \
  "$POSTGRES_ROLE_LOGIN_PROBE" \
  "$PROVISION_ROLE" "$CUTOVER_EVIDENCE_TOOL" "$RENDER_WORKER_STOP_TOOL" \
  "$SOURCE_REDIS_GUARD" "$SOURCE_POSTGRES_TRANSPORT" \
  "$ROOT_DIR/compose.yaml"; do
  [[ -f "$path" && ! -L "$path" ]] || fail "missing or unsafe cutover input: $path"
done
command -v docker >/dev/null 2>&1 || fail "Docker is unavailable"
command -v python3 >/dev/null 2>&1 || fail "Python is unavailable"

source_exec() {
  local scope="$1"
  shift
  python3 "$SOURCE_POSTGRES_TRANSPORT" exec-source \
    --source-env "$SOURCE_ENV_FILE" --scope "$scope" -- "$@"
}

source_pg_exec() {
  python3 "$SOURCE_POSTGRES_TRANSPORT" exec-libpq-docker \
    --source-env "$SOURCE_ENV_FILE" -- "$@"
}

SOURCE_PG_DOCKER_ENV=(
  --env PGHOST --env PGPORT --env PGDATABASE --env PGUSER
  --env PGSSLMODE --env PGSSLROOTCERT
  --env PGCONNECT_TIMEOUT
)
python3 "$SOURCE_POSTGRES_TRANSPORT" validate --source-env "$SOURCE_ENV_FILE" \
  >/dev/null || fail "the Render source transport contract is invalid"

set -a
# shellcheck disable=SC1090
source "$RUNTIME_ENV_FILE"
# shellcheck disable=SC1090
source "$DB_ENV_FILE"
set +a
[[ "$CUTOVER_ID" =~ ^[0-9a-f]{32}$ ]] ||
  fail "LECTURESIFT_PROVIDER_CUTOVER_ID must be exactly 32 lowercase hex characters"
[[ "$EXPECTED_BUILD_REVISION" =~ ^[0-9a-f]{40}$ ]] ||
  fail "LECTURESIFT_EXPECTED_BUILD_REVISION must be the exact 40-character release commit"
SOURCE_FINGERPRINT="$(source_exec fingerprint python3 "$CUTOVER_EVIDENCE_TOOL" source-fingerprint)" ||
  fail "the Render source identity could not be fingerprinted"
[[ "$SOURCE_FINGERPRINT" =~ ^[0-9a-f]{64}$ ]] ||
  fail "the Render source fingerprint is invalid"
: "${POSTGRES_DB:?Missing POSTGRES_DB}"
: "${POSTGRES_USER:?Missing POSTGRES_USER}"
: "${LECTURESIFT_APP_DB_USER:?Missing LECTURESIFT_APP_DB_USER}"
: "${LECTURESIFT_APP_DB_PASSWORD:?Missing LECTURESIFT_APP_DB_PASSWORD}"
: "${LECTURESIFT_WORKER_DB_USER:?Missing LECTURESIFT_WORKER_DB_USER}"
: "${LECTURESIFT_WORKER_DB_PASSWORD:?Missing LECTURESIFT_WORKER_DB_PASSWORD}"
: "${DATABASE_URL:?Missing DATABASE_URL}"
: "${LECTURESIFT_WORKER_DATABASE_URL:?Missing LECTURESIFT_WORKER_DATABASE_URL}"

python3 - <<'PY' || fail "API and worker database URLs must use distinct least-privilege target roles"
import os
from urllib.parse import urlsplit

api = urlsplit(os.environ["DATABASE_URL"])
worker = urlsplit(os.environ["LECTURESIFT_WORKER_DATABASE_URL"])
valid = (
    api.username == os.environ["LECTURESIFT_APP_DB_USER"]
    and worker.username == os.environ["LECTURESIFT_WORKER_DB_USER"]
    and api.username != worker.username
    and api.username != os.environ["POSTGRES_USER"]
    and worker.username != os.environ["POSTGRES_USER"]
    and (api.hostname or "").lower() == "postgres"
    and (worker.hostname or "").lower() == "postgres"
    and api.path.strip("/") == os.environ["POSTGRES_DB"]
    and worker.path.strip("/") == os.environ["POSTGRES_DB"]
)
raise SystemExit(0 if valid else 1)
PY

allowed_root="$(realpath -m -- "$ALLOWED_BACKUP_ROOT")"
[[ "$allowed_root" == "$ALLOWED_BACKUP_ROOT" ]] ||
  fail "the fixed cutover root resolves through a symlink"
install -d -o root -g root -m 0700 -- "$allowed_root" "$FAIL_STOP_ROOT"
BACKUP_ROOT="$(realpath -e -- "$allowed_root")"
[[ "$BACKUP_ROOT" == "$ALLOWED_BACKUP_ROOT" && ! -L "$BACKUP_ROOT" ]] ||
  fail "the cutover root is not the fixed real directory"
[[ "$(stat -c '%u' -- "$BACKUP_ROOT")" == "0" ]] || fail "the cutover root is not root-owned"
backup_mode="$(stat -c '%a' -- "$BACKUP_ROOT")"
(( (8#$backup_mode & 8#077) == 0 )) || fail "the cutover root must be private to root"
[[ ! -L "$FAIL_STOP_ROOT" && "$(realpath -e -- "$FAIL_STOP_ROOT")" == "$FAIL_STOP_ROOT" ]] ||
  fail "the migration fail-stop directory is unsafe"
if [[ -e "$FAIL_STOP_MARKER" || -L "$FAIL_STOP_MARKER" ]]; then
  fail "a previous PostgreSQL cutover left unproven state; inspect it and clear the marker manually"
fi

# Serialize this with backup, restore, Redis migration and rehearsals.
SHARED_BACKUP_LOCK_ROOT="/var/backups/lecturesift"
[[ -d "$SHARED_BACKUP_LOCK_ROOT" && ! -L "$SHARED_BACKUP_LOCK_ROOT" &&
   "$(realpath -e -- "$SHARED_BACKUP_LOCK_ROOT")" == "$SHARED_BACKUP_LOCK_ROOT" &&
   "$(stat -c '%u' -- "$SHARED_BACKUP_LOCK_ROOT")" == "0" ]] ||
  fail "the shared backup/restore lock root is unsafe"
shared_mode="$(stat -c '%a' -- "$SHARED_BACKUP_LOCK_ROOT")"
(( (8#$shared_mode & 8#077) == 0 )) || fail "the shared lock root must be private to root"
exec 9>"$SHARED_BACKUP_LOCK_ROOT/.backup.lock"
flock -n 9 || fail "a backup, restore or migration is already active"

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
RUN_DIR="$BACKUP_ROOT/postgres-cutover-$STAMP"
mkdir -m 0700 -- "$RUN_DIR"
SNAPSHOT_CONTAINER="lecturesift-render-snapshot-${STAMP,,}"
SNAPSHOT_CONTAINER="${SNAPSHOT_CONTAINER//[^a-z0-9_.-]/}"
compose=(docker compose --project-directory "$ROOT_DIR" --file "$ROOT_DIR/compose.yaml")
target_mutated="false"
migration_verified="false"
snapshot_open="false"
SOURCE_WORKER_STOP_EVIDENCE_SHA256=""

write_fail_stop_marker() {
  local state="$1" action="$2" tmp
  tmp="$(mktemp -- "$FAIL_STOP_ROOT/.postgres-cutover-XXXXXXXX")"
  {
    printf 'status=%s\n' "$state"
    printf 'recorded_at_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'migration_run=%s\n' "$(basename -- "$RUN_DIR")"
    printf 'operator_action=%s\n' "$action"
  } >"$tmp"
  chmod 0600 "$tmp"
  mv -f -- "$tmp" "$FAIL_STOP_MARKER"
}

close_snapshot() {
  [[ "$snapshot_open" == "true" ]] || return 0
  docker stop --time 5 "$SNAPSHOT_CONTAINER" >/dev/null 2>&1 || true
  docker rm -f "$SNAPSHOT_CONTAINER" >/dev/null 2>&1 || true
  snapshot_open="false"
}

canonical_manifest() {
  local source="$1" destination="$2" mode="${3:-strict}" compat_count
  [[ "$mode" == "strict" || "$mode" == "legacy-provider-sessions" ]] ||
    fail "an invalid manifest compatibility mode was requested"
  if [[ "$mode" == "strict" ]]; then
    python3 "$SCHEMA_VERIFIER" current \
      --manifest "$source" --contract "$SCHEMA_CONTRACT" \
      --preserved-contract "$PRESERVED_SCHEMA_CONTRACT" >/dev/null ||
      fail "a strict database manifest violates the exact current schema contract"
  fi
  tr -d '\r' <"$source" |
    grep -E '^(DATABASE|SCHEMA|SCHEMA_OBJECT|TABLE|ANOMALY|STATUS|SCHEMA_COMPAT|UNVALIDATED_FK|MANIFEST_COMPLETE)\|' |
    LC_ALL=C sort >"$destination"
  [[ "$(grep -c '^DATABASE|' "$destination")" == "1" &&
     "$(grep -c '^SCHEMA|' "$destination")" == "1" &&
     "$(grep -c '^TABLE|' "$destination")" -gt 0 ]] ||
    fail "a database manifest is incomplete"
  if grep -Eq '^(TABLE_DIFF|UNVALIDATED_FK)\|' "$source" ||
     awk -F'|' '$1 == "ANOMALY" && $3 != "0" {bad=1} END {exit(bad ? 0 : 1)}' "$source"; then
    fail "a database manifest contains an integrity anomaly"
  fi
  compat_count="$(grep -c '^SCHEMA_COMPAT|' "$source" || true)"
  if [[ "$mode" == "strict" ]]; then
    [[ "$compat_count" == "0" ]] || fail "a strict database manifest used schema compatibility"
  elif [[ "$compat_count" -gt 1 ]] ||
       grep '^SCHEMA_COMPAT|' "$source" |
         grep -Fvxq 'SCHEMA_COMPAT|legacy_missing_table|billing_payment_provider_sessions|integrity_checks_deferred_to_current_schema_migration'; then
    fail "the source manifest contains an unapproved legacy schema difference"
  fi
}

run_render_manifest() {
  local output_name="$1"
  [[ "$output_name" =~ ^[a-z0-9-]+[.]txt$ ]] || fail "unsafe manifest output name"
  source_pg_exec docker run --rm --user 0:0 \
    "${SOURCE_PG_DOCKER_ENV[@]}" \
    --volume "$RUN_DIR:/backup" --volume "$ROOT_DIR/deploy:/probe:ro" \
    --env "OUTPUT_NAME=$output_name" postgres:18-bookworm@sha256:1c59e2c3c818eaa0f0628f695b36e7c9e362d6b219b36a54a32df645cbd7e1af bash -euc '
      set +x
      psql --no-psqlrc -v ON_ERROR_STOP=1 \
        -v LECTURESIFT_ALLOW_LEGACY_PROVIDER_SESSIONS=on \
        -f /probe/rehearsal_manifest.sql >"/backup/$OUTPUT_NAME"
    '
}

render_pending_count() {
  source_pg_exec docker run --rm --user 0:0 \
    "${SOURCE_PG_DOCKER_ENV[@]}" \
    --env "PENDING_SQL=$PENDING_SQL" postgres:18-bookworm@sha256:1c59e2c3c818eaa0f0628f695b36e7c9e362d6b219b36a54a32df645cbd7e1af bash -euc '
      set +x
      psql --no-psqlrc -v ON_ERROR_STOP=1 \
        --tuples-only --no-align --command "$PENDING_SQL"
    ' | tr -d '\r[:space:]'
}

assert_render_frozen() {
  source_exec health python3 - <<'PY'
import json
import os
import ssl
import urllib.request

request = urllib.request.Request(
    os.environ["SOURCE_HEALTH_URL"], headers={"User-Agent": "LectureSift-Cutover/1"}
)
with urllib.request.urlopen(request, timeout=15, context=ssl.create_default_context()) as response:
    payload = json.load(response)
valid = response.status == 200 and payload.get("ok") is True and payload.get("maintenance_mode") == "freeze"
raise SystemExit(0 if valid else 1)
PY
}

assert_render_worker_and_queue_stopped() {
  local observed_stop_digest
  observed_stop_digest="$(python3 "$RENDER_WORKER_STOP_TOOL")" || return 1
  [[ "$observed_stop_digest" =~ ^[0-9a-f]{64}$ ]] || return 1
  if [[ -n "$SOURCE_WORKER_STOP_EVIDENCE_SHA256" &&
        "$observed_stop_digest" != "$SOURCE_WORKER_STOP_EVIDENCE_SHA256" ]]; then
    return 1
  fi
  SOURCE_WORKER_STOP_EVIDENCE_SHA256="$observed_stop_digest"

  # Probe the state and broker URLs independently with a host stdlib TLS
  # client. No source credential reaches the release-candidate image.
  source_exec redis timeout 45 python3 "$SOURCE_REDIS_GUARD" assert-idle \
    >>"$RUN_DIR/source-queue-check.log" 2>&1
}

target_manifest() {
  local destination="$1" mode="${2:-strict}"
  local -a compatibility_args=()
  case "$mode" in
    strict) ;;
    legacy-provider-sessions)
      compatibility_args=(-v LECTURESIFT_ALLOW_LEGACY_PROVIDER_SESSIONS=on)
      ;;
    *) fail "an invalid target manifest compatibility mode was requested" ;;
  esac
  "${compose[@]}" exec -T postgres psql --no-psqlrc --quiet \
    --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" -v ON_ERROR_STOP=1 \
    "${compatibility_args[@]}" \
    -f /tmp/lecturesift-cutover-manifest.sql >"$destination"
}

target_security_manifest() {
  local raw="$1" safe="$2"
  "${compose[@]}" exec -T postgres psql --no-psqlrc --quiet \
    --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" -v ON_ERROR_STOP=1 \
    --variable=owner_user="$POSTGRES_USER" \
    --variable=api_user="$LECTURESIFT_APP_DB_USER" \
    --variable=worker_user="$LECTURESIFT_WORKER_DB_USER" \
    <"$POSTGRES_SECURITY_MANIFEST" >"$raw"
  python3 "$POSTGRES_SECURITY_VALIDATOR" "$raw" >"$safe"
}

install_target_manifest() {
  "${compose[@]}" cp "$MANIFEST" postgres:/tmp/lecturesift-cutover-manifest.sql
}

remove_target_manifest() {
  "${compose[@]}" exec -T postgres rm -f /tmp/lecturesift-cutover-manifest.sql \
    >/dev/null 2>&1 || true
}

reset_and_restore_target() {
  local dump="$1"
  "${compose[@]}" exec -T postgres dropdb --username "$POSTGRES_USER" \
    --maintenance-db postgres --if-exists --force "$POSTGRES_DB"
  "${compose[@]}" exec -T postgres createdb --username "$POSTGRES_USER" \
    --maintenance-db postgres --template template0 --encoding UTF8 \
    --locale en_US.UTF8 --owner "$POSTGRES_USER" "$POSTGRES_DB"
  "${compose[@]}" exec -T postgres pg_restore --username "$POSTGRES_USER" \
    --exit-on-error --single-transaction --no-owner --no-acl \
    --dbname "$POSTGRES_DB" <"$dump"
}

provision_target_schema_and_roles() {
  "$PROVISION_ROLE"
}

target_stopped() {
  local service container running
  for service in api worker; do
    container="$("${compose[@]}" ps -q "$service")"
    if [[ -n "$container" ]]; then
      running="$(docker inspect -f '{{.State.Running}}' "$container" 2>/dev/null || printf unknown)"
      [[ "$running" == "false" ]] || return 1
    fi
  done
  for container in lecturesift-api-rehearsal lecturesift-worker-rehearsal; do
    if docker container inspect "$container" >/dev/null 2>&1; then
      running="$(docker inspect -f '{{.State.Running}}' "$container" 2>/dev/null || printf unknown)"
      [[ "$running" == "false" ]] || return 1
    fi
  done
}

probe_application_role() {
  local service="$1"
  "${compose[@]}" run --rm --no-deps -T --entrypoint python \
    -e "LECTURESIFT_PROBE_ROLE=$service" "$service" - <<'PY'
import os
import uuid
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

engine = create_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)
role = os.environ["LECTURESIFT_PROBE_ROLE"]
connection = engine.connect()
transaction = connection.begin()
try:
    row = connection.execute(text("""
        SELECT current_user, rolsuper, rolcreatedb, rolcreaterole,
               rolreplication, rolbypassrls,
               has_database_privilege(current_user, current_database(), 'TEMP'),
               has_schema_privilege(current_user, 'public', 'CREATE')
          FROM pg_roles WHERE rolname = current_user
    """)).one()
    assert row[0]
    assert not any(bool(value) for value in row[1:])
    assert role in {"api", "worker"}

    def denied(sql: str) -> None:
        savepoint = connection.begin_nested()
        try:
            connection.execute(text(sql))
        except DBAPIError:
            savepoint.rollback()
            return
        savepoint.rollback()
        raise AssertionError(f"unexpected database privilege: {sql}")

    denied("CREATE TEMP TABLE lecturesift_forbidden_temp(value integer)")
    denied("CREATE TABLE public.lecturesift_forbidden_ddl(value integer)")
    if role == "api":
        assert connection.execute(text("SELECT count(*) FROM public.billing_users")).scalar_one() >= 0
        privileges = connection.execute(text("""
            SELECT has_table_privilege(current_user, 'public.billing_users', 'SELECT'),
                   has_table_privilege(current_user, 'public.billing_users', 'INSERT'),
                   has_table_privilege(current_user, 'public.billing_users', 'UPDATE'),
                   has_table_privilege(current_user, 'public.billing_users', 'DELETE')
        """)).one()
        assert all(bool(value) for value in privileges)
    else:
        search_path = connection.execute(text("SELECT current_setting('search_path')")).scalar_one()
        assert search_path.replace('"', '').replace(' ', '').startswith("lecturesift_worker,public")
        privileges = connection.execute(text("""
            SELECT
              has_table_privilege(current_user, 'lecturesift_worker.billing_users', 'SELECT'),
              has_column_privilege(current_user, 'lecturesift_worker.billing_users', 'credit_minutes', 'UPDATE'),
              has_table_privilege(current_user, 'lecturesift_worker.billing_usage_events', 'SELECT'),
              has_table_privilege(current_user, 'lecturesift_worker.billing_usage_events', 'INSERT'),
              has_table_privilege(current_user, 'lecturesift_worker.lecturesift_runtime_metrics', 'SELECT'),
              has_table_privilege(current_user, 'lecturesift_worker.lecturesift_runtime_metrics', 'INSERT'),
              NOT has_table_privilege(current_user, 'public.billing_users', 'SELECT'),
              NOT has_table_privilege(current_user, 'public.billing_auth_tokens', 'SELECT'),
              NOT has_table_privilege(current_user, 'public.lecturesift_admin_account_events', 'SELECT'),
              NOT has_table_privilege(current_user, 'public.lecturesift_contact_messages', 'SELECT')
        """)).one()
        assert all(bool(value) for value in privileges)
        assert connection.execute(text("""
            SELECT count(*) FROM billing_users
             WHERE email <> '' OR password_salt <> '' OR password_hash <> ''
        """)).scalar_one() == 0
        for protected in (
            "public.billing_users",
            "public.billing_auth_tokens",
            "public.lecturesift_admin_account_events",
            "public.lecturesift_contact_messages",
        ):
            denied(f"SELECT count(*) FROM {protected}")

        probe_id = f"cutover-probe-{uuid.uuid4().hex}"
        connection.execute(
            text("""
                INSERT INTO lecturesift_runtime_metrics
                    (job_id, media_minutes, elapsed_seconds, size_bytes, created_at)
                VALUES (:job_id, 1, 1, 1, CURRENT_TIMESTAMP)
            """),
            {"job_id": probe_id},
        )
        assert connection.execute(
            text("SELECT count(*) FROM lecturesift_runtime_metrics WHERE job_id = :job_id"),
            {"job_id": probe_id},
        ).scalar_one() == 1
        user_id = connection.execute(text("SELECT id FROM billing_users LIMIT 1")).scalar_one_or_none()
        if user_id:
            connection.execute(
                text("""
                    INSERT INTO billing_usage_events
                        (id, user_id, job_id, plan_code, minutes, occurred_at)
                    VALUES (:id, :user_id, :job_id, 'cutover_probe', 1, CURRENT_TIMESTAMP)
                """),
                {"id": uuid.uuid4().hex, "user_id": user_id, "job_id": probe_id},
            )
            assert connection.execute(
                text("SELECT count(*) FROM billing_usage_events WHERE job_id = :job_id"),
                {"job_id": probe_id},
            ).scalar_one() == 1
finally:
    transaction.rollback()
    connection.close()
engine.dispose()
PY
}

cleanup() {
  local status="$?" rollback_ok="false"
  trap - EXIT
  set +e
  close_snapshot
  remove_target_manifest
  if [[ "$status" != "0" && "$target_mutated" == "true" && "$migration_verified" != "true" ]]; then
    echo "The cutover aborted after target mutation; attempting the fixed target rollback dump." >&2
    if reset_and_restore_target "$RUN_DIR/target-before.dump" &&
       install_target_manifest &&
       target_manifest "$RUN_DIR/target-rollback-raw.txt" strict; then
      tr -d '\r' <"$RUN_DIR/target-rollback-raw.txt" |
        grep -E '^(DATABASE|SCHEMA|SCHEMA_OBJECT|TABLE|ANOMALY|STATUS|SCHEMA_COMPAT|UNVALIDATED_FK|MANIFEST_COMPLETE)\|' |
        LC_ALL=C sort >"$RUN_DIR/target-rollback-raw.safe"
      if ! grep -Eq '^(TABLE_DIFF|SCHEMA_COMPAT|UNVALIDATED_FK)\|' \
           "$RUN_DIR/target-rollback-raw.txt" &&
         ! awk -F'|' '$1 == "ANOMALY" && $3 != "0" {bad=1} END {exit(bad ? 0 : 1)}' \
           "$RUN_DIR/target-rollback-raw.txt" &&
         cmp --silent "$RUN_DIR/target-before.safe" "$RUN_DIR/target-rollback-raw.safe" &&
         provision_target_schema_and_roles &&
         target_manifest "$RUN_DIR/target-rollback-check.txt" strict; then
        tr -d '\r' <"$RUN_DIR/target-rollback-check.txt" |
          grep -E '^(DATABASE|SCHEMA|SCHEMA_OBJECT|TABLE|ANOMALY|STATUS|SCHEMA_COMPAT|UNVALIDATED_FK|MANIFEST_COMPLETE)\|' |
          LC_ALL=C sort >"$RUN_DIR/target-rollback-check.safe"
        if ! grep -Eq '^(TABLE_DIFF|SCHEMA_COMPAT|UNVALIDATED_FK)\|' \
             "$RUN_DIR/target-rollback-check.txt" &&
           ! awk -F'|' '$1 == "ANOMALY" && $3 != "0" {bad=1} END {exit(bad ? 0 : 1)}' \
             "$RUN_DIR/target-rollback-check.txt" &&
           cmp --silent "$RUN_DIR/target-before.safe" \
             "$RUN_DIR/target-rollback-check.safe"; then
          rollback_ok="true"
        fi
      fi
    fi
    if [[ "$rollback_ok" == "true" ]]; then
      write_fail_stop_marker "postgres-cutover-aborted-target-rolled-back" \
        "inspect-run-and-remove-marker-manually;keep-api-worker-stopped"
      echo "The previous target was restored, but production remains fail-stopped for operator review." >&2
    else
      write_fail_stop_marker "postgres-cutover-target-unproven" \
        "repair-target-from-root-only-rollback-dump;keep-api-worker-stopped"
      echo "The target rollback could not be proven; do not start API or worker." >&2
    fi
    status=1
  elif [[ "$migration_verified" == "true" ]]; then
    rm -f -- "$FAIL_STOP_MARKER"
  else
    rm -f -- "$FAIL_STOP_MARKER"
  fi
  unset POSTGRES_PASSWORD
  unset LECTURESIFT_APP_DB_PASSWORD LECTURESIFT_WORKER_DB_PASSWORD
  unset DATABASE_URL LECTURESIFT_WORKER_DATABASE_URL
  unset SOURCE_WORKER_STOP_EVIDENCE_SHA256
  exit "$status"
}
trap cleanup EXIT

python3 "$CUTOVER_EVIDENCE_TOOL" begin-postgres \
  --cutover-id "$CUTOVER_ID" \
  --revision "$EXPECTED_BUILD_REVISION" \
  --source-fingerprint "$SOURCE_FINGERPRINT" ||
  fail "the global provider-cutover stop fence could not be established"
write_fail_stop_marker "postgres-cutover-in-progress" \
  "do-not-start-api-or-worker-until-this-command-exits-successfully"

assert_render_frozen || fail "the live Render health endpoint did not acknowledge exact freeze mode"
assert_render_worker_and_queue_stopped ||
  fail "the Render worker/queue stopped state could not be independently proved"
pending_before="$(render_pending_count)"
[[ "$pending_before" == "0" ]] ||
  fail "pending payments are not zero; provider reconciliation must finish before cutover"
run_render_manifest source-before.txt
canonical_manifest "$RUN_DIR/source-before.txt" "$RUN_DIR/source-before.safe" \
  legacy-provider-sessions
source_size="$(sed -n 's/^DATABASE_SIZE|//p' "$RUN_DIR/source-before.txt" | tr -d '\r')"
[[ "$(grep -c '^DATABASE_SIZE|' "$RUN_DIR/source-before.txt")" == "1" &&
   "$source_size" =~ ^[1-9][0-9]*$ && "$source_size" -le 1000000000000 ]] ||
  fail "the source database size is invalid or exceeds the supported bound"
"${compose[@]}" up -d --wait --wait-timeout 300 postgres
target_size="$("${compose[@]}" exec -T postgres psql --no-psqlrc --quiet \
  --tuples-only --no-align --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  --command 'SELECT pg_database_size(current_database())' | tr -d '\r[:space:]')"
[[ "$target_size" =~ ^[1-9][0-9]*$ && "$target_size" -le 1000000000000 ]] ||
  fail "the existing target database size is invalid or exceeds the supported bound"
available_bytes="$(df --output=avail -B1 -- "$BACKUP_ROOT" | awk 'NR == 2 {print $1}')"
[[ "$available_bytes" =~ ^[0-9]+$ ]] || fail "cutover storage capacity cannot be proven"
# One source dump, one target rollback dump, validation/list output and generous
# failure-recovery headroom, while preserving 5 GiB for the host.
required_bytes=$((source_size * 2 + target_size * 2 + 5368709120))
(( available_bytes >= required_bytes )) ||
  fail "insufficient cutover storage while preserving 5 GiB of host reserve"

# Keep a single exported MVCC snapshot open while pg_dump and the authoritative
# source manifest run. Live before/after manifests must also match it exactly.
source_pg_exec docker run -d --name "$SNAPSHOT_CONTAINER" --user 0:0 \
  "${SOURCE_PG_DOCKER_ENV[@]}" \
  --volume "$RUN_DIR:/backup" postgres:18-bookworm@sha256:1c59e2c3c818eaa0f0628f695b36e7c9e362d6b219b36a54a32df645cbd7e1af bash -euc '
    set +x
    psql --no-psqlrc -v ON_ERROR_STOP=1 <<"SQL"
BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;
\pset tuples_only on
\pset format unaligned
\o /backup/source-snapshot.info
SELECT pg_backend_pid() || chr(124) || pg_export_snapshot();
\o
SELECT pg_sleep(21600);
ROLLBACK;
SQL
  ' >/dev/null
snapshot_open="true"
snapshot_info=""
for _ in $(seq 1 150); do
  if [[ -s "$RUN_DIR/source-snapshot.info" ]]; then
    snapshot_info="$(tr -d '\r\n' <"$RUN_DIR/source-snapshot.info")"
    break
  fi
  docker inspect "$SNAPSHOT_CONTAINER" >/dev/null 2>&1 || break
  sleep 0.1
done
[[ "$snapshot_info" =~ ^[0-9]+\|[0-9A-Fa-f]+-[0-9A-Fa-f]+-[0-9A-Fa-f]+$ ]] ||
  fail "the Render consistent snapshot could not be exported"
SNAPSHOT_ID="${snapshot_info#*|}"

source_pg_exec docker run --rm --user 0:0 \
  "${SOURCE_PG_DOCKER_ENV[@]}" \
  --volume "$RUN_DIR:/backup" --volume "$ROOT_DIR/deploy:/probe:ro" \
  --env "SNAPSHOT_ID=$SNAPSHOT_ID" postgres:18-bookworm@sha256:1c59e2c3c818eaa0f0628f695b36e7c9e362d6b219b36a54a32df645cbd7e1af bash -euc '
    set +x
    {
      printf "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;\n"
      printf "SET TRANSACTION SNAPSHOT '\''%s'\'';\n" "$SNAPSHOT_ID"
      cat /probe/rehearsal_manifest.sql
      printf "COMMIT;\n"
    } | psql --no-psqlrc -v ON_ERROR_STOP=1 \
      -v LECTURESIFT_ALLOW_LEGACY_PROVIDER_SESSIONS=on \
      > /backup/source-snapshot.txt
    pg_dump --format=custom --no-owner --no-acl \
      --snapshot "$SNAPSHOT_ID" --file=/backup/render-final.dump
  '
close_snapshot
docker run --rm --user 0:0 --volume "$RUN_DIR:/backup:ro" postgres:18-bookworm@sha256:1c59e2c3c818eaa0f0628f695b36e7c9e362d6b219b36a54a32df645cbd7e1af \
  pg_restore --list /backup/render-final.dump >"$RUN_DIR/render-final.dump.list"
sha256sum "$RUN_DIR/render-final.dump" >"$RUN_DIR/render-final.dump.sha256"
canonical_manifest "$RUN_DIR/source-snapshot.txt" "$RUN_DIR/source-snapshot.safe" \
  legacy-provider-sessions

assert_render_frozen || fail "Render left freeze mode during the source capture"
assert_render_worker_and_queue_stopped ||
  fail "the Render worker or queue became active during source capture"
pending_after="$(render_pending_count)"
[[ "$pending_after" == "0" ]] || fail "a provider payment became pending during source capture"
run_render_manifest source-after.txt
canonical_manifest "$RUN_DIR/source-after.txt" "$RUN_DIR/source-after.safe" \
  legacy-provider-sessions
cmp --silent "$RUN_DIR/source-before.safe" "$RUN_DIR/source-snapshot.safe" ||
  fail "Render data changed before the exported snapshot"
cmp --silent "$RUN_DIR/source-snapshot.safe" "$RUN_DIR/source-after.safe" ||
  fail "Render data changed during the final dump"

# Caddy is intentionally left untouched. Only target writers are stopped.
"${compose[@]}" stop --timeout 600 api worker
target_stopped || fail "the OVH API/worker writer stop could not be proven"
"${compose[@]}" up -d --wait --wait-timeout 300 postgres
install_target_manifest
target_manifest "$RUN_DIR/target-before.txt"
canonical_manifest "$RUN_DIR/target-before.txt" "$RUN_DIR/target-before.safe"
"${compose[@]}" exec -T postgres pg_dump --username "$POSTGRES_USER" \
  --dbname "$POSTGRES_DB" --format custom --no-owner --no-acl \
  >"$RUN_DIR/target-before.dump"
docker run --rm --user 0:0 --volume "$RUN_DIR:/backup:ro" postgres:18-bookworm@sha256:1c59e2c3c818eaa0f0628f695b36e7c9e362d6b219b36a54a32df645cbd7e1af \
  pg_restore --list /backup/target-before.dump >"$RUN_DIR/target-before.dump.list"
sha256sum "$RUN_DIR/target-before.dump" >"$RUN_DIR/target-before.dump.sha256"

target_mutated="true"
reset_and_restore_target "$RUN_DIR/render-final.dump"
install_target_manifest
target_manifest "$RUN_DIR/target-restored-raw.txt" legacy-provider-sessions
canonical_manifest "$RUN_DIR/target-restored-raw.txt" \
  "$RUN_DIR/target-restored-raw.safe" legacy-provider-sessions
cmp --silent "$RUN_DIR/source-snapshot.safe" "$RUN_DIR/target-restored-raw.safe" ||
  fail "the restored OVH manifest does not exactly match the stable Render snapshot"

legacy_compat_marker='SCHEMA_COMPAT|legacy_missing_table|billing_payment_provider_sessions|integrity_checks_deferred_to_current_schema_migration'
legacy_provider_sessions_missing="false"
if grep -Fxq "$legacy_compat_marker" "$RUN_DIR/target-restored-raw.txt"; then
  legacy_provider_sessions_missing="true"
fi

# Only after raw source/target equality is proven may the owner migration add
# the current schema. Long-lived API/worker services remain stopped throughout.
provision_target_schema_and_roles
target_manifest "$RUN_DIR/target-migrated.txt" strict
canonical_manifest "$RUN_DIR/target-migrated.txt" "$RUN_DIR/target-migrated.safe" strict
grep -E '^(DATABASE|TABLE|STATUS)\|' "$RUN_DIR/target-restored-raw.txt" |
  LC_ALL=C sort >"$RUN_DIR/target-before-migration.data"
grep -E '^(DATABASE|TABLE|STATUS)\|' "$RUN_DIR/target-migrated.txt" |
  LC_ALL=C sort >"$RUN_DIR/target-after-migration.data"
if [[ "$legacy_provider_sessions_missing" == "true" ]]; then
  grep -Fxq 'TABLE|billing_payment_provider_sessions|0|0|0' \
    "$RUN_DIR/target-migrated.txt" ||
    fail "the migrated payment-provider session table is missing or unexpectedly non-empty"
  grep -Fv 'TABLE|billing_payment_provider_sessions|' \
    "$RUN_DIR/target-after-migration.data" \
    >"$RUN_DIR/target-after-migration.comparable"
else
  cp -- "$RUN_DIR/target-after-migration.data" \
    "$RUN_DIR/target-after-migration.comparable"
fi
cmp --silent "$RUN_DIR/target-before-migration.data" \
  "$RUN_DIR/target-after-migration.comparable" ||
  fail "the current schema migration changed pre-existing source data"
python3 "$SCHEMA_VERIFIER" transition \
  --before "$RUN_DIR/target-restored-raw.txt" \
  --after "$RUN_DIR/target-migrated.txt" \
  --contract "$SCHEMA_CONTRACT" \
  --preserved-contract "$PRESERVED_SCHEMA_CONTRACT" \
  >"$RUN_DIR/schema-transition.txt" ||
  fail "the current schema migration escaped its exact reviewed schema contract"

# Exercise the actual role-specific API and worker images, their generated env
# files and the least-privilege PostgreSQL role. These are one-shot probes only;
# neither long-running writer is started.
probe_application_role api
probe_application_role worker
target_manifest "$RUN_DIR/target-after-probes.txt" strict
canonical_manifest "$RUN_DIR/target-after-probes.txt" \
  "$RUN_DIR/target-after-probes.safe" strict
cmp --silent "$RUN_DIR/target-migrated.safe" "$RUN_DIR/target-after-probes.safe" ||
  fail "application/worker probes changed the migrated target"
target_security_manifest "$RUN_DIR/postgres-security-final.txt" \
  "$RUN_DIR/postgres-security-final.safe" ||
  fail "the target PostgreSQL authority manifest could not be proved"
POSTGRES_SECURITY_MANIFEST_SHA256="$(
  sha256sum "$RUN_DIR/postgres-security-final.safe" | awk '{print $1}'
)"
[[ "$POSTGRES_SECURITY_MANIFEST_SHA256" =~ ^[0-9a-f]{64}$ ]] ||
  fail "the target PostgreSQL authority manifest digest is invalid"
POSTGRES_ROLE_LOGIN_PROBE_SHA256="$(bash "$POSTGRES_ROLE_LOGIN_PROBE")" ||
  fail "the trusted target PostgreSQL role login proof failed"
[[ "$POSTGRES_ROLE_LOGIN_PROBE_SHA256" =~ ^[0-9a-f]{64}$ ]] ||
  fail "the target PostgreSQL role login proof digest is invalid"
target_stopped || fail "an OVH API/worker writer became active during target verification"
assert_render_frozen || fail "Render left freeze mode while the target migration was verified"
assert_render_worker_and_queue_stopped ||
  fail "the Render worker or queue became active while the target migration was verified"
[[ "$(render_pending_count)" == "0" ]] ||
  fail "a provider payment became pending while the target migration was verified"

{
  printf 'status=postgres-cutover-verified\n'
  printf 'verified_at_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf 'source_manifest_sha256=%s\n' "$(sha256sum "$RUN_DIR/source-snapshot.safe" | awk '{print $1}')"
  printf 'migrated_target_manifest_sha256=%s\n' "$(sha256sum "$RUN_DIR/target-migrated.safe" | awk '{print $1}')"
  printf 'source_legacy_provider_sessions_missing=%s\n' "$legacy_provider_sessions_missing"
  printf 'source_dump_sha256=%s\n' "$(cut -d' ' -f1 "$RUN_DIR/render-final.dump.sha256")"
  printf 'target_rollback_dump_sha256=%s\n' "$(cut -d' ' -f1 "$RUN_DIR/target-before.dump.sha256")"
  printf 'pending_payments_before=0\n'
  printf 'pending_payments_after=0\n'
  printf 'api_role_probe=verified\n'
  printf 'worker_role_probe=verified\n'
  printf 'source_worker_stop_evidence_sha256=%s\n' "$SOURCE_WORKER_STOP_EVIDENCE_SHA256"
  printf 'postgres_security_manifest_sha256=%s\n' "$POSTGRES_SECURITY_MANIFEST_SHA256"
  printf 'postgres_role_login_probe_sha256=%s\n' "$POSTGRES_ROLE_LOGIN_PROBE_SHA256"
  printf 'caddy_changed=false\n'
  printf 'api_worker_started=false\n'
} >"$RUN_DIR/CUTOVER_VERIFIED"
chmod 0600 "$RUN_DIR/CUTOVER_VERIFIED"
# The target has already passed every data/role invariant. From this point an
# evidence-write failure must leave the global fence in place, not roll a
# verified target back and accidentally leave a stale success proof.
migration_verified="true"
target_mutated="false"
python3 "$CUTOVER_EVIDENCE_TOOL" write-postgres \
  --cutover-id "$CUTOVER_ID" \
  --revision "$EXPECTED_BUILD_REVISION" \
  --source-fingerprint "$SOURCE_FINGERPRINT" \
  --run-id "$(basename -- "$RUN_DIR")" \
  --manifest-sha256 "$(sha256sum "$RUN_DIR/source-snapshot.safe" | awk '{print $1}')" \
  --migrated-manifest-sha256 "$(sha256sum "$RUN_DIR/target-migrated.safe" | awk '{print $1}')" \
  --postgres-security-manifest-sha256 "$POSTGRES_SECURITY_MANIFEST_SHA256" \
  --postgres-role-login-probe-sha256 "$POSTGRES_ROLE_LOGIN_PROBE_SHA256" \
  --source-dump-sha256 "$(cut -d' ' -f1 "$RUN_DIR/render-final.dump.sha256")" \
  --source-worker-stop-evidence-sha256 "$SOURCE_WORKER_STOP_EVIDENCE_SHA256" \
  --rollback-dump-sha256 "$(cut -d' ' -f1 "$RUN_DIR/target-before.dump.sha256")" ||
  fail "the global PostgreSQL cutover proof could not be recorded"
rm -f -- "$FAIL_STOP_MARKER"
echo "PostgreSQL cutover data is verified. API/worker remain stopped and Caddy was not changed; complete Redis migration and final acceptance gates before traffic cutover."
