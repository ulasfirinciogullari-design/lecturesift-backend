#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="${LECTURESIFT_ROOT:-/opt/lecturesift}"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run backup runtime recovery as root." >&2
  exit 1
fi
[[ -d "$ROOT_DIR" && ! -L "$ROOT_DIR" && "$(realpath -e -- "$ROOT_DIR")" == "$ROOT_DIR" ]] || {
  echo "The LectureSift root is missing or unsafe." >&2
  exit 1
}
[[ -f "$ROOT_DIR/compose.yaml" && ! -L "$ROOT_DIR/compose.yaml" ]] || {
  echo "The production Compose file is missing or unsafe." >&2
  exit 1
}

cd "$ROOT_DIR"

# This helper is ExecStopPost for the backup unit as well as the normal EXIT
# cleanup path. It is intentionally idempotent: ensure both private consumers
# are healthy first, then remove only the exact API-volume runtime fence. If
# recovery cannot be proved, the short-lived same-boot fence remains fail-safe.
mapfile -t api_containers < <(docker compose ps -a -q api)
mapfile -t worker_containers < <(docker compose ps -a -q worker)
[[ "${#api_containers[@]}" -eq 1 && -n "${api_containers[0]}" && \
   "${#worker_containers[@]}" -eq 1 && -n "${worker_containers[0]}" ]] || {
  echo "The existing API/worker containers could not be identified uniquely." >&2
  exit 1
}
api_container="${api_containers[0]}"
worker_container="${worker_containers[0]}"

# Start the already-created worker rather than `compose up`: a failed runtime
# edit cannot make cleanup consume newly generated/unvalidated environment
# files. API is never intentionally stopped by backup and must still exist.
docker start "$worker_container" >/dev/null
runtime_ready="false"
for _ in $(seq 1 180); do
  api_running="$(docker inspect -f '{{.State.Running}}' "$api_container")"
  worker_running="$(docker inspect -f '{{.State.Running}}' "$worker_container")"
  api_health="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}missing{{end}}' "$api_container")"
  worker_health="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}missing{{end}}' "$worker_container")"
  if [[ "$api_running" == "true" && "$worker_running" == "true" && \
        "$api_health" == "healthy" && "$worker_health" == "healthy" ]]; then
    runtime_ready="true"
    break
  fi
  sleep 1
done
[[ "$runtime_ready" == "true" ]] || {
  echo "The existing API/worker runtime did not become healthy; the drain fence remains active." >&2
  exit 1
}
docker compose exec -T api python - <<'PY'
import os
from pathlib import Path

work_dir = Path(os.environ.get("LECTURESIFT_WORK_DIR", "/var/lib/lecturesift")).resolve()
marker = (work_dir / ".runtime-maintenance.json").resolve(strict=False)
if marker.parent != work_dir or marker.name != ".runtime-maintenance.json":
    raise SystemExit("Unsafe runtime maintenance marker path")
marker.unlink(missing_ok=True)
PY

docker compose exec -T api python - <<'PY'
import json
import urllib.request

with urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=5) as response:
    payload = json.load(response)
if payload.get("maintenance_mode") != "off":
    raise SystemExit("The API runtime maintenance fence did not clear")
PY

echo "Backup runtime recovery verified."
