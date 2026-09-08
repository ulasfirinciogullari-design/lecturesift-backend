#!/usr/bin/env bash
set -Eeuo pipefail
set +x
umask 077

# Promote only one exact rehearsal-admitted Git revision into /opt. The
# previous tree is moved to a dated, root-owned rollback path and is not
# deleted. No service is enabled or started by this operation.

[[ "$(id -u)" == "0" ]] || {
  echo "Release promotion must run as root." >&2
  exit 1
}
[[ "${LECTURESIFT_PROMOTE_RELEASE_CONFIRM:-}" == \
   "PROMOTE-EXACT-REHEARSED-RELEASE" ]] || {
  echo "Release promotion requires the exact confirmation value." >&2
  exit 1
}
revision="${1:-}"
[[ "$revision" =~ ^[0-9a-f]{40}$ ]] || {
  echo "Release promotion requires one full lowercase commit." >&2
  exit 1
}

SOURCE_ROOT="/srv/lecturesift/worktrees/$revision"
TARGET_ROOT=/opt/lecturesift
INCOMING_ROOT="/opt/.lecturesift-incoming-$revision"
# Keep incoming, live and rollback trees on /opt so each exchange is a same-
# filesystem atomic rename even if /srv is mounted separately in the future.
PREVIOUS_ROOT=/opt/.lecturesift-previous
RUNTIME_ROOT=/run/lecturesift
STATE_ROOT=/var/lib/lecturesift/release-promotion
LOCK_FILE=$RUNTIME_ROOT/release-promotion.lock
TRANSACTION_MARKER=$STATE_ROOT/release-promotion.in-progress
release_env=/run/lecturesift/release.env
managed_units=(
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

fail() {
  echo "Release promotion failed: $*" >&2
  exit 1
}

# Bare systemd templates are fragment names, not valid systemctl invocations.
# Inspect the fixed instance used by the tracked OnFailure contract so an
# operational instance-specific drop-in cannot evade the exactness check.
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
          *) fail "$instance must be inactive during promotion" ;;
        esac
      done <<<"$instances"
      ;;
    *@.*)
      fail "unsupported unit template: $unit"
      ;;
    *)
      if systemctl is-active --quiet "$unit"; then
        fail "$unit must be inactive during promotion"
      fi
      ;;
  esac
}

for command_name in git python3 docker systemctl install stat realpath flock \
  find findmnt mv chmod chown date sync rm bash cmp; do
  command -v "$command_name" >/dev/null 2>&1 || fail "$command_name is unavailable"
done

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

secure_root_directory "$RUNTIME_ROOT" 0700
secure_root_directory "$STATE_ROOT" 0700
exec 9<>"$LOCK_FILE"
chmod 0600 -- "$LOCK_FILE"
[[ -f /proc/self/fd/9 && ! -L "$LOCK_FILE" && \
   "$(stat -Lc '%u:%g:%a:%h:%d:%i' -- /proc/self/fd/9)" == \
   "0:0:600:1:$(stat -c '%d:%i' -- "$LOCK_FILE")" ]] || \
  fail "the release-promotion lock is unsafe"
flock -n 9 || fail "another release promotion is active"
[[ ! -e "$TRANSACTION_MARKER" && ! -L "$TRANSACTION_MARKER" ]] || \
  fail "an interrupted release promotion requires operator recovery"

for unit in "${managed_units[@]}"; do
  assert_unit_inactive "$unit"
done
for container in lecturesift-api-1 lecturesift-worker-1 lecturesift-caddy-1 \
  lecturesift-caddy-staging lecturesift-egress-proxy-1; do
  if docker container inspect "$container" >/dev/null 2>&1; then
    [[ "$(docker container inspect --format '{{.State.Running}}' "$container")" == "false" ]] || \
      fail "application container is active: $container"
  fi
done

[[ -d "$SOURCE_ROOT" && ! -L "$SOURCE_ROOT" && \
   "$(realpath -e -- "$SOURCE_ROOT")" == "$SOURCE_ROOT" && \
   "$(stat -c '%u' -- "$SOURCE_ROOT")" == "0" ]] || \
  fail "the staged source root is unsafe"
