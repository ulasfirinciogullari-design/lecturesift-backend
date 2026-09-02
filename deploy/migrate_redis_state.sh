#!/usr/bin/env bash
set +x
set -euo pipefail

# Valkey/Redis RDB files are not imported across different server versions.
# This cutover copies only LectureSift's versioned JSON state after both the
# public API and worker have been frozen and drained.

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run the Redis state migration as root." >&2
  exit 1
fi
if [[ "${LECTURESIFT_REDIS_MIGRATION_CONFIRM:-}" != "YES" || \
      "${LECTURESIFT_SOURCE_FROZEN:-}" != "YES" || \
      "${LECTURESIFT_SOURCE_WORKER_STOPPED:-}" != "YES" ]]; then
  echo "Set all Redis migration flags only after the source API is frozen and its worker is stopped." >&2
  exit 1
fi

ROOT_DIR="${LECTURESIFT_ROOT:-/opt/lecturesift}"
SOURCE_ENV_FILE="${LECTURESIFT_SOURCE_ENV_FILE:-/root/.lecturesift-render-source.env}"
CUTOVER_EVIDENCE_TOOL="$ROOT_DIR/deploy/provider_cutover_evidence.py"
RENDER_WORKER_STOP_TOOL="$ROOT_DIR/deploy/render_worker_stop_evidence.py"
SOURCE_REDIS_GUARD="$ROOT_DIR/deploy/source_redis_guard.py"
TARGET_REDIS_MANIFEST_TOOL="$ROOT_DIR/deploy/target_redis_manifest.sh"
FAIL_STOP_ROOT="/var/lib/lecturesift/migration-fail-stop"
FAIL_STOP_MARKER="$FAIL_STOP_ROOT/redis-state-unproven"
POSTGRES_FAIL_STOP_MARKER="$FAIL_STOP_ROOT/postgres-cutover-unproven"
ALLOWED_BACKUP_ROOT="/var/backups/lecturesift"
REQUESTED_BACKUP_ROOT="${LECTURESIFT_BACKUP_DIR:-$ALLOWED_BACKUP_ROOT}"
[[ "$REQUESTED_BACKUP_ROOT" == "$ALLOWED_BACKUP_ROOT" ]] || {
  echo "Refusing a non-fixed Redis migration backup root." >&2
  exit 1
}
allowed_backup_root_normalized="$(realpath -m -- "$ALLOWED_BACKUP_ROOT")"
[[ "$allowed_backup_root_normalized" == "$ALLOWED_BACKUP_ROOT" ]] || {
  echo "The fixed Redis migration backup root resolves through a symlink." >&2
  exit 1
}
requested_backup_root_normalized="$(realpath -m -- "$REQUESTED_BACKUP_ROOT")"
[[ "$requested_backup_root_normalized" == "$allowed_backup_root_normalized" ]] || {
  echo "The Redis migration backup root escaped its fixed safety boundary." >&2
  exit 1
}
BACKUP_ROOT="$allowed_backup_root_normalized"
[[ -f "$SOURCE_ENV_FILE" && ! -L "$SOURCE_ENV_FILE" ]] || {
  echo "The root-only Render source environment is missing." >&2
  exit 1
}
source_mode="$(stat -c '%a' "$SOURCE_ENV_FILE")"
if [[ "$(stat -c '%u' "$SOURCE_ENV_FILE")" != "0" ]] || \
   (( (8#$source_mode & 8#077) != 0 )); then
  echo "The Render source environment must be root-owned and private." >&2
  exit 1
fi

umask 077
install -d -o root -g root -m 0700 -- "$FAIL_STOP_ROOT"
[[ ! -L "$FAIL_STOP_ROOT" && "$(realpath -e -- "$FAIL_STOP_ROOT")" == "$FAIL_STOP_ROOT" ]] || {
  echo "The Redis migration fail-stop directory is unsafe." >&2
  exit 1
}
if [[ -e "$FAIL_STOP_MARKER" || -L "$FAIL_STOP_MARKER" ]]; then
  echo "A previous Redis migration left unproven target state; recover it and clear the fail-stop marker manually." >&2
  exit 1
fi
if [[ -e "$POSTGRES_FAIL_STOP_MARKER" || -L "$POSTGRES_FAIL_STOP_MARKER" ]]; then
  echo "PostgreSQL cutover is still fail-stopped; Redis migration is forbidden." >&2
  exit 1
fi
install -d -o root -g root -m 0700 -- "$BACKUP_ROOT"
BACKUP_ROOT="$(realpath -e -- "$BACKUP_ROOT")"
[[ "$BACKUP_ROOT" == "$ALLOWED_BACKUP_ROOT" && ! -L "$BACKUP_ROOT" ]] || {
  echo "The Redis migration backup root is not the fixed real directory." >&2
  exit 1
}
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
RUN_DIR="$BACKUP_ROOT/redis-migration-$STAMP"
mkdir -m 0700 -- "$RUN_DIR"
TARGET_ROLLBACK_STATE="$RUN_DIR/target-before.json"
TARGET_ROLLBACK_METADATA="$RUN_DIR/target-rollback-metadata.json"
target_updated="false"
migration_committed="false"
target_had_state="0"
target_lock_acquired="false"
MIGRATION_LOCK_KEY="lecturesift:jobs:v2:write-lock"
MIGRATION_LOCK_TOKEN="$(cat /proc/sys/kernel/random/uuid)"
MIGRATION_LOCK_TOKEN_FILE="$RUN_DIR/redis-migration-lock.token"
CUTOVER_ID="${LECTURESIFT_PROVIDER_CUTOVER_ID:-}"
EXPECTED_BUILD_REVISION="${LECTURESIFT_EXPECTED_BUILD_REVISION:-}"
SOURCE_WORKER_STOP_EVIDENCE_SHA256=""
TARGET_REDIS_MANIFEST_SHA256=""

for helper in "$CUTOVER_EVIDENCE_TOOL" "$RENDER_WORKER_STOP_TOOL" \
  "$SOURCE_REDIS_GUARD" "$TARGET_REDIS_MANIFEST_TOOL"; do
  [[ -f "$helper" && ! -L "$helper" ]] || {
    echo "A Redis cutover evidence helper is missing or unsafe." >&2
    exit 1
  }
done
[[ "$CUTOVER_ID" =~ ^[0-9a-f]{32}$ ]] || {
  echo "LECTURESIFT_PROVIDER_CUTOVER_ID must be exactly 32 lowercase hex characters." >&2
  exit 1
}
[[ "$EXPECTED_BUILD_REVISION" =~ ^[0-9a-f]{40}$ ]] || {
  echo "LECTURESIFT_EXPECTED_BUILD_REVISION must be the exact 40-character release commit." >&2
  exit 1
}

release_target_lock_best_effort() {
  [[ "$target_lock_acquired" == "true" ]] || return 0
  docker compose exec -T redis redis-cli --raw EVAL \
    'if redis.call("GET", KEYS[1]) == ARGV[1] then return redis.call("DEL", KEYS[1]) else return 0 end' \
    1 "$MIGRATION_LOCK_KEY" "$MIGRATION_LOCK_TOKEN" >/dev/null 2>&1 || true
  target_lock_acquired="false"
}

release_target_lock_strict() {
  local delete_reply remaining

  [[ "$target_lock_acquired" == "true" ]] || {
    echo "The target Redis migration lock was not held at commit." >&2
    return 1
  }
  if ! delete_reply="$(docker compose exec -T redis redis-cli --raw EVAL \
    'if redis.call("GET", KEYS[1]) == ARGV[1] then return redis.call("DEL", KEYS[1]) else return 0 end' \
    1 "$MIGRATION_LOCK_KEY" "$MIGRATION_LOCK_TOKEN" | tr -d '\r')"; then
    echo "The target Redis migration lock could not be released at commit." >&2
    return 1
  fi
  [[ "$delete_reply" == "1" ]] || {
    echo "The target Redis migration lock token changed or expired before commit." >&2
    return 1
  }
  target_lock_acquired="false"
  if ! remaining="$(docker compose exec -T redis redis-cli --raw EXISTS \
    "$MIGRATION_LOCK_KEY" | tr -d '\r')"; then
    echo "The target Redis migration lock absence could not be verified." >&2
    return 1
  fi
  [[ "$remaining" == "0" ]] || {
    echo "A target Redis migration lock still exists after token-checked release." >&2
    return 1
  }
}

cleanup() {
  local original_status="$?" cleanup_failed="false"
  local rollback_current="$RUN_DIR/target-rollback-current.json"
  local marker_tmp=""
  trap - EXIT
  set +e
  if [[ "$target_updated" == "true" && "$migration_committed" != "true" ]]; then
    if [[ ! -f "${target_rollback_resp:-}" ]] || \
       ! timeout 35s docker compose exec -T redis redis-cli --pipe \
         <"$target_rollback_resp" >"${target_rollback_log:-/dev/null}" 2>&1 || \
       ! grep -Eq 'errors:[[:space:]]*0,[[:space:]]*replies:[[:space:]]*2' \
         "${target_rollback_log:-/dev/null}"; then
      cleanup_failed="true"
      echo "Failed to durably restore target Redis after an aborted migration." >&2
    elif [[ "$target_had_state" == "1" && -f "$TARGET_ROLLBACK_STATE" ]]; then
      if ! docker compose exec -T redis redis-cli --raw GET lecturesift:jobs:v2 \
          >"$rollback_current"; then
        cleanup_failed="true"
      else
        python3 - "$rollback_current" "$TARGET_ROLLBACK_STATE" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
expected = Path(sys.argv[2]).read_bytes()
value = path.read_bytes()
if value != expected + b"\n":
    raise SystemExit("redis-cli rollback verification framing mismatch")
path.write_bytes(value[:-1])
PY
        cmp --silent "$TARGET_ROLLBACK_STATE" "$rollback_current" || \
          cleanup_failed="true"
      fi
    else
      rollback_exists="$(docker compose exec -T redis redis-cli --raw \
        EXISTS lecturesift:jobs:v2 | tr -d '\r')" || cleanup_failed="true"
      [[ "${rollback_exists:-}" == "0" ]] || cleanup_failed="true"
    fi
  fi
  if [[ "$cleanup_failed" == "true" ]]; then
    marker_tmp="$(mktemp -- "$FAIL_STOP_ROOT/.redis-state-unproven-XXXXXXXX")"
    {
      printf 'status=redis-state-unproven\n'
      printf 'recorded_at_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
      printf 'migration_run=%s\n' "$(basename -- "$RUN_DIR")"
      printf 'operator_action=verify-target-redis-and-remove-marker-manually\n'
    } >"$marker_tmp"
    chmod 0600 "$marker_tmp"
    mv -f -- "$marker_tmp" "$FAIL_STOP_MARKER" || true
    echo "The target migration lock is retained and production preflight is blocked for manual recovery." >&2
  else
    release_target_lock_best_effort
    if [[ "$migration_committed" != "true" ]]; then
      rm -f -- "$FAIL_STOP_MARKER"
    fi
    rm -f -- "$MIGRATION_LOCK_TOKEN_FILE"
  fi
  unset SOURCE_REDIS_URL SOURCE_CELERY_BROKER_URL SOURCE_HEALTH_URL
  unset REDIS_URL CELERY_BROKER_URL PUBLIC_BASE_URL
  rm -f -- "$RUN_DIR/source-before.json" "$RUN_DIR/source-after.json" \
    "$RUN_DIR/source-final.json" "$RUN_DIR/target-written.json" \
    "$RUN_DIR/target-final.json" "$RUN_DIR/source-health-"*.json \
    "$rollback_current" "${target_rollback_resp:-}" "${target_rollback_log:-}"
  if [[ "$cleanup_failed" == "true" ]]; then
    echo "Redis migration cleanup could not prove a durable rollback." >&2
    original_status=1
  fi
  exit "$original_status"
}
marker_tmp="$(mktemp -- "$FAIL_STOP_ROOT/.redis-migration-in-progress-XXXXXXXX")"
{
  printf 'status=redis-migration-in-progress\n'
  printf 'recorded_at_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf 'migration_run=%s\n' "$(basename -- "$RUN_DIR")"
  printf 'operator_action=do-not-start-production-until-migration-exits-cleanly\n'
} >"$marker_tmp"
chmod 0600 "$marker_tmp"
mv -- "$marker_tmp" "$FAIL_STOP_MARKER"
trap cleanup EXIT
printf '%s\n' "$MIGRATION_LOCK_TOKEN" >"$MIGRATION_LOCK_TOKEN_FILE"
chmod 0600 "$MIGRATION_LOCK_TOKEN_FILE"

assert_target_broker_empty() {
  local phase="$1"
  local broker_state

  # Kombu stores every Redis-backed Celery queue, including the binary-suffix
  # priority variants, as a Redis list. Scan all list keys so a custom queue
  # name cannot bypass the cutover guard. The two reserved unacked structures
  # must also be empty and have their expected Redis types when present.
  if ! broker_state="$(docker compose exec -T redis redis-cli --raw EVAL_RO '
local function type_name(key)
  local reply = redis.call("TYPE", key)
  if type(reply) == "table" then
    return reply.ok
  end
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
    if key_type == "list" then
      queued = queued + redis.call("LLEN", key)
    end
  end
until cursor == "0"

local function checked_size(key, expected_type, command)
  local actual_type = type_name(key)
  if actual_type == "none" then
    return 0, nil
  end
  if actual_type ~= expected_type then
    return 0, key .. " has unexpected Redis type " .. actual_type
  end
  return redis.call(command, key), nil
end

local unacked, unacked_error = checked_size(KEYS[2], "hash", "HLEN")
if unacked_error then
  return redis.error_reply(unacked_error)
end
local unacked_index, index_error = checked_size(KEYS[3], "zset", "ZCARD")
if index_error then
  return redis.error_reply(index_error)
end
if queued ~= 0 or unacked ~= 0 or unacked_index ~= 0 then
  return {queued, unacked, unacked_index}
end
return 0
' 3 celery unacked unacked_index | tr -d '\r')"; then
    echo "The target Celery broker structures could not be inspected during $phase." >&2
    return 1
  fi
  [[ "$broker_state" == "0" ]] || {
    echo "Target Redis contains queued, priority, or unacknowledged Celery work during $phase." >&2
    return 1
  }
}

set -a
# shellcheck disable=SC1090
source "$SOURCE_ENV_FILE" >/dev/null 2>&1
set +a
SOURCE_REDIS_URL="${SOURCE_REDIS_URL:-${REDIS_URL:-}}"
SOURCE_CELERY_BROKER_URL="${SOURCE_CELERY_BROKER_URL:-${CELERY_BROKER_URL:-}}"
SOURCE_HEALTH_URL="${SOURCE_HEALTH_URL:-}"
if [[ -z "$SOURCE_HEALTH_URL" && -n "${PUBLIC_BASE_URL:-}" ]]; then
  SOURCE_HEALTH_URL="${PUBLIC_BASE_URL%/}/health"
fi
unset REDIS_URL CELERY_BROKER_URL PUBLIC_BASE_URL
export SOURCE_REDIS_URL SOURCE_CELERY_BROKER_URL SOURCE_HEALTH_URL
if [[ ! "$SOURCE_REDIS_URL" =~ ^rediss?:// ]] || [[ "$SOURCE_REDIS_URL" == *"@redis:6379"* ]]; then
  echo "The source environment does not contain a remote Redis URL." >&2
  exit 1
fi
if [[ ! "$SOURCE_CELERY_BROKER_URL" =~ ^rediss?:// ]] || \
   [[ "$SOURCE_CELERY_BROKER_URL" == *"@redis:6379"* ]]; then
  echo "The source environment does not contain its remote Celery broker URL." >&2
  exit 1
fi
if [[ ! "$SOURCE_HEALTH_URL" =~ ^https://[^[:space:]]+/health$ ]]; then
  echo "The source environment must provide its exact HTTPS SOURCE_HEALTH_URL." >&2
  exit 1
fi
: "${SOURCE_DATABASE_URL:?Missing SOURCE_DATABASE_URL for the shared cutover identity}"
export SOURCE_DATABASE_URL
SOURCE_FINGERPRINT="$(python3 "$CUTOVER_EVIDENCE_TOOL" source-fingerprint)" || {
  echo "The Render source identity could not be fingerprinted." >&2
  exit 1
}
[[ "$SOURCE_FINGERPRINT" =~ ^[0-9a-f]{64}$ ]] || {
  echo "The Render source fingerprint is invalid." >&2
  exit 1
}

cd "$ROOT_DIR"
for container in \
  lecturesift-api-1 lecturesift-worker-1 \
  lecturesift-api-rehearsal lecturesift-worker-rehearsal; do
  if docker container inspect "$container" >/dev/null 2>&1 && \
     [[ "$(docker inspect -f '{{.State.Running}}' "$container")" == "true" ]]; then
    echo "Stop every target API/worker container before the final Redis migration." >&2
    exit 1
  fi
done

python3 "$CUTOVER_EVIDENCE_TOOL" begin-redis \
  --cutover-id "$CUTOVER_ID" \
  --revision "$EXPECTED_BUILD_REVISION" \
  --source-fingerprint "$SOURCE_FINGERPRINT" || {
  echo "The Redis cutover does not match a verified PostgreSQL cutover session." >&2
  exit 1
}
bash "$TARGET_REDIS_MANIFEST_TOOL" init-salt || {
  echo "The confidential target Redis manifest salt could not be initialized." >&2
  exit 1
}

check_source_frozen() {
  local phase="$1" observed_stop_digest
  local health_file="$RUN_DIR/source-health-$phase.json"
  if ! curl --fail --silent --show-error --max-time 15 \
    --proto '=https' --tlsv1.2 "$SOURCE_HEALTH_URL" >"$health_file"; then
    echo "The source health endpoint could not prove freeze mode." >&2
    return 1
  fi
  if ! python3 - "$health_file" <<'PY'
import json
from pathlib import Path
import sys

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
raise SystemExit(0 if payload.get("maintenance_mode") == "freeze" else 1)
PY
  then
    echo "The live source API is not in exact freeze maintenance mode." >&2
    return 1
  fi

  observed_stop_digest="$(python3 "$RENDER_WORKER_STOP_TOOL")" || {
    echo "The source worker suspended state could not be independently proved." >&2
    return 1
  }
  [[ "$observed_stop_digest" =~ ^[0-9a-f]{64}$ ]] || return 1
  if [[ -n "$SOURCE_WORKER_STOP_EVIDENCE_SHA256" &&
        "$observed_stop_digest" != "$SOURCE_WORKER_STOP_EVIDENCE_SHA256" ]]; then
    echo "The Render worker identity/state proof changed during Redis migration." >&2
    return 1
  fi
  SOURCE_WORKER_STOP_EVIDENCE_SHA256="$observed_stop_digest"
}

check_source_frozen before
if [[ "$(docker compose exec -T redis redis-cli --raw EXISTS lecturesift:jobs:v2:write-lock | tr -d '\r')" != "0" ]]; then
  echo "Target Redis still has an active state write lock." >&2
  exit 1
fi
if docker compose exec -T redis redis-cli --scan --pattern 'lecturesift:job:*:processing' | grep -q .; then
  echo "Target Redis still has active processing locks." >&2
  exit 1
fi
lock_reply="$(docker compose exec -T redis redis-cli --raw SET \
  "$MIGRATION_LOCK_KEY" "$MIGRATION_LOCK_TOKEN" NX EX 3600 | tr -d '\r')"
if [[ "$lock_reply" != "OK" ]]; then
  echo "Could not acquire the exclusive target Redis migration lock." >&2
  exit 1
fi
target_lock_acquired="true"
assert_target_broker_empty "pre-export"
target_non_job_before_sha256="$(
  bash "$TARGET_REDIS_MANIFEST_TOOL" manifest migration non-job \
    "$MIGRATION_LOCK_TOKEN_FILE"
)" || {
  echo "The target non-job Redis state could not be captured before migration." >&2
  exit 1
}
[[ "$target_non_job_before_sha256" =~ ^[0-9a-f]{64}$ ]] || exit 1

export_log="$RUN_DIR/source-export.log"
if ! timeout 45 python3 "$SOURCE_REDIS_GUARD" export \
  "$RUN_DIR/source-before.json" >"$export_log" 2>&1; then
  echo "The source Redis state could not be exported. Root-only diagnostic: $export_log" >&2
  exit 1
fi

assert_target_broker_empty "before-import"
target_had_state="$(docker compose exec -T redis redis-cli --raw EXISTS lecturesift:jobs:v2 | tr -d '\r')"
target_payload_bytes="$(docker compose exec -T redis redis-cli --raw STRLEN lecturesift:jobs:v2 | tr -d '\r')"
[[ "$target_had_state" == "0" || "$target_had_state" == "1" ]] || {
  echo "The previous target Redis key-presence state is invalid." >&2
  exit 1
}
[[ "$target_payload_bytes" =~ ^[0-9]+$ ]] || {
  echo "The previous target Redis payload length is invalid." >&2
  exit 1
}
docker compose exec -T redis redis-cli --raw GET lecturesift:jobs:v2 \
  >"$TARGET_ROLLBACK_STATE"
python3 - "$TARGET_ROLLBACK_STATE" "$target_payload_bytes" <<'PY'
import os
from pathlib import Path
import sys

path = Path(sys.argv[1])
value = path.read_bytes()
expected = int(sys.argv[2])
if len(value) != expected + 1 or not value.endswith(b"\n"):
    raise SystemExit("redis-cli rollback capture framing mismatch")
with path.open("r+b") as stream:
    stream.truncate(expected)
    stream.flush()
    os.fsync(stream.fileno())
directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
try:
    os.fsync(directory_fd)
finally:
    os.close(directory_fd)
PY
chmod 0600 "$TARGET_ROLLBACK_STATE" "$export_log"

# Preserve an unambiguous, root-only rollback artifact before the first target
# mutation.  The raw copy is deliberately kept byte-for-byte (an existing
# empty string is distinct from an absent key); the canonical metadata records
# that distinction and binds the copy by digest without exposing its payload.
python3 - "$TARGET_ROLLBACK_STATE" "$TARGET_ROLLBACK_METADATA" \
  "$target_had_state" <<'PY'
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

state = Path(sys.argv[1])
destination = Path(sys.argv[2])
if sys.argv[3] not in {"0", "1"}:
    raise SystemExit("invalid Redis rollback presence state")
existed = sys.argv[3] == "1"
payload = state.read_bytes()
if not existed and payload:
    raise SystemExit("an absent Redis key produced a non-empty rollback payload")
document = {
    "existed": existed,
    "payload_bytes": len(payload),
    "payload_sha256": hashlib.sha256(payload).hexdigest(),
    "redis_key": "lecturesift:jobs:v2",
    "schema": "lecturesift-redis-rollback-v1",
}
encoded = json.dumps(
    document, sort_keys=True, separators=(",", ":"), ensure_ascii=True
).encode("ascii") + b"\n"
fd, temporary_name = tempfile.mkstemp(
    prefix=".target-rollback-metadata.", dir=destination.parent
)
temporary = Path(temporary_name)
try:
    os.fchmod(fd, 0o600)
    os.fchown(fd, 0, 0)
    with os.fdopen(fd, "wb") as stream:
        stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, destination)
    directory_fd = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
finally:
    temporary.unlink(missing_ok=True)
PY
[[ -f "$TARGET_ROLLBACK_STATE" && ! -L "$TARGET_ROLLBACK_STATE" && \
   -f "$TARGET_ROLLBACK_METADATA" && ! -L "$TARGET_ROLLBACK_METADATA" && \
   "$(stat -c '%u:%g:%a' -- "$TARGET_ROLLBACK_STATE")" == "0:0:600" && \
   "$(stat -c '%u:%g:%a' -- "$TARGET_ROLLBACK_METADATA")" == "0:0:600" ]] || {
  echo "The Redis rollback copy or its metadata is not root-owned and private." >&2
  exit 1
}

# Prepare the inverse write before touching the target. Cleanup can therefore
# restore the exact previous value (or exact absence) and fsync it with WAITAOF
# on one Redis connection even when the forward SET was applied but its own
# acknowledgement failed.
target_rollback_resp="$RUN_DIR/target-rollback.resp"
target_rollback_log="$RUN_DIR/target-rollback.log"
python3 - "$TARGET_ROLLBACK_STATE" "$target_rollback_resp" \
  "$target_had_state" <<'PY'
from pathlib import Path
import sys

source = Path(sys.argv[1]).read_bytes()
destination = Path(sys.argv[2])
had_state = sys.argv[3] == "1"

def command(*parts: bytes) -> bytes:
    payload = [f"*{len(parts)}\r\n".encode("ascii")]
    for part in parts:
        payload.extend((f"${len(part)}\r\n".encode("ascii"), part, b"\r\n"))
    return b"".join(payload)

rollback = (
    command(b"SET", b"lecturesift:jobs:v2", source)
    if had_state
    else command(b"DEL", b"lecturesift:jobs:v2")
)
destination.write_bytes(rollback + command(b"WAITAOF", b"1", b"0", b"0"))
destination.chmod(0o600)
PY

# SET and WAITAOF must run on the same Redis connection. Build a two-command
# RESP stream so the migration is not reported committed until Redis 7.4 has
# fsynced the imported state to its local AOF. An external timeout keeps a
# broken persistence layer fail-closed instead of hanging the maintenance
# window forever.
target_write_resp="$RUN_DIR/target-write.resp"
target_write_log="$RUN_DIR/target-write.log"
python3 - "$RUN_DIR/source-before.json" "$target_write_resp" <<'PY'
from pathlib import Path
import sys

source = Path(sys.argv[1]).read_bytes()
destination = Path(sys.argv[2])

def command(*parts: bytes) -> bytes:
    payload = [f"*{len(parts)}\r\n".encode("ascii")]
    for part in parts:
        payload.extend((f"${len(part)}\r\n".encode("ascii"), part, b"\r\n"))
    return b"".join(payload)

destination.write_bytes(
    command(b"SET", b"lecturesift:jobs:v2", source)
    + command(b"WAITAOF", b"1", b"0", b"0")
)
destination.chmod(0o600)
PY
target_updated="true"
if ! timeout 35s docker compose exec -T redis redis-cli --pipe \
  <"$target_write_resp" >"$target_write_log" 2>&1; then
  echo "Target Redis did not durably acknowledge the imported state." >&2
  exit 1
fi
if ! grep -Eq 'errors:[[:space:]]*0,[[:space:]]*replies:[[:space:]]*2' \
  "$target_write_log"; then
  echo "Target Redis persistence acknowledgement was incomplete." >&2
  exit 1
fi
docker compose exec -T redis redis-cli --raw GET lecturesift:jobs:v2 \
  >"$RUN_DIR/target-written.json"

if ! timeout 45 python3 "$SOURCE_REDIS_GUARD" export \
  "$RUN_DIR/source-after.json" >>"$export_log" 2>&1; then
  echo "The source Redis state could not be rechecked. Root-only diagnostic: $export_log" >&2
  exit 1
fi

check_source_frozen final
if ! timeout 45 python3 "$SOURCE_REDIS_GUARD" export \
  "$RUN_DIR/source-final.json" >>"$export_log" 2>&1; then
  echo "The frozen source Redis state could not be finally re-read. Root-only diagnostic: $export_log" >&2
  exit 1
fi

# This is deliberately after source-after and source-final. It is the target
# value compared immediately before commit, not an earlier post-write sample.
docker compose exec -T redis redis-cli --raw GET lecturesift:jobs:v2 \
  >"$RUN_DIR/target-final.json"

python3 - "$RUN_DIR" <<'PY'
import json
from pathlib import Path
import sys

root = Path(sys.argv[1])
before = json.loads((root / "source-before.json").read_text(encoding="utf-8"))
after = json.loads((root / "source-after.json").read_text(encoding="utf-8"))
final = json.loads((root / "source-final.json").read_text(encoding="utf-8"))
target_written = json.loads((root / "target-written.json").read_text(encoding="utf-8"))
target_final = json.loads((root / "target-final.json").read_text(encoding="utf-8"))
if before != after or after != final:
    raise SystemExit("Source Redis changed during the frozen migration")
if before != target_written or final != target_final:
    raise SystemExit("Target Redis state does not match the source")
print(f"Redis logical migration verified: {len(before.get('jobs', {}))} terminal jobs")
PY

assert_target_broker_empty "before-commit"

# The one authorized data mutation is lecturesift:jobs:v2.  Every other key,
# type, serialized value and absolute expiry must remain byte-logically equal.
target_non_job_after_sha256="$(
  bash "$TARGET_REDIS_MANIFEST_TOOL" manifest migration non-job \
    "$MIGRATION_LOCK_TOKEN_FILE"
)" || {
  echo "The target non-job Redis state could not be captured after migration." >&2
  exit 1
}
[[ "$target_non_job_after_sha256" == "$target_non_job_before_sha256" ]] || {
  echo "A non-job Redis key changed during the migration." >&2
  exit 1
}
target_redis_manifest_before_release="$(
  bash "$TARGET_REDIS_MANIFEST_TOOL" manifest migration full \
    "$MIGRATION_LOCK_TOKEN_FILE"
)" || {
  echo "The complete target Redis manifest could not be captured before lock release." >&2
  exit 1
}
[[ "$target_redis_manifest_before_release" =~ ^[0-9a-f]{64}$ ]] || exit 1

redis_state_sha256="$(sha256sum "$RUN_DIR/source-final.json" | awk '{print $1}')"
redis_rollback_sha256="$(sha256sum "$TARGET_ROLLBACK_STATE" | awk '{print $1}')"
metadata_rollback_sha256="$(python3 - "$TARGET_ROLLBACK_METADATA" \
  "$TARGET_ROLLBACK_STATE" "$target_had_state" <<'PY'
import hashlib
import json
from pathlib import Path
import sys

metadata_path = Path(sys.argv[1])
state_path = Path(sys.argv[2])
expected_existed = sys.argv[3] == "1"
raw = metadata_path.read_bytes()
metadata = json.loads(raw)
canonical = json.dumps(
    metadata, sort_keys=True, separators=(",", ":"), ensure_ascii=True
).encode("ascii") + b"\n"
required = {"schema", "redis_key", "existed", "payload_bytes", "payload_sha256"}
payload = state_path.read_bytes()
if raw != canonical or set(metadata) != required:
    raise SystemExit("non-canonical Redis rollback metadata")
if metadata != {
    "schema": "lecturesift-redis-rollback-v1",
    "redis_key": "lecturesift:jobs:v2",
    "existed": expected_existed,
    "payload_bytes": len(payload),
    "payload_sha256": hashlib.sha256(payload).hexdigest(),
}:
    raise SystemExit("Redis rollback metadata does not bind the retained state")
print(metadata["payload_sha256"])
PY
)" || {
  echo "The retained Redis rollback state could not be revalidated." >&2
  exit 1
}
[[ "$redis_state_sha256" =~ ^[0-9a-f]{64}$ && \
   "$redis_rollback_sha256" =~ ^[0-9a-f]{64}$ && \
   "$metadata_rollback_sha256" == "$redis_rollback_sha256" ]] || {
  echo "The Redis migration evidence hashes are invalid." >&2
  exit 1
}
release_target_lock_strict
migration_committed="true"
TARGET_REDIS_MANIFEST_SHA256="$(
  bash "$TARGET_REDIS_MANIFEST_TOOL" manifest steady full
)" || {
  echo "The committed target Redis manifest could not be re-proved." >&2
  exit 1
}
[[ "$TARGET_REDIS_MANIFEST_SHA256" == "$target_redis_manifest_before_release" ]] || {
  echo "The target Redis manifest changed across migration-lock release." >&2
  exit 1
}
python3 "$CUTOVER_EVIDENCE_TOOL" write-redis \
  --cutover-id "$CUTOVER_ID" \
  --revision "$EXPECTED_BUILD_REVISION" \
  --source-fingerprint "$SOURCE_FINGERPRINT" \
  --run-id "$(basename -- "$RUN_DIR")" \
  --state-sha256 "$redis_state_sha256" \
  --source-worker-stop-evidence-sha256 "$SOURCE_WORKER_STOP_EVIDENCE_SHA256" \
  --target-redis-manifest-sha256 "$TARGET_REDIS_MANIFEST_SHA256" \
  --rollback-sha256 "$redis_rollback_sha256" || {
  echo "The global Redis cutover proof could not be recorded; production remains fail-stopped." >&2
  exit 1
}
rm -f -- "$RUN_DIR/source-before.json" "$RUN_DIR/source-after.json" \
  "$RUN_DIR/source-final.json" "$RUN_DIR/target-written.json" \
  "$RUN_DIR/target-final.json" "$RUN_DIR/source-health-"*.json \
  "$RUN_DIR/source-worker-check.log" "$MIGRATION_LOCK_TOKEN_FILE" "$target_write_resp" \
  "$target_write_log" "$target_rollback_resp" "$target_rollback_log" "$export_log"
rm -f -- "$FAIL_STOP_MARKER"
[[ ! -e "$FAIL_STOP_MARKER" && ! -L "$FAIL_STOP_MARKER" ]] || {
  echo "The Redis migration committed but its production fail-stop marker could not be cleared." >&2
  exit 1
}
trap - EXIT
unset SOURCE_REDIS_URL SOURCE_CELERY_BROKER_URL SOURCE_HEALTH_URL
echo "Root-only rollback state and canonical metadata retained: $TARGET_ROLLBACK_STATE ; $TARGET_ROLLBACK_METADATA"
