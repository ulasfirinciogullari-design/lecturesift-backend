#!/usr/bin/env bash
set +x
set -euo pipefail
umask 077

# Create the first exact-format off-site recovery snapshot after PostgreSQL and
# Redis provider migration, without ever starting API/worker or touching
# Caddy/DNS.  This one-time bridge exists because the normal backup deliberately
# requires a healthy running API/worker in order to install its drain fence.

if [[ "$(id -u)" != "0" ]]; then
  echo "Run the first-cutover seed as root." >&2
  exit 1
fi
if [[ "${LECTURESIFT_FIRST_CUTOVER_SEED_CONFIRM:-}" != "YES" ||
      "${LECTURESIFT_SOURCE_FROZEN:-}" != "YES" ||
      "${LECTURESIFT_SOURCE_WORKER_STOPPED:-}" != "YES" ||
      "${LECTURESIFT_PROVIDER_RECONCILED:-}" != "YES" ]]; then
  echo "Set every first-cutover confirmation only while Render is frozen, drained and payment-reconciled." >&2
  exit 1
fi

ROOT_DIR="${LECTURESIFT_ROOT:-/opt/lecturesift}"
SOURCE_ENV_FILE="${LECTURESIFT_SOURCE_ENV_FILE:-/root/.lecturesift-render-source.env}"
RUNTIME_ENV_FILE="${LECTURESIFT_ENV_FILE:-/etc/lecturesift/runtime.env}"
API_ENV_FILE="${LECTURESIFT_API_ENV_FILE:-/etc/lecturesift/api.env}"
WORKER_ENV_FILE="${LECTURESIFT_WORKER_ENV_FILE:-/etc/lecturesift/worker.env}"
INSTAGRAM_ENV_FILE="${LECTURESIFT_INSTAGRAM_ENV_FILE:-/etc/lecturesift/instagram.env}"
DB_ENV_FILE="${LECTURESIFT_DB_ENV_FILE:-/etc/lecturesift/postgres.env}"
RESTIC_ENV_FILE="${LECTURESIFT_RESTIC_ENV_FILE:-/etc/lecturesift/restic.env}"
ROLE_ENV_GENERATOR="$ROOT_DIR/deploy/generate_role_envs.py"
CONFIGURATION_SNAPSHOT_TOOL="$ROOT_DIR/deploy/configuration_snapshot.py"
CONFIGURATION_SNAPSHOT_NAME="configuration-snapshot-v1"
CONFIGURATION_CHECKSUM_NAME="CONFIGURATION_SHA256SUMS"
RECOVERY_MANIFEST_VERSION=1
RECOVERY_MANIFEST="$ROOT_DIR/deploy/recovery_manifest_v${RECOVERY_MANIFEST_VERSION}.sql"
CUTOVER_MANIFEST="$ROOT_DIR/deploy/rehearsal_manifest.sql"
SCHEMA_CONTRACT="$ROOT_DIR/deploy/schema_contract_payment_provider_sessions_v1.txt"
SCHEMA_VERIFIER="$ROOT_DIR/deploy/verify_schema_transition.py"
CUTOVER_EVIDENCE_TOOL="$ROOT_DIR/deploy/provider_cutover_evidence.py"
RENDER_WORKER_STOP_TOOL="$ROOT_DIR/deploy/render_worker_stop_evidence.py"
SOURCE_REDIS_GUARD="$ROOT_DIR/deploy/source_redis_guard.py"
SOURCE_POSTGRES_TRANSPORT="$ROOT_DIR/deploy/source_postgres_transport.py"
TARGET_REDIS_MANIFEST_TOOL="$ROOT_DIR/deploy/target_redis_manifest.sh"
RELEASE_TOOL="$ROOT_DIR/deploy/release.sh"
RELEASE_MARKER="/run/lecturesift/release.env"
POSTGRES_CUTOVER_PROOF="/var/lib/lecturesift/provider-cutover/postgres-cutover.ok"
RETENTION_MARKER="/var/lib/lecturesift/recovery-drills/r2-retention-lock.ok"
ESCROW_MARKER="/var/lib/lecturesift/recovery-drills/restic-password-escrow.ok"
BACKUP_ROOT="/var/backups/lecturesift"
RESTIC_HOST="lecturesift-production"
EXPECTED_REPOSITORY_PATTERN='s3:https://[0-9a-f]{32}[.]eu[.]r2[.]cloudflarestorage[.]com/lecturesift-production-backups/restic'
PENDING_SQL="SELECT (SELECT count(*) FROM billing_manual_orders WHERE status = 'pending') + (SELECT count(*) FROM billing_payment_orders WHERE status IN ('created', 'pending'));"
CUTOVER_ID="${LECTURESIFT_PROVIDER_CUTOVER_ID:-}"
EXPECTED_BUILD_REVISION="${LECTURESIFT_EXPECTED_BUILD_REVISION:-}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
RUN_STARTED_EPOCH="$(date -u +%s)"
RUN_ID="first-cutover-seed-$STAMP-${BASHPID}"
RUN_DIR="$BACKUP_ROOT/$RUN_ID"
PAYLOAD_DIR="$RUN_DIR/payload"
RESTIC_CACHE_DIR="$RUN_DIR/restic-cache"
RESTIC_LOG="$RUN_DIR/restic.log"
RESTIC_EVENTS="$RUN_DIR/restic-events.jsonl"
SNAPSHOT_INFO_FILE="/tmp/lecturesift-$RUN_ID.snapshot"
SNAPSHOT_APP_NAME="lecturesift-$RUN_ID"
TARGET_RECOVERY_MANIFEST_FILE="/tmp/lecturesift-first-cutover-manifest.sql"
snapshot_export_pid=""
target_manifest_installed="false"
SOURCE_WORKER_STOP_EVIDENCE_SHA256=""
TARGET_REDIS_MANIFEST_SHA256=""

