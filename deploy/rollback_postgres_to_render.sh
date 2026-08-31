#!/usr/bin/env bash
set -euo pipefail
umask 077

# PostgreSQL reconciliation for an explicit OVH -> still-fenced Render
# rollback. This is a whole-database replacement, never a guessed row merge.
# Redis/R2 reconciliation and traffic switching are intentionally separate.

if [[ "$(id -u)" != "0" ]]; then
  echo "Run PostgreSQL rollback reconciliation as root." >&2
  exit 1
fi
for flag in LECTURESIFT_POSTGRES_ROLLBACK_CONFIRM LECTURESIFT_OVH_FROZEN \
  LECTURESIFT_OVH_WORKER_STOPPED LECTURESIFT_RENDER_FROZEN \
  LECTURESIFT_RENDER_WORKER_STOPPED LECTURESIFT_PROVIDER_RECONCILED; do
  [[ "${!flag:-}" == "YES" ]] || {
    echo "Set every rollback fence/reconciliation flag only after both providers are frozen and drained." >&2
    exit 1
  }
done

ROOT_DIR="${LECTURESIFT_ROOT:-/opt/lecturesift}"
SOURCE_ENV_FILE="/root/.lecturesift-render-source.env"
DB_ENV_FILE="/etc/lecturesift/postgres.env"
RUNTIME_ENV_FILE="/etc/lecturesift/runtime.env"
MANIFEST="$ROOT_DIR/deploy/rehearsal_manifest.sql"
ALLOWED_BACKUP_ROOT="/var/backups/lecturesift/postgres-rollback"
FAIL_STOP_ROOT="/var/lib/lecturesift/migration-fail-stop"
FAIL_STOP_MARKER="$FAIL_STOP_ROOT/postgres-rollback-unproven"
PENDING_SQL="SELECT (SELECT count(*) FROM billing_manual_orders WHERE status = 'pending') + (SELECT count(*) FROM billing_payment_orders WHERE status IN ('created', 'pending'));"
compose=(docker compose --project-directory "$ROOT_DIR" --file "$ROOT_DIR/compose.yaml")

fail() {
  echo "PostgreSQL rollback reconciliation failed: $*" >&2
  exit 1
}

check_private() {
  local path="$1" label="$2" mode
  [[ -f "$path" && ! -L "$path" && "$(stat -c '%u' -- "$path")" == "0" ]] ||
    fail "$label must be a root-owned regular non-symlink file"
  mode="$(stat -c '%a' -- "$path")"
  case "$mode" in 400|600) ;; *) fail "$label must have mode 0400 or 0600" ;; esac
}

[[ "${LECTURESIFT_SOURCE_ENV_FILE:-$SOURCE_ENV_FILE}" == "$SOURCE_ENV_FILE" ]] ||
  fail "the Render environment path is fixed"
[[ "${LECTURESIFT_DB_ENV_FILE:-$DB_ENV_FILE}" == "$DB_ENV_FILE" ]] ||
  fail "the target database environment path is fixed"
[[ "${LECTURESIFT_ENV_FILE:-$RUNTIME_ENV_FILE}" == "$RUNTIME_ENV_FILE" ]] ||
  fail "the runtime environment path is fixed"
[[ "${LECTURESIFT_POSTGRES_ROLLBACK_ROOT:-$ALLOWED_BACKUP_ROOT}" == "$ALLOWED_BACKUP_ROOT" ]] ||
  fail "the rollback backup root is fixed"
check_private "$SOURCE_ENV_FILE" "Render environment"
check_private "$DB_ENV_FILE" "OVH database environment"
check_private "$RUNTIME_ENV_FILE" "OVH runtime environment"
[[ -f "$MANIFEST" && ! -L "$MANIFEST" ]] || fail "the migration manifest is missing or unsafe"

