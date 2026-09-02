#!/usr/bin/env bash
set +x
set -euo pipefail
umask 077

# PostgreSQL reconciliation for an explicit OVH -> still-fenced Render
# rollback. This replaces the complete approved application-schema state while
# preserving the fenced target database's control-plane settings, never by a
# guessed row merge.
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
DATABASE_INVENTORY="$ROOT_DIR/deploy/rollback_database_inventory.sql"
SCHEMA_CONTRACT="$ROOT_DIR/deploy/schema_contract_payment_provider_sessions_v1.txt"
PRESERVED_SCHEMA_CONTRACT="$ROOT_DIR/deploy/schema_contract_billing_email_verifications_v1.txt"
SCHEMA_VERIFIER="$ROOT_DIR/deploy/verify_schema_transition.py"
RENDER_WORKER_STOP_TOOL="$ROOT_DIR/deploy/render_worker_stop_evidence.py"
SOURCE_POSTGRES_TRANSPORT="$ROOT_DIR/deploy/source_postgres_transport.py"
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
for path in "$MANIFEST" "$DATABASE_INVENTORY" "$SCHEMA_CONTRACT" \
  "$PRESERVED_SCHEMA_CONTRACT" "$SCHEMA_VERIFIER" \
  "$RENDER_WORKER_STOP_TOOL" "$SOURCE_POSTGRES_TRANSPORT"; do
  [[ -f "$path" && ! -L "$path" ]] || fail "a rollback schema input is missing or unsafe"
done

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
OVH_HEALTH_URL="${OVH_HEALTH_URL:-${PUBLIC_BASE_URL%/}/health}"
# shellcheck disable=SC1090
source "$DB_ENV_FILE"
set +a
: "${POSTGRES_DB:?Missing POSTGRES_DB}"
: "${POSTGRES_USER:?Missing POSTGRES_USER}"

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
SOURCE_WORKER_STOP_EVIDENCE_SHA256=""

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

manifest_integrity_valid() {
  local input="$1" mode="${2:-strict}" compat_count
  [[ "$mode" == "strict" || "$mode" == "legacy-provider-sessions" ]] || return 1
  if grep -Eq '^(TABLE_DIFF|UNVALIDATED_FK)\|' "$input" ||
     awk -F'|' '$1 == "ANOMALY" && $3 != "0" {bad=1} END {exit(bad ? 0 : 1)}' "$input"; then
    return 1
  fi
  compat_count="$(grep -c '^SCHEMA_COMPAT|' "$input" || true)"
  if [[ "$mode" == "strict" ]]; then
    [[ "$compat_count" == "0" ]] || return 1
  elif [[ "$compat_count" -gt 1 ]] ||
       grep '^SCHEMA_COMPAT|' "$input" |
         grep -Fvxq 'SCHEMA_COMPAT|legacy_missing_table|billing_payment_provider_sessions|integrity_checks_deferred_to_current_schema_migration'; then
    return 1
  fi
}

canonical() {
  local input="$1" output="$2" mode="${3:-strict}"
  if [[ "$mode" == "strict" ]]; then
    python3 "$SCHEMA_VERIFIER" current --manifest "$input" \
      --contract "$SCHEMA_CONTRACT" \
      --preserved-contract "$PRESERVED_SCHEMA_CONTRACT" >"$output.schema-contract" ||
      fail "a strict rollback manifest failed its current schema contract"
  else
    python3 "$SCHEMA_VERIFIER" legacy --manifest "$input" \
      --contract "$SCHEMA_CONTRACT" \
      --preserved-contract "$PRESERVED_SCHEMA_CONTRACT" >"$output.schema-contract" ||
      fail "a legacy rollback manifest failed its bounded schema contract"
  fi
  tr -d '\r' <"$input" |
    grep -E '^(DATABASE|SCHEMA|SCHEMA_OBJECT|TABLE|ANOMALY|STATUS|SCHEMA_COMPAT|UNVALIDATED_FK|MANIFEST_COMPLETE)\|' |
    LC_ALL=C sort >"$output"
  [[ "$(grep -c '^DATABASE|' "$output")" == "1" &&
     "$(grep -c '^SCHEMA|' "$output")" == "1" &&
     "$(grep -c '^TABLE|' "$output")" -gt 0 ]] || fail "a rollback manifest is incomplete"
  manifest_integrity_valid "$input" "$mode" ||
    fail "a rollback manifest contains integrity anomalies or an unapproved schema difference"
}

