#!/bin/bash -p
set -Eeuo pipefail
set +x
umask 077
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
export GIT_ATTR_NOSYSTEM=1
IFS=$' \t\n'
unset CDPATH ENV BASH_ENV
unset -f id git python3 sha256sum realpath stat find flock awk install mktemp rm \
  mv chmod chown sync tr env bash tar df timeout wc 2>/dev/null || true
hash -r

# Fixed, independently installed trust boundary for release staging.  No file
# from the transported candidate is executed until this program has bounded
# both transports and matched their complete canonical tree to a private
# operator review record.

STAGER_PATH=/usr/local/sbin/lecturesift-release-stage-controller
EXACT_CONTROLLER_PATH=/usr/local/sbin/lecturesift-exact-rehearsal-controller
ALLOWLIST_ROOT=/etc/lecturesift/exact-rehearsal-allowlist
STATE_ROOT=/run/lecturesift-release-stage-controller
MAX_TRANSPORT_BYTES=$((512 * 1024 * 1024))
MAX_EXPANDED_BYTES=$((2 * 1024 * 1024 * 1024))
MAX_TREE_ENTRIES=100000
MAX_GIT_OBJECTS=200000
MAX_MEMBER_PATH_BYTES=4096
MAX_PAX_METADATA_BYTES=65536
INVENTORY_VMEM_KIB=$((768 * 1024))
MIN_STATE_FREE_BYTES=$((12 * 1024 * 1024 * 1024))

revision="${LECTURESIFT_STAGE_REVISION:-}"
archive="${LECTURESIFT_STAGE_ARCHIVE:-}"
bundle="${LECTURESIFT_STAGE_BUNDLE:-}"
archive_sha256="${LECTURESIFT_STAGE_ARCHIVE_SHA256:-}"
bundle_sha256="${LECTURESIFT_STAGE_BUNDLE_SHA256:-}"
child_started=false
candidate_evidence="/var/lib/lecturesift/release-candidates/$revision.ok"

fail() {
  echo "TRUSTED_RELEASE_STAGE_REFUSED|$*" >&2
  exit 1
}

[[ "$(id -u)" == "0" ]] || fail root-required
[[ "$#" == "0" ]] || fail arguments-forbidden
[[ "$revision" =~ ^[0-9a-f]{40}$ ]] || fail bad-revision
[[ "$archive_sha256" =~ ^[0-9a-f]{64}$ ]] || fail bad-archive-hash
[[ "$bundle_sha256" =~ ^[0-9a-f]{64}$ ]] || fail bad-bundle-hash
[[ "$archive" == "/var/tmp/lecturesift-$revision.tar" && \
   "$bundle" == "/var/tmp/lecturesift-$revision.bundle" ]] || \
  fail unexpected-upload-path

for command_name in id git python3 sha256sum realpath stat find flock awk install \
  mktemp rm mv chmod chown sync tr env bash tar df timeout wc; do
  command -v "$command_name" >/dev/null 2>&1 || fail missing-trusted-host-command
done

