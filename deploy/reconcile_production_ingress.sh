#!/usr/bin/env bash
set -Eeuo pipefail
set +x
umask 077

# Keep runtime ownership converged after boot as well as after a Docker/core
# restart. The transactional worker has its own fixed flock, so an operator
# handoff and this loop can never mutate ownership concurrently.

[[ "$(id -u)" == "0" ]] || exit 1
ROOT_DIR="${LECTURESIFT_ROOT:-/opt/lecturesift}"
HANDOFF="$ROOT_DIR/deploy/handoff_production_ingress.sh"
[[ -f "$HANDOFF" && ! -L "$HANDOFF" ]] || exit 1
# This loop retries instead of exiting, so OnFailure= alone would never see a
# failed reconciliation. The alert unit sends at most one email per hour.
FAILURE_ALERT_UNIT=lecturesift-failure-alert@lecturesift-ingress-selector.service.service

stopping=false
stop_loop() { stopping=true; }
trap stop_loop HUP INT TERM

while [[ "$stopping" == "false" ]]; do
  if ! bash "$HANDOFF" reconcile; then
    echo "Ingress reconciliation attempt failed; durable state remains fail-closed." >&2
    # Only queue the email; reconciliation never waits for or depends on it.
    systemctl start --no-block "$FAILURE_ALERT_UNIT" >/dev/null 2>&1 || true
  fi
  sleep 30 &
  wait "$!" || true
done