canonical_schema_dump() {
  local input="$1" output="$2"
  tr -d '\r' <"$input" |
    sed -E '/^--/d; /^\\(un)?restrict[[:space:]]/d; /^[[:space:]]*$/d' \
    >"$output"
  [[ -s "$output" ]] || fail "an approved app-schema dump is empty"
}

canonical_database_inventory() {
  local input="$1" output="$2"
  tr -d '\r' <"$input" | grep -E \
    '^(DATABASE_ATTRIBUTES|DATABASE_ACL_DIGEST|DATABASE_ROLE_SETTINGS_DIGEST|DATABASE_DEFAULT_ACL_DIGEST|DATABASE_EXTENSION_DIGEST|APP_SCHEMA_SET|UNAPPROVED_SCHEMA|APP_EXTENSION_COUNT|APP_OWNER_ANOMALY|APP_ACL_DIGEST|APP_ACL_NONOWNER)\|' |
    LC_ALL=C sort >"$output"
  [[ "$(wc -l <"$output" | tr -d '[:space:]')" == "11" ]] ||
    fail "a database policy inventory is incomplete"
  [[ "$(grep -c '^UNAPPROVED_SCHEMA|0|' "$output")" == "1" ]] ||
    fail "an unapproved application schema is present"
  [[ "$(grep -c '^APP_EXTENSION_COUNT|0|' "$output")" == "1" ]] ||
    fail "an extension inside an application schema blocks automatic rollback"
  [[ "$(grep -c '^APP_OWNER_ANOMALY|0|' "$output")" == "1" ]] ||
    fail "an application object has an unapproved owner"
}