check_fixed_program() {
  local path="$1" expected_self="${2:-false}" mode
  [[ -f "$path" && ! -L "$path" && "$(realpath -e -- "$path")" == "$path" && \
     "$(stat -c '%u:%g' -- "$path")" == "0:0" ]] || return 1
  mode="$(stat -c '%a' -- "$path")"
  (( (8#$mode & 8#022) == 0 )) || return 1
  [[ "$expected_self" != "true" || "$(realpath -e -- "$0")" == "$path" ]]
}
check_fixed_program "$STAGER_PATH" true || fail stager-not-fixed-root-file
check_fixed_program "$EXACT_CONTROLLER_PATH" || fail unsafe-exact-controller

install -d -o root -g root -m 0700 -- "$STATE_ROOT"
[[ -d "$STATE_ROOT" && ! -L "$STATE_ROOT" && \
   "$(realpath -e -- "$STATE_ROOT")" == "$STATE_ROOT" && \
   "$(stat -c '%u:%g:%a' -- "$STATE_ROOT")" == "0:0:700" ]] || \
  fail unsafe-stage-state-root
exec 8>"$STATE_ROOT/.controller.lock"
chmod 0600 "$STATE_ROOT/.controller.lock"
chown root:root "$STATE_ROOT/.controller.lock"
flock -n 8 || fail another-release-stage-controller-active
[[ ! -e "$candidate_evidence" && ! -L "$candidate_evidence" ]] || \
  fail candidate-evidence-already-exists

for upload in "$archive" "$bundle"; do
  [[ -f "$upload" && ! -L "$upload" && \
     "$(realpath -e -- "$upload")" == "$upload" && \
     "$(stat -c '%u:%g' -- "$upload")" == "0:0" ]] || fail unsafe-upload
  mode="$(stat -c '%a' -- "$upload")"
  [[ "$mode" == "400" || "$mode" == "600" ]] || fail upload-mode
  bytes="$(stat -c '%s' -- "$upload")"
  [[ "$bytes" =~ ^[0-9]+$ && "$bytes" -le "$MAX_TRANSPORT_BYTES" ]] || \
    fail oversized-transport
done

# Authenticate the exact immutable bytes before asking Git or tarfile to parse
# them.  This is deliberately before every bundle import/clone operation.
printf '%s  %s\n' "$archive_sha256" "$archive" | sha256sum -c --status || \
  fail archive-hash
printf '%s  %s\n' "$bundle_sha256" "$bundle" | sha256sum -c --status || \
  fail bundle-hash

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
  [[ "${reviewed[$digest_field]:-}" =~ ^[0-9a-f]{64}$ ]] || fail invalid-review-digest
done
[[ "$(sha256sum "$STAGER_PATH" | awk '{print $1}')" == \
   "${reviewed[trusted_stage_controller_sha256]}" ]] || fail stager-review-hash
[[ "$(sha256sum "$EXACT_CONTROLLER_PATH" | awk '{print $1}')" == \
   "${reviewed[trusted_controller_sha256]}" ]] || fail exact-controller-review-hash

state_free_bytes="$(df -PB1 --output=avail "$STATE_ROOT" | awk 'NR == 2 {print $1}')"
[[ "$state_free_bytes" =~ ^[0-9]+$ && "$state_free_bytes" -ge "$MIN_STATE_FREE_BYTES" ]] || \
  fail insufficient-stage-disk-reserve
state="$(mktemp -d -- "$STATE_ROOT/$revision.XXXXXXXX")"
[[ -d "$state" && ! -L "$state" && \
   "$(stat -c '%u:%g:%a' -- "$state")" == "0:0:700" ]] || \
  fail unsafe-stage-state
cleanup() {
  local status="$?" resolved=""
  trap - EXIT
  if [[ "$status" != "0" && "$child_started" == "true" && \
        ( -e "$candidate_evidence" || -L "$candidate_evidence" ) ]]; then
    evidence_root="$(dirname -- "$candidate_evidence")"
    if [[ -d "$evidence_root" && ! -L "$evidence_root" && \
          "$(realpath -e -- "$evidence_root")" == "$evidence_root" && \
          "$(stat -c '%u:%g:%a' -- "$evidence_root")" == "0:0:700" && \
          -f "$candidate_evidence" && ! -L "$candidate_evidence" && \
          "$(realpath -e -- "$candidate_evidence")" == "$candidate_evidence" && \
          "$(stat -c '%u:%g:%a' -- "$candidate_evidence")" == "0:0:600" ]]; then
      rm -f -- "$candidate_evidence"
      sync -f "$evidence_root"
    else
      status=1
      echo "TRUSTED_RELEASE_STAGE_REFUSED|unsafe-candidate-evidence-invalidation" >&2
    fi
  fi
  resolved="$(realpath -e -- "$state" 2>/dev/null || true)"
  if [[ "$resolved" == "$STATE_ROOT/$revision."* && "$resolved" != "$STATE_ROOT" ]]; then
    rm -rf --one-file-system -- "$resolved"
  else
    status=1
    echo "TRUSTED_RELEASE_STAGE_REFUSED|unsafe-stage-state-cleanup" >&2
  fi
  exit "$status"
}
trap cleanup EXIT

bundle_head="$({
  ulimit -v $((768 * 1024))
  ulimit -t 60
  ulimit -f 1024
  ulimit -n 256
  ulimit -u 128
  timeout --signal=KILL 60 git bundle list-heads "$bundle"
})" || fail bundle-head-read
[[ "$bundle_head" == "$revision HEAD" ]] || fail bundle-head
git -c core.hooksPath=/dev/null init --bare --quiet "$state/repository.git"
( ulimit -v $((3 * 1024 * 1024)); ulimit -t 300; ulimit -n 1024; ulimit -u 512; \
  timeout --signal=KILL 360 git -c core.hooksPath=/dev/null -c protocol.file.allow=always \
    -C "$state/repository.git" bundle verify "$bundle" >/dev/null ) || fail bundle-verify
( ulimit -v $((3 * 1024 * 1024)); ulimit -t 300; ulimit -n 1024; ulimit -u 512; \
  timeout --signal=KILL 360 git -c core.hooksPath=/dev/null -c protocol.file.allow=always \
    -C "$state/repository.git" fetch --quiet --no-tags "$bundle" "$revision" ) || \
  fail bundle-import
[[ "$(git -C "$state/repository.git" rev-parse --verify 'FETCH_HEAD^{commit}')" == "$revision" ]] || \
  fail imported-revision
git -C "$state/repository.git" ls-tree -rz --name-only "$revision" | python3 -c '
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
[[ ! -e "$state/repository.git/info/attributes" && \
   ! -L "$state/repository.git/info/attributes" ]] || fail repository-export-attributes
( ulimit -v $((3 * 1024 * 1024)); ulimit -t 300; ulimit -n 1024; ulimit -u 512; \
  timeout --signal=KILL 360 git -C "$state/repository.git" fsck --strict --no-progress >/dev/null ) || \
  fail bundle-object-integrity
timeout --signal=KILL 180 git -C "$state/repository.git" \
  cat-file --batch-all-objects --batch-check='%(objecttype) %(objectsize)' | \
  awk -v max_objects="$MAX_GIT_OBJECTS" -v max_bytes="$MAX_EXPANDED_BYTES" '
    { count += 1; total += $2; if ($2 > 1073741824) exit 2 }
    count > max_objects || total > max_bytes { exit 3 }
    END { if (count == 0) exit 4 }
  ' || fail git-object-bound
( ulimit -v $((3 * 1024 * 1024)); ulimit -t 300; ulimit -f $((MAX_EXPANDED_BYTES / 1024)); \
  timeout --signal=KILL 360 git -c core.attributesFile=/dev/null \
    -C "$state/repository.git" archive --format=tar "$revision" >"$state/git-tree.tar" ) || \
  fail git-tree-export

tree_sha256="$(
  (
    ulimit -v "$INVENTORY_VMEM_KIB"
    ulimit -t 300
    ulimit -f 1024
    ulimit -n 256
    ulimit -u 128
    timeout --signal=KILL 360 python3 - \
      "$archive" "$state/git-tree.tar" "$MAX_TREE_ENTRIES" \
      "$MAX_EXPANDED_BYTES" "$MAX_MEMBER_PATH_BYTES" \
      "$MAX_PAX_METADATA_BYTES" <<'PY'
import hashlib
import json
from pathlib import PurePosixPath
import sys
import tarfile

limit_entries = int(sys.argv[3])
limit_bytes = int(sys.argv[4])
limit_path_bytes = int(sys.argv[5])
limit_pax_bytes = int(sys.argv[6])

def encoded_size(value, label, limit):
    if not isinstance(value, str) or "\0" in value:
        raise SystemExit(f"invalid {label}")
    try:
        size = len(value.encode("utf-8", "surrogateescape"))
    except UnicodeEncodeError:
        raise SystemExit(f"invalid {label}")
    if size > limit:
        raise SystemExit("release tree header metadata exceeds safety contract")
    return size

def inventory(path, strip_source):
    result = []
    seen = set()
    entries = 0
    expanded = 0
    with tarfile.open(path, "r|") as stream:
        for member in stream:
            encoded_size(member.name, "member path", limit_path_bytes)
            encoded_size(member.linkname, "member link path", limit_path_bytes)
            pax_bytes = 0
            for key, value in member.pax_headers.items():
                pax_bytes += encoded_size(key, "PAX key", 256)
                pax_bytes += encoded_size(value, "PAX value", limit_path_bytes)
                if pax_bytes > limit_pax_bytes:
                    raise SystemExit("release tree header metadata exceeds safety contract")
            raw = member.name.rstrip("/")
            item = PurePosixPath(raw)
            if item.is_absolute() or not item.parts or ".." in item.parts:
                raise SystemExit("unsafe release-tree path")
            if strip_source:
                if item.parts[0] != "source":
                    raise SystemExit("archive is missing source prefix")
                relative_parts = item.parts[1:]
                if not relative_parts:
                    if member.isdir():
                        continue
                    raise SystemExit("invalid source root entry")
                relative = PurePosixPath(*relative_parts).as_posix()
            else:
                relative = item.as_posix()
            entries += 1
            expanded += member.size
            if (
                entries > limit_entries
                or member.size < 0
                or member.size > 1024 * 1024 * 1024
                or expanded > limit_bytes
                or relative in seen
                or not (member.isdir() or member.isfile())
            ):
                raise SystemExit("release tree exceeds safety contract")
            seen.add(relative)
            if member.isdir():
                result.append((relative, "d", 1, ""))
                continue
            extracted = stream.extractfile(member)
            if extracted is None:
                raise SystemExit("release tree file cannot be read")
            digest = hashlib.sha256()
            for chunk in iter(lambda: extracted.read(1024 * 1024), b""):
                digest.update(chunk)
            result.append((relative, "f", int(bool(member.mode & 0o111)), digest.hexdigest()))
    if entries == 0:
        raise SystemExit("empty release tree")
    result.sort(key=lambda row: row[0])
    return result

transport = inventory(sys.argv[1], True)
git_tree = inventory(sys.argv[2], False)
if transport != git_tree:
    raise SystemExit("archive does not equal reviewed bundle revision")
payload = json.dumps(git_tree, separators=(",", ":"), ensure_ascii=True).encode()
print(hashlib.sha256(payload).hexdigest())
PY
  )
)" || fail release-tree-validation
[[ "$tree_sha256" =~ ^[0-9a-f]{64}$ && \
   "$tree_sha256" == "${reviewed[source_tree_sha256]}" ]] || \
  fail unreviewed-source-tree