fail() {
  echo "First-cutover seed failed: $*" >&2
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

for fixed in \
  "$SOURCE_ENV_FILE:/root/.lecturesift-render-source.env:Render source environment" \
  "$RUNTIME_ENV_FILE:/etc/lecturesift/runtime.env:Runtime environment" \
  "$API_ENV_FILE:/etc/lecturesift/api.env:API environment" \
  "$WORKER_ENV_FILE:/etc/lecturesift/worker.env:Worker environment" \
  "$INSTAGRAM_ENV_FILE:/etc/lecturesift/instagram.env:Instagram environment" \
  "$DB_ENV_FILE:/etc/lecturesift/postgres.env:Database environment" \
  "$RESTIC_ENV_FILE:/etc/lecturesift/restic.env:Restic environment"; do
  path="${fixed%%:*}"
  remainder="${fixed#*:}"
  expected="${remainder%%:*}"
  label="${remainder#*:}"
  [[ "$path" == "$expected" ]] || fail "$label path is fixed"
  check_private_file "$path" "$label"
done

for path in \
  "$ROLE_ENV_GENERATOR" "$CONFIGURATION_SNAPSHOT_TOOL" "$RECOVERY_MANIFEST" \
  "$CUTOVER_MANIFEST" "$SCHEMA_CONTRACT" "$SCHEMA_VERIFIER" \
  "$CUTOVER_EVIDENCE_TOOL" "$RENDER_WORKER_STOP_TOOL" \
  "$SOURCE_REDIS_GUARD" "$SOURCE_POSTGRES_TRANSPORT" \
  "$TARGET_REDIS_MANIFEST_TOOL" "$RELEASE_TOOL" "$ROOT_DIR/compose.yaml"; do
  [[ -f "$path" && ! -L "$path" && "$(stat -c '%u' -- "$path")" == "0" ]] ||
    fail "missing, non-root or unsafe seed input: $path"
  mode="$(stat -c '%a' -- "$path")"
  (( (8#$mode & 8#022) == 0 )) || fail "seed input is group/other writable: $path"
done
for command_name in \
  docker python3 restic realpath sha256sum stat install grep sed awk df \
  flock timeout date cat cmp tr seq sleep find mkdir rm chmod mv sort dirname bash git; do
  command -v "$command_name" >/dev/null 2>&1 || fail "$command_name is unavailable"
done
for path in "$RUNTIME_ENV_FILE" "$API_ENV_FILE" "$WORKER_ENV_FILE" \
  "$INSTAGRAM_ENV_FILE" "$DB_ENV_FILE" "$RESTIC_ENV_FILE"; do
  [[ "$(stat -c '%a' -- "$path")" == "600" ]] ||
    fail "configuration snapshots require mode 0600 for $path"
done
[[ "$CUTOVER_ID" =~ ^[0-9a-f]{32}$ ]] ||
  fail "LECTURESIFT_PROVIDER_CUTOVER_ID must be exactly 32 lowercase hex characters"
[[ "$EXPECTED_BUILD_REVISION" =~ ^[0-9a-f]{40}$ ]] ||
  fail "LECTURESIFT_EXPECTED_BUILD_REVISION must be the exact 40-character release commit"
[[ "$RUN_STARTED_EPOCH" =~ ^[1-9][0-9]*$ ]] ||
  fail "the first-cutover run start time could not be recorded"

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
# shellcheck disable=SC1090
source "$RESTIC_ENV_FILE"
set +a
set +x
: "${POSTGRES_USER:?Missing POSTGRES_USER}"
: "${POSTGRES_DB:?Missing POSTGRES_DB}"
: "${RESTIC_REPOSITORY:?Missing RESTIC_REPOSITORY}"
: "${RESTIC_PASSWORD:?Missing RESTIC_PASSWORD}"
: "${RESTIC_AWS_ACCESS_KEY_ID:?Missing RESTIC_AWS_ACCESS_KEY_ID}"
: "${RESTIC_AWS_SECRET_ACCESS_KEY:?Missing RESTIC_AWS_SECRET_ACCESS_KEY}"
[[ "$RESTIC_REPOSITORY" =~ ^${EXPECTED_REPOSITORY_PATTERN}$ ]] ||
  fail "the Restic repository is not the exact dedicated EU production backup target"

export LECTURESIFT_ENV_FILE="$RUNTIME_ENV_FILE"
export LECTURESIFT_API_ENV_FILE="$API_ENV_FILE"
export LECTURESIFT_WORKER_ENV_FILE="$WORKER_ENV_FILE"
export LECTURESIFT_INSTAGRAM_ENV_FILE="$INSTAGRAM_ENV_FILE"
python3 "$ROLE_ENV_GENERATOR" --check || fail "the generated service-role environments are stale or unsafe"

LECTURESIFT_EXPECTED_BUILD_REVISION="$EXPECTED_BUILD_REVISION" bash "$RELEASE_TOOL" prepare
check_private_file "$RELEASE_MARKER" "Prepared release marker"
[[ "$(cat -- "$RELEASE_MARKER")" == \
   "LECTURESIFT_EXPECTED_BUILD_REVISION=$EXPECTED_BUILD_REVISION" ]] ||
  fail "the prepared clean checkout is not the requested exact release"
release_lock="$(dirname -- "$RELEASE_MARKER")/.release.lock"
[[ -f "$release_lock" && ! -L "$release_lock" &&
   "$(stat -c '%u' -- "$release_lock")" == "0" &&
   "$(stat -c '%a' -- "$release_lock")" == "600" ]] ||
  fail "the cooperative release lock is missing or unsafe"
exec 8>"$release_lock"
flock -n 8 || fail "another release operation is active"

assert_release_checkout_unchanged() {
  local revision status
  revision="$(git -C "$ROOT_DIR" rev-parse --verify 'HEAD^{commit}' 2>/dev/null)" ||
    return 1
  status="$(git -C "$ROOT_DIR" status --porcelain=v1 --untracked-files=all)" ||
    return 1
  [[ "${revision,,}" == "$EXPECTED_BUILD_REVISION" && -z "$status" &&
     "$(cat -- "$RELEASE_MARKER")" == \
       "LECTURESIFT_EXPECTED_BUILD_REVISION=$EXPECTED_BUILD_REVISION" ]]
}
assert_release_checkout_unchanged || fail "the release checkout changed after preparation"

SOURCE_FINGERPRINT="$(source_exec fingerprint python3 "$CUTOVER_EVIDENCE_TOOL" source-fingerprint)" ||
  fail "the Render source identity could not be fingerprinted"
[[ "$SOURCE_FINGERPRINT" =~ ^[0-9a-f]{64}$ ]] || fail "the source fingerprint is invalid"
SOURCE_WORKER_STOP_EVIDENCE_SHA256="$(python3 "$RENDER_WORKER_STOP_TOOL")" ||
  fail "the Render worker suspended state could not be proved"
[[ "$SOURCE_WORKER_STOP_EVIDENCE_SHA256" =~ ^[0-9a-f]{64}$ ]] ||
  fail "the Render worker stop evidence digest is invalid"
TARGET_REDIS_MANIFEST_SHA256="$(
  bash "$TARGET_REDIS_MANIFEST_TOOL" manifest steady full
)" || fail "the complete target Redis manifest could not be proved"
[[ "$TARGET_REDIS_MANIFEST_SHA256" =~ ^[0-9a-f]{64}$ ]] ||
  fail "the target Redis manifest digest is invalid"
python3 "$CUTOVER_EVIDENCE_TOOL" validate-seed-ready \
  --cutover-id "$CUTOVER_ID" \
  --revision "$EXPECTED_BUILD_REVISION" \
  --source-fingerprint "$SOURCE_FINGERPRINT" \
  --source-worker-stop-evidence-sha256 "$SOURCE_WORKER_STOP_EVIDENCE_SHA256" \
  --target-redis-manifest-sha256 "$TARGET_REDIS_MANIFEST_SHA256" ||
  fail "matching verified PostgreSQL and Redis cutover proofs are required"
[[ "$POSTGRES_CUTOVER_PROOF" == \
   "/var/lib/lecturesift/provider-cutover/postgres-cutover.ok" ]] ||
  fail "the PostgreSQL cutover proof path is fixed"
check_private_file "$POSTGRES_CUTOVER_PROOF" "PostgreSQL cutover proof"
EXPECTED_MIGRATED_TARGET_MANIFEST_SHA256="$(
  sed -n 's/^migrated_target_manifest_sha256=//p' "$POSTGRES_CUTOVER_PROOF"
)"
EXPECTED_POSTGRES_SECURITY_MANIFEST_SHA256="$(
  sed -n 's/^postgres_security_manifest_sha256=//p' "$POSTGRES_CUTOVER_PROOF"
)"
EXPECTED_POSTGRES_ROLE_LOGIN_PROBE_SHA256="$(
  sed -n 's/^postgres_role_login_probe_sha256=//p' "$POSTGRES_CUTOVER_PROOF"
)"
[[ "$(grep -c '^migrated_target_manifest_sha256=' "$POSTGRES_CUTOVER_PROOF")" == "1" &&
   "$EXPECTED_MIGRATED_TARGET_MANIFEST_SHA256" =~ ^[0-9a-f]{64}$ ]] ||
  fail "the PostgreSQL cutover proof has no unambiguous migrated-target manifest digest"