require_owner_only_app_acl() {
  local inventory="$1"
  [[ "$(grep -c '^APP_ACL_NONOWNER|0$' "$inventory")" == "1" ]] ||
    fail "the reconstructed Render app schemas grant privileges beyond the database owner"
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

check_render_health_freeze() {
  source_exec health python3 - <<'PY' || fail "Render did not acknowledge exact freeze mode"
import json
import os
import ssl
import urllib.request

request = urllib.request.Request(
    os.environ["SOURCE_HEALTH_URL"],
    headers={"User-Agent": "LectureSift-Rollback/1"},
)
with urllib.request.urlopen(
    request, timeout=15, context=ssl.create_default_context()
) as response:
    payload = json.load(response)
raise SystemExit(
    0
    if response.status == 200
    and payload.get("ok") is True
    and payload.get("maintenance_mode") == "freeze"
    else 1
)
PY
}

assert_render_worker_stopped() {
  local observed_stop_digest
  observed_stop_digest="$(python3 "$RENDER_WORKER_STOP_TOOL")" || return 1
  [[ "$observed_stop_digest" =~ ^[0-9a-f]{64}$ ]] || return 1
  if [[ -n "$SOURCE_WORKER_STOP_EVIDENCE_SHA256" &&
        "$observed_stop_digest" != "$SOURCE_WORKER_STOP_EVIDENCE_SHA256" ]]; then
    return 1
  fi
  SOURCE_WORKER_STOP_EVIDENCE_SHA256="$observed_stop_digest"
}

render_command() {
  local operation="$1" output_name="${2:-}"
  [[ "$operation" =~ ^(manifest|legacy-manifest|database-inventory|schema-dump|pending|dump|reset-restore-original|reset-restore-approved)$ ]] || fail "unsafe Render operation"
  [[ -z "$output_name" || "$output_name" =~ ^[a-z0-9-]+[.](txt|sql|dump)$ ]] || fail "unsafe Render output"
  source_pg_exec docker run --rm --user 0:0 \
    "${SOURCE_PG_DOCKER_ENV[@]}" \
    --volume "$RUN_DIR:/backup" --volume "$ROOT_DIR/deploy:/probe:ro" \
    --env "OPERATION=$operation" --env "OUTPUT_NAME=$output_name" \
    --env "PENDING_SQL=$PENDING_SQL" postgres:18-bookworm@sha256:1c59e2c3c818eaa0f0628f695b36e7c9e362d6b219b36a54a32df645cbd7e1af bash -euc '
      set +x
      case "$OPERATION" in
        manifest)
          psql --no-psqlrc -v ON_ERROR_STOP=1 \
            -f /probe/rehearsal_manifest.sql >"/backup/$OUTPUT_NAME"
          ;;
        legacy-manifest)
          psql --no-psqlrc -v ON_ERROR_STOP=1 \
            -v LECTURESIFT_ALLOW_LEGACY_PROVIDER_SESSIONS=on \
            -f /probe/rehearsal_manifest.sql >"/backup/$OUTPUT_NAME"
          ;;
        database-inventory)
          psql --no-psqlrc -v ON_ERROR_STOP=1 \
            -f /probe/rollback_database_inventory.sql >"/backup/$OUTPUT_NAME"
          ;;
        schema-dump)
          pg_dump --schema-only --no-owner --no-acl \
            --schema=public --schema=lecturesift_worker \
            --serializable-deferrable --file="/backup/$OUTPUT_NAME"
          ;;
        pending)
          psql --no-psqlrc -v ON_ERROR_STOP=1 \
            --tuples-only --no-align --command "$PENDING_SQL"
          ;;
        dump)
          # Target rollback copy preserves its provider-local ACLs.  It is
          # restored only by reset-restore-original after a failed mutation.
          pg_dump --format=custom --no-owner \
            --schema=public --schema=lecturesift_worker \
            --serializable-deferrable --file="/backup/$OUTPUT_NAME"
          ;;
        reset-restore-original|reset-restore-approved)
          psql --no-psqlrc -v ON_ERROR_STOP=1 \
            --command "DROP SCHEMA IF EXISTS lecturesift_worker CASCADE; DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
          if [[ "$OPERATION" == "reset-restore-original" ]]; then
            pg_restore --dbname "$PGDATABASE" --exit-on-error \
              --single-transaction --no-owner "/backup/$OUTPUT_NAME"
          else
            pg_restore --dbname "$PGDATABASE" --exit-on-error \
              --single-transaction --no-owner --no-acl "/backup/$OUTPUT_NAME"
            psql --no-psqlrc -v ON_ERROR_STOP=1 <<'SQL'
REVOKE ALL PRIVILEGES ON SCHEMA public FROM PUBLIC;
SELECT format('REVOKE ALL PRIVILEGES ON SCHEMA %I FROM PUBLIC', nspname)
FROM pg_namespace WHERE nspname = 'lecturesift_worker'
\gexec
SELECT format('REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA %I FROM PUBLIC', nspname)
FROM pg_namespace WHERE nspname IN ('public', 'lecturesift_worker')
\gexec
SELECT format('REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA %I FROM PUBLIC', nspname)
FROM pg_namespace WHERE nspname IN ('public', 'lecturesift_worker')
\gexec
SELECT format('REVOKE ALL PRIVILEGES ON ALL ROUTINES IN SCHEMA %I FROM PUBLIC', nspname)
FROM pg_namespace WHERE nspname IN ('public', 'lecturesift_worker')
\gexec
SQL
          fi
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
  local broker_state
  broker_state="$("${compose[@]}" exec -T redis redis-cli --raw EVAL_RO '