# Export only after the allowlist match.  The candidate stager is now part of
# the exact reviewed tree and may cross the host-root-equivalent boundary.
install -d -o root -g root -m 0700 -- "$state/reviewed"
tar -xf "$state/git-tree.tar" -C "$state/reviewed" --no-same-owner --no-same-permissions
chown -R root:root -- "$state/reviewed"
chmod -R go-w -- "$state/reviewed"
candidate="$state/reviewed/deploy/stage_release_candidate.sh"
[[ -f "$candidate" && ! -L "$candidate" && \
   "$(realpath -e -- "$candidate")" == "$state/reviewed/deploy/stage_release_candidate.sh" ]] || \
  fail missing-reviewed-candidate-stager
[[ "$(sha256sum "$archive" | awk '{print $1}')" == "$archive_sha256" && \
   "$(sha256sum "$bundle" | awk '{print $1}')" == "$bundle_sha256" ]] || \
  fail transport-changed-before-candidate

# Mint a one-run root-only authorization only after the complete transported
# tree and both installed controllers match the private review record.  The
# candidate must atomically consume it before parsing either transport.
nonce="$(tr -d '-' </proc/sys/kernel/random/uuid)"
[[ "$nonce" =~ ^[0-9a-f]{32}$ ]] || fail invalid-stage-nonce
candidate_sha256="$(sha256sum "$candidate" | awk '{print $1}')"
controller_sha256="$(sha256sum "$STAGER_PATH" | awk '{print $1}')"
handoff="$state/handoff.attestation"
handoff_temporary="$(mktemp -- "$state/.handoff.XXXXXXXX")"
{
  printf 'version=1\nrevision=%s\nnonce=%s\n' "$revision" "$nonce"
  printf 'archive_sha256=%s\nbundle_sha256=%s\nsource_tree_sha256=%s\n' \
    "$archive_sha256" "$bundle_sha256" "$tree_sha256"
  printf 'candidate_stager_sha256=%s\ntrusted_stage_controller_sha256=%s\n' \
    "$candidate_sha256" "$controller_sha256"
  printf 'controller_path=%s\n' "$STAGER_PATH"
} >"$handoff_temporary"
chmod 0600 "$handoff_temporary"
chown root:root "$handoff_temporary"
sync -f "$handoff_temporary"
mv -T -- "$handoff_temporary" "$handoff"
sync -f "$state"
handoff_sha256="$(sha256sum "$handoff" | awk '{print $1}')"
[[ "$handoff_sha256" =~ ^[0-9a-f]{64}$ ]] || fail invalid-stage-handoff-digest