[[ "$(grep -c '^postgres_security_manifest_sha256=' "$POSTGRES_CUTOVER_PROOF")" == "1" &&
   "$EXPECTED_POSTGRES_SECURITY_MANIFEST_SHA256" =~ ^[0-9a-f]{64}$ ]] ||
  fail "the PostgreSQL cutover proof has no unambiguous security-manifest digest"
[[ "$(grep -c '^postgres_role_login_probe_sha256=' "$POSTGRES_CUTOVER_PROOF")" == "1" &&
   "$EXPECTED_POSTGRES_ROLE_LOGIN_PROBE_SHA256" =~ ^[0-9a-f]{64}$ ]] ||
  fail "the PostgreSQL cutover proof has no unambiguous trusted role-login digest"

for marker in \
  /var/lib/lecturesift/migration-fail-stop/redis-state-unproven \
  /var/lib/lecturesift/migration-fail-stop/postgres-cutover-unproven \
  /var/lib/lecturesift/migration-fail-stop/postgres-rollback-unproven; do
  [[ ! -e "$marker" && ! -L "$marker" ]] || fail "a migration fail-stop marker still exists"
done

backup_parent="$(dirname -- "$BACKUP_ROOT")"
[[ -d "$backup_parent" && ! -L "$backup_parent" &&
   "$(realpath -e -- "$backup_parent")" == "$backup_parent" ]] ||
  fail "the backup parent is missing or unsafe"
install -d -o root -g root -m 0700 -- "$BACKUP_ROOT"
[[ ! -L "$BACKUP_ROOT" && "$(realpath -e -- "$BACKUP_ROOT")" == "$BACKUP_ROOT" &&
   "$(stat -c '%u' -- "$BACKUP_ROOT")" == "0" ]] || fail "the backup root is unsafe"
