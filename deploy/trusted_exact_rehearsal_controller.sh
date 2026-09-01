#!/bin/bash -p
set -Eeuo pipefail
set +x
umask 077
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
export GIT_ATTR_NOSYSTEM=1
IFS=$' \t\n'
unset CDPATH ENV BASH_ENV
unset -f id git python3 sha256sum realpath stat find flock awk install mktemp rm \
  mv chmod chown sync tr env bash df timeout 2>/dev/null || true
hash -r

# This controller is effective only after it has been reviewed and installed at
# the fixed root-owned path below. Running this repository copy is forbidden:
# the candidate worktree is the object being authorized, not the authority.

CONTROLLER_PATH=/usr/local/sbin/lecturesift-exact-rehearsal-controller
STAGE_CONTROLLER_PATH=/usr/local/sbin/lecturesift-release-stage-controller
ALLOWLIST_ROOT=/etc/lecturesift/exact-rehearsal-allowlist
WORKTREE_ROOT=/srv/lecturesift/worktrees
EVIDENCE_ROOT=/var/lib/lecturesift/release-candidates
ADMISSION_ROOT=/var/lib/lecturesift/rehearsal-admissions
STATE_ROOT=/run/lecturesift-trusted-rehearsal-controller
revision="${LECTURESIFT_EXPECTED_REHEARSAL_REVISION:-}"
requested_mode="${LECTURESIFT_TRUSTED_CONTROLLER_MODE:-rehearsal}"
rehearsal_admission="$ADMISSION_ROOT/$revision.ok"
child_started=false
MAX_REVIEW_TREE_BYTES=$((2 * 1024 * 1024 * 1024))
MIN_STATE_FREE_BYTES=$((8 * 1024 * 1024 * 1024))

fail() {
  echo "TRUSTED_EXACT_REHEARSAL_REFUSED|$*" >&2
  exit 1
}

[[ "$(id -u)" == "0" ]] || fail root-required
[[ "$#" == "0" ]] || fail arguments-forbidden
[[ "$revision" =~ ^[0-9a-f]{40}$ ]] || fail bad-revision
[[ "$requested_mode" == "rehearsal" || "$requested_mode" == "authorize-build" ]] || \
  fail bad-controller-mode
[[ -f "$CONTROLLER_PATH" && ! -L "$CONTROLLER_PATH" && \
   "$(realpath -e -- "$0")" == "$CONTROLLER_PATH" && \
   "$(stat -c '%u:%g' -- "$CONTROLLER_PATH")" == "0:0" ]] || \
  fail controller-not-fixed-root-file