local function type_name(key)
  local reply = redis.call("TYPE", key)
  if type(reply) == "table" then return reply.ok end
  return reply
end
local cursor = "0"
local queued = 0
local default_queue = KEYS[1]
local priority_separator = string.char(6) .. string.char(22)
repeat
  local scanned = redis.call("SCAN", cursor, "COUNT", 250)
  cursor = scanned[1]
  for _, key in ipairs(scanned[2]) do
    local key_type = type_name(key)
    local priority_at = string.find(key, priority_separator, 1, true)
    local known_queue_structure = key == default_queue or priority_at ~= nil
    if known_queue_structure and key_type ~= "list" then
      return redis.error_reply(key .. " has unexpected Redis type " .. key_type)
    end
    if key_type == "list" then queued = queued + redis.call("LLEN", key) end
  end
until cursor == "0"
local function checked_size(key, expected_type, command)
  local actual_type = type_name(key)
  if actual_type == "none" then return 0, nil end
  if actual_type ~= expected_type then
    return 0, key .. " has unexpected Redis type " .. actual_type
  end
  return redis.call(command, key), nil
end
local unacked, unacked_error = checked_size(KEYS[2], "hash", "HLEN")
if unacked_error then return redis.error_reply(unacked_error) end
local unacked_index, index_error = checked_size(KEYS[3], "zset", "ZCARD")
if index_error then return redis.error_reply(index_error) end
local write_lock = redis.call("EXISTS", KEYS[4])
local processing_cursor = "0"
local processing = 0
repeat
  local scanned = redis.call("SCAN", processing_cursor, "MATCH", ARGV[1], "COUNT", 250)
  processing_cursor = scanned[1]
  processing = processing + #scanned[2]
until processing_cursor == "0"
if queued ~= 0 or unacked ~= 0 or unacked_index ~= 0 or
   write_lock ~= 0 or processing ~= 0 then
  return {queued, unacked, unacked_index, write_lock, processing}
end
return 0
' 4 celery unacked unacked_index lecturesift:jobs:v2:write-lock \
    'lecturesift:job:*:processing' | tr -d '\r')" || return 1
  [[ "$broker_state" == "0" ]] || return 1
  "${compose[@]}" exec -T redis redis-cli --raw GET lecturesift:jobs:v2 |
    python3 -c '
import json
import sys

raw = sys.stdin.read().strip()
payload = json.loads(raw) if raw else {"version": 2, "jobs": {}}
if not isinstance(payload, dict) or payload.get("version") != 2:
    raise SystemExit(1)
jobs = payload.get("jobs")
if not isinstance(jobs, dict) or any(not isinstance(job, dict) for job in jobs.values()):
    raise SystemExit(1)
raise SystemExit(1 if any(str(job.get("status") or "") in {"queued", "working"} for job in jobs.values()) else 0)
'
}

