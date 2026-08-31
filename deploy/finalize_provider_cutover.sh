#!/usr/bin/env bash
set -euo pipefail
umask 077

# This is the only command allowed to turn matching PostgreSQL/Redis cutover
# proofs into the production-start gate. It never starts API/worker, changes
# Caddy/DNS, or claims that a Redis/R2 rollback has been performed.

if [[ "$(id -u)" != "0" ]]; then
  echo "Run the provider cutover finalizer as root." >&2
  exit 1
fi
if [[ "${LECTURESIFT_PROVIDER_CUTOVER_FINALIZE_CONFIRM:-}" != "YES" ]]; then
  echo "Set LECTURESIFT_PROVIDER_CUTOVER_FINALIZE_CONFIRM=YES only at the final frozen-source gate." >&2
  exit 1
fi

ROOT_DIR="${LECTURESIFT_ROOT:-/opt/lecturesift}"
SOURCE_ENV_FILE="${LECTURESIFT_SOURCE_ENV_FILE:-/root/.lecturesift-render-source.env}"
RUNTIME_ENV_FILE="${LECTURESIFT_ENV_FILE:-/etc/lecturesift/runtime.env}"
DB_ENV_FILE="${LECTURESIFT_DB_ENV_FILE:-/etc/lecturesift/postgres.env}"
RESTIC_ENV_FILE="${LECTURESIFT_RESTIC_ENV_FILE:-/etc/lecturesift/restic.env}"
CUTOVER_EVIDENCE_TOOL="$ROOT_DIR/deploy/provider_cutover_evidence.py"
RELEASE_TOOL="$ROOT_DIR/deploy/release.sh"
RELEASE_MARKER="/run/lecturesift/release.env"
RECOVERY_ROOT="/var/lib/lecturesift/recovery-drills"
RETENTION_MARKER="$RECOVERY_ROOT/r2-retention-lock.ok"
EXPECTED_REPOSITORY_PATTERN='s3:https://[0-9a-f]{32}[.]eu[.]r2[.]cloudflarestorage[.]com/lecturesift-production-backups/restic'
CUTOVER_ID="${LECTURESIFT_PROVIDER_CUTOVER_ID:-}"
EXPECTED_BUILD_REVISION="${LECTURESIFT_EXPECTED_BUILD_REVISION:-}"
PENDING_SQL="SELECT (SELECT count(*) FROM billing_manual_orders WHERE status = 'pending') + (SELECT count(*) FROM billing_payment_orders WHERE status IN ('created', 'pending'));"