backup_mode="$(stat -c '%a' -- "$BACKUP_ROOT")"
(( (8#$backup_mode & 8#077) == 0 )) || fail "the backup root must be private to root"
exec 9>"$BACKUP_ROOT/.backup.lock"
flock -n 9 || fail "a backup, restore, rehearsal or migration is already active"
while IFS= read -r -d '' stale; do
  stale_name="${stale##*/}"
  [[ "$stale_name" =~ ^first-cutover-seed-[0-9]{8}T[0-9]{6}Z-[0-9]+$ ]] ||
    fail "an unexpected first-cutover staging name was found"
  [[ ! -L "$stale" && "$(stat -c '%u' -- "$stale")" == "0" ]] ||
    fail "an old first-cutover staging directory is unsafe"
  stale_mode="$(stat -c '%a' -- "$stale")"
  (( (8#$stale_mode & 8#077) == 0 )) || fail "an old first-cutover staging directory is not private"
  stale_real="$(realpath -e -- "$stale")"
  [[ "$stale_real" == "$BACKUP_ROOT/$stale_name" && "$stale_real" != "$BACKUP_ROOT" ]] ||
    fail "an old first-cutover staging directory escaped the backup root"
  rm -rf --one-file-system -- "$stale_real"
done < <(find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d \
  -name 'first-cutover-seed-????????T??????Z-*' -mmin +60 -print0)
[[ ! -e "$RUN_DIR" && ! -L "$RUN_DIR" ]] || fail "the unique seed run path already exists"
mkdir -m 0700 -- "$RUN_DIR"
install -d -o root -g root -m 0700 -- "$PAYLOAD_DIR" "$RESTIC_CACHE_DIR"

compose=(docker compose --project-directory "$ROOT_DIR" --file "$ROOT_DIR/compose.yaml")

remove_target_manifest() {
  [[ "$target_manifest_installed" == "true" ]] || return 0
  "${compose[@]}" exec -T postgres rm -f "$TARGET_RECOVERY_MANIFEST_FILE" \
    >/dev/null 2>&1 || true
  target_manifest_installed="false"
}

close_snapshot() {
  local remaining=""
  "${compose[@]}" exec -T postgres psql --no-psqlrc --quiet \
    --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
    --variable=seed_app="$SNAPSHOT_APP_NAME" <<'SQL' >/dev/null 2>&1 || true
SELECT pg_terminate_backend(pid)
FROM pg_stat_activity
WHERE application_name = :'seed_app' AND pid <> pg_backend_pid();
SQL
  remaining="$("${compose[@]}" exec -T postgres psql --no-psqlrc --quiet \
    --tuples-only --no-align --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
    --variable=seed_app="$SNAPSHOT_APP_NAME" <<'SQL' 2>/dev/null || true
SELECT count(*) FROM pg_stat_activity WHERE application_name = :'seed_app';
SQL
)"
  if [[ -n "${snapshot_export_pid:-}" ]]; then
    for _ in $(seq 1 50); do
      kill -0 "$snapshot_export_pid" >/dev/null 2>&1 || break
      sleep 0.1
    done
    kill "$snapshot_export_pid" >/dev/null 2>&1 || true
    wait "$snapshot_export_pid" >/dev/null 2>&1 || true
    snapshot_export_pid=""
  fi
  "${compose[@]}" exec -T postgres rm -f "$SNAPSHOT_INFO_FILE" >/dev/null 2>&1 || true
  [[ -z "$remaining" || "$(printf '%s' "$remaining" | tr -d '\r[:space:]')" == "0" ]]
}

cleanup() {
  local status="$?" resolved=""
  trap - EXIT
  set +e
  close_snapshot || status=1
  remove_target_manifest
  if [[ -d "$RUN_DIR" && ! -L "$RUN_DIR" ]]; then
    resolved="$(realpath -e -- "$RUN_DIR" 2>/dev/null || true)"
    if [[ "$resolved" == "$BACKUP_ROOT/$RUN_ID" && "$resolved" != "$BACKUP_ROOT" ]]; then
      rm -rf --one-file-system -- "$resolved"
    else
      echo "Refusing unsafe first-cutover staging cleanup: $RUN_DIR" >&2
      status=1
    fi
  fi
  unset RESTIC_REPOSITORY RESTIC_PASSWORD RESTIC_AWS_ACCESS_KEY_ID RESTIC_AWS_SECRET_ACCESS_KEY
  unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY RESTIC_CACHE_DIR
  exit "$status"
}
trap cleanup EXIT

assert_target_writers_stopped() {
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

assert_data_services_running() {
  local service container running
  for service in postgres redis; do
    container="$("${compose[@]}" ps -q "$service")"
    [[ -n "$container" ]] || return 1
    running="$(docker inspect -f '{{.State.Running}}' "$container" 2>/dev/null || printf unknown)"
    [[ "$running" == "true" ]] || return 1
  done
}

assert_source_frozen_and_idle() {
  local observed_stop_digest
  source_exec health python3 - <<'PY' || return 1
import json
import os
import ssl
import urllib.request

request = urllib.request.Request(
    os.environ["SOURCE_HEALTH_URL"], headers={"User-Agent": "LectureSift-First-Cutover-Seed/1"}
)
with urllib.request.urlopen(request, timeout=15, context=ssl.create_default_context()) as response:
    payload = json.load(response)
valid = response.status == 200 and payload.get("ok") is True and payload.get("maintenance_mode") == "freeze"
raise SystemExit(0 if valid else 1)
PY
  observed_stop_digest="$(python3 "$RENDER_WORKER_STOP_TOOL")" || return 1
  [[ "$observed_stop_digest" == "$SOURCE_WORKER_STOP_EVIDENCE_SHA256" ]] || return 1
  source_exec redis timeout 45 python3 "$SOURCE_REDIS_GUARD" assert-idle >/dev/null 2>&1
}

assert_target_redis_manifest_unchanged() {
  local observed_manifest
  observed_manifest="$(bash "$TARGET_REDIS_MANIFEST_TOOL" manifest steady full)" || return 1
  [[ "$observed_manifest" == "$TARGET_REDIS_MANIFEST_SHA256" ]]
}

assert_target_queue_idle() {
  local broker_state
  broker_state="$("${compose[@]}" exec -T redis redis-cli --raw EVAL_RO '
local function type_name(key)
  local reply = redis.call("TYPE", key)
  if type(reply) == "table" then return reply.ok end
  return reply
end
local cursor = "0"
local queued = 0
repeat
  local scanned = redis.call("SCAN", cursor, "COUNT", 250)
  cursor = scanned[1]
  for _, key in ipairs(scanned[2]) do
    local kind = type_name(key)
    if kind == "list" then queued = queued + redis.call("LLEN", key) end
  end
until cursor == "0"
local function checked_size(key, expected_type, command)
  local actual = type_name(key)
  if actual == "none" then return 0 end
  if actual ~= expected_type then return redis.error_reply(key .. " has unexpected type " .. actual) end
  return redis.call(command, key)
end
local unacked = checked_size(KEYS[1], "hash", "HLEN")
local unacked_index = checked_size(KEYS[2], "zset", "ZCARD")
local write_lock = redis.call("EXISTS", KEYS[3])
if queued ~= 0 or unacked ~= 0 or unacked_index ~= 0 or write_lock ~= 0 then return 1 end
return 0
' 3 unacked unacked_index lecturesift:jobs:v2:write-lock | tr -d '\r')" || return 1
  [[ "$broker_state" == "0" ]] || return 1
  if "${compose[@]}" exec -T redis redis-cli --scan \
      --pattern 'lecturesift:job:*:processing' | grep -q .; then
    return 1
  fi
  "${compose[@]}" exec -T redis redis-cli --raw GET lecturesift:jobs:v2 |
    python3 -c 'import json,sys; raw=sys.stdin.read().strip(); jobs=(json.loads(raw).get("jobs", {}) if raw else {}); raise SystemExit(1 if any(str(v.get("status", "")) in {"queued", "working"} for v in jobs.values()) else 0)'
}

assert_target_database_idle() {
  local count=""
  for _ in $(seq 1 5); do
    count="$("${compose[@]}" exec -T postgres psql --no-psqlrc --quiet \
      --tuples-only --no-align --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
      --command "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() AND pid <> pg_backend_pid() AND backend_type = 'client backend';" \
      | tr -d '\r[:space:]')" || return 1
    [[ "$count" == "0" ]] && return 0
    sleep 1
  done
  return 1
}

source_pending_count() {
  source_pg_exec docker run --rm --pull=never --network bridge --user 0 \
    "${SOURCE_PG_DOCKER_ENV[@]}" \
    --env "PENDING_SQL=$PENDING_SQL" postgres:18-bookworm@sha256:1c59e2c3c818eaa0f0628f695b36e7c9e362d6b219b36a54a32df645cbd7e1af bash -euc '
      set +x
      psql --no-psqlrc -v ON_ERROR_STOP=1 \
        --tuples-only --no-align --command "$PENDING_SQL"
    ' | tr -d '\r[:space:]'
}

target_pending_count() {
  "${compose[@]}" exec -T postgres psql --no-psqlrc --quiet \
    --tuples-only --no-align --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
    --command "$PENDING_SQL" | tr -d '\r[:space:]'
}

canonical_manifest() {
  local source="$1" destination="$2"
  python3 "$SCHEMA_VERIFIER" current \
    --manifest "$source" --contract "$SCHEMA_CONTRACT" >/dev/null ||
    fail "a database manifest violates the exact current schema contract"
  tr -d '\r' <"$source" |
    grep -E '^(DATABASE|SCHEMA|SCHEMA_OBJECT|TABLE|ANOMALY|STATUS|SCHEMA_COMPAT|UNVALIDATED_FK|MANIFEST_COMPLETE)\|' |
    LC_ALL=C sort >"$destination"
  [[ "$(grep -c '^DATABASE|' "$destination")" == "1" &&
     "$(grep -c '^SCHEMA|' "$destination")" == "1" &&
     "$(grep -c '^TABLE|' "$destination")" -gt 0 ]] || fail "a database manifest is incomplete"
  if grep -Eq '^(TABLE_DIFF|SCHEMA_COMPAT|UNVALIDATED_FK)\|' "$source" ||
     awk -F'|' '$1 == "ANOMALY" && $3 != "0" {bad=1} END {exit(bad ? 0 : 1)}' "$source"; then
    fail "a database manifest contains an integrity anomaly"
  fi
}

target_manifest() {
  local destination="$1"
  "${compose[@]}" exec -T postgres psql --no-psqlrc --quiet \
    --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" -v ON_ERROR_STOP=1 \
    -f "$TARGET_RECOVERY_MANIFEST_FILE" >"$destination"
}

validate_marker_permissions() {
  local marker="$1" expected_mode="$2"
  [[ -f "$marker" && ! -L "$marker" && "$(stat -c '%u' -- "$marker")" == "0" ]] || return 1
  [[ "$(stat -c '%a' -- "$marker")" == "$expected_mode" ]] || return 1
}

export AWS_ACCESS_KEY_ID="$RESTIC_AWS_ACCESS_KEY_ID"
export AWS_SECRET_ACCESS_KEY="$RESTIC_AWS_SECRET_ACCESS_KEY"
export RESTIC_CACHE_DIR
RESTIC_CONFIG_JSON="$RUN_DIR/restic-config.json"
restic cat config >"$RESTIC_CONFIG_JSON" 2>"$RESTIC_LOG" || fail "the exact Restic repository cannot be opened"
REPOSITORY_ID_SHA256="$(python3 - "$RESTIC_CONFIG_JSON" <<'PY'
import hashlib
import json
from pathlib import Path
import sys

value = str(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8")).get("id") or "")
if not value:
    raise SystemExit(1)
print(hashlib.sha256(value.encode("ascii")).hexdigest())
PY
)" || fail "the Restic repository identity could not be hashed"
[[ "$REPOSITORY_ID_SHA256" =~ ^[0-9a-f]{64}$ ]] || fail "the repository identity hash is invalid"
CURRENT_KEY_HINT="$(restic key list --json 2>>"$RESTIC_LOG" | python3 -c '
import json,re,sys
keys=json.load(sys.stdin)
current=[str(item.get("id") or "") for item in keys if item.get("current") is True]
raise SystemExit(1) if len(current) != 1 or re.fullmatch(r"[0-9a-fA-F]{8,64}", current[0]) is None else None
print(current[0].lower())
')" || fail "the current Restic key hint is invalid"
CURRENT_KEY_ID="$(restic list keys --quiet 2>>"$RESTIC_LOG" | python3 -c '
import re,sys
hint=sys.argv[1].lower()
matches=[line.strip().lower() for line in sys.stdin if re.fullmatch(r"[0-9a-fA-F]{64}", line.strip()) and line.strip().lower().startswith(hint)]
raise SystemExit(1) if len(matches) != 1 else None
print(matches[0])
' "$CURRENT_KEY_HINT")" || fail "the current Restic key identity could not be resolved"

validate_marker_permissions "$RETENTION_MARKER" 600 || fail "the R2 retention proof is missing or unsafe"
grep -Fqx 'status=immutable-retention-verified' "$RETENTION_MARKER" || fail "the R2 retention proof is invalid"
grep -Fqx 'version=1' "$RETENTION_MARKER" || fail "the R2 retention proof version is invalid"
grep -Fqx 'bucket=lecturesift-production-backups' "$RETENTION_MARKER" || fail "the R2 retention proof names another bucket"
grep -Fqx 'retention_days=90' "$RETENTION_MARKER" || fail "the R2 retention proof has the wrong duration"
grep -Fqx "repository_id_sha256=$REPOSITORY_ID_SHA256" "$RETENTION_MARKER" || fail "the R2 retention proof belongs to another repository"
repository_target_sha256="$(printf '%s' "$RESTIC_REPOSITORY" | sha256sum | awk '{print $1}')"
grep -Fqx "repository_target_sha256=$repository_target_sha256" "$RETENTION_MARKER" || fail "the R2 retention proof belongs to another target"
retention_time="$(sed -n 's/^verified_at_utc=//p' "$RETENTION_MARKER")"
[[ "$(grep -c '^verified_at_utc=' "$RETENTION_MARKER")" == "1" ]] || fail "the R2 retention proof timestamp is ambiguous"
retention_epoch="$(date -u -d "$retention_time" +%s 2>/dev/null)" || fail "the R2 retention proof timestamp is invalid"
now_epoch="$(date -u +%s)"
retention_mtime="$(stat -c '%Y' -- "$RETENTION_MARKER")"
(( retention_epoch >= now_epoch - 604800 && retention_epoch <= now_epoch + 300 )) || fail "the R2 retention proof is stale"
(( retention_epoch >= retention_mtime - 300 && retention_epoch <= retention_mtime + 300 )) ||
  fail "the R2 retention proof timestamp does not match its atomic marker"

[[ -f "$ESCROW_MARKER" && ! -L "$ESCROW_MARKER" && "$(stat -c '%u' -- "$ESCROW_MARKER")" == "0" ]] ||
  fail "the Restic password escrow proof is missing or unsafe"
escrow_mode="$(stat -c '%a' -- "$ESCROW_MARKER")"
(( (8#$escrow_mode & 8#022) == 0 )) || fail "the Restic escrow proof is writable by another user"
grep -Fqx 'status=verified' "$ESCROW_MARKER" || fail "the Restic escrow proof is invalid"
grep -Fqx 'escrow_type=encrypted-off-host' "$ESCROW_MARKER" || fail "the Restic password is not proven off-host"
grep -Fqx 'recovery_test=decrypt-and-repository-opened' "$ESCROW_MARKER" || fail "the Restic password recovery test is missing"
grep -Fqx "repository_id_sha256=$REPOSITORY_ID_SHA256" "$ESCROW_MARKER" || fail "the escrow proof belongs to another repository"
grep -Fqx "restic_key_id=$CURRENT_KEY_ID" "$ESCROW_MARKER" || fail "the escrow proof belongs to another Restic key"

assert_target_writers_stopped || fail "the OVH API/worker or a rehearsal writer is running"
assert_data_services_running || fail "the existing PostgreSQL/Redis services are not already running"
assert_target_database_idle || fail "an unexpected client is connected to target PostgreSQL"
assert_target_queue_idle || fail "the target queue/job state is not empty and terminal"
assert_target_redis_manifest_unchanged ||
  fail "the complete target Redis state no longer matches the migration proof"
assert_source_frozen_and_idle || fail "Render freeze, stopped worker or empty queue could not be re-proved"
[[ "$(source_pending_count)" == "0" ]] || fail "Render has pending provider payments"
[[ "$(target_pending_count)" == "0" ]] || fail "OVH has pending provider payments"

database_size="$("${compose[@]}" exec -T postgres psql --no-psqlrc --quiet \
  --tuples-only --no-align --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  --command 'SELECT pg_database_size(current_database())' | tr -d '\r[:space:]')"
redis_used_memory="$("${compose[@]}" exec -T redis redis-cli --raw INFO memory |
  awk -F: '$1 == "used_memory" {gsub("\r", "", $2); print $2}')"
available_bytes="$(df --output=avail -B1 -- "$BACKUP_ROOT" | awk 'NR == 2 {print $1}')"
[[ "$database_size" =~ ^[1-9][0-9]*$ && "$redis_used_memory" =~ ^[0-9]+$ &&
   "$available_bytes" =~ ^[0-9]+$ ]] || fail "seed storage capacity could not be measured"
(( database_size <= 1000000000000 && redis_used_memory <= 1000000000000 )) ||
  fail "seed source size exceeds the supported bound"
required_bytes=$((database_size * 3 + redis_used_memory * 3 + 5368709120))
(( available_bytes >= required_bytes )) || fail "insufficient seed storage while preserving 5 GiB"

"${compose[@]}" cp "$RECOVERY_MANIFEST" "postgres:$TARGET_RECOVERY_MANIFEST_FILE"
target_manifest_installed="true"
target_manifest "$RUN_DIR/target-before.txt"
canonical_manifest "$RUN_DIR/target-before.txt" "$RUN_DIR/target-before.safe"

"${compose[@]}" exec -T postgres rm -f "$SNAPSHOT_INFO_FILE" >/dev/null 2>&1 || true
"${compose[@]}" exec -T -e "PGAPPNAME=$SNAPSHOT_APP_NAME" postgres \
  psql --no-psqlrc --quiet --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  >"$RUN_DIR/snapshot-session.log" 2>&1 <<SQL &
\set ON_ERROR_STOP on
BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;
\pset tuples_only on
\pset format unaligned
\o $SNAPSHOT_INFO_FILE
SELECT pg_backend_pid() || '|' || pg_export_snapshot();
\o
SELECT pg_sleep(21600);
ROLLBACK;
SQL
snapshot_export_pid="$!"
snapshot_info=""
for _ in $(seq 1 100); do
  if "${compose[@]}" exec -T postgres test -s "$SNAPSHOT_INFO_FILE" >/dev/null 2>&1; then
    snapshot_info="$("${compose[@]}" exec -T postgres cat "$SNAPSHOT_INFO_FILE" | tr -d '\r\n')"
    break
  fi
  kill -0 "$snapshot_export_pid" >/dev/null 2>&1 || break
  sleep 0.1
done
[[ "$snapshot_info" =~ ^[0-9]+\|[0-9A-Fa-f]+-[0-9A-Fa-f]+-[0-9A-Fa-f]+$ ]] ||
  fail "the target database snapshot could not be exported"
SNAPSHOT_ID="${snapshot_info#*|}"

# Bind the seed to the exact strict migrated target state. Run the same
# rehearsal manifest used by migrate_postgres.sh inside the exported snapshot,
# and compare it before pg_dump consumes that very snapshot.
{
  printf '\\set ON_ERROR_STOP on\n'
  printf 'BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;\n'
  printf "SET TRANSACTION SNAPSHOT '%s';\n" "$SNAPSHOT_ID"
  cat "$CUTOVER_MANIFEST"
  printf '\nCOMMIT;\n'
} | "${compose[@]}" exec -T postgres psql --no-psqlrc --quiet \
  --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  >"$RUN_DIR/target-cutover-snapshot.txt"
canonical_manifest "$RUN_DIR/target-cutover-snapshot.txt" \
  "$RUN_DIR/target-cutover-snapshot.safe"
TARGET_BEFORE_MANIFEST_SHA256="$(
  sha256sum "$RUN_DIR/target-cutover-snapshot.safe" | awk '{print $1}'
)"
[[ "$TARGET_BEFORE_MANIFEST_SHA256" =~ ^[0-9a-f]{64}$ ]] ||
  fail "the current strict target manifest digest is invalid"
[[ "$TARGET_BEFORE_MANIFEST_SHA256" == \
   "$EXPECTED_MIGRATED_TARGET_MANIFEST_SHA256" ]] ||
  fail "target PostgreSQL changed after the verified schema migration"

"${compose[@]}" exec -T postgres pg_dump --username "$POSTGRES_USER" \
  --dbname "$POSTGRES_DB" --format custom --no-owner --no-acl \
  --snapshot "$SNAPSHOT_ID" >"$PAYLOAD_DIR/postgres.dump"
{
  printf '\\set ON_ERROR_STOP on\n'
  printf 'BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;\n'
  printf "SET TRANSACTION SNAPSHOT '%s';\n" "$SNAPSHOT_ID"
  cat "$RECOVERY_MANIFEST"
  printf '\nCOMMIT;\n'
} | "${compose[@]}" exec -T postgres psql --no-psqlrc --quiet \
  --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  >"$RUN_DIR/target-snapshot.txt"
close_snapshot || fail "the exported database snapshot could not be closed cleanly"
canonical_manifest "$RUN_DIR/target-snapshot.txt" "$RUN_DIR/target-snapshot.safe"
target_manifest "$RUN_DIR/target-after.txt"
canonical_manifest "$RUN_DIR/target-after.txt" "$RUN_DIR/target-after.safe"
cmp --silent "$RUN_DIR/target-before.safe" "$RUN_DIR/target-snapshot.safe" ||
  fail "target PostgreSQL changed before the seed snapshot"
cmp --silent "$RUN_DIR/target-snapshot.safe" "$RUN_DIR/target-after.safe" ||
  fail "target PostgreSQL changed during the seed snapshot"
assert_target_database_idle || fail "an unexpected PostgreSQL client appeared during seed capture"

docker image inspect postgres:18-bookworm@sha256:1c59e2c3c818eaa0f0628f695b36e7c9e362d6b219b36a54a32df645cbd7e1af >/dev/null 2>&1 || fail "pinned PostgreSQL image is not present"
docker image inspect redis:7.4-alpine@sha256:ff02b58f971e7d7d156a1267e283fcbbeee91773b6aa36c49dac28ecfe28eadf >/dev/null 2>&1 || fail "pinned Redis image is not present"
docker run --rm --pull=never --network none --user 0 \
  --volume "$PAYLOAD_DIR:/backup:ro" postgres:18-bookworm@sha256:1c59e2c3c818eaa0f0628f695b36e7c9e362d6b219b36a54a32df645cbd7e1af \
  pg_restore --list /backup/postgres.dump >"$RUN_DIR/postgres.dump.list"

"${compose[@]}" exec -T redis redis-cli --raw GET lecturesift:jobs:v2 \
  >"$RUN_DIR/redis-state-before.json"
persistence_before="$("${compose[@]}" exec -T redis redis-cli --raw INFO persistence)"
aof_enabled="$(printf '%s\n' "$persistence_before" | awk -F: '$1 == "aof_enabled" {gsub("\r", "", $2); print $2}')"
aof_last_write_status="$(printf '%s\n' "$persistence_before" | awk -F: '$1 == "aof_last_write_status" {gsub("\r", "", $2); print $2}')"
aof_last_rewrite_status="$(printf '%s\n' "$persistence_before" | awk -F: '$1 == "aof_last_bgrewrite_status" {gsub("\r", "", $2); print $2}')"
aof_rewrite_active="$(printf '%s\n' "$persistence_before" | awk -F: '$1 == "aof_rewrite_in_progress" {gsub("\r", "", $2); print $2}')"
rdb_save_active="$(printf '%s\n' "$persistence_before" | awk -F: '$1 == "rdb_bgsave_in_progress" {gsub("\r", "", $2); print $2}')"
[[ "$aof_enabled" == "1" && "$aof_last_write_status" == "ok" &&
   "$aof_last_rewrite_status" == "ok" && "$aof_rewrite_active" == "0" &&
   "$rdb_save_active" == "0" ]] || fail "Redis persistence is not idle and healthy before seed capture"
waitaof_text="$("${compose[@]}" exec -T redis redis-cli --raw WAITAOF 1 0 5000 | tr -d '\r')" ||
  fail "Redis WAITAOF failed before seed capture"
mapfile -t waitaof_reply <<<"$waitaof_text"
[[ "${#waitaof_reply[@]}" == "2" && "${waitaof_reply[0]}" =~ ^[1-9][0-9]*$ &&
   "${waitaof_reply[1]}" =~ ^[0-9]+$ ]] || fail "Redis did not prove a local AOF fsync before seed capture"
lastsave_before="$("${compose[@]}" exec -T redis redis-cli --raw LASTSAVE | tr -d '\r')"
[[ "$lastsave_before" =~ ^[0-9]+$ ]] || fail "Redis returned an invalid LASTSAVE value"
if (( $(date -u +%s) <= lastsave_before )); then sleep 1; fi
bgsave_reply="$("${compose[@]}" exec -T redis redis-cli --raw BGSAVE | tr -d '\r')"
[[ "$bgsave_reply" == "Background saving started" ]] || fail "Redis did not start the seed BGSAVE"
bgsave_complete="false"
for _ in $(seq 1 180); do
  persistence_info="$("${compose[@]}" exec -T redis redis-cli --raw INFO persistence)"
  bgsave_active="$(printf '%s\n' "$persistence_info" | awk -F: '$1 == "rdb_bgsave_in_progress" {gsub("\r", "", $2); print $2}')"
  bgsave_status="$(printf '%s\n' "$persistence_info" | awk -F: '$1 == "rdb_last_bgsave_status" {gsub("\r", "", $2); print $2}')"
  lastsave_after="$("${compose[@]}" exec -T redis redis-cli --raw LASTSAVE | tr -d '\r')"
  if [[ "$lastsave_after" =~ ^[0-9]+$ && "$bgsave_active" == "0" &&
        "$bgsave_status" == "ok" ]] && (( lastsave_after > lastsave_before )); then
    bgsave_complete="true"
    break
  fi
  sleep 1
done
[[ "$bgsave_complete" == "true" ]] || fail "Redis seed BGSAVE did not complete successfully"
changes_after_save="$(printf '%s\n' "$persistence_info" |
  awk -F: '$1 == "rdb_changes_since_last_save" {gsub("\r", "", $2); print $2}')"
[[ "$changes_after_save" == "0" ]] ||
  fail "Redis changed immediately after the seed BGSAVE"
"${compose[@]}" cp redis:/data/dump.rdb "$PAYLOAD_DIR/redis-dump.rdb"
redis_persistence_after_copy="$("${compose[@]}" exec -T redis redis-cli --raw INFO persistence)"
copy_bgsave_active="$(printf '%s\n' "$redis_persistence_after_copy" |
  awk -F: '$1 == "rdb_bgsave_in_progress" {gsub("\r", "", $2); print $2}')"
copy_changes="$(printf '%s\n' "$redis_persistence_after_copy" |
  awk -F: '$1 == "rdb_changes_since_last_save" {gsub("\r", "", $2); print $2}')"
copy_lastsave="$("${compose[@]}" exec -T redis redis-cli --raw LASTSAVE | tr -d '\r')"
[[ "$copy_bgsave_active" == "0" && "$copy_changes" == "0" &&
   "$copy_lastsave" == "$lastsave_after" ]] ||
  fail "Redis changed while its seed RDB was being copied"
"${compose[@]}" exec -T redis redis-cli --raw GET lecturesift:jobs:v2 \
  >"$RUN_DIR/redis-state-after.json"
cmp --silent "$RUN_DIR/redis-state-before.json" "$RUN_DIR/redis-state-after.json" ||
  fail "target Redis logical state changed during seed capture"
assert_target_queue_idle || fail "target queue state changed during seed capture"
target_manifest "$RUN_DIR/target-final.txt"
canonical_manifest "$RUN_DIR/target-final.txt" "$RUN_DIR/target-final.safe"
cmp --silent "$RUN_DIR/target-snapshot.safe" "$RUN_DIR/target-final.safe" ||
  fail "target PostgreSQL changed while Redis was being captured"
assert_target_database_idle || fail "an unexpected PostgreSQL client appeared during Redis capture"
redis_version="$("${compose[@]}" exec -T redis redis-cli --raw INFO server |
  awk -F: '$1 == "redis_version" {gsub("\r", "", $2); print $2}')"
[[ "$redis_version" =~ ^7[.]4([.]|$) ]] || fail "the seed Redis version is not 7.4"
docker run --rm --pull=never --network none --read-only --cap-drop ALL \
  --security-opt no-new-privileges --pids-limit 64 \
  -v "$PAYLOAD_DIR:/backup:ro" --entrypoint redis-check-rdb \
  redis:7.4-alpine@sha256:ff02b58f971e7d7d156a1267e283fcbbeee91773b6aa36c49dac28ecfe28eadf /backup/redis-dump.rdb >/dev/null

database_identity_line="$(tr -d '\r' <"$RUN_DIR/target-snapshot.txt" | grep '^DATABASE|')"
schema_line="$(tr -d '\r' <"$RUN_DIR/target-snapshot.txt" | grep '^SCHEMA|')"
database_size_line="$(tr -d '\r' <"$RUN_DIR/target-snapshot.txt" | grep '^DATABASE_SIZE|')"
database_size_bytes="${database_size_line#DATABASE_SIZE|}"
table_line_count="$(tr -d '\r' <"$RUN_DIR/target-snapshot.txt" | grep -c '^TABLE|')"
[[ "$table_line_count" =~ ^[1-9][0-9]*$ && "$database_size_bytes" =~ ^[1-9][0-9]*$ ]] ||
  fail "the seed database manifest is incomplete"
database_identity_sha256="$(printf '%s\n' "$database_identity_line" | sha256sum | awk '{print $1}')"
schema_fingerprint_sha256="$(printf '%s\n' "$schema_line" | sha256sum | awk '{print $1}')"
data_fingerprint_sha256="$(tr -d '\r' <"$RUN_DIR/target-snapshot.txt" |
  grep '^TABLE|' | LC_ALL=C sort | sha256sum | awk '{print $1}')"
schema_manifest_sha256="$(sha256sum "$RECOVERY_MANIFEST" | awk '{print $1}')"
for digest in "$database_identity_sha256" "$schema_fingerprint_sha256" \
  "$data_fingerprint_sha256" "$schema_manifest_sha256"; do
  [[ "$digest" =~ ^[0-9a-f]{64}$ ]] || fail "a seed metadata digest is invalid"
done
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
} >"$PAYLOAD_DIR/BACKUP_METADATA"
(cd "$PAYLOAD_DIR" && sha256sum postgres.dump redis-dump.rdb BACKUP_METADATA >SHA256SUMS)
chmod 0600 "$PAYLOAD_DIR/postgres.dump" "$PAYLOAD_DIR/redis-dump.rdb" \
  "$PAYLOAD_DIR/BACKUP_METADATA" "$PAYLOAD_DIR/SHA256SUMS"