set -a
# shellcheck disable=SC1090
source "$RUNTIME_ENV_FILE"
OVH_HEALTH_URL="${OVH_HEALTH_URL:-${PUBLIC_BASE_URL%/}/health}"
# shellcheck disable=SC1090
source "$SOURCE_ENV_FILE"
RENDER_CELERY_BROKER_URL="${SOURCE_CELERY_BROKER_URL:-${CELERY_BROKER_URL:-}}"
# shellcheck disable=SC1090
source "$DB_ENV_FILE"
set +a
: "${SOURCE_DATABASE_URL:?Missing SOURCE_DATABASE_URL}"
: "${SOURCE_HEALTH_URL:?Missing SOURCE_HEALTH_URL}"
: "${POSTGRES_DB:?Missing POSTGRES_DB}"
: "${POSTGRES_USER:?Missing POSTGRES_USER}"
[[ "$RENDER_CELERY_BROKER_URL" =~ ^rediss?:// &&
   "$RENDER_CELERY_BROKER_URL" != *"@redis:6379"* ]] ||
  fail "the Render environment must contain its remote Celery broker URL"

python3 - <<'PY' || fail "the Render rollback target is not a TLS Render PostgreSQL URL"
import os
from urllib.parse import parse_qs, urlsplit

url = urlsplit(os.environ["SOURCE_DATABASE_URL"])
host = (url.hostname or "").lower().rstrip(".")
valid = (
    url.scheme in {"postgres", "postgresql"}
    and bool(url.username) and bool(url.password) and bool(url.path.strip("/"))
    and host.endswith(".render.com")
    and parse_qs(url.query).get("sslmode", [""])[0] in {"require", "verify-ca", "verify-full"}
)
raise SystemExit(0 if valid else 1)
PY
python3 - <<'PY' || fail "SOURCE_HEALTH_URL must be the direct HTTPS Render health endpoint"
import os
from urllib.parse import urlsplit

url = urlsplit(os.environ["SOURCE_HEALTH_URL"])
host = (url.hostname or "").lower().rstrip(".")
valid = url.scheme == "https" and host.endswith(".onrender.com") and url.path.rstrip("/").endswith("/health")
raise SystemExit(0 if valid else 1)
PY

allowed_root="$(realpath -m -- "$ALLOWED_BACKUP_ROOT")"
[[ "$allowed_root" == "$ALLOWED_BACKUP_ROOT" ]] || fail "the fixed rollback root resolves through a symlink"
install -d -o root -g root -m 0700 -- "$allowed_root" "$FAIL_STOP_ROOT"
BACKUP_ROOT="$(realpath -e -- "$allowed_root")"
[[ "$BACKUP_ROOT" == "$ALLOWED_BACKUP_ROOT" && ! -L "$BACKUP_ROOT" ]] ||
  fail "the rollback root is not the fixed real directory"
