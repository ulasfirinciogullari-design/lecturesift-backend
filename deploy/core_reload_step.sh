#!/usr/bin/env bash
set -uo pipefail
set +x

# Run one ExecReload= step of lecturesift.service and keep its exit status.
# A failed reload leaves the unit active with its old configuration, so its
# OnFailure= never fires; queue the rate-limited operator email here instead,
# without waiting for it. On a reload timeout systemd kills only this control
# process; the parent-death signal kills the step with it, as before.

FAILURE_ALERT_UNIT=lecturesift-failure-alert@lecturesift.service.service

if (( $# == 0 )); then
  echo "Usage: core_reload_step.sh <command> [argument...]" >&2
  exit 2
fi

status=0
if command -v setpriv >/dev/null 2>&1; then
  setpriv --pdeathsig KILL -- "$@" || status=$?
else
  "$@" || status=$?
fi
if (( status != 0 )); then
  echo "Reload step failed with status $status; queueing the operator alert." >&2
  systemctl start --no-block "$FAILURE_ALERT_UNIT" >/dev/null 2>&1 || true
fi
exit "$status"