python3 "$CONFIGURATION_SNAPSHOT_TOOL" create \
  --destination "$PAYLOAD_DIR/$CONFIGURATION_SNAPSHOT_NAME" \
  --deploy-root "$ROOT_DIR"
python3 "$CONFIGURATION_SNAPSHOT_TOOL" verify \
  --snapshot-root "$PAYLOAD_DIR/$CONFIGURATION_SNAPSHOT_NAME" \
  --deploy-root "$ROOT_DIR" --quiet
assert_release_checkout_unchanged ||
  fail "the release checkout changed while configuration was captured"

assert_seed_state_unchanged() {
  local check_name="$1" persistence current_lastsave current_changes current_bgsave
  local current_aof_write current_aof_rewrite
  [[ "$check_name" =~ ^(preupload|postupload)$ ]] || return 1
  assert_target_writers_stopped || return 1
  assert_data_services_running || return 1
  assert_target_database_idle || return 1
  assert_target_queue_idle || return 1
  assert_target_redis_manifest_unchanged || return 1
  target_manifest "$RUN_DIR/target-$check_name.txt"
  canonical_manifest "$RUN_DIR/target-$check_name.txt" "$RUN_DIR/target-$check_name.safe"
  cmp --silent "$RUN_DIR/target-snapshot.safe" "$RUN_DIR/target-$check_name.safe" || return 1
  "${compose[@]}" exec -T redis redis-cli --raw GET lecturesift:jobs:v2 \
    >"$RUN_DIR/redis-state-$check_name.json"
  cmp --silent "$RUN_DIR/redis-state-after.json" \
    "$RUN_DIR/redis-state-$check_name.json" || return 1
  persistence="$("${compose[@]}" exec -T redis redis-cli --raw INFO persistence)" || return 1
  current_bgsave="$(printf '%s\n' "$persistence" |
    awk -F: '$1 == "rdb_bgsave_in_progress" {gsub("\r", "", $2); print $2}')"
  current_changes="$(printf '%s\n' "$persistence" |
    awk -F: '$1 == "rdb_changes_since_last_save" {gsub("\r", "", $2); print $2}')"
  current_aof_write="$(printf '%s\n' "$persistence" |
    awk -F: '$1 == "aof_last_write_status" {gsub("\r", "", $2); print $2}')"
  current_aof_rewrite="$(printf '%s\n' "$persistence" |
    awk -F: '$1 == "aof_last_bgrewrite_status" {gsub("\r", "", $2); print $2}')"
  current_lastsave="$("${compose[@]}" exec -T redis redis-cli --raw LASTSAVE | tr -d '\r')" ||
    return 1
  [[ "$current_bgsave" == "0" && "$current_changes" == "0" &&
     "$current_aof_write" == "ok" && "$current_aof_rewrite" == "ok" &&
     "$current_lastsave" == "$lastsave_after" ]]
}

