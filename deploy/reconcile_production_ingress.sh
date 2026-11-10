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
# A single failed attempt is usually a transient timeout that the next attempt,
# 30 s later, repairs; emailing each one only teaches the reader to ignore the
# alert. Email once the failure has persisted for this many attempts in a row
# (about 1.5 minutes). Durable state stays fail-closed from the first failure.
ALERT_AFTER_ATTEMPTS=3
failed_attempts=0

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

# One reconciliation attempt. Failures are counted while they persist, the
# backup window included, so a failure that outlives the window is emailed on
# the first attempt after it; a success resets the count.
reconcile_once() {
  if ! bash "$HANDOFF" reconcile; then
    failed_attempts=$(( failed_attempts + 1 ))
    echo "Ingress reconciliation attempt failed (${failed_attempts} in a row); durable state remains fail-closed." >&2
    if in_backup_window; then
      echo "The nightly backup holds the API in drain; not emailing for this attempt." >&2
    elif (( failed_attempts < ALERT_AFTER_ATTEMPTS )); then
      echo "Not emailing yet: the next attempt runs in 30 s and usually repairs a transient failure." >&2
    else
      # Only queue the email; reconciliation never waits for or depends on it.
      systemctl start --no-block "$FAILURE_ALERT_UNIT" >/dev/null 2>&1 || true
    fi
    return 0
  fi
  failed_attempts=0
}

stopping=false
stop_loop() { stopping=true; }
trap stop_loop HUP INT TERM

while [[ "$stopping" == "false" ]]; do
  reconcile_once
  sleep 30 &
  wait "$!" || true
done
