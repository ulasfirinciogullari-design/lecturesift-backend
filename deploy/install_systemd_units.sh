#!/usr/bin/env bash
set -Eeuo pipefail
set +x
umask 077

# Install only the systemd fragments from the exact admitted production tree.
# Existing fragments are retained in a root-private rollback directory and are
# restored automatically if any verification fails. Enablement is never
# changed here; ingress ownership is selected only by the cutover handoff.

[[ "$(id -u)" == "0" ]] || {
  echo "Systemd installation must run as root." >&2
  exit 1
}
[[ "${LECTURESIFT_INSTALL_SYSTEMD_CONFIRM:-}" == \
   "INSTALL-EXACT-ADMITTED-SYSTEMD-UNITS" ]] || {
  echo "Systemd installation requires the exact confirmation value." >&2
  exit 1
}

ROOT_DIR="${LECTURESIFT_ROOT:-/opt/lecturesift}"
UNIT_ROOT=/etc/systemd/system
BACKUP_ROOT=/var/lib/lecturesift/systemd-unit-backups
TRANSACTION_MARKER=$BACKUP_ROOT/systemd-install.in-progress
RUNTIME_ROOT=/run/lecturesift
LOCK_FILE=$RUNTIME_ROOT/systemd-install.lock
units=(
  lecturesift.service
  lecturesift-ingress.service
  lecturesift-ingress-selector.service
  lecturesift-caddy-staging.service
  lecturesift-backup.service
  lecturesift-backup.timer
  lecturesift-backup-alert@.service
  lecturesift-instagram.service
  lecturesift-instagram.timer
  lecturesift-r2-retention-probe.service
)
obsolete_units=(caddy.service lecturesift-api.service lecturesift-worker.service)

fail() {
  echo "Systemd installation failed: $*" >&2
  exit 1
}

# systemctl does not accept a bare template such as foo@.service as a unit
# invocation.  Query the fixed instance used by the tracked OnFailure contract;
# systemd still reports the template fragment path and any operational
# instance-specific drop-in without starting or enabling the instance.
systemd_query_unit() {
  local unit="$1"
  case "$unit" in
    lecturesift-backup-alert@.service)
      printf '%s\n' 'lecturesift-backup-alert@lecturesift-backup.service.service'
      ;;
    *@.*)
      return 1
      ;;
    *)
      printf '%s\n' "$unit"
      ;;
  esac
}

assert_unit_inactive() {
  local unit="$1" instances instance remainder state
  case "$unit" in
    lecturesift-backup-alert@.service)
      instances="$(systemctl list-units --all --type=service --no-legend --plain \
        'lecturesift-backup-alert@*.service')" || \
        fail "could not inspect loaded instances for $unit"
      while read -r instance remainder; do
        [[ -n "$instance" ]] || continue
        state="$(systemctl show --property=ActiveState --value "$instance")" || \
          fail "could not inspect $instance"
        case "$state" in
          inactive|failed) ;;
          *) fail "$instance must be inactive during installation" ;;
        esac
      done <<<"$instances"
      ;;
    *@.*)
      fail "unsupported unit template: $unit"
      ;;
    *)
      if systemctl is-active --quiet "$unit"; then
        fail "$unit must be inactive during installation"
      fi
      ;;
  esac
}

for command_name in git python3 cmp systemctl systemd-analyze install stat \
  realpath flock date sync awk chmod rm mv ln; do
  command -v "$command_name" >/dev/null 2>&1 || fail "$command_name is unavailable"
done
[[ "$ROOT_DIR" == "/opt/lecturesift" && -d "$ROOT_DIR" && ! -L "$ROOT_DIR" && \
   "$(realpath -e -- "$ROOT_DIR")" == "$ROOT_DIR" && \
   "$(stat -c '%u' -- "$ROOT_DIR")" == "0" ]] || \
  fail "the production root is not the fixed root-owned directory"