# Re-prove every mutable condition immediately before the irreversible R2
# upload.  No API/worker start/stop command exists in this script.
assert_seed_state_unchanged preupload ||
  fail "the target PostgreSQL/Redis state changed before seed upload"
assert_source_frozen_and_idle || fail "Render changed before the seed upload"
[[ "$(source_pending_count)" == "0" && "$(target_pending_count)" == "0" ]] ||
  fail "pending provider state appeared before the seed upload"

restic backup "$PAYLOAD_DIR" --json --host "$RESTIC_HOST" \
  --tag lecturesift --tag production --tag first-cutover-seed \
  >"$RESTIC_EVENTS" 2>>"$RESTIC_LOG" || fail "the encrypted first-cutover snapshot upload failed"
assert_seed_state_unchanged postupload ||
  fail "the target PostgreSQL/Redis state changed during seed upload"
assert_source_frozen_and_idle || fail "Render changed during the seed upload"
[[ "$(source_pending_count)" == "0" && "$(target_pending_count)" == "0" ]] ||
  fail "pending provider state appeared during the seed upload"
assert_release_checkout_unchanged || fail "the release checkout changed during seed upload"
RESTIC_SNAPSHOT_ID="$(python3 - "$RESTIC_EVENTS" <<'PY'
import json
from pathlib import Path
import re
import sys