set +e
child_started=true
env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin LANG=C.UTF-8 \
  LECTURESIFT_STAGE_REVISION="$revision" \
  LECTURESIFT_STAGE_ARCHIVE="$archive" \
  LECTURESIFT_STAGE_BUNDLE="$bundle" \
  LECTURESIFT_STAGE_ARCHIVE_SHA256="$archive_sha256" \
  LECTURESIFT_STAGE_BUNDLE_SHA256="$bundle_sha256" \
  LECTURESIFT_TRUSTED_STAGE_HANDOFF="$handoff" \
  LECTURESIFT_TRUSTED_STAGE_NONCE="$nonce" \
  LECTURESIFT_TRUSTED_STAGE_HANDOFF_SHA256="$handoff_sha256" \
  bash -p "$candidate"
child_status="$?"
set -e
[[ "$child_status" == "0" ]] || fail candidate-stage-failed

consumed="$state/handoff.consumed"
completion="$state/handoff.completed"
[[ ! -e "$handoff" && ! -L "$handoff" ]] || fail stage-handoff-not-consumed
for artifact in "$consumed" "$completion"; do
  [[ -f "$artifact" && ! -L "$artifact" && \
     "$(realpath -e -- "$artifact")" == "$artifact" && \
     "$(stat -c '%u:%g:%a' -- "$artifact")" == "0:0:600" ]] || \
    fail invalid-stage-handoff-completion-artifact
