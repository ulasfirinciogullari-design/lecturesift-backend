#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="${LECTURESIFT_ROOT:-/opt/lecturesift}"
ENV_FILE="${LECTURESIFT_ENV_FILE:-/etc/lecturesift/runtime.env}"
REHEARSAL_ENV_FILE="${LECTURESIFT_REHEARSAL_ENV_FILE:-/etc/lecturesift/rehearsal.env}"
RELEASE_ENV_FILE="${LECTURESIFT_RELEASE_ENV_FILE:-/run/lecturesift/release.env}"
rehearsal_db="${1:-}"
rehearsal_redis_container="lecturesift-redis-rehearsal"
rehearsal_proxy_container="lecturesift-egress-proxy-rehearsal"
rehearsal_network="lecturesift_backend"
rehearsal_api_work_volume="lecturesift-api-rehearsal-work"
rehearsal_worker_work_volume="lecturesift-worker-rehearsal-work"
rehearsal_compose_file=""

check_private_env() {
  local path="$1"
  local label="$2"
  if [[ ! -f "$path" || -L "$path" ]]; then
    echo "$label must be a regular non-symlink file: $path" >&2
    exit 1
  fi
  if [[ "$(stat -c '%u' -- "$path")" != "0" ]]; then
    echo "$label must be owned by root: $path" >&2
    exit 1
  fi
  case "$(stat -c '%a' -- "$path")" in
    400|600) ;;
    *)
      echo "$label must have mode 0400 or 0600: $path" >&2
      exit 1
      ;;
  esac
}

if [[ "$(id -u)" != "0" ]]; then
  echo "Run this rehearsal stack as root." >&2
  exit 1
fi
if [[ "${LECTURESIFT_REHEARSAL_ORCHESTRATED:-}" != "YES" ]]; then
  echo "Run the rehearsal only through deploy/rehearsal_restore.sh." >&2
  exit 1
fi
if [[ ! "$rehearsal_db" =~ ^lecturesift_rehearsal_[0-9]{14}$ ]]; then
  echo "Invalid rehearsal database name." >&2
  exit 1
fi
check_private_env "$ENV_FILE" "Production runtime environment"
check_private_env "$REHEARSAL_ENV_FILE" "Rehearsal environment"
[[ -f "$ROOT_DIR/deploy/release.sh" && ! -L "$ROOT_DIR/deploy/release.sh" ]] || {
  echo "Exact release helper is missing or unsafe." >&2
  exit 1
}
export LECTURESIFT_RELEASE_ENV_FILE="$RELEASE_ENV_FILE"
bash "$ROOT_DIR/deploy/release.sh" prepare
bash "$ROOT_DIR/deploy/release.sh" build

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
# Rehearsal-only storage credentials stay outside runtime.env so they cannot
# enter the generated production API environment. Source them only after the
# dedicated file has passed the same root-only checks as production secrets.
# shellcheck disable=SC1090
source "$REHEARSAL_ENV_FILE"
set +a
: "${DATABASE_URL:?Missing DATABASE_URL}"
: "${LECTURESIFT_WORKER_DATABASE_URL:?Missing LECTURESIFT_WORKER_DATABASE_URL}"
: "${CELERY_BROKER_URL:?Missing CELERY_BROKER_URL}"
: "${REDIS_URL:?Missing REDIS_URL}"
: "${S3_BUCKET:?Missing S3_BUCKET}"
: "${LECTURESIFT_REHEARSAL_S3_BUCKET:?Set a dedicated LECTURESIFT_REHEARSAL_S3_BUCKET}"
: "${LECTURESIFT_REHEARSAL_S3_ACCESS_KEY_ID:?Set a dedicated rehearsal storage access key}"
: "${LECTURESIFT_REHEARSAL_S3_SECRET_ACCESS_KEY:?Set a dedicated rehearsal storage secret key}"

if [[ "$LECTURESIFT_REHEARSAL_S3_BUCKET" == "$S3_BUCKET" ]]; then
  echo "Rehearsal object storage must not use the production bucket." >&2
  exit 1
fi
if [[ ! "$LECTURESIFT_REHEARSAL_S3_BUCKET" =~ ^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$ ]]; then
  echo "The rehearsal bucket name is not a valid isolated S3 bucket name." >&2
  exit 1
