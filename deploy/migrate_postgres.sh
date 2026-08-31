#!/usr/bin/env bash
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
PROVISION_ROLE="$ROOT_DIR/deploy/provision_database_role.sh"
CUTOVER_EVIDENCE_TOOL="$ROOT_DIR/deploy/provider_cutover_evidence.py"
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
for path in "$MANIFEST" "$PROVISION_ROLE" "$CUTOVER_EVIDENCE_TOOL" "$ROOT_DIR/compose.yaml"; do
  [[ -f "$path" && ! -L "$path" ]] || fail "missing or unsafe cutover input: $path"
done
command -v docker >/dev/null 2>&1 || fail "Docker is unavailable"
command -v python3 >/dev/null 2>&1 || fail "Python is unavailable"

set -a
# shellcheck disable=SC1090
source "$SOURCE_ENV_FILE"
set +a
: "${SOURCE_DATABASE_URL:?Missing SOURCE_DATABASE_URL}"
: "${SOURCE_HEALTH_URL:?Missing SOURCE_HEALTH_URL}"
render_source_database_url="$SOURCE_DATABASE_URL"
render_source_health_url="$SOURCE_HEALTH_URL"
render_source_redis_url="${SOURCE_REDIS_URL:-${REDIS_URL:-}}"
render_source_broker_url="${SOURCE_CELERY_BROKER_URL:-${CELERY_BROKER_URL:-}}"
set -a
# shellcheck disable=SC1090
source "$RUNTIME_ENV_FILE"
# shellcheck disable=SC1090
source "$DB_ENV_FILE"
set +a
SOURCE_DATABASE_URL="$render_source_database_url"
SOURCE_HEALTH_URL="$render_source_health_url"
SOURCE_REDIS_URL="$render_source_redis_url"
SOURCE_CELERY_BROKER_URL="$render_source_broker_url"
export SOURCE_DATABASE_URL SOURCE_HEALTH_URL SOURCE_REDIS_URL SOURCE_CELERY_BROKER_URL
[[ "$CUTOVER_ID" =~ ^[0-9a-f]{32}$ ]] ||
  fail "LECTURESIFT_PROVIDER_CUTOVER_ID must be exactly 32 lowercase hex characters"
[[ "$EXPECTED_BUILD_REVISION" =~ ^[0-9a-f]{40}$ ]] ||
  fail "LECTURESIFT_EXPECTED_BUILD_REVISION must be the exact 40-character release commit"
SOURCE_FINGERPRINT="$(python3 "$CUTOVER_EVIDENCE_TOOL" source-fingerprint)" ||
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

# The external source must be the TLS Render endpoint, never the local target.
python3 - <<'PY' || fail "SOURCE_DATABASE_URL is not a distinct TLS Render database"
import os
from urllib.parse import parse_qs, urlsplit

url = urlsplit(os.environ["SOURCE_DATABASE_URL"])
host = (url.hostname or "").lower().rstrip(".")
query = parse_qs(url.query)
valid = (
    url.scheme in {"postgres", "postgresql"}
    and bool(url.username) and bool(url.password) and bool(url.path.strip("/"))
    and host.endswith(".render.com")
    and host not in {"localhost", "postgres", "127.0.0.1", "::1"}
    and query.get("sslmode", [""])[0] in {"require", "verify-ca", "verify-full"}
)
raise SystemExit(0 if valid else 1)
PY
python3 - <<'PY' || fail "SOURCE_HEALTH_URL must be an HTTPS Render health endpoint"
import os
from urllib.parse import urlsplit

