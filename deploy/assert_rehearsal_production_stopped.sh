#!/usr/bin/env bash
set -Eeuo pipefail
set +x

# Candidate containers use a dedicated internal backend, with only PostgreSQL
# temporarily attached. Keeping every production HTTP/runtime container and
# the production egress proxy stopped is still an independent defense against
# alias reuse, operator topology drift and accidental concurrent processing.

fail() {
  echo "REHEARSAL_PRODUCTION_STOP_GATE_FAILED|$*" >&2
  exit 1
}

command -v docker >/dev/null 2>&1 || fail docker-unavailable
for container in \
  lecturesift-api-1 \
  lecturesift-worker-1 \
  lecturesift-caddy-1 \
  lecturesift-egress-proxy-1; do
  if docker container inspect "$container" >/dev/null 2>&1; then
    running="$(docker container inspect --format '{{.State.Running}}' "$container")" || \
      fail "container-state-unreadable:$container"
    [[ "$running" == "false" ]] || fail "active-production-container:$container"
  fi
done

echo "REHEARSAL_PRODUCTION_STOP_GATE_OK"