fi
if [[ "$LECTURESIFT_REHEARSAL_S3_ACCESS_KEY_ID" == "$S3_ACCESS_KEY_ID" ||
      "$LECTURESIFT_REHEARSAL_S3_SECRET_ACCESS_KEY" == "$S3_SECRET_ACCESS_KEY" ]]; then
  echo "Rehearsal object storage must use a distinct least-privilege token." >&2
  exit 1
fi

# A rehearsal is intentionally run before VPS cutover. Refuse to start it if
# the normal VPS consumers are running, even though the rehearsal also gets a
# dedicated Redis instance below. This makes an accidental queue overlap fail
# closed during future Compose changes.
for container in lecturesift-api-1 lecturesift-worker-1; do
  if docker container inspect "$container" >/dev/null 2>&1 &&
     [[ "$(docker inspect -f '{{.State.Running}}' "$container")" == "true" ]]; then
    echo "Stop the normal VPS API and worker before starting a rehearsal." >&2
    exit 1
  fi
done

api_database_url="${DATABASE_URL%/*}/$rehearsal_db"
worker_database_url="${LECTURESIFT_WORKER_DATABASE_URL%/*}/$rehearsal_db"
if [[ "$api_database_url" == "$worker_database_url" ]]; then
  echo "Rehearsal API and worker database roles must remain distinct." >&2
  exit 1
fi
CELERY_BROKER_URL="redis://$rehearsal_redis_container:6379/0"
REDIS_URL="$CELERY_BROKER_URL"
S3_BUCKET="$LECTURESIFT_REHEARSAL_S3_BUCKET"
S3_ACCESS_KEY_ID="$LECTURESIFT_REHEARSAL_S3_ACCESS_KEY_ID"
S3_SECRET_ACCESS_KEY="$LECTURESIFT_REHEARSAL_S3_SECRET_ACCESS_KEY"
export CELERY_BROKER_URL REDIS_URL S3_BUCKET
export S3_ACCESS_KEY_ID S3_SECRET_ACCESS_KEY
export LECTURESIFT_REHEARSAL=1 LECTURESIFT_MAINTENANCE_MODE=off

cd "$ROOT_DIR"
rehearsal_compose_file="$(mktemp -- /run/lecturesift-rehearsal-compose-XXXXXXXX.yaml)"
chmod 0600 "$rehearsal_compose_file"
trap 'rm -f -- "$rehearsal_compose_file"' EXIT
cat >"$rehearsal_compose_file" <<EOF
services:
  api:
    volumes:
      - type: volume
        source: rehearsal_api_work
        target: /var/lib/lecturesift
  worker:
    volumes:
      - type: volume
        source: rehearsal_worker_work
        target: /var/lib/lecturesift
volumes:
  rehearsal_api_work:
    external: true
    name: $rehearsal_api_work_volume
  rehearsal_worker_work:
    external: true
    name: $rehearsal_worker_work_volume
EOF
compose=(docker compose --project-directory "$ROOT_DIR" \
  --file "$ROOT_DIR/compose.yaml" --file "$rehearsal_compose_file")

# A rehearsal must never inherit production api_work/worker_work. These
# dedicated, disposable volumes start empty and are removed by the enclosing
# restore orchestrator on every normal success/error/signal path.
for container in lecturesift-api-rehearsal lecturesift-worker-rehearsal \
  "$rehearsal_redis_container" "$rehearsal_proxy_container"; do
  if docker container inspect "$container" >/dev/null 2>&1; then
    docker rm -f "$container" >/dev/null
  fi
done
for volume in "$rehearsal_api_work_volume" "$rehearsal_worker_work_volume"; do
  if docker volume inspect "$volume" >/dev/null 2>&1; then
    docker volume rm "$volume" >/dev/null || {
      echo "A stale rehearsal work volume is still in use: $volume" >&2
      exit 1
    }
  fi
  docker volume create --label lecturesift.rehearsal=true "$volume" >/dev/null
done

"${compose[@]}" config --quiet