root_mode="$(stat -c '%a' -- "$BACKUP_ROOT")"
[[ "$(stat -c '%u' -- "$BACKUP_ROOT")" == "0" ]] && (( (8#$root_mode & 8#077) == 0 )) ||
  fail "the rollback root must be private to root"
[[ ! -L "$FAIL_STOP_ROOT" && "$(realpath -e -- "$FAIL_STOP_ROOT")" == "$FAIL_STOP_ROOT" ]] ||
  fail "the fail-stop root is unsafe"
[[ ! -e "$FAIL_STOP_MARKER" && ! -L "$FAIL_STOP_MARKER" ]] ||
  fail "a previous Render rollback left unproven state"

SHARED_BACKUP_LOCK_ROOT="/var/backups/lecturesift"
[[ -d "$SHARED_BACKUP_LOCK_ROOT" && ! -L "$SHARED_BACKUP_LOCK_ROOT" &&
   "$(realpath -e -- "$SHARED_BACKUP_LOCK_ROOT")" == "$SHARED_BACKUP_LOCK_ROOT" &&
   "$(stat -c '%u' -- "$SHARED_BACKUP_LOCK_ROOT")" == "0" ]] ||
  fail "the shared backup/restore lock root is unsafe"
lock_mode="$(stat -c '%a' -- "$SHARED_BACKUP_LOCK_ROOT")"
(( (8#$lock_mode & 8#077) == 0 )) || fail "the shared lock root must be private to root"
exec 9>"$SHARED_BACKUP_LOCK_ROOT/.backup.lock"
flock -n 9 || fail "a backup, restore or migration is already active"

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
RUN_DIR="$BACKUP_ROOT/postgres-rollback-$STAMP"
mkdir -m 0700 -- "$RUN_DIR"
snapshot_export_pid=""
snapshot_app_name="lecturesift-rollback-snapshot-$STAMP"
snapshot_info_file="/tmp/lecturesift-rollback-snapshot-$STAMP"
render_mutated="false"
render_verified="false"

write_marker() {
  local state="$1" action="$2" tmp
  tmp="$(mktemp -- "$FAIL_STOP_ROOT/.postgres-rollback-XXXXXXXX")"
  {
    printf 'status=%s\n' "$state"
    printf 'recorded_at_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'rollback_run=%s\n' "$(basename -- "$RUN_DIR")"
    printf 'operator_action=%s\n' "$action"
  } >"$tmp"
  chmod 0600 "$tmp"
  mv -f -- "$tmp" "$FAIL_STOP_MARKER"
}

close_snapshot() {
  local remaining=""
  if [[ -n "$snapshot_export_pid" ]]; then
    "${compose[@]}" exec -T postgres psql --no-psqlrc --quiet \
      --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
      --variable=snapshot_app="$snapshot_app_name" <<'SQL' >/dev/null 2>&1 || true
SELECT pg_terminate_backend(pid)
FROM pg_stat_activity
WHERE application_name = :'snapshot_app' AND pid <> pg_backend_pid();
SQL
    remaining="$("${compose[@]}" exec -T postgres psql --no-psqlrc --quiet \
      --tuples-only --no-align --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
      --variable=snapshot_app="$snapshot_app_name" <<'SQL' 2>/dev/null || true
SELECT count(*) FROM pg_stat_activity WHERE application_name = :'snapshot_app';
SQL
)"
    for _ in $(seq 1 50); do
      kill -0 "$snapshot_export_pid" >/dev/null 2>&1 || break
      sleep 0.1
    done
    kill "$snapshot_export_pid" >/dev/null 2>&1 || true
    wait "$snapshot_export_pid" >/dev/null 2>&1 || true
    snapshot_export_pid=""
    [[ "$(printf '%s' "$remaining" | tr -d '\r[:space:]')" == "0" ]] || return 1
  fi
  "${compose[@]}" exec -T postgres rm -f "$snapshot_info_file" >/dev/null 2>&1 || true
}

canonical() {
  local input="$1" output="$2"
  tr -d '\r' <"$input" |
    grep -E '^(DATABASE|SCHEMA|TABLE|ANOMALY|STATUS|UNVALIDATED_FK)\|' |
    LC_ALL=C sort >"$output"
  [[ "$(grep -c '^DATABASE|' "$output")" == "1" &&
     "$(grep -c '^SCHEMA|' "$output")" == "1" &&
     "$(grep -c '^TABLE|' "$output")" -gt 0 ]] || fail "a rollback manifest is incomplete"
  if grep -Eq '^(TABLE_DIFF|UNVALIDATED_FK)\|' "$input" ||
     awk -F'|' '$1 == "ANOMALY" && $3 != "0" {bad=1} END {exit(bad ? 0 : 1)}' "$input"; then
    fail "a rollback manifest contains integrity anomalies"
  fi
}

check_health_freeze() {
  local url="$1" label="$2"
  HEALTH_CHECK_URL="$url" python3 - <<'PY' || fail "$label did not acknowledge exact freeze mode"
import json
import os
import ssl
import urllib.request

request = urllib.request.Request(os.environ["HEALTH_CHECK_URL"], headers={"User-Agent": "LectureSift-Rollback/1"})
with urllib.request.urlopen(request, timeout=15, context=ssl.create_default_context()) as response:
    payload = json.load(response)
raise SystemExit(0 if response.status == 200 and payload.get("ok") is True and payload.get("maintenance_mode") == "freeze" else 1)
PY
}

assert_render_worker_stopped() {
  timeout 30 docker run --rm --pull=never --network bridge --user 0 \
    -e RENDER_CELERY_BROKER_URL --entrypoint python lecturesift-backend:local -c '
import os
from celery import Celery

app = Celery(broker=os.environ["RENDER_CELERY_BROKER_URL"])
with app.connection_for_read() as connection:
    connection.ensure_connection(max_retries=1, timeout=8)
raise SystemExit(1 if app.control.ping(timeout=8) else 0)
' >>"$RUN_DIR/render-worker-check.log" 2>&1
}

render_command() {
  local operation="$1" output_name="${2:-}"
  [[ "$operation" =~ ^(manifest|pending|dump|reset-restore)$ ]] || fail "unsafe Render operation"
  [[ -z "$output_name" || "$output_name" =~ ^[a-z0-9-]+[.](txt|dump)$ ]] || fail "unsafe Render output"
  docker run --rm --user 0:0 \
    --volume "$SOURCE_ENV_FILE:/run/secrets/render-source.env:ro" \
    --volume "$RUN_DIR:/backup" --volume "$ROOT_DIR/deploy:/probe:ro" \
    --env "OPERATION=$operation" --env "OUTPUT_NAME=$output_name" \
    --env "PENDING_SQL=$PENDING_SQL" postgres:18-bookworm bash -euc '
      set -a
      source /run/secrets/render-source.env
      set +a
      case "$OPERATION" in
        manifest)
          psql --no-psqlrc "$SOURCE_DATABASE_URL" -v ON_ERROR_STOP=1 \
            -f /probe/rehearsal_manifest.sql >"/backup/$OUTPUT_NAME"
          ;;
        pending)
          psql --no-psqlrc "$SOURCE_DATABASE_URL" -v ON_ERROR_STOP=1 \
            --tuples-only --no-align --command "$PENDING_SQL"
          ;;
        dump)
          pg_dump "$SOURCE_DATABASE_URL" --format=custom --no-owner --no-acl \
            --serializable-deferrable --file="/backup/$OUTPUT_NAME"
          ;;
        reset-restore)
          psql --no-psqlrc "$SOURCE_DATABASE_URL" -v ON_ERROR_STOP=1 \
            --command "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
          pg_restore --dbname "$SOURCE_DATABASE_URL" --exit-on-error \
            --single-transaction --no-owner --no-acl "/backup/$OUTPUT_NAME"
          ;;
      esac
    '
}

local_pending() {
  "${compose[@]}" exec -T postgres psql --no-psqlrc --quiet \
    --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
    --tuples-only --no-align --command "$PENDING_SQL" | tr -d '\r[:space:]'
}

queue_idle() {
  local pending unacked lock
  lock="$("${compose[@]}" exec -T redis redis-cli --raw EXISTS lecturesift:jobs:v2:write-lock | tr -d '\r')" || return 1
  pending="$("${compose[@]}" exec -T redis redis-cli --raw LLEN celery | tr -d '\r')" || return 1
  unacked="$("${compose[@]}" exec -T redis redis-cli --raw HLEN unacked | tr -d '\r')" || return 1
  [[ "$lock" == "0" && "$pending" == "0" && "$unacked" == "0" ]] || return 1
  ! "${compose[@]}" exec -T redis redis-cli --scan --pattern 'lecturesift:job:*:processing' | grep -q .
}

cleanup() {
  local status="$?" restored="false"
  trap - EXIT
  set +e
  close_snapshot || true
  "${compose[@]}" exec -T postgres rm -f /tmp/lecturesift-rollback-manifest.sql >/dev/null 2>&1 || true
  if [[ "$status" != "0" && "$render_mutated" == "true" && "$render_verified" != "true" ]]; then
    echo "The Render replacement failed; restoring its root-only pre-operation dump." >&2
    if render_command reset-restore render-before.dump &&
       render_command manifest render-rollback-check.txt; then
      tr -d '\r' <"$RUN_DIR/render-rollback-check.txt" |
        grep -E '^(DATABASE|SCHEMA|TABLE|ANOMALY|STATUS|UNVALIDATED_FK)\|' |
        LC_ALL=C sort >"$RUN_DIR/render-rollback-check.safe"
      cmp --silent "$RUN_DIR/render-before.safe" "$RUN_DIR/render-rollback-check.safe" && restored="true"
    fi
    if [[ "$restored" == "true" ]]; then
      write_marker "postgres-rollback-aborted-render-restored" \
        "inspect-both-frozen-databases-and-clear-marker-manually"
    else
      write_marker "postgres-rollback-render-unproven" \
        "repair-still-fenced-render-from-render-before-dump"
    fi
    status=1
  elif [[ "$render_verified" == "true" ]]; then
    rm -f -- "$FAIL_STOP_MARKER"
  else
    rm -f -- "$FAIL_STOP_MARKER"
  fi
  unset SOURCE_DATABASE_URL SOURCE_HEALTH_URL RENDER_CELERY_BROKER_URL POSTGRES_PASSWORD
  exit "$status"
}
trap cleanup EXIT

write_marker "postgres-rollback-in-progress" \
  "keep-both-providers-frozen-and-do-not-change-traffic"
check_health_freeze "$OVH_HEALTH_URL" "OVH"
check_health_freeze "$SOURCE_HEALTH_URL" "Render"
assert_render_worker_stopped || fail "the Render worker stopped state could not be independently proved"
"${compose[@]}" stop --timeout 600 api worker
for service in api worker; do
  container="$("${compose[@]}" ps -q "$service")"
  [[ -z "$container" || "$(docker inspect -f '{{.State.Running}}' "$container")" == "false" ]] ||
    fail "the OVH $service writer is still running"
done
queue_idle || fail "the OVH queue is not completely drained"
[[ "$(local_pending)" == "0" ]] || fail "OVH still has pending provider payments"
[[ "$(render_command pending | tr -d '\r[:space:]')" == "0" ]] ||
  fail "Render still has pending provider payments"

"${compose[@]}" cp "$MANIFEST" postgres:/tmp/lecturesift-rollback-manifest.sql
"${compose[@]}" exec -T postgres psql --no-psqlrc --quiet \
  --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" -v ON_ERROR_STOP=1 \
  -f /tmp/lecturesift-rollback-manifest.sql >"$RUN_DIR/ovh-before.txt"
canonical "$RUN_DIR/ovh-before.txt" "$RUN_DIR/ovh-before.safe"
ovh_size="$(sed -n 's/^DATABASE_SIZE|//p' "$RUN_DIR/ovh-before.txt" | tr -d '\r')"
[[ "$(grep -c '^DATABASE_SIZE|' "$RUN_DIR/ovh-before.txt")" == "1" &&
   "$ovh_size" =~ ^[1-9][0-9]*$ && "$ovh_size" -le 1000000000000 ]] ||
  fail "the OVH database size is invalid or exceeds the supported bound"
available_bytes="$(df --output=avail -B1 -- "$BACKUP_ROOT" | awk 'NR == 2 {print $1}')"
[[ "$available_bytes" =~ ^[0-9]+$ ]] || fail "rollback storage capacity cannot be proven"
required_bytes=$((ovh_size * 4 + 5368709120))
(( available_bytes >= required_bytes )) ||
  fail "insufficient rollback storage while preserving 5 GiB of host reserve"
"${compose[@]}" exec -T postgres rm -f "$snapshot_info_file" >/dev/null 2>&1 || true
"${compose[@]}" exec -T -e "PGAPPNAME=$snapshot_app_name" postgres \
  psql --no-psqlrc --quiet --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  >"$RUN_DIR/snapshot-export.log" 2>&1 <<SQL &
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
  if "${compose[@]}" exec -T postgres test -s "$snapshot_info_file" >/dev/null 2>&1; then
    snapshot_info="$("${compose[@]}" exec -T postgres cat "$snapshot_info_file" | tr -d '\r\n')"
    break
  fi
  kill -0 "$snapshot_export_pid" >/dev/null 2>&1 || break
  sleep 0.1
done
[[ "$snapshot_info" =~ ^[0-9]+\|[0-9A-Fa-f]+-[0-9A-Fa-f]+-[0-9A-Fa-f]+$ ]] ||
  fail "the OVH consistent snapshot could not be exported"
snapshot_id="${snapshot_info#*|}"
"${compose[@]}" exec -T postgres pg_dump --username "$POSTGRES_USER" \
  --dbname "$POSTGRES_DB" --format custom --no-owner --no-acl \
  --snapshot "$snapshot_id" >"$RUN_DIR/ovh-final.dump"
{
  printf 'BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;\n'
  printf "SET TRANSACTION SNAPSHOT '%s';\n" "$snapshot_id"
  cat "$MANIFEST"
  printf 'COMMIT;\n'
} | "${compose[@]}" exec -T postgres psql --no-psqlrc --quiet \
  --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  >"$RUN_DIR/ovh-final.txt"
close_snapshot || fail "the OVH snapshot could not be closed cleanly"
canonical "$RUN_DIR/ovh-final.txt" "$RUN_DIR/ovh-final.safe"
sha256sum "$RUN_DIR/ovh-final.dump" >"$RUN_DIR/ovh-final.dump.sha256"
docker run --rm --user 0:0 --volume "$RUN_DIR:/backup:ro" postgres:18-bookworm \
  pg_restore --list /backup/ovh-final.dump >"$RUN_DIR/ovh-final.dump.list"
"${compose[@]}" exec -T postgres psql --no-psqlrc --quiet \
  --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" -v ON_ERROR_STOP=1 \
  -f /tmp/lecturesift-rollback-manifest.sql >"$RUN_DIR/ovh-after.txt"
canonical "$RUN_DIR/ovh-after.txt" "$RUN_DIR/ovh-after.safe"
cmp --silent "$RUN_DIR/ovh-before.safe" "$RUN_DIR/ovh-final.safe" ||
  fail "OVH changed before its exported rollback snapshot"
cmp --silent "$RUN_DIR/ovh-final.safe" "$RUN_DIR/ovh-after.safe" ||
  fail "OVH changed during its final rollback dump"
[[ "$(local_pending)" == "0" ]] || fail "an OVH provider payment became pending during reconciliation"

render_command manifest render-before.txt
canonical "$RUN_DIR/render-before.txt" "$RUN_DIR/render-before.safe"
render_size="$(sed -n 's/^DATABASE_SIZE|//p' "$RUN_DIR/render-before.txt" | tr -d '\r')"
[[ "$(grep -c '^DATABASE_SIZE|' "$RUN_DIR/render-before.txt")" == "1" &&
   "$render_size" =~ ^[1-9][0-9]*$ && "$render_size" -le 1000000000000 ]] ||
  fail "the Render database size is invalid or exceeds the supported bound"
available_bytes="$(df --output=avail -B1 -- "$BACKUP_ROOT" | awk 'NR == 2 {print $1}')"
[[ "$available_bytes" =~ ^[0-9]+$ ]] || fail "Render rollback capacity cannot be proven"
required_bytes=$((render_size * 2 + 5368709120))
(( available_bytes >= required_bytes )) ||
  fail "insufficient space for the Render rollback dump while preserving 5 GiB of host reserve"
render_command dump render-before.dump
docker run --rm --user 0:0 --volume "$RUN_DIR:/backup:ro" postgres:18-bookworm \
  pg_restore --list /backup/render-before.dump >"$RUN_DIR/render-before.dump.list"
sha256sum "$RUN_DIR/render-before.dump" >"$RUN_DIR/render-before.dump.sha256"
render_command manifest render-after-capture.txt
canonical "$RUN_DIR/render-after-capture.txt" "$RUN_DIR/render-after-capture.safe"
cmp --silent "$RUN_DIR/render-before.safe" "$RUN_DIR/render-after-capture.safe" ||
  fail "still-fenced Render changed while its rollback dump was captured"
check_health_freeze "$SOURCE_HEALTH_URL" "Render"
assert_render_worker_stopped || fail "the Render worker became reachable during reconciliation"
[[ "$(render_command pending | tr -d '\r[:space:]')" == "0" ]] ||
  fail "a Render provider payment became pending during reconciliation"

if cmp --silent "$RUN_DIR/ovh-final.safe" "$RUN_DIR/render-before.safe"; then
  {
    printf 'status=postgres-reconciliation-not-required\n'
    printf 'verified_at_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'both_databases_identical=true\n'
    printf 'traffic_changed=false\n'
  } >"$RUN_DIR/RECONCILIATION_VERIFIED"
  render_verified="true"
  rm -f -- "$FAIL_STOP_MARKER"
  echo "OVH and still-fenced Render PostgreSQL are already identical. No database was replaced and traffic remains unchanged."
  exit 0
fi

{
  printf 'status=whole-database-replacement-required\n'
  printf 'ovh_manifest_sha256=%s\n' "$(sha256sum "$RUN_DIR/ovh-final.safe" | awk '{print $1}')"
  printf 'render_manifest_sha256=%s\n' "$(sha256sum "$RUN_DIR/render-before.safe" | awk '{print $1}')"
  printf 'automatic_row_merge=false\n'
} >"$RUN_DIR/RECONCILIATION_REQUIRED"
if [[ "${LECTURESIFT_RENDER_REPLACE_CONFIRM:-}" != "REPLACE_STILL_FENCED_RENDER" ]]; then
  rm -f -- "$FAIL_STOP_MARKER"
  echo "The databases differ. A verified OVH dump and Render rollback dump were prepared; no automatic row merge or replacement was attempted." >&2
  echo "Review $RUN_DIR/RECONCILIATION_REQUIRED, keep both sides frozen, then explicitly authorize whole-database replacement if appropriate." >&2
  exit 2
fi

render_mutated="true"
render_command reset-restore ovh-final.dump
render_command manifest render-restored.txt
canonical "$RUN_DIR/render-restored.txt" "$RUN_DIR/render-restored.safe"
cmp --silent "$RUN_DIR/ovh-final.safe" "$RUN_DIR/render-restored.safe" ||
  fail "still-fenced Render does not exactly match the OVH snapshot after replacement"
check_health_freeze "$SOURCE_HEALTH_URL" "Render"
[[ "$(render_command pending | tr -d '\r[:space:]')" == "0" ]] ||
  fail "pending provider state appeared after the Render replacement"

{
  printf 'status=postgres-rollback-reconciled\n'
  printf 'verified_at_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf 'render_matches_ovh=true\n'
  printf 'automatic_row_merge=false\n'
  printf 'whole_database_replacement=true\n'
  printf 'redis_reconciliation_complete=false\n'
  printf 'r2_reconciliation_complete=false\n'
  printf 'traffic_changed=false\n'
} >"$RUN_DIR/RECONCILIATION_VERIFIED"
chmod 0600 "$RUN_DIR/RECONCILIATION_VERIFIED"
render_verified="true"
render_mutated="false"
rm -f -- "$FAIL_STOP_MARKER"
echo "PostgreSQL is reconciled to still-fenced Render. Do not switch traffic until Redis, R2 and payment/provider gates are separately reconciled and verified."