cleanup() {
  local status="$?" restored="false"
  trap - EXIT
  set +e
  close_snapshot || true
  "${compose[@]}" exec -T postgres rm -f \
    /tmp/lecturesift-rollback-manifest.sql \
    /tmp/lecturesift-rollback-database-inventory.sql >/dev/null 2>&1 || true
  if [[ "$status" != "0" && "$render_mutated" == "true" && "$render_verified" != "true" ]]; then
    echo "The Render replacement failed; restoring its root-only pre-operation dump." >&2
    if render_command reset-restore-original render-before.dump &&
       render_command legacy-manifest render-rollback-check.txt &&
       render_command schema-dump render-rollback-check.sql &&
       render_command database-inventory render-rollback-inventory.txt; then
      tr -d '\r' <"$RUN_DIR/render-rollback-check.txt" |
        grep -E '^(DATABASE|SCHEMA|SCHEMA_OBJECT|TABLE|ANOMALY|STATUS|SCHEMA_COMPAT|UNVALIDATED_FK|MANIFEST_COMPLETE)\|' |
        LC_ALL=C sort >"$RUN_DIR/render-rollback-check.safe"
      canonical_schema_dump "$RUN_DIR/render-rollback-check.sql" \
        "$RUN_DIR/render-rollback-check.schema.safe"
      canonical_database_inventory "$RUN_DIR/render-rollback-inventory.txt" \
        "$RUN_DIR/render-rollback-inventory.safe"
      if python3 "$SCHEMA_VERIFIER" legacy \
           --manifest "$RUN_DIR/render-rollback-check.txt" \
           --contract "$SCHEMA_CONTRACT" \
           --preserved-contract "$PRESERVED_SCHEMA_CONTRACT" >/dev/null &&
         cmp --silent "$RUN_DIR/render-before.safe" \
           "$RUN_DIR/render-rollback-check.safe" &&
         cmp --silent "$RUN_DIR/render-before.schema.safe" \
           "$RUN_DIR/render-rollback-check.schema.safe" &&
         cmp --silent "$RUN_DIR/render-before-inventory.safe" \
           "$RUN_DIR/render-rollback-inventory.safe"; then
        restored="true"
      fi
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
  unset SOURCE_WORKER_STOP_EVIDENCE_SHA256 POSTGRES_PASSWORD
  exit "$status"
}
trap cleanup EXIT

write_marker "postgres-rollback-in-progress" \
  "keep-both-providers-frozen-and-do-not-change-traffic"
check_health_freeze "$OVH_HEALTH_URL" "OVH"
check_render_health_freeze
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
"${compose[@]}" cp "$DATABASE_INVENTORY" \
  postgres:/tmp/lecturesift-rollback-database-inventory.sql
"${compose[@]}" exec -T postgres psql --no-psqlrc --quiet \
  --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" -v ON_ERROR_STOP=1 \
  -f /tmp/lecturesift-rollback-manifest.sql >"$RUN_DIR/ovh-before.txt"
canonical "$RUN_DIR/ovh-before.txt" "$RUN_DIR/ovh-before.safe"
"${compose[@]}" exec -T postgres psql --no-psqlrc --quiet \
  --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" -v ON_ERROR_STOP=1 \
  -f /tmp/lecturesift-rollback-database-inventory.sql >"$RUN_DIR/ovh-inventory.txt"
canonical_database_inventory "$RUN_DIR/ovh-inventory.txt" \
  "$RUN_DIR/ovh-inventory.safe"
python3 "$SCHEMA_VERIFIER" current \
  --manifest "$RUN_DIR/ovh-before.txt" \
  --contract "$SCHEMA_CONTRACT" \
  --preserved-contract "$PRESERVED_SCHEMA_CONTRACT" \
  >"$RUN_DIR/ovh-schema-contract.txt" ||
  fail "OVH does not match the exact current provider-session schema contract"
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
  --schema=public --schema=lecturesift_worker \
  --snapshot "$snapshot_id" >"$RUN_DIR/ovh-final.dump"
"${compose[@]}" exec -T postgres pg_dump --username "$POSTGRES_USER" \
  --dbname "$POSTGRES_DB" --schema-only --no-owner --no-acl \
  --schema=public --schema=lecturesift_worker \
  --snapshot "$snapshot_id" >"$RUN_DIR/ovh-final-schema.sql"
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
canonical_schema_dump "$RUN_DIR/ovh-final-schema.sql" "$RUN_DIR/ovh-final.schema.safe"
sha256sum "$RUN_DIR/ovh-final.dump" >"$RUN_DIR/ovh-final.dump.sha256"
docker run --rm --user 0:0 --volume "$RUN_DIR:/backup:ro" postgres:18-bookworm@sha256:1c59e2c3c818eaa0f0628f695b36e7c9e362d6b219b36a54a32df645cbd7e1af \
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

render_command legacy-manifest render-before.txt
canonical "$RUN_DIR/render-before.txt" "$RUN_DIR/render-before.safe" \
  legacy-provider-sessions