docker network inspect "$rehearsal_network" >/dev/null 2>&1 || {
  echo "Missing private rehearsal network: $rehearsal_network" >&2
  exit 1
}
docker run -d --pull=never --name "$rehearsal_redis_container" \
  --network "$rehearsal_network" --read-only \
  --tmpfs /data:rw,noexec,nosuid,size=128m \
  --security-opt no-new-privileges --pids-limit 128 \
  redis:7.4-alpine redis-server --save '' --appendonly no >/dev/null
for _ in $(seq 1 30); do
  if docker exec "$rehearsal_redis_container" redis-cli --raw ping \
    | grep -q '^PONG$'; then
    break
  fi
  sleep 1
done
if ! docker exec "$rehearsal_redis_container" redis-cli --raw ping \
  | grep -q '^PONG$'; then
  echo "The isolated rehearsal Redis instance did not become ready." >&2
  exit 1
fi

# The production worker has no direct Internet route. Rehearsal uses the same
# proxy boundary so R2/OpenAI/remote-source tests cannot accidentally certify a
# path that production forbids. --use-aliases publishes the service name
# `egress-proxy` expected by HTTP_PROXY/HTTPS_PROXY in compose.yaml.
"${compose[@]}" run -d --name "$rehearsal_proxy_container" --no-deps \
  --use-aliases egress-proxy >/dev/null
proxy_ready="false"
for _ in $(seq 1 30); do
  if [[ "$(docker inspect -f '{{.State.Health.Status}}' \
      "$rehearsal_proxy_container" 2>/dev/null || true)" == "healthy" ]]; then
    proxy_ready="true"
    break
  fi
  sleep 1
done
if [[ "$proxy_ready" != "true" ]]; then
  echo "The isolated rehearsal egress proxy did not become healthy." >&2
  docker logs --tail 100 "$rehearsal_proxy_container" >&2 || true
  exit 1
fi

DATABASE_URL="$worker_database_url" \
  "${compose[@]}" run -d --name lecturesift-worker-rehearsal --no-deps \
  -e DATABASE_URL -e CELERY_BROKER_URL -e REDIS_URL -e S3_BUCKET \
  -e S3_ACCESS_KEY_ID -e S3_SECRET_ACCESS_KEY \
  -e LECTURESIFT_REHEARSAL -e LECTURESIFT_MAINTENANCE_MODE worker >/dev/null
DATABASE_URL="$api_database_url" \
  "${compose[@]}" run -d --name lecturesift-api-rehearsal --no-deps \
  --publish 127.0.0.1:18000:8000 \
  -e DATABASE_URL -e CELERY_BROKER_URL -e REDIS_URL -e S3_BUCKET \
  -e S3_ACCESS_KEY_ID -e S3_SECRET_ACCESS_KEY \
  -e LECTURESIFT_REHEARSAL -e LECTURESIFT_MAINTENANCE_MODE api >/dev/null

ready="false"
for _ in $(seq 1 90); do
  if curl --fail --silent --show-error --max-time 5 \
    http://127.0.0.1:18000/health >/dev/null; then
    ready="true"
    break
  fi
  sleep 2
done
if [[ "$ready" != "true" ]]; then
  echo "Rehearsal API did not become healthy." >&2
  docker logs --tail 100 lecturesift-api-rehearsal >&2 || true
  exit 1
fi

worker_ready="false"
for _ in $(seq 1 60); do
  if curl --fail --silent --show-error --max-time 8 \
    http://127.0.0.1:18000/rollout/health \
    | jq --exit-status \
      '.durable_processing_ready == true and .queue.connected == true and
       .storage.connected == true and .worker.reachable == true and
       .worker.workers >= 1' >/dev/null; then
    worker_ready="true"
    break
  fi
  sleep 2
done
if [[ "$worker_ready" != "true" ]]; then
  echo "Rehearsal worker did not become healthy." >&2
  docker logs --tail 100 lecturesift-worker-rehearsal >&2 || true
  exit 1
fi

echo "HEALTH"
curl --fail --silent --show-error http://127.0.0.1:18000/health
echo
echo "BILLING"
curl --fail --silent --show-error http://127.0.0.1:18000/billing/health
echo
echo "ROLLOUT"
curl --fail --silent --show-error http://127.0.0.1:18000/rollout/health
echo
echo "INSTAGRAM"
curl --fail --silent --show-error http://127.0.0.1:18000/instagram/health
echo
echo "Rehearsal API and worker are bound to localhost only."