summaries = []
for raw in Path(sys.argv[1]).read_text(encoding="utf-8").splitlines():
    if not raw.strip():
        continue
    event = json.loads(raw)
    if event.get("message_type") == "summary":
        summaries.append(event)
if len(summaries) != 1:
    raise SystemExit(1)
snapshot = str(summaries[0].get("snapshot_id") or "").lower()
if re.fullmatch(r"[0-9a-f]{64}", snapshot) is None:
    raise SystemExit(1)
print(snapshot)
PY
)" || fail "Restic did not return one full seed snapshot identity"
restic snapshots --json "$RESTIC_SNAPSHOT_ID" >"$RUN_DIR/snapshot.json" 2>>"$RESTIC_LOG" ||
  fail "the uploaded seed snapshot could not be reopened"
python3 "$CUTOVER_EVIDENCE_TOOL" validate-seed-snapshot \
  --snapshot-json "$RUN_DIR/snapshot.json" \
  --expected-snapshot-id "$RESTIC_SNAPSHOT_ID" \
  --run-started-epoch "$RUN_STARTED_EPOCH" ||
  fail "the uploaded seed snapshot identity, tags or start time are invalid"

BACKUP_SET_SHA256="$(sha256sum "$PAYLOAD_DIR/SHA256SUMS" | awk '{print $1}')"
DATABASE_DUMP_SHA256="$(sha256sum "$PAYLOAD_DIR/postgres.dump" | awk '{print $1}')"
REDIS_DUMP_SHA256="$(sha256sum "$PAYLOAD_DIR/redis-dump.rdb" | awk '{print $1}')"
CONFIGURATION_CHECKSUMS_SHA256="$(sha256sum "$PAYLOAD_DIR/$CONFIGURATION_SNAPSHOT_NAME/$CONFIGURATION_CHECKSUM_NAME" | awk '{print $1}')"
payload_real="$(realpath -e -- "$PAYLOAD_DIR")"
[[ "$payload_real" == "$RUN_DIR/payload" && "$payload_real" != "$RUN_DIR" ]] ||
  fail "the plaintext seed staging directory escaped its fixed run"