render_command database-inventory render-before-inventory.txt
canonical_database_inventory "$RUN_DIR/render-before-inventory.txt" \
  "$RUN_DIR/render-before-inventory.safe"
render_command schema-dump render-before-schema.sql
canonical_schema_dump "$RUN_DIR/render-before-schema.sql" \
  "$RUN_DIR/render-before.schema.safe"
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
docker run --rm --user 0:0 --volume "$RUN_DIR:/backup:ro" postgres:18-bookworm@sha256:1c59e2c3c818eaa0f0628f695b36e7c9e362d6b219b36a54a32df645cbd7e1af \
  pg_restore --list /backup/render-before.dump >"$RUN_DIR/render-before.dump.list"
sha256sum "$RUN_DIR/render-before.dump" >"$RUN_DIR/render-before.dump.sha256"
render_command legacy-manifest render-after-capture.txt
canonical "$RUN_DIR/render-after-capture.txt" "$RUN_DIR/render-after-capture.safe" \
  legacy-provider-sessions
render_command database-inventory render-after-capture-inventory.txt
canonical_database_inventory "$RUN_DIR/render-after-capture-inventory.txt" \
  "$RUN_DIR/render-after-capture-inventory.safe"
render_command schema-dump render-after-capture-schema.sql
canonical_schema_dump "$RUN_DIR/render-after-capture-schema.sql" \
  "$RUN_DIR/render-after-capture.schema.safe"
cmp --silent "$RUN_DIR/render-before.safe" "$RUN_DIR/render-after-capture.safe" ||
  fail "still-fenced Render changed while its rollback dump was captured"
cmp --silent "$RUN_DIR/render-before-inventory.safe" \
  "$RUN_DIR/render-after-capture-inventory.safe" ||
  fail "Render database policy changed while its rollback dump was captured"
cmp --silent "$RUN_DIR/render-before.schema.safe" \
  "$RUN_DIR/render-after-capture.schema.safe" ||
  fail "Render app schemas changed while its rollback dump was captured"
check_render_health_freeze
assert_render_worker_stopped || fail "the Render worker became reachable during reconciliation"
[[ "$(render_command pending | tr -d '\r[:space:]')" == "0" ]] ||
  fail "a Render provider payment became pending during reconciliation"

if cmp --silent "$RUN_DIR/ovh-final.safe" "$RUN_DIR/render-before.safe" &&
   cmp --silent "$RUN_DIR/ovh-final.schema.safe" \
     "$RUN_DIR/render-before.schema.safe"; then
  {
    printf 'status=postgres-reconciliation-not-required\n'
    printf 'verified_at_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'both_databases_identical=true\n'
    printf 'source_worker_stop_evidence_sha256=%s\n' "$SOURCE_WORKER_STOP_EVIDENCE_SHA256"
    printf 'traffic_changed=false\n'
  } >"$RUN_DIR/RECONCILIATION_VERIFIED"
  render_verified="true"
  rm -f -- "$FAIL_STOP_MARKER"
  echo "OVH and still-fenced Render PostgreSQL are already identical. No database was replaced and traffic remains unchanged."
  exit 0
fi

{
  printf 'status=approved-application-state-replacement-required\n'
  printf 'ovh_manifest_sha256=%s\n' "$(sha256sum "$RUN_DIR/ovh-final.safe" | awk '{print $1}')"
  printf 'render_manifest_sha256=%s\n' "$(sha256sum "$RUN_DIR/render-before.safe" | awk '{print $1}')"
  printf 'automatic_row_merge=false\n'
  printf 'source_worker_stop_evidence_sha256=%s\n' "$SOURCE_WORKER_STOP_EVIDENCE_SHA256"
} >"$RUN_DIR/RECONCILIATION_REQUIRED"
if [[ "${LECTURESIFT_RENDER_REPLACE_CONFIRM:-}" != "REPLACE_STILL_FENCED_RENDER" ]]; then
  rm -f -- "$FAIL_STOP_MARKER"
  echo "The databases differ. A verified OVH dump and Render rollback dump were prepared; no automatic row merge or replacement was attempted." >&2
  echo "Review $RUN_DIR/RECONCILIATION_REQUIRED, keep both sides frozen, then explicitly authorize replacement of the complete approved application-schema state if appropriate." >&2
  exit 2