(( (8#$(stat -c '%a' -- "$ROOT_DIR") & 8#022) == 0 )) || \
  fail "the production root is group/other writable"

secure_root_directory() {
  local path="$1" mode="$2"
  if [[ -e "$path" || -L "$path" ]]; then
    [[ -d "$path" && ! -L "$path" && "$(realpath -e -- "$path")" == "$path" && \
       "$(stat -c '%u:%g:%a' -- "$path")" == "0:0:${mode#0}" ]] || \
      fail "unsafe privileged directory: $path"
    return
  fi
  install -d -o root -g root -m "$mode" -- "$path"
}

secure_root_directory "$UNIT_ROOT" 0755
secure_root_directory "$RUNTIME_ROOT" 0700
secure_root_directory "$BACKUP_ROOT" 0700

# The parent is root-only, so opening this fixed name cannot be redirected by
# an unprivileged symlink race.  Validate the opened inode as well as its path.
exec 9<>"$LOCK_FILE"
chmod 0600 -- "$LOCK_FILE"
[[ -f "/proc/self/fd/9" && ! -L "$LOCK_FILE" && \
   "$(stat -Lc '%u:%g:%a:%h:%d:%i' -- /proc/self/fd/9)" == \
   "0:0:600:1:$(stat -c '%d:%i' -- "$LOCK_FILE")" ]] || \
  fail "the systemd installation lock is unsafe"
flock -n 9 || fail "another systemd installation is active"
[[ ! -e "$TRANSACTION_MARKER" && ! -L "$TRANSACTION_MARKER" ]] || \
  fail "an interrupted systemd installation requires operator recovery"

revision="$(git -C "$ROOT_DIR" rev-parse --verify 'HEAD^{commit}')" || \
  fail "the production revision cannot be read"
[[ "$revision" =~ ^[0-9a-f]{40}$ ]] || fail "the production revision is invalid"
[[ -z "$(git -C "$ROOT_DIR" status --porcelain=v1 --untracked-files=all)" ]] || \
  fail "the production checkout is not clean"
python3 "$ROOT_DIR/deploy/validate_rehearsal_admission.py" \
  --root "$ROOT_DIR" --expected-revision "$revision" >/dev/null || \
  fail "the production checkout is not the exact admitted rehearsal revision"

for unit in "${units[@]}"; do
  assert_unit_inactive "$unit"
done
for unit in lecturesift.service lecturesift-ingress.service \
  lecturesift-caddy-staging.service lecturesift-backup.timer \
  lecturesift-instagram.timer; do
  systemctl is-enabled --quiet "$unit" && fail "$unit must be disabled before installation"
done
for unit in "${obsolete_units[@]}"; do
  systemctl is-active --quiet "$unit" && fail "obsolete $unit is active"
  systemctl is-enabled --quiet "$unit" && fail "obsolete $unit is enabled"
  [[ ! -e "$UNIT_ROOT/$unit" && ! -L "$UNIT_ROOT/$unit" ]] || \
    fail "obsolete local fragment $unit must be reviewed and removed separately"
done

source_paths=()
for unit in "${units[@]}"; do
  source_path="$ROOT_DIR/deploy/$unit"
  [[ -f "$source_path" && ! -L "$source_path" ]] || fail "missing unit source: $unit"
  [[ "$(git -C "$ROOT_DIR" ls-tree "$revision" -- "deploy/$unit" | awk '{print $1}')" == \
     "100644" ]] || fail "unexpected Git mode for $unit"
  git -C "$ROOT_DIR" cat-file blob "$revision:deploy/$unit" | \
    cmp --silent - "$source_path" || fail "working unit differs from its exact Git blob: $unit"
  source_paths+=("$source_path")
done
systemd-analyze verify "${source_paths[@]}" >/dev/null || \
  fail "the admitted unit set failed systemd verification"

installed_set_is_exact() {
  local unit query_unit destination loaded
  for unit in "${units[@]}"; do
    query_unit="$(systemd_query_unit "$unit")" || return 1
    destination="$UNIT_ROOT/$unit"
    [[ -f "$destination" && ! -L "$destination" && \
       "$(realpath -e -- "$destination")" == "$destination" && \
       "$(stat -c '%u:%g:%a:%h' -- "$destination")" == "0:0:644:1" ]] || return 1
    cmp --silent "$ROOT_DIR/deploy/$unit" "$destination" || return 1
    loaded="$(systemctl show --property=FragmentPath --value "$query_unit")" || return 1
    [[ "$loaded" == "$destination" ]] || return 1
    [[ -z "$(systemctl show --property=DropInPaths --value "$query_unit")" ]] || return 1
    [[ "$(systemctl show --property=NeedDaemonReload --value "$query_unit")" == "no" ]] || return 1
  done
  systemd-analyze verify "${source_paths[@]}" >/dev/null
}

if installed_set_is_exact; then
  echo "SYSTEMD_UNITS_ALREADY_CURRENT|revision=$revision|enablement=unchanged"
  exit 0
fi

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
[[ "$stamp" =~ ^[0-9]{8}T[0-9]{6}Z$ ]] || fail "the backup timestamp is invalid"
backup_dir="$BACKUP_ROOT/$revision-$stamp-$$"
[[ ! -e "$backup_dir" && ! -L "$backup_dir" ]] || fail "the unit backup already exists"
install -d -o root -g root -m 0700 "$backup_dir"
restored=false
transaction_armed=false
installed=()
previous=()
previous_absent=()
temporary_paths=()

atomic_install() {
  local source="$1" destination="$2" mode="$3"
  local temporary="$UNIT_ROOT/.lecturesift-install-${destination##*/}-$stamp-$$"
  [[ ! -e "$temporary" && ! -L "$temporary" ]] || return 1
  temporary_paths+=("$temporary")
  install -o root -g root -m "$mode" -- "$source" "$temporary" || return 1
  sync -f -- "$temporary" || return 1
  mv -fT -- "$temporary" "$destination" || return 1
}

restore_previous_units() {
  local unit query_unit rollback_failed=false marker_source
  [[ "$restored" == "false" ]] || return 0
  restored=true
  for unit in "${previous[@]}"; do
    atomic_install "$backup_dir/$unit" "$UNIT_ROOT/$unit" 0644 || rollback_failed=true
  done
  for unit in "${previous_absent[@]}"; do
    rm -f -- "$UNIT_ROOT/$unit" || rollback_failed=true
  done
  sync -f -- "$UNIT_ROOT" || rollback_failed=true
  systemctl daemon-reload >/dev/null 2>&1 || rollback_failed=true
  for unit in "${previous[@]}"; do
    [[ -f "$UNIT_ROOT/$unit" && ! -L "$UNIT_ROOT/$unit" && \
       "$(stat -c '%u:%g:%a:%h' -- "$UNIT_ROOT/$unit" 2>/dev/null)" == \
       "0:0:644:1" ]] && \
      cmp --silent "$backup_dir/$unit" "$UNIT_ROOT/$unit" || rollback_failed=true
  done
  for unit in "${previous_absent[@]}"; do
    [[ ! -e "$UNIT_ROOT/$unit" && ! -L "$UNIT_ROOT/$unit" ]] || rollback_failed=true
  done
  for unit in "${units[@]}"; do
    query_unit="$(systemd_query_unit "$unit")" || {
      rollback_failed=true
      continue
    }
    [[ "$(systemctl show --property=NeedDaemonReload --value "$query_unit" 2>/dev/null)" == \
       "no" ]] || rollback_failed=true
  done
  if [[ "$rollback_failed" == "false" && "$transaction_armed" == "true" ]]; then
    rm -f -- "$TRANSACTION_MARKER" || rollback_failed=true
    sync -f -- "$BACKUP_ROOT" || rollback_failed=true
  fi
  if [[ "$rollback_failed" == "true" ]]; then
    marker_source="$backup_dir/.rollback-unproven.$$"
    printf '%s\n' "SYSTEMD_UNIT_ROLLBACK_UNPROVEN|revision=$revision" >"$marker_source" || true
    chmod 0600 -- "$marker_source" >/dev/null 2>&1 || true
    mv -fT -- "$marker_source" "$backup_dir/ROLLBACK_UNPROVEN" >/dev/null 2>&1 || true
    sync -f -- "$backup_dir" >/dev/null 2>&1 || true
    echo "Systemd unit rollback is unproven; inspect $backup_dir before retrying." >&2
  fi
}

cleanup() {
  local status="$?"
  trap - EXIT
  if [[ "$status" != "0" ]]; then
    restore_previous_units
  fi
  for temporary in "${temporary_paths[@]}"; do
    [[ ! -e "$temporary" && ! -L "$temporary" ]] || rm -f -- "$temporary" || true
  done
  exit "$status"
}
trap cleanup EXIT

for unit in "${units[@]}"; do
  destination="$UNIT_ROOT/$unit"
  if [[ -e "$destination" || -L "$destination" ]]; then
    [[ -f "$destination" && ! -L "$destination" && \
       "$(realpath -e -- "$destination")" == "$destination" && \
       "$(stat -c '%u:%g:%a:%h' -- "$destination")" == "0:0:644:1" ]] || \
      fail "existing unit fragment is unsafe: $unit"
    install -o root -g root -m 0600 -- "$destination" "$backup_dir/$unit"
    sync -f -- "$backup_dir/$unit"
    previous+=("$unit")
  else
    previous_absent+=("$unit")
  fi
done
sync -f -- "$backup_dir"

# Publish a durable fail-stop fence only after the complete rollback set is on
# disk and before replacing the first fragment. Production preflight rejects
# this marker, so a power loss cannot boot from a mixed unit set.
marker_temporary="$BACKUP_ROOT/.systemd-install.in-progress-$stamp-$$"
temporary_paths+=("$marker_temporary")
printf '%s\n' \
  "schema=lecturesift-systemd-install-v1" \
  "revision=$revision" \
  "backup=$backup_dir" >"$marker_temporary"
chmod 0600 -- "$marker_temporary"
sync -f -- "$marker_temporary"
ln -- "$marker_temporary" "$TRANSACTION_MARKER" || \
  fail "the systemd installation transaction could not be armed"
rm -f -- "$marker_temporary"
transaction_armed=true
sync -f -- "$BACKUP_ROOT"

for unit in "${units[@]}"; do
  atomic_install "$ROOT_DIR/deploy/$unit" "$UNIT_ROOT/$unit" 0644 || \
    fail "atomic fragment installation failed: $unit"
  installed+=("$unit")
done
sync -f "$UNIT_ROOT"
systemctl daemon-reload

for unit in "${units[@]}"; do
  query_unit="$(systemd_query_unit "$unit")" || fail "unsupported unit template: $unit"
  cmp --silent "$ROOT_DIR/deploy/$unit" "$UNIT_ROOT/$unit" || \
    fail "installed fragment differs from the admitted source: $unit"
  [[ "$(systemctl show --property=FragmentPath --value "$query_unit")" == "$UNIT_ROOT/$unit" ]] || \
    fail "systemd did not load the exact local fragment: $unit"
  [[ -z "$(systemctl show --property=DropInPaths --value "$query_unit")" ]] || \
    fail "unexpected systemd drop-in for $unit"
  [[ "$(systemctl show --property=NeedDaemonReload --value "$query_unit")" == "no" ]] || \
    fail "systemd still requires a reload for $unit"
done
installed_paths=()
for unit in "${installed[@]}"; do
  installed_paths+=("$UNIT_ROOT/$unit")
done
systemd-analyze verify "${installed_paths[@]}" >/dev/null || \
  fail "the installed unit set failed final systemd verification"

rm -f -- "$TRANSACTION_MARKER"
sync -f -- "$BACKUP_ROOT"
transaction_armed=false
trap - EXIT
echo "SYSTEMD_UNITS_INSTALLED|revision=$revision|backup=$backup_dir|enablement=unchanged"
