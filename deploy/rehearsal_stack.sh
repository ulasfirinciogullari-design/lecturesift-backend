#!/usr/bin/env bash
set +x
set -euo pipefail
umask 077

ROOT_DIR="${LECTURESIFT_ROOT:-/opt/lecturesift}"
RUNTIME_ENV_FILE="${LECTURESIFT_ENV_FILE:-/etc/lecturesift/runtime.env}"
PRODUCTION_DB_ENV_FILE="${LECTURESIFT_DB_ENV_FILE:-/etc/lecturesift/postgres.env}"
PRODUCTION_API_ENV_FILE="${LECTURESIFT_API_ENV_FILE:-/etc/lecturesift/api.env}"
PRODUCTION_WORKER_ENV_FILE="${LECTURESIFT_WORKER_ENV_FILE:-/etc/lecturesift/worker.env}"
REHEARSAL_ENV_FILE="${LECTURESIFT_REHEARSAL_ENV_FILE:-/etc/lecturesift/rehearsal.env}"
RELEASE_ENV_FILE="${LECTURESIFT_RELEASE_ENV_FILE:-/run/lecturesift/release.env}"
REHEARSAL_RUN_DIR="${LECTURESIFT_REHEARSAL_RUN_DIR:-}"
rehearsal_db="${1:-}"
rehearsal_run="${rehearsal_db#lecturesift_rehearsal_}"
rehearsal_redis_container="lecturesift-redis-rehearsal"
rehearsal_api_proxy_container="lecturesift-egress-proxy-api-rehearsal"
rehearsal_worker_proxy_container="lecturesift-egress-proxy-worker-rehearsal"
rehearsal_api_container="lecturesift-api-rehearsal"
rehearsal_worker_container="lecturesift-worker-rehearsal"
production_postgres_container="lecturesift-postgres-1"
rehearsal_backend_network="lecturesift_rehearsal_backend"
rehearsal_egress_network="lecturesift_egress"
rehearsal_api_proxy_network="lecturesift_rehearsal_api_proxy"
rehearsal_worker_proxy_network="lecturesift_rehearsal_worker_proxy"
rehearsal_api_work_volume="lecturesift-api-rehearsal-work"
rehearsal_worker_work_volume="lecturesift-worker-rehearsal-work"
rehearsal_api_work_bytes=$((512 * 1024 * 1024))
rehearsal_worker_work_bytes=$((2 * 1024 * 1024 * 1024))
requested_api_database_url="${LECTURESIFT_REHEARSAL_API_DATABASE_URL:-}"
requested_worker_database_url="${LECTURESIFT_REHEARSAL_WORKER_DATABASE_URL:-}"
generated_api_env="$REHEARSAL_RUN_DIR/rehearsal-api.env"
generated_worker_env="$REHEARSAL_RUN_DIR/rehearsal-worker.env"
generated_env_proof="$REHEARSAL_RUN_DIR/rehearsal-environment-proof.json"
generated_r2_capability_proof="$REHEARSAL_RUN_DIR/rehearsal-r2-negative-capability.json"
generated_api_proxy_config="$REHEARSAL_RUN_DIR/rehearsal-api-squid.conf"
generated_worker_proxy_config="$REHEARSAL_RUN_DIR/rehearsal-worker-squid.conf"
synthetic_audio="$REHEARSAL_RUN_DIR/rehearsal-synthetic-lecture.mp3"
synthetic_audio_temporary="$REHEARSAL_RUN_DIR/.rehearsal-synthetic-lecture.mp3.partial"
postgres_network_connected=false
postgres_network_handed_off=false

fail() {
  echo "Rehearsal stack isolation failed: $*" >&2
  exit 1
}

check_private_file() {
  local path="$1" label="$2" mode
  [[ -f "$path" && ! -L "$path" && "$(realpath -e -- "$path")" == "$path" && \
     "$(stat -c '%u:%g' -- "$path")" == "0:0" ]] || \
    fail "$label must be a root-owned regular non-symlink file"
  mode="$(stat -c '%a' -- "$path")"
  [[ "$mode" == "400" || "$mode" == "600" ]] || \
    fail "$label must have mode 0400 or 0600"
}

check_private_run_dir() {
  [[ "$REHEARSAL_RUN_DIR" =~ ^/var/backups/lecturesift/rehearsal/[0-9]{8}T[0-9]{6}Z$ && \
     -d "$REHEARSAL_RUN_DIR" && ! -L "$REHEARSAL_RUN_DIR" && \
     "$(realpath -e -- "$REHEARSAL_RUN_DIR")" == "$REHEARSAL_RUN_DIR" && \
     "$(stat -c '%u:%g:%a' -- "$REHEARSAL_RUN_DIR")" == "0:0:700" ]] || \
    fail "the generated environment root is not the exact private rehearsal run"
  [[ "${REHEARSAL_RUN_DIR##*/}" == \
      "${rehearsal_run:0:8}T${rehearsal_run:8:6}Z" ]] || \
    fail "the generated environment run identity does not match the database"
}