fi

render_mutated="true"
render_command reset-restore-approved ovh-final.dump
render_command manifest render-restored.txt
canonical "$RUN_DIR/render-restored.txt" "$RUN_DIR/render-restored.safe" strict
cmp --silent "$RUN_DIR/ovh-final.safe" "$RUN_DIR/render-restored.safe" ||
  fail "still-fenced Render does not exactly match the OVH snapshot after replacement"
render_command schema-dump render-restored-schema.sql
canonical_schema_dump "$RUN_DIR/render-restored-schema.sql" \
  "$RUN_DIR/render-restored.schema.safe"
cmp --silent "$RUN_DIR/ovh-final.schema.safe" \
  "$RUN_DIR/render-restored.schema.safe" ||
  fail "still-fenced Render does not contain the complete approved OVH app schemas"
render_command database-inventory render-restored-inventory.txt
canonical_database_inventory "$RUN_DIR/render-restored-inventory.txt" \
  "$RUN_DIR/render-restored-inventory.safe"
require_owner_only_app_acl "$RUN_DIR/render-restored-inventory.safe"
grep '^DATABASE_' "$RUN_DIR/render-before-inventory.safe" \
  >"$RUN_DIR/render-before.database-control"
grep '^DATABASE_' "$RUN_DIR/render-restored-inventory.safe" \
  >"$RUN_DIR/render-restored.database-control"
cmp --silent "$RUN_DIR/render-before.database-control" \
  "$RUN_DIR/render-restored.database-control" ||
  fail "Render database-level ACL/settings/extensions changed during replacement"
grep '^APP_SCHEMA_SET|' "$RUN_DIR/ovh-inventory.safe" \
  >"$RUN_DIR/ovh.app-schema-set"
grep '^APP_SCHEMA_SET|' "$RUN_DIR/render-restored-inventory.safe" \
  >"$RUN_DIR/render-restored.app-schema-set"
cmp --silent "$RUN_DIR/ovh.app-schema-set" \
  "$RUN_DIR/render-restored.app-schema-set" ||
  fail "Render app schema set differs from the OVH source"
check_render_health_freeze
[[ "$(render_command pending | tr -d '\r[:space:]')" == "0" ]] ||
  fail "pending provider state appeared after the Render replacement"

{
  printf 'status=postgres-rollback-reconciled\n'
  printf 'verified_at_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf 'render_matches_ovh=true\n'
  printf 'automatic_row_merge=false\n'
  printf 'whole_database_replacement=false\n'
  printf 'replacement_scope=public-and-lecturesift-worker-schemas\n'
  printf 'approved_app_schemas_replaced=true\n'
  printf 'target_database_policy_preserved=true\n'
  printf 'target_app_acl_policy=database-owner-only\n'
  printf 'source_worker_stop_evidence_sha256=%s\n' "$SOURCE_WORKER_STOP_EVIDENCE_SHA256"
  printf 'redis_reconciliation_complete=false\n'
  printf 'r2_reconciliation_complete=false\n'
  printf 'traffic_changed=false\n'
} >"$RUN_DIR/RECONCILIATION_VERIFIED"
chmod 0600 "$RUN_DIR/RECONCILIATION_VERIFIED"
render_verified="true"
render_mutated="false"
rm -f -- "$FAIL_STOP_MARKER"
echo "PostgreSQL is reconciled to still-fenced Render. Do not switch traffic until Redis, R2 and payment/provider gates are separately reconciled and verified."