rm -rf --one-file-system -- "$payload_real"
[[ ! -e "$PAYLOAD_DIR" && ! -L "$PAYLOAD_DIR" ]] ||
  fail "the plaintext seed staging directory could not be removed"
python3 "$CUTOVER_EVIDENCE_TOOL" write-seed \
  --cutover-id "$CUTOVER_ID" \
  --revision "$EXPECTED_BUILD_REVISION" \
  --source-fingerprint "$SOURCE_FINGERPRINT" \
  --run-id "$RUN_ID" \
  --snapshot-id "$RESTIC_SNAPSHOT_ID" \
  --repository-id-sha256 "$REPOSITORY_ID_SHA256" \
  --backup-set-sha256 "$BACKUP_SET_SHA256" \
  --database-dump-sha256 "$DATABASE_DUMP_SHA256" \
  --migrated-manifest-sha256 "$TARGET_BEFORE_MANIFEST_SHA256" \
  --postgres-security-manifest-sha256 "$EXPECTED_POSTGRES_SECURITY_MANIFEST_SHA256" \
  --postgres-role-login-probe-sha256 "$EXPECTED_POSTGRES_ROLE_LOGIN_PROBE_SHA256" \
  --redis-dump-sha256 "$REDIS_DUMP_SHA256" \
  --source-worker-stop-evidence-sha256 "$SOURCE_WORKER_STOP_EVIDENCE_SHA256" \
  --target-redis-manifest-sha256 "$TARGET_REDIS_MANIFEST_SHA256" \
  --configuration-checksums-sha256 "$CONFIGURATION_CHECKSUMS_SHA256" ||
  fail "the repository-bound first-cutover seed proof could not be recorded"

echo "First-cutover Restic seed verified: $RESTIC_SNAPSHOT_ID. API/worker remain stopped; Caddy/DNS were unchanged. Run the isolated Restic restore rehearsal next."
