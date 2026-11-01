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
BACKUP_UNIT=lecturesift-backup.service

# The nightly backup drains the API and stops the worker, so the activated
# gate fails by design while it runs and until the worker is healthy again.
# Hold the email for that window only; a failure that outlives it is emailed.
in_backup_window() {
  local state finished_at now
  state="$(systemctl show --property=ActiveState --value "$BACKUP_UNIT" 2>/dev/null)" || return 1
  case "$state" in activating|active|deactivating|reloading) return 0 ;; esac
  finished_at="$(systemctl show --property=InactiveEnterTimestampMonotonic --value "$BACKUP_UNIT" 2>/dev/null)" || return 1
  [[ "$finished_at" =~ ^[0-9]+$ && "$finished_at" != "0" ]] || return 1
  now="$(awk '{ printf "%d", $1 * 1000000 }' /proc/uptime)" || return 1
  (( now - finished_at < 300000000 ))
}

stopping=false
stop_loop() { stopping=true; }
trap stop_loop HUP INT TERM

while [[ "$stopping" == "false" ]]; do
  if ! bash "$HANDOFF" reconcile; then
    echo "Ingress reconciliation attempt failed; durable state remains fail-closed." >&2
    if in_backup_window; then
      echo "The nightly backup holds the API in drain; not emailing for this attempt." >&2
    else
      # Only queue the email; reconciliation never waits for or depends on it.
      systemctl start --no-block "$FAILURE_ALERT_UNIT" >/dev/null 2>&1 || true
    fi
  fi
  sleep 30 &
  wait "$!" || true
done