done
[[ "$(sha256sum "$consumed" | awk '{print $1}')" == "$handoff_sha256" ]] || \
  fail consumed-stage-handoff-changed
evidence_root="$(dirname -- "$candidate_evidence")"
[[ -d "$evidence_root" && ! -L "$evidence_root" && \
   "$(realpath -e -- "$evidence_root")" == "$evidence_root" && \
   "$(stat -c '%u:%g:%a' -- "$evidence_root")" == "0:0:700" && \
   -f "$candidate_evidence" && ! -L "$candidate_evidence" && \
   "$(realpath -e -- "$candidate_evidence")" == "$candidate_evidence" && \
   "$(stat -c '%u:%g:%a' -- "$candidate_evidence")" == "0:0:600" ]] || \
  fail unsafe-candidate-evidence
python3 - "$completion" "$candidate_evidence" "$revision" "$nonce" \
  "$handoff_sha256" "$controller_sha256" "$archive_sha256" \
  "$bundle_sha256" "$tree_sha256" <<'PY'
import hashlib
from pathlib import Path
import re
import sys

completion = Path(sys.argv[1])
evidence = Path(sys.argv[2])
allowed = {
    "version", "status", "revision", "nonce", "handoff_sha256",
    "trusted_stage_controller_sha256", "candidate_evidence_sha256",
}
values = {}
for line in completion.read_text(encoding="ascii").splitlines():
    if not line or "=" not in line:
        raise SystemExit("invalid stage completion syntax")
    key, value = line.split("=", 1)
    if key not in allowed or key in values or not value:
        raise SystemExit("invalid stage completion field")
    values[key] = value
evidence_allowed = {
    "status", "revision", "archive_sha256", "bundle_sha256", "tree_sha256",
    "app_image_id", "proxy_image_id", "archive_equals_bundle_commit",
    "containers_unchanged", "listeners_unchanged",
    "trusted_stage_controller_sha256", "trusted_stage_handoff_sha256",
    "trusted_stage_handoff_nonce",
}
evidence_values = {}
for line in evidence.read_text(encoding="ascii").splitlines():
    if not line or "=" not in line:
        raise SystemExit("invalid candidate evidence syntax")
    key, value = line.split("=", 1)
    if key not in evidence_allowed or key in evidence_values or not value:
        raise SystemExit("invalid candidate evidence field")
    evidence_values[key] = value
if set(values) != allowed or (
    values["version"] != "1"
    or values["status"] != "staged"
    or values["revision"] != sys.argv[3]
    or values["nonce"] != sys.argv[4]
    or values["handoff_sha256"] != sys.argv[5]
    or values["trusted_stage_controller_sha256"] != sys.argv[6]
    or not re.fullmatch(r"[0-9a-f]{64}", values["candidate_evidence_sha256"])
    or hashlib.sha256(evidence.read_bytes()).hexdigest()
       != values["candidate_evidence_sha256"]
):
    raise SystemExit("stage completion does not bind candidate evidence")
if set(evidence_values) != evidence_allowed or (
    evidence_values["status"] != "verified"
    or evidence_values["revision"] != sys.argv[3]
    or evidence_values["archive_sha256"] != sys.argv[7]
    or evidence_values["bundle_sha256"] != sys.argv[8]
    or evidence_values["tree_sha256"] != sys.argv[9]
    or evidence_values["trusted_stage_controller_sha256"] != sys.argv[6]
    or evidence_values["trusted_stage_handoff_sha256"] != sys.argv[5]
    or evidence_values["trusted_stage_handoff_nonce"] != sys.argv[4]
    or evidence_values["archive_equals_bundle_commit"] != "true"
    or evidence_values["containers_unchanged"] != "true"
    or evidence_values["listeners_unchanged"] != "true"
    or not re.fullmatch(r"sha256:[0-9a-f]{64}", evidence_values["app_image_id"])
    or not re.fullmatch(r"sha256:[0-9a-f]{64}", evidence_values["proxy_image_id"])
):
    raise SystemExit("candidate evidence does not bind trusted stage handoff")
PY