url = urlsplit(os.environ["SOURCE_HEALTH_URL"])
host = (url.hostname or "").lower().rstrip(".")
valid = url.scheme == "https" and host.endswith(".onrender.com") and url.path.rstrip("/").endswith("/health")
raise SystemExit(0 if valid else 1)
PY
[[ "$SOURCE_REDIS_URL" =~ ^rediss?:// && "$SOURCE_REDIS_URL" != *"@redis:6379"* ]] ||
  fail "the source environment must contain its remote Redis URL"
[[ "$SOURCE_CELERY_BROKER_URL" =~ ^rediss?:// &&
   "$SOURCE_CELERY_BROKER_URL" != *"@redis:6379"* ]] ||
  fail "the source environment must contain its remote Celery broker URL"

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
  local source="$1" destination="$2"
  tr -d '\r' <"$source" |
    grep -E '^(DATABASE|SCHEMA|TABLE|ANOMALY|STATUS|UNVALIDATED_FK)\|' |
    LC_ALL=C sort >"$destination"
  [[ "$(grep -c '^DATABASE|' "$destination")" == "1" &&
     "$(grep -c '^SCHEMA|' "$destination")" == "1" &&
     "$(grep -c '^TABLE|' "$destination")" -gt 0 ]] ||
    fail "a database manifest is incomplete"
  if grep -Eq '^(TABLE_DIFF|UNVALIDATED_FK)\|' "$source" ||
     awk -F'|' '$1 == "ANOMALY" && $3 != "0" {bad=1} END {exit(bad ? 0 : 1)}' "$source"; then
    fail "a database manifest contains an integrity anomaly"
  fi
}

run_render_manifest() {
  local output_name="$1"
  [[ "$output_name" =~ ^[a-z0-9-]+[.]txt$ ]] || fail "unsafe manifest output name"
  docker run --rm --user 0:0 \
    --volume "$SOURCE_ENV_FILE:/run/secrets/render-source.env:ro" \
    --volume "$RUN_DIR:/backup" --volume "$ROOT_DIR/deploy:/probe:ro" \
    --env "OUTPUT_NAME=$output_name" postgres:18-bookworm bash -euc '
      set -a
      source /run/secrets/render-source.env
      set +a
      psql --no-psqlrc "$SOURCE_DATABASE_URL" -v ON_ERROR_STOP=1 \
        -f /probe/rehearsal_manifest.sql >"/backup/$OUTPUT_NAME"
    '
}

render_pending_count() {
  docker run --rm --user 0:0 \
    --volume "$SOURCE_ENV_FILE:/run/secrets/render-source.env:ro" \
    --env "PENDING_SQL=$PENDING_SQL" postgres:18-bookworm bash -euc '
      set -a
      source /run/secrets/render-source.env
      set +a
      psql --no-psqlrc "$SOURCE_DATABASE_URL" -v ON_ERROR_STOP=1 \
        --tuples-only --no-align --command "$PENDING_SQL"
    ' | tr -d '\r[:space:]'
}

assert_render_frozen() {
  python3 - <<'PY'
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
  # A flag is not evidence. Establish a real broker connection, prove that no
  # Celery worker responds, then inspect all queue-priority/unacked structures
  # and LectureSift's durable job states through the remote Redis endpoint.
  timeout 30 docker run --rm --pull=never --network bridge --user 0 \
    -e SOURCE_CELERY_BROKER_URL --entrypoint python lecturesift-backend:local -c '
import os
from celery import Celery

app = Celery(broker=os.environ["SOURCE_CELERY_BROKER_URL"])
with app.connection_for_read() as connection:
    connection.ensure_connection(max_retries=1, timeout=8)
replies = app.control.ping(timeout=8)
raise SystemExit(1 if replies else 0)
' >>"$RUN_DIR/source-worker-check.log" 2>&1 || return 1
  timeout 30 docker run --rm --pull=never --network bridge --user 0 \
    -e SOURCE_REDIS_URL --entrypoint python lecturesift-backend:local -c '
import os
from redis import Redis

client = Redis.from_url(os.environ["SOURCE_REDIS_URL"], socket_connect_timeout=8, socket_timeout=15)
assert client.ping()
for key in client.scan_iter(match=b"celery*", count=250):
    if client.type(key) == b"list" and client.llen(key):
        raise SystemExit(1)
if client.hlen("unacked") or client.zcard("unacked_index"):
    raise SystemExit(1)
if client.exists("lecturesift:jobs:v2:write-lock"):
    raise SystemExit(1)
if any(True for _ in client.scan_iter(match="lecturesift:job:*:processing", count=250)):
    raise SystemExit(1)
raw = client.get("lecturesift:jobs:v2")
if raw:
    import json
    jobs = json.loads(raw).get("jobs", {})
    if any(str(job.get("status", "")) in {"queued", "working"} for job in jobs.values()):
        raise SystemExit(1)
' >>"$RUN_DIR/source-queue-check.log" 2>&1
}

target_manifest() {
  local destination="$1"
  "${compose[@]}" exec -T postgres psql --no-psqlrc --quiet \
    --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" -v ON_ERROR_STOP=1 \
    -f /tmp/lecturesift-cutover-manifest.sql >"$destination"
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
    --locale en_US.UTF-8 --owner "$POSTGRES_USER" "$POSTGRES_DB"
  "${compose[@]}" exec -T postgres pg_restore --username "$POSTGRES_USER" \
    --exit-on-error --single-transaction --no-owner --no-acl \
    --dbname "$POSTGRES_DB" <"$dump"
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
       target_manifest "$RUN_DIR/target-rollback-check.txt"; then
      tr -d '\r' <"$RUN_DIR/target-rollback-check.txt" |
        grep -E '^(DATABASE|SCHEMA|TABLE|ANOMALY|STATUS|UNVALIDATED_FK)\|' |
        LC_ALL=C sort >"$RUN_DIR/target-rollback-check.safe"
      if cmp --silent "$RUN_DIR/target-before.safe" "$RUN_DIR/target-rollback-check.safe"; then
        rollback_ok="true"
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
  unset SOURCE_DATABASE_URL SOURCE_HEALTH_URL SOURCE_REDIS_URL SOURCE_CELERY_BROKER_URL
  unset POSTGRES_PASSWORD
  unset LECTURESIFT_APP_DB_PASSWORD LECTURESIFT_WORKER_DB_PASSWORD
  unset DATABASE_URL LECTURESIFT_WORKER_DATABASE_URL
  unset render_source_database_url render_source_health_url
  unset render_source_redis_url render_source_broker_url
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
canonical_manifest "$RUN_DIR/source-before.txt" "$RUN_DIR/source-before.safe"
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
docker run -d --name "$SNAPSHOT_CONTAINER" --user 0:0 \
  --volume "$SOURCE_ENV_FILE:/run/secrets/render-source.env:ro" \
  --volume "$RUN_DIR:/backup" postgres:18-bookworm bash -euc '
    set -a
    source /run/secrets/render-source.env
    set +a
    psql --no-psqlrc "$SOURCE_DATABASE_URL" -v ON_ERROR_STOP=1 <<"SQL"
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

docker run --rm --user 0:0 \
  --volume "$SOURCE_ENV_FILE:/run/secrets/render-source.env:ro" \
  --volume "$RUN_DIR:/backup" --volume "$ROOT_DIR/deploy:/probe:ro" \
  --env "SNAPSHOT_ID=$SNAPSHOT_ID" postgres:18-bookworm bash -euc '
    set -a
    source /run/secrets/render-source.env
    set +a
    {
      printf "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;\n"
      printf "SET TRANSACTION SNAPSHOT '\''%s'\'';\n" "$SNAPSHOT_ID"
      cat /probe/rehearsal_manifest.sql
      printf "COMMIT;\n"
    } | psql --no-psqlrc "$SOURCE_DATABASE_URL" -v ON_ERROR_STOP=1 \
      > /backup/source-snapshot.txt
    pg_dump "$SOURCE_DATABASE_URL" --format=custom --no-owner --no-acl \
      --snapshot "$SNAPSHOT_ID" --file=/backup/render-final.dump
  '
close_snapshot
docker run --rm --user 0:0 --volume "$RUN_DIR:/backup:ro" postgres:18-bookworm \
  pg_restore --list /backup/render-final.dump >"$RUN_DIR/render-final.dump.list"
sha256sum "$RUN_DIR/render-final.dump" >"$RUN_DIR/render-final.dump.sha256"
canonical_manifest "$RUN_DIR/source-snapshot.txt" "$RUN_DIR/source-snapshot.safe"

assert_render_frozen || fail "Render left freeze mode during the source capture"
assert_render_worker_and_queue_stopped ||
  fail "the Render worker or queue became active during source capture"
pending_after="$(render_pending_count)"
[[ "$pending_after" == "0" ]] || fail "a provider payment became pending during source capture"
run_render_manifest source-after.txt
canonical_manifest "$RUN_DIR/source-after.txt" "$RUN_DIR/source-after.safe"
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
docker run --rm --user 0:0 --volume "$RUN_DIR:/backup:ro" postgres:18-bookworm \
  pg_restore --list /backup/target-before.dump >"$RUN_DIR/target-before.dump.list"
sha256sum "$RUN_DIR/target-before.dump" >"$RUN_DIR/target-before.dump.sha256"

target_mutated="true"
reset_and_restore_target "$RUN_DIR/render-final.dump"
install_target_manifest
target_manifest "$RUN_DIR/target-restored.txt"
canonical_manifest "$RUN_DIR/target-restored.txt" "$RUN_DIR/target-restored.safe"
cmp --silent "$RUN_DIR/source-snapshot.safe" "$RUN_DIR/target-restored.safe" ||
  fail "the restored OVH manifest does not exactly match the stable Render snapshot"

# Exercise the actual role-specific API and worker images, their generated env
# files and the least-privilege PostgreSQL role. These are one-shot probes only;
# neither long-running writer is started.
probe_application_role api
probe_application_role worker
target_manifest "$RUN_DIR/target-after-probes.txt"
canonical_manifest "$RUN_DIR/target-after-probes.txt" "$RUN_DIR/target-after-probes.safe"
cmp --silent "$RUN_DIR/source-snapshot.safe" "$RUN_DIR/target-after-probes.safe" ||
  fail "application/worker probes changed the restored data"

{
  printf 'status=postgres-cutover-verified\n'
  printf 'verified_at_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf 'source_manifest_sha256=%s\n' "$(sha256sum "$RUN_DIR/source-snapshot.safe" | awk '{print $1}')"
  printf 'source_dump_sha256=%s\n' "$(cut -d' ' -f1 "$RUN_DIR/render-final.dump.sha256")"
  printf 'target_rollback_dump_sha256=%s\n' "$(cut -d' ' -f1 "$RUN_DIR/target-before.dump.sha256")"
  printf 'pending_payments_before=0\n'
  printf 'pending_payments_after=0\n'
  printf 'api_role_probe=verified\n'
  printf 'worker_role_probe=verified\n'
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
  --source-dump-sha256 "$(cut -d' ' -f1 "$RUN_DIR/render-final.dump.sha256")" \
  --rollback-dump-sha256 "$(cut -d' ' -f1 "$RUN_DIR/target-before.dump.sha256")" ||
  fail "the global PostgreSQL cutover proof could not be recorded"
rm -f -- "$FAIL_STOP_MARKER"
echo "PostgreSQL cutover data is verified. API/worker remain stopped and Caddy was not changed; complete Redis migration and final acceptance gates before traffic cutover."