controller_mode="$(stat -c '%a' -- "$CONTROLLER_PATH")"
(( (8#$controller_mode & 8#022) == 0 )) || fail controller-writable

[[ -d "$ALLOWLIST_ROOT" && ! -L "$ALLOWLIST_ROOT" && \
   "$(realpath -e -- "$ALLOWLIST_ROOT")" == "$ALLOWLIST_ROOT" && \
   "$(stat -c '%u:%g:%a' -- "$ALLOWLIST_ROOT")" == "0:0:700" ]] || \
  fail unsafe-allowlist-root
allowlist="$ALLOWLIST_ROOT/$revision.allow"
[[ -f "$allowlist" && ! -L "$allowlist" && \
   "$(realpath -e -- "$allowlist")" == "$allowlist" && \
   "$(stat -c '%u:%g:%a' -- "$allowlist")" == "0:0:600" ]] || \
  fail missing-private-review-allowlist

declare -A reviewed=()
while IFS='=' read -r key value || [[ -n "${key:-}${value:-}" ]]; do
  case "$key" in
    version|revision|source_tree_sha256|orchestrator_sha256|trusted_controller_sha256|trusted_stage_controller_sha256|reviewed_docker_root_equivalent) ;;
    *) fail unknown-allowlist-field ;;
  esac
  [[ -n "$value" && -z "${reviewed[$key]+present}" ]] || \
    fail duplicate-or-empty-allowlist-field
  reviewed["$key"]="$value"
done <"$allowlist"
[[ "${#reviewed[@]}" == "7" && "${reviewed[version]:-}" == "2" && \
   "${reviewed[revision]:-}" == "$revision" && \
   "${reviewed[reviewed_docker_root_equivalent]:-}" == "true" ]] || \
  fail incomplete-review-allowlist
for digest_field in source_tree_sha256 orchestrator_sha256 trusted_controller_sha256 trusted_stage_controller_sha256; do
  [[ "${reviewed[$digest_field]:-}" =~ ^[0-9a-f]{64}$ ]] || \
    fail invalid-review-digest
done
[[ "$(sha256sum "$CONTROLLER_PATH" | awk '{print $1}')" == \
   "${reviewed[trusted_controller_sha256]}" ]] || fail controller-review-hash
[[ -f "$STAGE_CONTROLLER_PATH" && ! -L "$STAGE_CONTROLLER_PATH" && \
   "$(realpath -e -- "$STAGE_CONTROLLER_PATH")" == "$STAGE_CONTROLLER_PATH" && \
   "$(stat -c '%u:%g' -- "$STAGE_CONTROLLER_PATH")" == "0:0" ]] || \
  fail unsafe-stage-controller
(( (8#$(stat -c '%a' -- "$STAGE_CONTROLLER_PATH") & 8#022) == 0 )) || \
  fail stage-controller-writable
[[ "$(sha256sum "$STAGE_CONTROLLER_PATH" | awk '{print $1}')" == \
   "${reviewed[trusted_stage_controller_sha256]}" ]] || fail stage-controller-review-hash

root="$WORKTREE_ROOT/$revision"
helper="$root/deploy/run_exact_rehearsal.sh"
evidence="$EVIDENCE_ROOT/$revision.ok"
[[ -d "$root" && ! -L "$root" && "$(realpath -e -- "$root")" == "$root" && \
   "$(stat -c '%u:%g' -- "$root")" == "0:0" ]] || fail unsafe-worktree
[[ -z "$(find "$root" -xdev \( ! -user root -o -perm /022 -o -type l \) -print -quit)" ]] || \
  fail mutable-or-linked-worktree
[[ -f "$helper" && ! -L "$helper" ]] || fail missing-orchestrator
for command_name in git python3 sha256sum realpath stat find flock awk install \
  mktemp rm mv chmod chown sync tr env bash df timeout; do
  command -v "$command_name" >/dev/null 2>&1 || fail missing-trusted-host-command
done
install -d -o root -g root -m 0700 -- "$STATE_ROOT"
[[ -d "$STATE_ROOT" && ! -L "$STATE_ROOT" && \
   "$(realpath -e -- "$STATE_ROOT")" == "$STATE_ROOT" && \
   "$(stat -c '%u:%g:%a' -- "$STATE_ROOT")" == "0:0:700" ]] || \
  fail unsafe-controller-state-root
state_free_bytes="$(df -B1 --output=avail -- "$STATE_ROOT" | awk 'NR == 2 {print $1}')"
[[ "$state_free_bytes" =~ ^[0-9]+$ && "$state_free_bytes" -ge "$MIN_STATE_FREE_BYTES" ]] || \
  fail insufficient-controller-disk-reserve
exec 9>"$STATE_ROOT/.controller.lock"
chmod 0600 "$STATE_ROOT/.controller.lock"
flock -n 9 || fail another-trusted-controller-active
state="$(mktemp -d -- "$STATE_ROOT/$revision.XXXXXXXX")"
[[ -d "$state" && ! -L "$state" && "$(stat -c '%u:%g:%a' -- "$state")" == "0:0:700" ]] || \
  fail unsafe-controller-state
invalidate_rehearsal_admission() {
  if [[ ! -e "$rehearsal_admission" && ! -L "$rehearsal_admission" ]]; then
    return 0
  fi
  [[ -d "$ADMISSION_ROOT" && ! -L "$ADMISSION_ROOT" && \
     "$(realpath -e -- "$ADMISSION_ROOT")" == "$ADMISSION_ROOT" && \
     "$(stat -c '%u:%g:%a' -- "$ADMISSION_ROOT")" == "0:0:700" && \
     -f "$rehearsal_admission" && ! -L "$rehearsal_admission" && \
     "$(realpath -e -- "$rehearsal_admission")" == "$rehearsal_admission" && \
     "$(stat -c '%u:%g:%a' -- "$rehearsal_admission")" == "0:0:600" ]] || \
    return 1
  rm -f -- "$rehearsal_admission"
  sync -f "$ADMISSION_ROOT"
}

cleanup() {
  local status="$?" resolved=""
  trap - EXIT
  if [[ "$status" != "0" && "$requested_mode" == "rehearsal" && \
        "$child_started" == "true" ]]; then
    if ! invalidate_rehearsal_admission; then
      status=1
      echo "TRUSTED_EXACT_REHEARSAL_REFUSED|unsafe-admission-invalidation" >&2
    fi
  fi
  resolved="$(realpath -e -- "$state" 2>/dev/null || true)"
  if [[ "$resolved" == "$STATE_ROOT/$revision."* && "$resolved" != "$STATE_ROOT" ]]; then
    rm -rf --one-file-system -- "$resolved"
  else
    status=1
    echo "TRUSTED_EXACT_REHEARSAL_REFUSED|unsafe-state-cleanup" >&2
  fi
  exit "$status"
}
trap cleanup EXIT

git_safe=(git -c core.fsmonitor=false -c core.hooksPath=/dev/null -C "$root")
[[ "$("${git_safe[@]}" rev-parse --verify 'HEAD^{commit}')" == "$revision" ]] || \
  fail worktree-revision
"${git_safe[@]}" fsck --strict --no-progress >/dev/null || fail worktree-object-integrity
[[ -z "$("${git_safe[@]}" status --porcelain=v1 --untracked-files=all)" ]] || \
  fail dirty-worktree
[[ -z "$("${git_safe[@]}" ls-tree -r "$revision" | \
  awk '$1 == "120000" || $1 == "160000" {print; exit}')" ]] || \
  fail link-or-submodule-tree
"${git_safe[@]}" ls-tree -rz --name-only "$revision" | python3 -c '
import sys
pending = b""
while True:
    chunk = sys.stdin.buffer.read(65536)
    if not chunk:
        break
    pending += chunk
    while b"\0" in pending:
        path, pending = pending.split(b"\0", 1)
        if path == b".gitattributes" or path.endswith(b"/.gitattributes"):
            raise SystemExit(3)
        if len(path) > 1048576:
            raise SystemExit(4)
if pending:
    raise SystemExit(5)
' || fail export-attributes-forbidden
[[ ! -e "$root/.git/info/attributes" && ! -L "$root/.git/info/attributes" ]] || \
  fail repository-export-attributes

( ulimit -v $((3 * 1024 * 1024)); ulimit -t 300; \
  ulimit -f $((MAX_REVIEW_TREE_BYTES / 1024)); \
  timeout --signal=KILL 360 git -c core.fsmonitor=false -c core.hooksPath=/dev/null \
    -c core.attributesFile=/dev/null -C "$root" archive --format=tar "$revision" \
    >"$state/reviewed-tree.tar" ) || fail review-tree-export
source_tree_sha256="$(timeout --signal=KILL 360 python3 - "$state/reviewed-tree.tar" <<'PY'
import hashlib
import json
from pathlib import PurePosixPath
import sys
import tarfile

archive_path = sys.argv[1]
inventory = []
seen = set()
members = 0
total_size = 0
with tarfile.open(archive_path, mode="r|") as archive:
    for member in archive:
        members += 1
        if members > 100_000:
            raise SystemExit("review tree contains too many entries")
        raw_name = member.name.rstrip("/")
        path = PurePosixPath(raw_name)
        if (
            not raw_name
            or path.is_absolute()
            or ".." in path.parts
            or raw_name in seen
            or not (member.isdir() or member.isfile())
        ):
            raise SystemExit("review tree contains an unsafe entry")
        seen.add(raw_name)
        if member.isdir():
            inventory.append((raw_name, "d", 1, ""))
            continue
        total_size += member.size
        if member.size < 0 or member.size > 1024 * 1024 * 1024 or total_size > 4 * 1024 * 1024 * 1024:
            raise SystemExit("review tree is unreasonably large")
        stream = archive.extractfile(member)
        if stream is None:
            raise SystemExit("review tree file cannot be read")
        digest = hashlib.sha256()
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        inventory.append((raw_name, "f", int(bool(member.mode & 0o111)), digest.hexdigest()))
inventory.sort(key=lambda item: item[0])
payload = json.dumps(inventory, separators=(",", ":"), ensure_ascii=True).encode()
print(hashlib.sha256(payload).hexdigest())
PY
)" || fail source-tree-review-hash
[[ "$source_tree_sha256" == "${reviewed[source_tree_sha256]}" ]] || \
  fail unreviewed-source-tree
[[ "$(sha256sum "$helper" | awk '{print $1}')" == \
   "${reviewed[orchestrator_sha256]}" ]] || fail unreviewed-orchestrator

if [[ "$requested_mode" == "authorize-build" ]]; then
  # This branch deliberately runs before any candidate Dockerfile is built and
  # therefore before release-candidate image evidence can exist.
  printf 'TRUSTED_CANDIDATE_BUILD_AUTHORIZED|revision=%s|tree=%s\n' \
    "$revision" "$source_tree_sha256"
  exit 0
fi

[[ -f "$evidence" && ! -L "$evidence" && \
   "$(realpath -e -- "$evidence")" == "$evidence" && \
   "$(stat -c '%u:%g:%a' -- "$evidence")" == "0:0:600" ]] || \
  fail unsafe-candidate-evidence

if [[ -e "$ADMISSION_ROOT" || -L "$ADMISSION_ROOT" ]]; then
  [[ -d "$ADMISSION_ROOT" && ! -L "$ADMISSION_ROOT" && \
     "$(realpath -e -- "$ADMISSION_ROOT")" == "$ADMISSION_ROOT" && \
     "$(stat -c '%u:%g:%a' -- "$ADMISSION_ROOT")" == "0:0:700" ]] || \
    fail unsafe-admission-root
else
  install -d -o root -g root -m 0700 -- "$ADMISSION_ROOT"
fi
invalidate_rehearsal_admission || fail unsafe-existing-admission

evidence_tree_sha256="$(python3 - "$evidence" "$revision" \
  "${reviewed[trusted_stage_controller_sha256]}" <<'PY'
import re
import sys
from pathlib import Path

allowed = {
    "status", "revision", "archive_sha256", "bundle_sha256", "tree_sha256",
    "app_image_id", "proxy_image_id", "archive_equals_bundle_commit",
    "containers_unchanged", "listeners_unchanged",
    "trusted_stage_controller_sha256", "trusted_stage_handoff_sha256",
    "trusted_stage_handoff_nonce",
}
values = {}
for line in Path(sys.argv[1]).read_text(encoding="utf-8").splitlines():
    if not line or "=" not in line:
        raise SystemExit("invalid candidate evidence")
    key, value = line.split("=", 1)
    if key not in allowed or key in values or "\x00" in value:
        raise SystemExit("invalid candidate evidence")
    values[key] = value
if set(values) != allowed or values["status"] != "verified" or values["revision"] != sys.argv[2]:
    raise SystemExit("invalid candidate evidence")
for key in ("archive_sha256", "bundle_sha256", "tree_sha256"):
    if not re.fullmatch(r"[0-9a-f]{64}", values[key]):
        raise SystemExit("invalid candidate evidence")
for key in ("app_image_id", "proxy_image_id"):
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", values[key]):
        raise SystemExit("invalid candidate evidence")
for key in ("archive_equals_bundle_commit", "containers_unchanged", "listeners_unchanged"):
    if values[key] != "true":
        raise SystemExit("invalid candidate evidence")
for key in ("trusted_stage_controller_sha256", "trusted_stage_handoff_sha256"):
    if not re.fullmatch(r"[0-9a-f]{64}", values[key]):
        raise SystemExit("invalid candidate evidence")
if not re.fullmatch(r"[0-9a-f]{32}", values["trusted_stage_handoff_nonce"]):
    raise SystemExit("invalid candidate evidence")
if values["trusted_stage_controller_sha256"] != sys.argv[3]:
    raise SystemExit("candidate evidence does not bind the reviewed stage controller")
print(values["tree_sha256"])
PY
)" || fail invalid-candidate-evidence
[[ "$evidence_tree_sha256" == "$source_tree_sha256" ]] || \
  fail staged-tree-not-independently-reviewed

# Recheck immutable inputs immediately before crossing the trust boundary.
[[ -z "$("${git_safe[@]}" status --porcelain=v1 --untracked-files=all)" ]] || \
  fail worktree-changed-before-exec
[[ "$(sha256sum "$helper" | awk '{print $1}')" == \
   "${reviewed[orchestrator_sha256]}" ]] || fail orchestrator-changed-before-exec

# Mint a one-run handoff only after every independent review hash has passed.
# The candidate must atomically consume this root-only file before it can write
# an admission, so a direct `run_exact_rehearsal.sh` invocation has no reusable
# authorization artifact.
nonce="$(tr -d '-' </proc/sys/kernel/random/uuid)"
[[ "$nonce" =~ ^[0-9a-f]{32}$ ]] || fail invalid-controller-nonce
handoff="$state/handoff.attestation"
handoff_temporary="$(mktemp -- "$state/.handoff.XXXXXXXX")"
controller_sha256="$(sha256sum "$CONTROLLER_PATH" | awk '{print $1}')"
{
  printf 'version=1\nrevision=%s\nnonce=%s\n' "$revision" "$nonce"
  printf 'source_tree_sha256=%s\norchestrator_sha256=%s\n' \
    "$source_tree_sha256" "${reviewed[orchestrator_sha256]}"
  printf 'trusted_controller_sha256=%s\ncontroller_path=%s\n' \
    "$controller_sha256" "$CONTROLLER_PATH"
} >"$handoff_temporary"
chmod 0600 "$handoff_temporary"
chown root:root "$handoff_temporary"
sync -f "$handoff_temporary"
mv -T -- "$handoff_temporary" "$handoff"
sync -f "$state"
handoff_sha256="$(sha256sum "$handoff" | awk '{print $1}')"
[[ "$handoff_sha256" =~ ^[0-9a-f]{64}$ ]] || fail invalid-handoff-digest

set +e
child_started=true
env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin LANG=C.UTF-8 \
  LECTURESIFT_EXPECTED_REHEARSAL_REVISION="$revision" \
  LECTURESIFT_TRUSTED_REHEARSAL_HANDOFF="$handoff" \
  LECTURESIFT_TRUSTED_REHEARSAL_NONCE="$nonce" \
  LECTURESIFT_TRUSTED_REHEARSAL_HANDOFF_SHA256="$handoff_sha256" \
  bash "$helper"
child_status="$?"
set -e
[[ "$child_status" == "0" ]] || fail candidate-rehearsal-failed

completion="$state/handoff.completed"
consumed="$state/handoff.consumed"
[[ ! -e "$handoff" && ! -L "$handoff" ]] || fail handoff-not-consumed
for artifact in "$consumed" "$completion"; do
  [[ -f "$artifact" && ! -L "$artifact" && \
     "$(realpath -e -- "$artifact")" == "$artifact" && \
     "$(stat -c '%u:%g:%a' -- "$artifact")" == "0:0:600" ]] || \
    fail invalid-handoff-completion-artifact
done
[[ "$(sha256sum "$consumed" | awk '{print $1}')" == "$handoff_sha256" ]] || \
  fail consumed-handoff-changed
python3 - "$completion" "$revision" "$nonce" "$handoff_sha256" \
  "/var/lib/lecturesift/rehearsal-admissions/$revision.ok" <<'PY'
import hashlib
from pathlib import Path
import re
import sys

completion = Path(sys.argv[1])
admission = Path(sys.argv[5])
allowed = {
    "version", "status", "revision", "nonce", "handoff_sha256",
    "admission_sha256",
}
values = {}
for line in completion.read_text(encoding="ascii").splitlines():
    if not line or "=" not in line:
        raise SystemExit("invalid trusted completion syntax")
    key, value = line.split("=", 1)
    if key not in allowed or key in values or not value:
        raise SystemExit("invalid trusted completion field")
    values[key] = value
if set(values) != allowed:
    raise SystemExit("incomplete trusted completion")
if (
    values["version"] != "1"
    or values["status"] != "admitted"
    or values["revision"] != sys.argv[2]
    or values["nonce"] != sys.argv[3]
    or values["handoff_sha256"] != sys.argv[4]
    or not re.fullmatch(r"[0-9a-f]{64}", values["admission_sha256"])
    or hashlib.sha256(admission.read_bytes()).hexdigest()
       != values["admission_sha256"]
):
    raise SystemExit("trusted completion does not bind the admission")
PY
echo "TRUSTED_EXACT_REHEARSAL_COMPLETE|revision=$revision"