check_root_public_config() {
  local path="$1" label="$2"
  [[ -f "$path" && ! -L "$path" && "$(realpath -e -- "$path")" == "$path" && \
     "$(stat -c '%u:%g:%a' -- "$path")" == "0:0:644" ]] || \
    fail "$label must be a root-owned regular mode 0644 file"
}

write_candidate_tmp_file() {
  local container="$1" source="$2" destination="$3" mode="${4:-0400}"
  local uid="${5:-10001}" gid="${6:-10001}" labels source_resolved
  case "$container" in
    "$rehearsal_api_container"|"$rehearsal_worker_container") ;;
    *) fail "refusing to write into an unknown candidate container" ;;
  esac
  [[ "$destination" == /tmp/* && "$destination" != *"/../"* && \
     "$destination" != *"/./"* && "$destination" != *"//"* ]] || \
    fail "candidate write destination must be an absolute safe /tmp path"
  case "$destination" in
    /tmp/lecturesift-rehearsal-synthetic-audio.py|\
    /tmp/lecturesift-rehearsal-synthetic-lecture.mp3) ;;
    *) fail "candidate write destination is not allowlisted" ;;
  esac
  case "$mode" in
    0400|0600) ;;
    *) fail "candidate write mode is not allowlisted" ;;
  esac
  [[ "$uid" == "10001" && "$gid" == "10001" ]] || \
    fail "candidate writes must use the unprivileged application identity"
  [[ -f "$source" && ! -L "$source" ]] || \
    fail "candidate write source must be a regular non-symlink file"
  source_resolved="$(realpath -e -- "$source")" || \
    fail "candidate write source cannot be resolved"
  [[ "$source_resolved" == "$source" ]] || \
    fail "candidate write source must already be canonical"
  labels="$(container_labels "$container")" || \
    fail "cannot inspect candidate container labels"
  [[ "$labels" == "true|$rehearsal_run" ]] || \
    fail "refusing to write into an unlabeled or foreign candidate container"

  docker exec --user "$uid:$gid" -i "$container" sh -eu -c '
destination="$1"
mode="$2"
expected_uid="$3"
expected_gid="$4"
case "$destination" in /tmp/*) ;; *) exit 64 ;; esac
temporary="$(mktemp /tmp/.lecturesift-rehearsal-write.XXXXXX)"
cleanup_candidate_write() { rm -f -- "$temporary"; }
trap cleanup_candidate_write EXIT HUP INT TERM
cat >"$temporary"
chmod "$mode" "$temporary"
test -f "$temporary"
test ! -L "$temporary"
mv -fT -- "$temporary" "$destination"
trap - EXIT HUP INT TERM
test -f "$destination"
test ! -L "$destination"
test "$(stat -c "%u:%g:%a" -- "$destination")" = \
  "$expected_uid:$expected_gid:${mode#0}"
' sh "$destination" "$mode" "$uid" "$gid" <"$source" || \
    fail "candidate tmpfs write failed"
}

rehearsal_api_get() {
  local path="$1" timeout="${2:-8}" expected_status="${3:-200}"
  case "$path|$expected_status" in
    /health\|200|/billing/health\|200|/rollout/health\|200|/instagram/health\|503) ;;
    *) fail "rehearsal API probe path and status are not allowlisted" ;;
  esac
  [[ "$timeout" =~ ^[0-9]+$ ]] || fail "rehearsal API probe timeout is invalid"
  (( timeout >= 1 && timeout <= 30 )) || \
    fail "rehearsal API probe timeout must be between 1 and 30 seconds"

  docker exec --user 10001:10001 "$rehearsal_api_container" \
    python -I -c '
import sys
import urllib.error
import urllib.request

path = sys.argv[1]
timeout = int(sys.argv[2])
expected_status = int(sys.argv[3])
allowed_requests = {
    ("/health", 200),
    ("/billing/health", 200),
    ("/rollout/health", 200),
    ("/instagram/health", 503),
}
if (path, expected_status) not in allowed_requests:
    raise SystemExit("rehearsal API probe path and status are not allowlisted")
if not 1 <= timeout <= 30:
    raise SystemExit("rehearsal API probe timeout is outside the safe range")
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
request = urllib.request.Request(
    "http://127.0.0.1:8000" + path,
    headers={"Accept": "application/json"},
)
try:
    response = opener.open(request, timeout=timeout)
except urllib.error.HTTPError as error:
    response = error
with response:
    if response.status != expected_status:
        raise SystemExit(f"unexpected rehearsal API status: {response.status}")
    body = response.read(1048577)
if len(body) > 1048576:
    raise SystemExit("rehearsal API response exceeds 1 MiB")
sys.stdout.buffer.write(body)
' "$path" "$timeout" "$expected_status"
}

container_labels() {
  docker container inspect --format \
    '{{ index .Config.Labels "lecturesift.rehearsal" }}|{{ index .Config.Labels "lecturesift.rehearsal.run" }}' \
    "$1"
}

volume_labels() {
  docker volume inspect --format \
    '{{ index .Labels "lecturesift.rehearsal" }}|{{ index .Labels "lecturesift.rehearsal.run" }}' \
    "$1"
}

network_labels() {
  docker network inspect --format \
    '{{ index .Labels "lecturesift.rehearsal" }}|{{ index .Labels "lecturesift.rehearsal.run" }}' \
    "$1"
}

remove_current_container() {
  local container="$1" labels
  docker container inspect "$container" >/dev/null 2>&1 || return 0
  labels="$(container_labels "$container")" || fail "cannot inspect $container labels"
  [[ "$labels" == "true|$rehearsal_run" ]] || \
    fail "refusing to delete an unlabeled or foreign fixed-name container: $container"
  docker rm -f "$container" >/dev/null
}

remove_current_volume() {
  local volume="$1" labels
  docker volume inspect "$volume" >/dev/null 2>&1 || return 0
  labels="$(volume_labels "$volume")" || fail "cannot inspect $volume labels"
  [[ "$labels" == "true|$rehearsal_run" ]] || \
    fail "refusing to delete an unlabeled or foreign fixed-name volume: $volume"
  docker volume rm "$volume" >/dev/null || \
    fail "the current rehearsal volume is still in use: $volume"
}

remove_current_network() {
  local network="$1" labels
  docker network inspect "$network" >/dev/null 2>&1 || return 0
  labels="$(network_labels "$network")" || fail "cannot inspect $network labels"
  [[ "$labels" == "true|$rehearsal_run" ]] || \
    fail "refusing to delete an unlabeled or foreign fixed-name network: $network"
  docker network rm "$network" >/dev/null || \
    fail "the current rehearsal proxy network is still in use: $network"
}

[[ "$(id -u)" == "0" ]] || fail "run this rehearsal stack as root"
[[ "${LECTURESIFT_REHEARSAL_ORCHESTRATED:-}" == "YES" ]] || \
  fail "run the rehearsal only through deploy/rehearsal_restore.sh"
[[ "$rehearsal_db" =~ ^lecturesift_rehearsal_[0-9]{14}$ ]] || \
  fail "invalid rehearsal database name"
[[ -n "$requested_api_database_url" && -n "$requested_worker_database_url" ]] || \
  fail "isolated rehearsal database URLs are required"
check_private_run_dir
for specification in \
  "$RUNTIME_ENV_FILE|production runtime environment" \
  "$PRODUCTION_DB_ENV_FILE|production database environment" \
  "$PRODUCTION_API_ENV_FILE|production API environment" \
  "$PRODUCTION_WORKER_ENV_FILE|production worker environment" \
  "$REHEARSAL_ENV_FILE|rehearsal environment"; do
  check_private_file "${specification%%|*}" "${specification#*|}"
done
[[ -f "$ROOT_DIR/deploy/release.sh" && ! -L "$ROOT_DIR/deploy/release.sh" && \
   -f "$ROOT_DIR/deploy/assert_rehearsal_production_stopped.sh" && \
   ! -L "$ROOT_DIR/deploy/assert_rehearsal_production_stopped.sh" && \
   -f "$ROOT_DIR/deploy/generate_rehearsal_envs.py" && \
   ! -L "$ROOT_DIR/deploy/generate_rehearsal_envs.py" && \
   -f "$ROOT_DIR/deploy/prove_rehearsal_r2_isolation.py" && \
   ! -L "$ROOT_DIR/deploy/prove_rehearsal_r2_isolation.py" && \
   -f "$ROOT_DIR/deploy/rehearsal_synthetic_audio.py" && \
   ! -L "$ROOT_DIR/deploy/rehearsal_synthetic_audio.py" ]] || \
  fail "release or rehearsal environment helper is missing"

bash "$ROOT_DIR/deploy/assert_rehearsal_production_stopped.sh" >/dev/null || \
  fail "production runtime or egress proxy is active"
export LECTURESIFT_RELEASE_ENV_FILE="$RELEASE_ENV_FILE"
bash "$ROOT_DIR/deploy/release.sh" prepare
bash "$ROOT_DIR/deploy/release.sh" build
check_private_file "$RELEASE_ENV_FILE" "release environment"

bash "$ROOT_DIR/deploy/assert_rehearsal_production_stopped.sh" >/dev/null || \
  fail "production runtime or egress proxy became active"

LECTURESIFT_REHEARSAL_API_DATABASE_URL="$requested_api_database_url" \
LECTURESIFT_REHEARSAL_WORKER_DATABASE_URL="$requested_worker_database_url" \
  python3 "$ROOT_DIR/deploy/generate_rehearsal_envs.py" \
    --runtime "$RUNTIME_ENV_FILE" --database "$PRODUCTION_DB_ENV_FILE" \
    --api "$PRODUCTION_API_ENV_FILE" \
    --worker "$PRODUCTION_WORKER_ENV_FILE" --rehearsal "$REHEARSAL_ENV_FILE" \
    --release "$RELEASE_ENV_FILE" --output-dir "$REHEARSAL_RUN_DIR"
check_private_file "$generated_api_env" "generated rehearsal API environment"
check_private_file "$generated_worker_env" "generated rehearsal worker environment"
check_private_file "$generated_env_proof" "rehearsal environment proof"
check_root_public_config "$generated_api_proxy_config" "generated rehearsal API egress policy"
check_root_public_config "$generated_worker_proxy_config" "generated rehearsal worker egress policy"

cleanup_generated_envs() {
  local status="$?" labels networks
  trap - EXIT
  # A failed inner stack owns and removes its temporary PostgreSQL attachment.
  # After every stack proof succeeds, lifecycle ownership is handed to the
  # outer restore orchestrator so the same private endpoint remains available
  # for the application, format and purge E2Es that run after this script.
  if [[ "$postgres_network_connected" == "true" &&
        "$postgres_network_handed_off" != "true" ]]; then
    labels="$(network_labels "$rehearsal_backend_network" 2>/dev/null || true)"
    networks="$(docker container inspect --format \
      '{{range $name, $_ := .NetworkSettings.Networks}}{{println $name}}{{end}}' \
      "$production_postgres_container" 2>/dev/null || true)"
    if [[ "$labels" == "true|$rehearsal_run" ]] && \
       grep -Fqx "$rehearsal_backend_network" <<<"$networks"; then
      docker network disconnect "$rehearsal_backend_network" \
        "$production_postgres_container" >/dev/null || status=1
    else
      echo "Rehearsal stack isolation failed: unsafe PostgreSQL network cleanup" >&2
      status=1
    fi
  fi
  rm -f -- "$generated_api_env" "$generated_worker_env" \
    "$generated_api_proxy_config" "$generated_worker_proxy_config" \
    "$synthetic_audio" "$synthetic_audio_temporary"
  exit "$status"
}
trap cleanup_generated_envs EXIT

# Literal inequality is not a capability boundary.  First prove that the
# rehearsal identity really works on its own R2 bucket, then require both a
# bucket-list and a missing-object read to be unambiguously denied against the
# production bucket.  The helper is read-only and writes only a secret-free,
# root-private artifact that the final admission hashes.
python3 "$ROOT_DIR/deploy/prove_rehearsal_r2_isolation.py" \
  --runtime "$RUNTIME_ENV_FILE" --api "$PRODUCTION_API_ENV_FILE" \
  --rehearsal "$REHEARSAL_ENV_FILE" \
  --environment-proof "$generated_env_proof" \
  --output "$generated_r2_capability_proof" >/dev/null || \
  fail "dedicated rehearsal R2 credentials were not proved production-inaccessible"
check_private_file "$generated_r2_capability_proof" \
  "rehearsal R2 negative-capability proof"

# Direct constrained containers structurally prevent compose.yaml's production
# api.env and worker.env from entering a rehearsal process.
for container in "$rehearsal_api_container" "$rehearsal_worker_container" \
  "$rehearsal_redis_container" "$rehearsal_api_proxy_container" \
  "$rehearsal_worker_proxy_container"; do
  remove_current_container "$container"
done
for network in "$rehearsal_backend_network" "$rehearsal_api_proxy_network" \
  "$rehearsal_worker_proxy_network"; do
  remove_current_network "$network"
  docker network create --driver bridge --internal \
    --label lecturesift.rehearsal=true \
    --label "lecturesift.rehearsal.run=$rehearsal_run" "$network" >/dev/null
done
for specification in \
  "$rehearsal_api_work_volume|$rehearsal_api_work_bytes" \
  "$rehearsal_worker_work_volume|$rehearsal_worker_work_bytes"; do
  volume="${specification%%|*}"
  work_bytes="${specification#*|}"
  remove_current_volume "$volume"
  docker volume create --driver local --opt type=tmpfs --opt device=tmpfs \
    --opt "o=size=$work_bytes,uid=10001,gid=10001,mode=0700,noexec,nosuid,nodev" \
    --label lecturesift.rehearsal=true \
    --label "lecturesift.rehearsal.run=$rehearsal_run" "$volume" >/dev/null
  docker volume inspect "$volume" | EXPECTED_BYTES="$work_bytes" python3 -c '
import json, os, sys
payload = json.load(sys.stdin)
if len(payload) != 1:
    raise SystemExit("unexpected work volume inspection")
item = payload[0]
options = item.get("Options") or {}
mount = set((options.get("o") or "").split(","))
required = {
    "size=" + os.environ["EXPECTED_BYTES"], "uid=10001", "gid=10001",
    "mode=0700", "noexec", "nosuid", "nodev",
}
if item.get("Driver") != "local" or options.get("type") != "tmpfs" or not required.issubset(mount):
    raise SystemExit("rehearsal work volume is not bounded tmpfs")
' || fail "cannot prove the rehearsal work-volume quota"
done
for network in "$rehearsal_backend_network" "$rehearsal_egress_network"; do
  docker network inspect "$network" >/dev/null 2>&1 || \
    fail "missing private rehearsal network: $network"
done
docker network inspect "$rehearsal_backend_network" | python3 -c '
import json, sys
payload = json.load(sys.stdin)
if len(payload) != 1 or payload[0].get("Internal") is not True:
    raise SystemExit("rehearsal backend network is not internal")
' || fail "the rehearsal backend network has a direct Internet route"

# Candidate containers never join the production backend.  PostgreSQL is the
# sole required production data-plane endpoint, so attach only that validated
# service to the dedicated internal network under the hostname already bound
# into the short-lived clone-role URLs.  Production Redis remains unreachable.
docker container inspect "$production_postgres_container" | python3 -c '
import json, sys
payload = json.load(sys.stdin)
if len(payload) != 1:
    raise SystemExit("unexpected PostgreSQL container inspection")
container = payload[0]
labels = container.get("Config", {}).get("Labels") or {}
if (
    labels.get("com.docker.compose.project") != "lecturesift"
    or labels.get("com.docker.compose.service") != "postgres"
    or not container.get("State", {}).get("Running")
):
    raise SystemExit("the expected production PostgreSQL service is unavailable")
' || fail "cannot validate the production PostgreSQL endpoint"
docker network connect --alias postgres "$rehearsal_backend_network" \
  "$production_postgres_container"
postgres_network_connected=true

docker run -d --pull=never --name "$rehearsal_redis_container" \
  --label lecturesift.rehearsal=true \
  --label "lecturesift.rehearsal.run=$rehearsal_run" \
  --network "$rehearsal_backend_network" --read-only \
  --tmpfs /data:rw,noexec,nosuid,size=128m \
  --security-opt no-new-privileges --pids-limit 128 \
  --memory 192m --memory-swap 192m --cpus 0.50 \
  redis:7.4-alpine@sha256:ff02b58f971e7d7d156a1267e283fcbbeee91773b6aa36c49dac28ecfe28eadf redis-server --save '' --appendonly no >/dev/null
for _ in $(seq 1 30); do
  docker exec "$rehearsal_redis_container" redis-cli --raw ping \
    | grep -q '^PONG$' && break
  sleep 1
done
docker exec "$rehearsal_redis_container" redis-cli --raw ping \
  | grep -q '^PONG$' || fail "the isolated rehearsal Redis instance is not ready"

start_rehearsal_proxy() {
  local container="$1" alias="$2" config="$3" proxy_network="$4"
  local proxy_ready="false"
  docker create --pull=never --name "$container" \
    --label lecturesift.rehearsal=true \
    --label "lecturesift.rehearsal.run=$rehearsal_run" \
    --network "$rehearsal_egress_network" \
    --read-only --init --security-opt no-new-privileges --pids-limit 128 \
    --memory 256m --memory-swap 256m --cpus 0.50 \
    --tmpfs /run/squid:rw,noexec,nosuid,nodev,size=8m,uid=13,gid=13,mode=0755 \
    --tmpfs /tmp:rw,noexec,nosuid,nodev,size=128m,uid=13,gid=13,mode=1777 \
    --mount "type=bind,src=$config,dst=/etc/squid/squid.conf,readonly" \
    lecturesift-egress-proxy:local >/dev/null
  docker network connect --alias "$alias" "$proxy_network" "$container"
  docker start "$container" >/dev/null
  for _ in $(seq 1 30); do
    if docker exec "$container" \
        squidclient -h 127.0.0.1 -p 3128 mgr:info >/dev/null 2>&1; then
      proxy_ready="true"
      break
    fi
    sleep 1
  done
  [[ "$proxy_ready" == "true" ]] || fail "the isolated $alias proxy is not ready"
}

start_rehearsal_proxy "$rehearsal_api_proxy_container" \
  egress-proxy-api "$generated_api_proxy_config" "$rehearsal_api_proxy_network"
start_rehearsal_proxy "$rehearsal_worker_proxy_container" \
  egress-proxy-worker "$generated_worker_proxy_config" "$rehearsal_worker_proxy_network"

for specification in \
  "$rehearsal_api_proxy_container|$rehearsal_api_proxy_network|egress-proxy-api" \
  "$rehearsal_worker_proxy_container|$rehearsal_worker_proxy_network|egress-proxy-worker"; do
  container="${specification%%|*}"
  remainder="${specification#*|}"
  expected_proxy_network="${remainder%%|*}"
  expected_alias="${remainder#*|}"
  docker inspect "$container" | \
  EXPECTED_PROXY_NETWORK="$expected_proxy_network" \
  EXPECTED_EGRESS_NETWORK="$rehearsal_egress_network" \
  EXPECTED_ALIAS="$expected_alias" python3 -c '
import json, os, sys

payload = json.load(sys.stdin)[0]
networks = payload["NetworkSettings"].get("Networks") or {}
expected = {os.environ["EXPECTED_PROXY_NETWORK"], os.environ["EXPECTED_EGRESS_NETWORK"]}
if set(networks) != expected:
    raise SystemExit("proxy has an unexpected network attachment")
aliases = networks[os.environ["EXPECTED_PROXY_NETWORK"]].get("Aliases") or []
if os.environ["EXPECTED_ALIAS"] not in aliases:
    raise SystemExit("proxy lacks its role-specific backend alias")
' || fail "resolved proxy topology verification failed"
done

docker create --pull=never --name "$rehearsal_worker_container" \
  --label lecturesift.rehearsal=true \
  --label "lecturesift.rehearsal.run=$rehearsal_run" \
  --network "$rehearsal_backend_network" --read-only --init --user 10001:10001 \
  --security-opt no-new-privileges --pids-limit 768 \
  --memory 3584m --memory-swap 3584m --cpus 3.25 \
  --tmpfs /tmp:rw,noexec,nosuid,nodev,size=768m,mode=1777 \
  --mount "type=volume,src=$rehearsal_worker_work_volume,dst=/var/lib/lecturesift" \
  --env-file "$generated_worker_env" \
  lecturesift-backend:local celery -A lecturesift.queue.celery_app worker \
    --loglevel=INFO --concurrency=1 --max-tasks-per-child=10 \
    --max-memory-per-child=2500000 >/dev/null
docker network connect "$rehearsal_worker_proxy_network" \
  "$rehearsal_worker_container"
docker start "$rehearsal_worker_container" >/dev/null

ai_provider="$(python3 - "$generated_env_proof" <<'PY'
import json
import sys

payload = json.load(open(sys.argv[1], encoding="utf-8"))
value = payload.get("ai_provider")
if value not in {"dedicated", "intentionally_absent"}:
    raise SystemExit("invalid rehearsal AI provider proof")
print(value)
PY
)" || fail "cannot verify the rehearsal AI provider proof"
if [[ "$ai_provider" == "dedicated" ]]; then
  write_candidate_tmp_file "$rehearsal_worker_container" \
    "$ROOT_DIR/deploy/rehearsal_synthetic_audio.py" \
    /tmp/lecturesift-rehearsal-synthetic-audio.py 0400
  docker exec --user 10001:10001 -e PYTHONPATH=/app "$rehearsal_worker_container" \
    python -P /tmp/lecturesift-rehearsal-synthetic-audio.py
  [[ ! -e "$synthetic_audio_temporary" && ! -L "$synthetic_audio_temporary" ]] || \
    fail "dedicated rehearsal synthetic-audio staging path already exists"
  docker exec --user 10001:10001 "$rehearsal_worker_container" sh -eu -c '
source=/var/lib/lecturesift/rehearsal-synthetic-lecture.mp3
test -f "$source"
test ! -L "$source"
test "$(stat -c "%u:%g" -- "$source")" = "10001:10001"
cat -- "$source"
' >"$synthetic_audio_temporary" || \
    fail "dedicated rehearsal synthetic audio could not be streamed privately"
  [[ -f "$synthetic_audio_temporary" && ! -L "$synthetic_audio_temporary" && \
     "$(stat -c '%u:%g' -- "$synthetic_audio_temporary")" == "0:0" ]] || \
    fail "dedicated rehearsal synthetic audio was not staged privately"
  chmod 0600 "$synthetic_audio_temporary"
  mv -fT -- "$synthetic_audio_temporary" "$synthetic_audio"
fi

docker create --pull=never --name "$rehearsal_api_container" \
  --label lecturesift.rehearsal=true \
  --label "lecturesift.rehearsal.run=$rehearsal_run" \
  --network "$rehearsal_backend_network" --read-only --init --user 10001:10001 \
  --security-opt no-new-privileges --pids-limit 384 \
  --memory 768m --memory-swap 768m --cpus 1.00 \
  --tmpfs /tmp:rw,noexec,nosuid,nodev,size=128m,mode=1777 \
  --mount "type=volume,src=$rehearsal_api_work_volume,dst=/var/lib/lecturesift" \
  --env-file "$generated_api_env" \
  lecturesift-backend:local uvicorn main:app --host 127.0.0.1 --port 8000 \
    --proxy-headers --forwarded-allow-ips=127.0.0.1 >/dev/null
docker network connect "$rehearsal_api_proxy_network" "$rehearsal_api_container"
docker start "$rehearsal_api_container" >/dev/null
bash "$ROOT_DIR/deploy/assert_rehearsal_production_stopped.sh" >/dev/null || \
  fail "production runtime or egress proxy became active"
if [[ "$ai_provider" == "dedicated" ]]; then
  write_candidate_tmp_file "$rehearsal_api_container" "$synthetic_audio" \
    /tmp/lecturesift-rehearsal-synthetic-lecture.mp3 0400
fi

# Resolve the actual container topology and environment.  The API has only the
# internal backend network and its own R2-only proxy but no AI/provider
# credential. The worker has a separate R2/OpenAI policy, and
# both candidate roles are timestamp-derived rather than the production owner.
for specification in \
  "$rehearsal_api_container|lecturesift_rehearsal_api_$rehearsal_run|api" \
  "$rehearsal_worker_container|lecturesift_rehearsal_worker_$rehearsal_run|worker"; do
  container="${specification%%|*}"
  remainder="${specification#*|}"
  expected_role="${remainder%%|*}"
  role_kind="${remainder#*|}"
  docker inspect "$container" | \
  EXPECTED_NETWORK="$rehearsal_backend_network" EXPECTED_ROLE="$expected_role" \
  EXPECTED_PROXY_NETWORK="lecturesift_rehearsal_${role_kind}_proxy" \
  EXPECTED_KIND="$role_kind" python3 -c '
import json, os, sys
from urllib.parse import urlsplit

payload = json.load(sys.stdin)[0]
environment = {}
for item in payload["Config"].get("Env") or []:
    key, _, value = item.partition("=")
    environment[key] = value
networks = set((payload["NetworkSettings"].get("Networks") or {}).keys())
if networks != {os.environ["EXPECTED_NETWORK"], os.environ["EXPECTED_PROXY_NETWORK"]}:
    raise SystemExit("candidate has an unexpected network attachment")
host_bindings = payload.get("HostConfig", {}).get("PortBindings") or {}
runtime_ports = payload.get("NetworkSettings", {}).get("Ports") or {}
if host_bindings:
    raise SystemExit("candidate has an unexpected host port binding")
if any(value is not None for value in runtime_ports.values()):
    raise SystemExit("candidate has an unexpected runtime host port binding")
url = urlsplit(environment.get("DATABASE_URL", ""))
if url.username != os.environ["EXPECTED_ROLE"] or url.hostname != "postgres":
    raise SystemExit("candidate database identity is not isolated")
forbidden = {
    "POSTGRES_USER", "POSTGRES_PASSWORD", "IYZICO_API_KEY", "IYZICO_SECRET_KEY",
    "PAYTR_MERCHANT_KEY", "PAYTR_MERCHANT_SALT", "RESEND_API_KEY", "SMTP_PASSWORD",
    "INSTAGRAM_ACCESS_TOKEN", "INSTAGRAM_APP_SECRET", "INSTAGRAM_ADMIN_TOKEN",
}
if forbidden.intersection(environment):
    raise SystemExit("candidate received a production/provider credential key")
if os.environ["EXPECTED_KIND"] == "api":
    if "OPENAI_API_KEY" in environment:
        raise SystemExit("API received an AI credential")
expected_proxy = "http://egress-proxy-" + os.environ["EXPECTED_KIND"] + ":3128"
if any(environment.get(key) != expected_proxy for key in (
    "HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"
)):
    raise SystemExit("candidate lacks its role-specific rehearsal proxy")
' || fail "resolved candidate isolation verification failed"
done

# Runtime proof of the data-plane boundary: both candidate roles can resolve
# only rehearsal Redis, never either production service name.  This catches a
# future accidental reattachment to lecturesift_backend before any E2E work.
for container in "$rehearsal_api_container" "$rehearsal_worker_container"; do
  docker exec "$container" python -c '
import socket
for host in ("redis", "lecturesift-redis-1"):
    try:
        socket.getaddrinfo(host, 6379)
    except socket.gaierror:
        continue
    raise SystemExit("production Redis became resolvable: " + host)
addresses = socket.getaddrinfo("lecturesift-redis-rehearsal", 6379)
if not addresses:
    raise SystemExit("rehearsal Redis is not resolvable")
' || fail "candidate production Redis isolation probe failed"
done

for specification in \
  "$rehearsal_api_container|$rehearsal_api_work_bytes" \
  "$rehearsal_worker_container|$rehearsal_worker_work_bytes"; do
  container="${specification%%|*}"
  work_bytes="${specification#*|}"
  docker exec -e EXPECTED_WORK_BYTES="$work_bytes" "$container" python -c '
import os
stats = os.statvfs("/var/lib/lecturesift")
capacity = stats.f_frsize * stats.f_blocks
expected = int(os.environ["EXPECTED_WORK_BYTES"])
if capacity <= 0 or capacity > expected:
    raise SystemExit(f"unbounded rehearsal work mount: {capacity}>{expected}")
' || fail "candidate work-volume quota probe failed"
done

ready=false
for _ in $(seq 1 90); do
  if rehearsal_api_get /health 5 >/dev/null 2>&1; then
    ready=true
    break
  fi
  sleep 2
done
if [[ "$ready" != "true" ]]; then
  docker logs --tail 100 "$rehearsal_api_container" >&2 || true
  fail "rehearsal API did not become healthy"
fi

# The API needs R2, so its dedicated proxy permits only the generated R2 host.
# Prove both that arbitrary public egress is denied and that it has no direct
# route around that proxy. Storage health below separately proves the admitted
# R2 path works; the API can never inherit the worker's optional OpenAI route.
docker exec "$rehearsal_api_container" python -c '
import socket, urllib.error, urllib.request
try:
    socket.getaddrinfo("egress-proxy-worker", 3128)
except socket.gaierror:
    pass
else:
    raise SystemExit("API can resolve the worker egress proxy")
for host in ("example.com", "api.openai.com"):
    with socket.create_connection(("egress-proxy-api", 3128), timeout=5) as connection:
        request = f"CONNECT {host}:443 HTTP/1.1\r\nHost: {host}:443\r\n\r\n"
        connection.sendall(request.encode("ascii"))
        status = connection.makefile("rb").readline(4097).decode("ascii", "strict").strip()
    if status not in {"HTTP/1.0 403 Forbidden", "HTTP/1.1 403 Forbidden"}:
        raise SystemExit("API proxy did not explicitly deny: " + host)
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
try:
    opener.open("https://1.1.1.1/", timeout=5)
except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError):
    pass
else:
    raise SystemExit("direct Internet egress unexpectedly succeeded")
' || fail "candidate Internet isolation probe failed"

worker_ready=false
for _ in $(seq 1 60); do
  if rehearsal_api_get /rollout/health 8 \
    | jq --exit-status \
      '.durable_processing_ready == true and .queue.connected == true and
       .storage.connected == true and .worker.reachable == true and
       .worker.workers >= 1' >/dev/null; then
    worker_ready=true
    break
  fi
  sleep 2
done
if [[ "$worker_ready" != "true" ]]; then
  docker logs --tail 100 "$rehearsal_worker_container" >&2 || true
  fail "rehearsal worker did not become healthy"
fi

echo "HEALTH"
rehearsal_api_get /health 8
echo
echo "BILLING"
rehearsal_api_get /billing/health 8
echo
echo "ROLLOUT"
rehearsal_api_get /rollout/health 8
echo
echo "INSTAGRAM"
instagram_health="$(rehearsal_api_get /instagram/health 8 503)" || \
  fail "Instagram provider-disable health proof failed"
printf '%s\n' "$instagram_health" | jq --exit-status '
  type == "object" and keys == ["detail"] and
  (.detail | type == "object" and keys == ["code", "message"] and
    .code == "LS-IG-01" and (.message | type == "string" and length > 0))
' >/dev/null || fail "Instagram health did not prove the exact safe disabled-provider response"
printf '%s\n' "$instagram_health"
echo
echo "Rehearsal API and worker use generated allowlists with no host ingress."
postgres_network_handed_off=true
