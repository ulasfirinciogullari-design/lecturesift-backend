#!/usr/bin/env bash
set -euo pipefail
umask 077
set +x

# Run the Redis manifest reader with trusted host Python. It never imports or
# executes the candidate application image and receives no production env.

[[ "$(id -u)" == "0" ]] || {
  echo "Target Redis manifest must run as root." >&2
  exit 1
}

ROOT_DIR="${LECTURESIFT_ROOT:-/opt/lecturesift}"
TOOL="$ROOT_DIR/deploy/redis_logical_manifest.py"
SALT_FILE="/var/lib/lecturesift/provider-cutover/redis-manifest.salt"
EXPECTED_IMAGE="redis:7.4-alpine@sha256:ff02b58f971e7d7d156a1267e283fcbbeee91773b6aa36c49dac28ecfe28eadf"
MODE="${1:-}"
POLICY="${2:-}"
PROJECTION="${3:-full}"
LOCK_TOKEN_FILE="${4:-}"

[[ -f "$TOOL" && ! -L "$TOOL" ]] || {
  echo "The Redis manifest helper is missing or unsafe." >&2
  exit 1
}

case "$MODE" in
  init-salt)
    [[ $# -eq 1 ]] || {
      echo "Usage: $0 init-salt" >&2
      exit 1
    }
    exec python3 "$TOOL" init-salt
    ;;
  manifest)
    [[ $# -ge 2 && $# -le 4 ]] || {
      echo "Usage: $0 manifest steady|migration [full|non-job] [lock-token-file]" >&2
      exit 1
    }
    ;;
  *)
    echo "Usage: $0 init-salt | manifest steady|migration [full|non-job] [lock-token-file]" >&2
    exit 1
    ;;
esac

[[ "$POLICY" == "steady" || "$POLICY" == "migration" ]] || {
  echo "The Redis manifest policy is invalid." >&2
  exit 1
}
[[ "$PROJECTION" == "full" || "$PROJECTION" == "non-job" ]] || {
  echo "The Redis manifest projection is invalid." >&2
  exit 1
}
[[ -f "$SALT_FILE" && ! -L "$SALT_FILE" ]] || {
  echo "The Redis manifest salt is missing or unsafe." >&2
  exit 1
}
command -v docker >/dev/null 2>&1 || {
  echo "Docker is unavailable." >&2
  exit 1
}
command -v python3 >/dev/null 2>&1 || {
  echo "Host Python is unavailable." >&2
  exit 1
}

compose=(docker compose --project-directory "$ROOT_DIR" --file "$ROOT_DIR/compose.yaml")
container="$("${compose[@]}" ps -q redis)"
[[ "$container" =~ ^[0-9a-f]{64}$ ]] || {
  echo "The exact target Redis container could not be identified." >&2
  exit 1
}

redis_identity() {
  docker inspect --format \
    '{{.Id}}|{{.Config.Image}}|{{index .Config.Labels "com.docker.compose.project"}}|{{index .Config.Labels "com.docker.compose.service"}}|{{.State.Running}}|{{if .State.Health}}{{.State.Health.Status}}{{end}}|{{.RestartCount}}|{{.State.StartedAt}}|{{with index .NetworkSettings.Networks "lecturesift_backend"}}{{.IPAddress}}{{end}}' \
    "$container"
}

identity_before="$(redis_identity)" || {
  echo "The target Redis container identity could not be read." >&2
  exit 1
}
IFS='|' read -r redis_id redis_image redis_project redis_service redis_running \
  redis_health redis_restarts redis_started redis_ip <<<"$identity_before"
[[ "$redis_id" == "$container" && "$redis_image" == "$EXPECTED_IMAGE" &&
   "$redis_project" == "lecturesift" && "$redis_service" == "redis" &&
   "$redis_running" == "true" && "$redis_health" == "healthy" &&
   "$redis_restarts" =~ ^[0-9]+$ && -n "$redis_started" && -n "$redis_ip" ]] || {
  echo "The target Redis container identity, health, or private network is invalid." >&2
  exit 1
}

manifest_args=(manifest --host "$redis_ip" --policy "$POLICY" --projection "$PROJECTION")

if [[ "$POLICY" == "migration" ]]; then
  [[ -n "$LOCK_TOKEN_FILE" && -f "$LOCK_TOKEN_FILE" && ! -L "$LOCK_TOKEN_FILE" ]] || {
    echo "Migration-policy Redis manifest requires a private lock-token file." >&2
    exit 1
  }
  token_mode="$(stat -c '%a' -- "$LOCK_TOKEN_FILE")"
  [[ "$(stat -c '%u' -- "$LOCK_TOKEN_FILE")" == "0" &&
     ( "$token_mode" == "400" || "$token_mode" == "600" ) ]] || {
    echo "The Redis migration lock-token file is not root-private." >&2
    exit 1
  }
  manifest_args+=(--lock-token-file "$LOCK_TOKEN_FILE")
elif [[ -n "$LOCK_TOKEN_FILE" ]]; then
  echo "Steady-policy Redis manifest forbids a lock-token file." >&2
  exit 1
fi

output="$(python3 "$TOOL" "${manifest_args[@]}")" || exit 1
[[ "$output" =~ ^[0-9a-f]{64}$ ]] || {
  echo "The target Redis manifest digest is invalid." >&2
  exit 1
}
identity_after="$(redis_identity)" || {
  echo "The target Redis container identity could not be re-read." >&2
  exit 1
}
[[ "$identity_after" == "$identity_before" ]] || {
  echo "The target Redis container changed during manifest capture." >&2
  exit 1
}
printf '%s\n' "$output"