fail() {
  echo "Provider cutover finalization failed: $*" >&2
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

[[ "$SOURCE_ENV_FILE" == "/root/.lecturesift-render-source.env" ]] ||
  fail "the Render source environment path is fixed"
[[ "$RUNTIME_ENV_FILE" == "/etc/lecturesift/runtime.env" ]] ||
  fail "the runtime environment path is fixed"
[[ "$DB_ENV_FILE" == "/etc/lecturesift/postgres.env" ]] ||
  fail "the database environment path is fixed"
[[ "$RESTIC_ENV_FILE" == "/etc/lecturesift/restic.env" ]] ||
  fail "the Restic environment path is fixed"
for item in \
  "$SOURCE_ENV_FILE:Render source environment" \
  "$RUNTIME_ENV_FILE:Runtime environment" \
  "$DB_ENV_FILE:Database environment" \
  "$RESTIC_ENV_FILE:Restic environment"; do
  check_private_file "${item%%:*}" "${item#*:}"
done
for path in "$CUTOVER_EVIDENCE_TOOL" "$RELEASE_TOOL" "$ROOT_DIR/compose.yaml"; do
  [[ -f "$path" && ! -L "$path" ]] || fail "missing or unsafe finalizer input: $path"
done
for command_name in docker python3 restic sha256sum realpath flock curl timeout; do
  command -v "$command_name" >/dev/null 2>&1 || fail "$command_name is unavailable"
done
[[ "$CUTOVER_ID" =~ ^[0-9a-f]{32}$ ]] ||
  fail "LECTURESIFT_PROVIDER_CUTOVER_ID must be exactly 32 lowercase hex characters"
[[ "$EXPECTED_BUILD_REVISION" =~ ^[0-9a-f]{40}$ ]] ||
  fail "LECTURESIFT_EXPECTED_BUILD_REVISION must be the exact 40-character release commit"

set -a
# shellcheck disable=SC1090
source "$SOURCE_ENV_FILE"
set +a
: "${SOURCE_DATABASE_URL:?Missing SOURCE_DATABASE_URL}"
: "${SOURCE_HEALTH_URL:?Missing SOURCE_HEALTH_URL}"
render_database_url="$SOURCE_DATABASE_URL"
render_health_url="$SOURCE_HEALTH_URL"
render_redis_url="${SOURCE_REDIS_URL:-${REDIS_URL:-}}"
render_broker_url="${SOURCE_CELERY_BROKER_URL:-${CELERY_BROKER_URL:-}}"
set -a
# shellcheck disable=SC1090
source "$RUNTIME_ENV_FILE"
# shellcheck disable=SC1090
source "$DB_ENV_FILE"
# shellcheck disable=SC1090
source "$RESTIC_ENV_FILE"
set +a
SOURCE_DATABASE_URL="$render_database_url"
SOURCE_HEALTH_URL="$render_health_url"
SOURCE_REDIS_URL="$render_redis_url"
SOURCE_CELERY_BROKER_URL="$render_broker_url"
export SOURCE_DATABASE_URL SOURCE_HEALTH_URL SOURCE_REDIS_URL SOURCE_CELERY_BROKER_URL
: "${POSTGRES_USER:?Missing POSTGRES_USER}"
: "${POSTGRES_DB:?Missing POSTGRES_DB}"
: "${RESTIC_REPOSITORY:?Missing RESTIC_REPOSITORY}"
: "${RESTIC_PASSWORD:?Missing RESTIC_PASSWORD}"
: "${RESTIC_AWS_ACCESS_KEY_ID:?Missing RESTIC_AWS_ACCESS_KEY_ID}"
: "${RESTIC_AWS_SECRET_ACCESS_KEY:?Missing RESTIC_AWS_SECRET_ACCESS_KEY}"
[[ "$RESTIC_REPOSITORY" =~ ^${EXPECTED_REPOSITORY_PATTERN}$ ]] ||
  fail "the Restic repository is not the dedicated production backup target"

SOURCE_FINGERPRINT="$(python3 "$CUTOVER_EVIDENCE_TOOL" source-fingerprint)" ||
  fail "the Render source identity could not be fingerprinted"
[[ "$SOURCE_FINGERPRINT" =~ ^[0-9a-f]{64}$ ]] || fail "the source fingerprint is invalid"

# The release helper independently proves a clean Git HEAD and writes the
# root-only, atomic exact-revision marker used by the normal production gate.
LECTURESIFT_EXPECTED_BUILD_REVISION="$EXPECTED_BUILD_REVISION" bash "$RELEASE_TOOL" prepare
check_private_file "$RELEASE_MARKER" "Prepared release marker"
[[ "$(cat -- "$RELEASE_MARKER")" == \
   "LECTURESIFT_EXPECTED_BUILD_REVISION=$EXPECTED_BUILD_REVISION" ]] ||
  fail "the prepared clean source tree is not the requested exact release"

SHARED_LOCK_ROOT="/var/backups/lecturesift"
[[ -d "$SHARED_LOCK_ROOT" && ! -L "$SHARED_LOCK_ROOT" &&
   "$(realpath -e -- "$SHARED_LOCK_ROOT")" == "$SHARED_LOCK_ROOT" &&
   "$(stat -c '%u' -- "$SHARED_LOCK_ROOT")" == "0" ]] ||
  fail "the shared backup/cutover lock root is unsafe"
shared_mode="$(stat -c '%a' -- "$SHARED_LOCK_ROOT")"
(( (8#$shared_mode & 8#077) == 0 )) || fail "the shared lock root must be private"
exec 9>"$SHARED_LOCK_ROOT/.backup.lock"
flock -n 9 || fail "a backup, restore, rehearsal or migration is already active"

for marker in \
  /var/lib/lecturesift/migration-fail-stop/redis-state-unproven \
  /var/lib/lecturesift/migration-fail-stop/postgres-cutover-unproven \
  /var/lib/lecturesift/migration-fail-stop/postgres-rollback-unproven; do
  [[ ! -e "$marker" && ! -L "$marker" ]] || fail "a migration fail-stop marker still exists"
done

compose=(docker compose --project-directory "$ROOT_DIR" --file "$ROOT_DIR/compose.yaml")

assert_target_writers_stopped() {
  local service container running
  for service in api worker; do
    container="$("${compose[@]}" ps -q "$service")"
    if [[ -n "$container" ]]; then
      running="$(docker inspect -f '{{.State.Running}}' "$container" 2>/dev/null || printf unknown)"
      [[ "$running" == "false" ]] || return 1
    fi
  done
  # Rehearsal containers use isolated work volumes, but they still carry
  # application credentials.  Treat any surviving rehearsal writer as a
  # blocker instead of assuming a container name implies harmless state.
  for container in lecturesift-api-rehearsal lecturesift-worker-rehearsal; do
    if docker container inspect "$container" >/dev/null 2>&1; then
      running="$(docker inspect -f '{{.State.Running}}' "$container" 2>/dev/null || printf unknown)"
      [[ "$running" == "false" ]] || return 1
    fi
  done
}

assert_source_frozen_and_idle() {
  python3 - <<'PY' || return 1
import json
import os
import ssl
import urllib.request

request = urllib.request.Request(
    os.environ["SOURCE_HEALTH_URL"], headers={"User-Agent": "LectureSift-Final-Cutover/1"}
)
with urllib.request.urlopen(request, timeout=15, context=ssl.create_default_context()) as response:
    payload = json.load(response)
valid = response.status == 200 and payload.get("ok") is True and payload.get("maintenance_mode") == "freeze"
raise SystemExit(0 if valid else 1)
PY
  timeout 30 docker run --rm --pull=never --network bridge --user 0 \
    -e SOURCE_CELERY_BROKER_URL --entrypoint python lecturesift-backend:local -c '
import os
from celery import Celery

app = Celery(broker=os.environ["SOURCE_CELERY_BROKER_URL"])
with app.connection_for_read() as connection:
    connection.ensure_connection(max_retries=1, timeout=8)
raise SystemExit(1 if app.control.ping(timeout=8) else 0)
' >/dev/null 2>&1 || return 1
  timeout 30 docker run --rm --pull=never --network bridge --user 0 \
    -e SOURCE_REDIS_URL --entrypoint python lecturesift-backend:local -c '
import json
import os
from redis import Redis

client = Redis.from_url(os.environ["SOURCE_REDIS_URL"], socket_connect_timeout=8, socket_timeout=15)
assert client.ping()
for key in client.scan_iter(match="*", count=250):
    if client.type(key) == b"list" and client.llen(key):
        raise SystemExit(1)
if client.hlen("unacked") or client.zcard("unacked_index"):
    raise SystemExit(1)
if client.exists("lecturesift:jobs:v2:write-lock"):
    raise SystemExit(1)
if any(True for _ in client.scan_iter(match="lecturesift:job:*:processing", count=250)):
    raise SystemExit(1)
raw = client.get("lecturesift:jobs:v2")
jobs = json.loads(raw).get("jobs", {}) if raw else {}
raise SystemExit(1 if any(str(job.get("status", "")) in {"queued", "working"} for job in jobs.values()) else 0)
' >/dev/null 2>&1
}

assert_target_queue_idle() {
  local broker_state
  broker_state="$("${compose[@]}" exec -T redis redis-cli --raw EVAL '
local cursor = "0"
local queued = 0
repeat
  local scanned = redis.call("SCAN", cursor, "COUNT", 250)
  cursor = scanned[1]
  for _, key in ipairs(scanned[2]) do
    local kind = redis.call("TYPE", key)
    if type(kind) == "table" then kind = kind.ok end
    if kind == "list" then queued = queued + redis.call("LLEN", key) end
  end
until cursor == "0"
local unacked = redis.call("EXISTS", KEYS[1]) == 1 and redis.call("HLEN", KEYS[1]) or 0
local unacked_index = redis.call("EXISTS", KEYS[2]) == 1 and redis.call("ZCARD", KEYS[2]) or 0
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

source_pending_count() {
  docker run --rm --pull=never --network bridge --user 0 \
    --volume "$SOURCE_ENV_FILE:/run/secrets/render-source.env:ro" \
    --env "PENDING_SQL=$PENDING_SQL" postgres:18-bookworm bash -euc '
      set -a
      source /run/secrets/render-source.env
      set +a
      psql --no-psqlrc "$SOURCE_DATABASE_URL" -v ON_ERROR_STOP=1 \
        --tuples-only --no-align --command "$PENDING_SQL"
    ' | tr -d '\r[:space:]'
}

target_pending_count() {
  "${compose[@]}" exec -T postgres psql --no-psqlrc --quiet \
    --tuples-only --no-align --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
    --command "$PENDING_SQL" | tr -d '\r[:space:]'
}

validate_recovery_marker() {
  local marker="$1" mode completed completed_epoch now marker_mtime
  [[ -f "$marker" && ! -L "$marker" && "$(stat -c '%u' -- "$marker")" == "0" ]] || return 1
  mode="$(stat -c '%a' -- "$marker")"
  (( (8#$mode & 8#022) == 0 )) || return 1
  grep -Fqx 'status=success' "$marker" || return 1
  grep -Fqx 'drill_scope=current-latest' "$marker" || return 1
  grep -Fqx 'postgres_restore=verified' "$marker" || return 1
  grep -Fqx 'redis_restore=verified' "$marker" || return 1
  grep -Fqx 'restored_payload_removed=true' "$marker" || return 1
  grep -Fqx 'live_services_touched=false' "$marker" || return 1
  grep -Fqx "repository_id_sha256=$REPOSITORY_ID_SHA256" "$marker" || return 1
  [[ "$(grep -c '^completed_at_utc=' "$marker")" == "1" ]] || return 1
  completed="$(sed -n 's/^completed_at_utc=//p' "$marker")"
  completed_epoch="$(date -u -d "$completed" +%s 2>/dev/null)" || return 1
  now="$(date -u +%s)"
  marker_mtime="$(stat -c '%Y' -- "$marker")"
  (( completed_epoch >= now - 7776000 && completed_epoch <= now + 300 )) || return 1
  (( completed_epoch >= marker_mtime - 300 && completed_epoch <= marker_mtime + 300 )) || return 1
}

validate_retention_marker() {
  local mode verified verified_epoch now marker_mtime target_sha
  [[ -f "$RETENTION_MARKER" && ! -L "$RETENTION_MARKER" &&
     "$(stat -c '%u' -- "$RETENTION_MARKER")" == "0" ]] || return 1
  mode="$(stat -c '%a' -- "$RETENTION_MARKER")"
  [[ "$mode" == "600" ]] || return 1
  grep -Fqx 'status=immutable-retention-verified' "$RETENTION_MARKER" || return 1
  grep -Fqx 'version=1' "$RETENTION_MARKER" || return 1
  grep -Fqx 'bucket=lecturesift-production-backups' "$RETENTION_MARKER" || return 1
  grep -Fqx 'retention_days=90' "$RETENTION_MARKER" || return 1
  grep -Fqx "repository_id_sha256=$REPOSITORY_ID_SHA256" "$RETENTION_MARKER" || return 1
  target_sha="$(printf '%s' "$RESTIC_REPOSITORY" | sha256sum | awk '{print $1}')"
  grep -Fqx "repository_target_sha256=$target_sha" "$RETENTION_MARKER" || return 1
  grep -Eq '^probe_key=restic/data/[.]lecturesift-retention-probes/v1/probe-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{32}[.]json$' "$RETENTION_MARKER" || return 1
  grep -Eq '^probe_payload_sha256=[0-9a-f]{64}$' "$RETENTION_MARKER" || return 1
  [[ "$(grep -c '^verified_at_utc=' "$RETENTION_MARKER")" == "1" ]] || return 1
  verified="$(sed -n 's/^verified_at_utc=//p' "$RETENTION_MARKER")"
  verified_epoch="$(date -u -d "$verified" +%s 2>/dev/null)" || return 1
  now="$(date -u +%s)"
  marker_mtime="$(stat -c '%Y' -- "$RETENTION_MARKER")"
  (( verified_epoch >= now - 604800 && verified_epoch <= now + 300 )) || return 1
  (( verified_epoch >= marker_mtime - 300 && verified_epoch <= marker_mtime + 300 )) || return 1
}

export AWS_ACCESS_KEY_ID="$RESTIC_AWS_ACCESS_KEY_ID"
export AWS_SECRET_ACCESS_KEY="$RESTIC_AWS_SECRET_ACCESS_KEY"
restic_config="$(restic cat config 2>/dev/null)" || fail "the exact Restic repository cannot be opened"
REPOSITORY_ID_SHA256="$(printf '%s' "$restic_config" | python3 -c '
import hashlib,json,sys
value = str(json.load(sys.stdin).get("id") or "")
if not value: raise SystemExit(1)
print(hashlib.sha256(value.encode("ascii")).hexdigest())
')" || fail "the Restic repository identity could not be hashed"
unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY
[[ "$REPOSITORY_ID_SHA256" =~ ^[0-9a-f]{64}$ ]] || fail "the repository identity hash is invalid"

[[ -d "$RECOVERY_ROOT" && ! -L "$RECOVERY_ROOT" &&
   "$(realpath -e -- "$RECOVERY_ROOT")" == "$RECOVERY_ROOT" ]] ||
  fail "the recovery evidence root is missing or unsafe"
recovery_marker=""
while IFS= read -r candidate; do
  if validate_recovery_marker "$candidate"; then
    recovery_marker="$candidate"
  fi
done < <(find "$RECOVERY_ROOT" -maxdepth 1 -type f -name 'restic-restore-*.ok' -printf '%T@ %p\n' |
  sort -n | cut -d' ' -f2-)
[[ -n "$recovery_marker" ]] || fail "no recent current-latest Restic restore proof matches this repository"
validate_retention_marker || fail "no recent repository-bound R2 retention-lock proof is valid"

"${compose[@]}" up -d --wait --wait-timeout 300 postgres redis
assert_target_writers_stopped || fail "the OVH API/worker are running before finalization"
assert_source_frozen_and_idle || fail "Render freeze, stopped worker or empty queue could not be re-proved"
[[ "$(source_pending_count)" == "0" ]] || fail "Render has pending provider payments"
[[ "$(target_pending_count)" == "0" ]] || fail "OVH has pending provider payments"
assert_target_queue_idle || fail "the OVH broker/job state is not empty and terminal"

# Repeat every volatile condition immediately before the atomic final proof.
assert_target_writers_stopped || fail "the OVH API/worker started during finalization"
assert_source_frozen_and_idle || fail "Render changed after the recovery checks"
[[ "$(source_pending_count)" == "0" && "$(target_pending_count)" == "0" ]] ||
  fail "pending provider state appeared during finalization"
assert_target_queue_idle || fail "target queue/job state changed during finalization"

python3 "$CUTOVER_EVIDENCE_TOOL" finalize \
  --cutover-id "$CUTOVER_ID" \
  --revision "$EXPECTED_BUILD_REVISION" \
  --source-fingerprint "$SOURCE_FINGERPRINT" \
  --recovery-marker "$(basename -- "$recovery_marker")" \
  --recovery-sha256 "$(sha256sum "$recovery_marker" | awk '{print $1}')" \
  --retention-marker "$(basename -- "$RETENTION_MARKER")" \
  --retention-sha256 "$(sha256sum "$RETENTION_MARKER" | awk '{print $1}')" \
  --repository-id-sha256 "$REPOSITORY_ID_SHA256" ||
  fail "the exact matching provider-cutover proof could not be finalized"

echo "Provider cutover gate verified and recorded. API/worker remain stopped; Caddy/DNS are unchanged. Redis/R2 rollback was not asserted or performed."