(( (8#$(stat -c '%a' -- "$SOURCE_ROOT") & 8#022) == 0 )) || \
  fail "the staged source root is group/other writable"
[[ "$(git -C "$SOURCE_ROOT" rev-parse --verify 'HEAD^{commit}')" == "$revision" && \
   -z "$(git -C "$SOURCE_ROOT" status --porcelain=v1 --untracked-files=all)" ]] || \
  fail "the staged source is not the exact clean revision"
python3 "$SOURCE_ROOT/deploy/validate_rehearsal_admission.py" \
  --root "$SOURCE_ROOT" --expected-revision "$revision" >/dev/null || \
  fail "the exact rehearsal admission is missing or invalid"

target_revision=""
legacy_tree_identity=""
previous_root_preexisting=false
[[ ! -e "$PREVIOUS_ROOT" && ! -L "$PREVIOUS_ROOT" ]] || previous_root_preexisting=true
if [[ -e "$TARGET_ROOT" || -L "$TARGET_ROOT" ]]; then
  [[ -d "$TARGET_ROOT" && ! -L "$TARGET_ROOT" && \
     "$(realpath -e -- "$TARGET_ROOT")" == "$TARGET_ROOT" ]] || \
    fail "the existing production root is unsafe"
  # Do not ask Git to parse an unverified legacy tree. Only a root-owned,
  # non-writable real .git directory admits the normal Git validation path.
  if [[ -d "$TARGET_ROOT/.git" && ! -L "$TARGET_ROOT/.git" && \
        "$(realpath -e -- "$TARGET_ROOT/.git")" == "$TARGET_ROOT/.git" && \
        "$(stat -c '%u:%g' -- "$TARGET_ROOT/.git")" == "0:0" ]] && \
     (( (8#$(stat -c '%a' -- "$TARGET_ROOT/.git") & 8#022) == 0 )); then
    target_revision="$(git -C "$TARGET_ROOT" rev-parse --verify 'HEAD^{commit}')" || \
      fail "the existing Git production root revision is invalid"
    [[ "$(stat -c '%u:%g' -- "$TARGET_ROOT")" == "0:0" ]] || \
      fail "the existing Git production root is not root owned"
    (( (8#$(stat -c '%a' -- "$TARGET_ROOT") & 8#022) == 0 )) || \
      fail "the existing Git production root is group/other writable"
    [[ "$target_revision" =~ ^[0-9a-f]{40}$ && \
       -z "$(git -C "$TARGET_ROOT" status --porcelain=v1 --untracked-files=all)" ]] || \
      fail "the existing production checkout is not clean"
  else
    [[ "$previous_root_preexisting" == "false" && \
       "${LECTURESIFT_PROMOTE_LEGACY_ROOT_CONFIRM:-}" == \
       "PRESERVE-UNVERIFIED-LEGACY-ROOT" ]] || \
      fail "a non-Git production root is allowed only for the explicit first promotion"
    [[ "$(stat -c '%U:%G:%a' -- "$TARGET_ROOT")" == "ubuntu:ubuntu:775" ]] || \
      fail "the one-time legacy root does not match the reviewed ownership/mode"
    while IFS= read -r mount_target; do
      case "$mount_target" in
        "$TARGET_ROOT"|"$TARGET_ROOT"/*)
          fail "the one-time legacy root contains a nested mount"
          ;;
      esac
    done < <(findmnt -rn -o TARGET)
    target_revision="legacy-unverified"
    legacy_tree_identity="$(stat -c '%d:%i:%u:%g:%a' -- "$TARGET_ROOT")"
    [[ "$legacy_tree_identity" =~ ^[0-9]+:[0-9]+:[0-9]+:[0-9]+:775$ ]] || \
      fail "the one-time legacy root identity is invalid"
  fi
fi

# A successful retry is a verification-only no-op.  It does not create another
# rollback tree or rewrite an already exact release.
if [[ "$target_revision" == "$revision" ]]; then
  python3 "$TARGET_ROOT/deploy/validate_rehearsal_admission.py" \
    --root "$TARGET_ROOT" --expected-revision "$revision" >/dev/null || \
    fail "the current production tree no longer matches rehearsal admission"
  bash "$TARGET_ROOT/deploy/release.sh" prepare >/dev/null || \
    fail "the current production release marker could not be prepared"
  bash "$TARGET_ROOT/deploy/release.sh" verify >/dev/null || \
    fail "the current production images no longer match the admitted source"
  LECTURESIFT_INSTALL_SYSTEMD_CONFIRM=INSTALL-EXACT-ADMITTED-SYSTEMD-UNITS \
    bash "$TARGET_ROOT/deploy/install_systemd_units.sh" >/dev/null || \
    fail "the current systemd unit set could not be verified"
  echo "REHEARSED_RELEASE_ALREADY_CURRENT|revision=$revision|services_started=false"
  exit 0
fi

[[ ! -e "$INCOMING_ROOT" && ! -L "$INCOMING_ROOT" ]] || \
  fail "an earlier incoming release requires operator review"
git -c core.hooksPath=/dev/null clone --quiet --no-local --no-hardlinks \
  --no-checkout "$SOURCE_ROOT" "$INCOMING_ROOT" || fail "the admitted source could not be copied"
git -c core.hooksPath=/dev/null -C "$INCOMING_ROOT" checkout --quiet --detach "$revision" || \
  fail "the copied release could not be checked out"
chown -R root:root -- "$INCOMING_ROOT"
find "$INCOMING_ROOT" -xdev -type d -exec chmod 0755 -- {} +
find "$INCOMING_ROOT" -xdev -type f -perm /111 -exec chmod 0755 -- {} +
find "$INCOMING_ROOT" -xdev -type f ! -perm /111 -exec chmod 0644 -- {} +
[[ -z "$(find "$INCOMING_ROOT" -xdev \( ! -user root -o -perm /022 \) -print -quit)" ]] || \
  fail "the copied release metadata is unsafe"
[[ "$(git -C "$INCOMING_ROOT" rev-parse --verify 'HEAD^{commit}')" == "$revision" && \
   -z "$(git -C "$INCOMING_ROOT" status --porcelain=v1 --untracked-files=all)" ]] || \
  fail "the copied release is not exact and clean"
python3 "$INCOMING_ROOT/deploy/validate_rehearsal_admission.py" \
  --root "$INCOMING_ROOT" --expected-revision "$revision" >/dev/null || \
  fail "the copied release no longer matches rehearsal admission"

sync -f -- "$INCOMING_ROOT"
secure_root_directory "$PREVIOUS_ROOT" 0700
[[ "$(stat -c '%d' -- /opt)" == "$(stat -c '%d' -- "$INCOMING_ROOT")" && \
   "$(stat -c '%d' -- /opt)" == "$(stat -c '%d' -- "$PREVIOUS_ROOT")" ]] || \
  fail "incoming, live and previous release roots are not on one filesystem"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
old_revision="${target_revision:-none}"
previous="$PREVIOUS_ROOT/lecturesift-$stamp-$old_revision"
[[ ! -e "$previous" && ! -L "$previous" ]] || fail "the previous-release path exists"

target_moved=false
incoming_moved=false
release_env_existed=false
release_env_backup="$STATE_ROOT/release.env.before-$stamp-$revision-$$"
if [[ -e "$release_env" || -L "$release_env" ]]; then
  [[ -f "$release_env" && ! -L "$release_env" && \
     "$(realpath -e -- "$release_env")" == "$release_env" && \
     "$(stat -c '%u:%g:%h' -- "$release_env")" == "0:0:1" && \
     $((8#$(stat -c '%a' -- "$release_env") & 8#077)) == 0 ]] || \
    fail "the release marker is unsafe"
  [[ ! -e "$release_env_backup" && ! -L "$release_env_backup" ]] || \
    fail "the release-marker backup path exists"
  install -o root -g root -m 0600 -- "$release_env" "$release_env_backup"
  release_env_existed=true
fi

marker_temporary="$STATE_ROOT/.release-promotion.in-progress-$stamp-$$"
printf '%s\n' \
  "schema=lecturesift-release-promotion-v1" \
  "new_revision=$revision" \
  "old_revision=$old_revision" \
  "incoming=$INCOMING_ROOT" \
  "previous=$previous" >"$marker_temporary"
chmod 0600 -- "$marker_temporary"
sync -f -- "$marker_temporary"
mv -T -- "$marker_temporary" "$TRANSACTION_MARKER"
sync -f -- "$STATE_ROOT"

rollback_promotion() {
  local failed_tree="$PREVIOUS_ROOT/failed-$stamp-$revision"
  local rollback_proven=true restored_revision
  if [[ "$incoming_moved" == "true" && -d "$TARGET_ROOT" && ! -L "$TARGET_ROOT" ]]; then
    if [[ ! -e "$failed_tree" && ! -L "$failed_tree" ]]; then
      mv -T -- "$TARGET_ROOT" "$failed_tree" || rollback_proven=false
    else
      rollback_proven=false
    fi
  fi
  if [[ "$target_moved" == "true" && -d "$previous" && ! -L "$previous" ]]; then
    mv -T -- "$previous" "$TARGET_ROOT" || rollback_proven=false
  fi
  if [[ "$release_env_existed" == "true" && -f "$release_env_backup" ]]; then
    install -o root -g root -m 0600 -- "$release_env_backup" "$release_env" || \
      rollback_proven=false
  elif [[ "$release_env_existed" == "false" ]]; then
    rm -f -- "$release_env" || rollback_proven=false
  fi
  sync -f -- /opt || rollback_proven=false
  sync -f -- "$RUNTIME_ROOT" || rollback_proven=false
  if [[ "$old_revision" == "none" ]]; then
    [[ ! -e "$TARGET_ROOT" && ! -L "$TARGET_ROOT" ]] || rollback_proven=false
  else
    if [[ -d "$TARGET_ROOT" && ! -L "$TARGET_ROOT" ]]; then
      if [[ "$old_revision" == "legacy-unverified" ]]; then
        [[ "$(stat -c '%d:%i:%u:%g:%a' -- "$TARGET_ROOT" 2>/dev/null)" == \
           "$legacy_tree_identity" ]] || rollback_proven=false
      else
        restored_revision="$(git -C "$TARGET_ROOT" rev-parse --verify 'HEAD^{commit}' 2>/dev/null)" || \
          rollback_proven=false
        [[ "$restored_revision" == "$old_revision" ]] || rollback_proven=false
      fi
    else
      rollback_proven=false
    fi
  fi
  if [[ "$rollback_proven" == "true" ]]; then
    rm -f -- "$release_env_backup" "$TRANSACTION_MARKER" || rollback_proven=false
    sync -f -- "$STATE_ROOT" || rollback_proven=false
  fi
  if [[ "$rollback_proven" != "true" ]]; then
    echo "Release rollback is unproven; retain $TRANSACTION_MARKER and stop for operator recovery." >&2
  fi
}

promoted_code_and_units_are_exact() {
  local unit query_unit destination
  [[ -d "$TARGET_ROOT" && ! -L "$TARGET_ROOT" && \
     "$(git -C "$TARGET_ROOT" rev-parse --verify 'HEAD^{commit}' 2>/dev/null)" == \
     "$revision" && \
     -z "$(git -C "$TARGET_ROOT" status --porcelain=v1 --untracked-files=all 2>/dev/null)" ]] || \
    return 1
  python3 "$TARGET_ROOT/deploy/validate_rehearsal_admission.py" \
    --root "$TARGET_ROOT" --expected-revision "$revision" >/dev/null 2>&1 || return 1
  bash "$TARGET_ROOT/deploy/release.sh" verify >/dev/null 2>&1 || return 1
  for unit in "${managed_units[@]}"; do
    query_unit="$(systemd_query_unit "$unit")" || return 1
    destination="/etc/systemd/system/$unit"
    [[ -f "$destination" && ! -L "$destination" && \
       "$(stat -c '%u:%g:%a:%h' -- "$destination" 2>/dev/null)" == \
       "0:0:644:1" ]] || return 1
    cmp --silent "$TARGET_ROOT/deploy/$unit" "$destination" || return 1
    [[ "$(systemctl show --property=FragmentPath --value "$query_unit" 2>/dev/null)" == \
       "$destination" ]] || return 1
    [[ -z "$(systemctl show --property=DropInPaths --value "$query_unit" 2>/dev/null)" ]] || \
      return 1
    [[ "$(systemctl show --property=NeedDaemonReload --value "$query_unit" 2>/dev/null)" == \
       "no" ]] || return 1
  done
}

cleanup() {
  local status="$?"
  trap - EXIT
  if [[ "$status" != "0" ]]; then
    if promoted_code_and_units_are_exact; then
      echo "Exact promoted code and unit set reached the commit point; retaining it with the transaction marker for operator verification." >&2
    else
      rollback_promotion
    fi
  fi
  exit "$status"
}
trap cleanup EXIT

if [[ -e "$TARGET_ROOT" || -L "$TARGET_ROOT" ]]; then
  mv -T -- "$TARGET_ROOT" "$previous"
  target_moved=true
fi
mv -T -- "$INCOMING_ROOT" "$TARGET_ROOT"
incoming_moved=true
sync -f /opt
sync -f "$PREVIOUS_ROOT"

python3 "$TARGET_ROOT/deploy/validate_rehearsal_admission.py" \
  --root "$TARGET_ROOT" --expected-revision "$revision" >/dev/null || \
  fail "the promoted production tree does not match rehearsal admission"
bash "$TARGET_ROOT/deploy/release.sh" prepare >/dev/null || \
  fail "the exact production release marker could not be prepared"
bash "$TARGET_ROOT/deploy/release.sh" verify >/dev/null || \
  fail "the rehearsed production images no longer match the promoted source"
LECTURESIFT_INSTALL_SYSTEMD_CONFIRM=INSTALL-EXACT-ADMITTED-SYSTEMD-UNITS \
  bash "$TARGET_ROOT/deploy/install_systemd_units.sh" || \
  fail "the exact systemd unit set could not be installed"

rm -f -- "$release_env_backup" "$TRANSACTION_MARKER"
sync -f -- "$STATE_ROOT"
trap - EXIT
echo "REHEARSED_RELEASE_PROMOTED|revision=$revision|previous=$previous|services_started=false"
