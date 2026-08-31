#!/usr/bin/env bash
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
target_updated="false"
migration_committed="false"
target_had_state="0"
target_lock_acquired="false"
MIGRATION_LOCK_KEY="lecturesift:jobs:v2:write-lock"
MIGRATION_LOCK_TOKEN="$(cat /proc/sys/kernel/random/uuid)"
CUTOVER_ID="${LECTURESIFT_PROVIDER_CUTOVER_ID:-}"
EXPECTED_BUILD_REVISION="${LECTURESIFT_EXPECTED_BUILD_REVISION:-}"

[[ -f "$CUTOVER_EVIDENCE_TOOL" && ! -L "$CUTOVER_EVIDENCE_TOOL" ]] || {
  echo "The provider-cutover evidence helper is missing or unsafe." >&2
  exit 1
}
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
    elif [[ "$target_had_state" == "1" && -f "$RUN_DIR/target-before.json" ]]; then
      if ! docker compose exec -T redis redis-cli --raw GET lecturesift:jobs:v2 \
          >"$rollback_current"; then
        cleanup_failed="true"
      else
        python3 - "$rollback_current" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
value = path.read_bytes()
if value.endswith(b"\n"):
    value = value[:-1]
if value.endswith(b"\r"):
    value = value[:-1]
path.write_bytes(value)
PY
        cmp --silent "$RUN_DIR/target-before.json" "$rollback_current" || \
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

assert_target_broker_empty() {
  local phase="$1"
  local broker_state

  # Kombu stores every Redis-backed Celery queue, including the binary-suffix
  # priority variants, as a Redis list. Scan all list keys so a custom queue
  # name cannot bypass the cutover guard. The two reserved unacked structures
  # must also be empty and have their expected Redis types when present.
  if ! broker_state="$(docker compose exec -T redis redis-cli --raw EVAL '
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
SOURCE_REDIS_URL="${REDIS_URL:-}"
SOURCE_CELERY_BROKER_URL="${CELERY_BROKER_URL:-}"
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

check_source_frozen() {
  local phase="$1"
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

  # An operator flag alone is not proof that a worker is gone. A Celery remote
  # control ping against the source broker must return no consumers; broker
  # connection failures also fail closed.
  if ! timeout 30 docker run --rm --pull=never --network bridge --user 0 \
    -e SOURCE_CELERY_BROKER_URL --entrypoint python lecturesift-backend:local -c \
    'import os; from celery import Celery; app=Celery(broker=os.environ["SOURCE_CELERY_BROKER_URL"]); replies=app.control.ping(timeout=8); raise SystemExit(1 if replies else 0)' \
    >>"$RUN_DIR/source-worker-check.log" 2>&1; then
    echo "The source worker is reachable or its stopped state could not be independently proved." >&2
    return 1
  fi
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

export_log="$RUN_DIR/source-export.log"
if ! docker run --rm --pull=never --network bridge --user 0 \
  -e SOURCE_REDIS_URL \
  -v "$ROOT_DIR/deploy:/deploy:ro" \
  -v "$RUN_DIR:/migration" \
  --entrypoint python lecturesift-backend:local \
  /deploy/export_redis_state.py /migration/source-before.json >"$export_log" 2>&1; then
  echo "The source Redis state could not be exported. Root-only diagnostic: $export_log" >&2
  exit 1
fi

assert_target_broker_empty "before-import"
target_had_state="$(docker compose exec -T redis redis-cli --raw EXISTS lecturesift:jobs:v2 | tr -d '\r')"
docker compose exec -T redis redis-cli --raw GET lecturesift:jobs:v2 \
  >"$RUN_DIR/target-before.json"
python3 - "$RUN_DIR/target-before.json" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
value = path.read_bytes()
if value.endswith(b"\n"):
    value = value[:-1]
if value.endswith(b"\r"):
    value = value[:-1]
path.write_bytes(value)
PY
chmod 0600 "$RUN_DIR/target-before.json" "$export_log"

# Prepare the inverse write before touching the target. Cleanup can therefore
# restore the exact previous value (or exact absence) and fsync it with WAITAOF
# on one Redis connection even when the forward SET was applied but its own
# acknowledgement failed.
target_rollback_resp="$RUN_DIR/target-rollback.resp"
target_rollback_log="$RUN_DIR/target-rollback.log"
python3 - "$RUN_DIR/target-before.json" "$target_rollback_resp" \
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

if ! docker run --rm --pull=never --network bridge --user 0 \
  -e SOURCE_REDIS_URL \
  -v "$ROOT_DIR/deploy:/deploy:ro" \
  -v "$RUN_DIR:/migration" \
  --entrypoint python lecturesift-backend:local \
  /deploy/export_redis_state.py /migration/source-after.json >>"$export_log" 2>&1; then
  echo "The source Redis state could not be rechecked. Root-only diagnostic: $export_log" >&2
  exit 1
fi

check_source_frozen final
if ! docker run --rm --pull=never --network bridge --user 0 \
  -e SOURCE_REDIS_URL \
  -v "$ROOT_DIR/deploy:/deploy:ro" \
  -v "$RUN_DIR:/migration" \
  --entrypoint python lecturesift-backend:local \
  /deploy/export_redis_state.py /migration/source-final.json >>"$export_log" 2>&1; then
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

redis_state_sha256="$(sha256sum "$RUN_DIR/source-final.json" | awk '{print $1}')"
redis_rollback_sha256="$(sha256sum "$RUN_DIR/target-before.json" | awk '{print $1}')"
[[ "$redis_state_sha256" =~ ^[0-9a-f]{64}$ && \
   "$redis_rollback_sha256" =~ ^[0-9a-f]{64}$ ]] || {
  echo "The Redis migration evidence hashes are invalid." >&2
  exit 1
}
release_target_lock_strict
migration_committed="true"
python3 "$CUTOVER_EVIDENCE_TOOL" write-redis \
  --cutover-id "$CUTOVER_ID" \
  --revision "$EXPECTED_BUILD_REVISION" \
  --source-fingerprint "$SOURCE_FINGERPRINT" \
  --run-id "$(basename -- "$RUN_DIR")" \
  --state-sha256 "$redis_state_sha256" \
  --rollback-sha256 "$redis_rollback_sha256" || {
  echo "The global Redis cutover proof could not be recorded; production remains fail-stopped." >&2
  exit 1
}
rm -f -- "$RUN_DIR/source-before.json" "$RUN_DIR/source-after.json" \
  "$RUN_DIR/source-final.json" "$RUN_DIR/target-written.json" \
  "$RUN_DIR/target-final.json" "$RUN_DIR/source-health-"*.json \
  "$RUN_DIR/source-worker-check.log" "$target_write_resp" \
  "$target_write_log" "$target_rollback_resp" "$target_rollback_log" "$export_log"
rm -f -- "$FAIL_STOP_MARKER"
[[ ! -e "$FAIL_STOP_MARKER" && ! -L "$FAIL_STOP_MARKER" ]] || {
  echo "The Redis migration committed but its production fail-stop marker could not be cleared." >&2
  exit 1
}
trap - EXIT
unset SOURCE_REDIS_URL SOURCE_CELERY_BROKER_URL SOURCE_HEALTH_URL
echo "Rollback copy retained: $RUN_DIR/target-before.json"
