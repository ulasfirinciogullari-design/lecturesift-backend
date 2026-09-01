#!/bin/bash -p
set -Eeuo pipefail
set +x
umask 077
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
export GIT_ATTR_NOSYSTEM=1
IFS=$' \t\n'
unset CDPATH ENV BASH_ENV
unset -f id git python3 sha256sum realpath stat find awk install mktemp rm \
  mv chmod chown sync tar docker sort grep env du wc findmnt timeout 2>/dev/null || true
hash -r

# Stage one immutable release candidate from two independently transported
# inputs.  The bundle is the revision authority.  The source archive is never
# trusted merely because its transport hash matches: its complete tree is
# compared with the bundle commit before Docker sees it.
revision="${LECTURESIFT_STAGE_REVISION:-}"
archive="${LECTURESIFT_STAGE_ARCHIVE:-}"
bundle="${LECTURESIFT_STAGE_BUNDLE:-}"
archive_sha256="${LECTURESIFT_STAGE_ARCHIVE_SHA256:-}"
bundle_sha256="${LECTURESIFT_STAGE_BUNDLE_SHA256:-}"
trusted_stage_handoff="${LECTURESIFT_TRUSTED_STAGE_HANDOFF:-}"
trusted_stage_nonce="${LECTURESIFT_TRUSTED_STAGE_NONCE:-}"
trusted_stage_handoff_sha256="${LECTURESIFT_TRUSTED_STAGE_HANDOFF_SHA256:-}"
trusted_stage_controller=/usr/local/sbin/lecturesift-release-stage-controller
trusted_stage_state_base=/var/lib/lecturesift
trusted_stage_state_parent=$trusted_stage_state_base/controller-state
trusted_stage_state_root=$trusted_stage_state_parent/release-stage
trusted_stage_handoff_state=""
trusted_stage_handoff_consumed=""
trusted_stage_source_tree_sha256=""
trusted_stage_controller_sha256=""
MAX_TRANSPORT_BYTES=$((2 * 1024 * 1024 * 1024))
MAX_EXPANDED_BYTES=$((4 * 1024 * 1024 * 1024))
MAX_TREE_ENTRIES=100000

fail() {
  echo "EXACT_RELEASE_STAGE_FAILED|$*" >&2
  exit 1
}

verify_no_git_export_attributes() {
  local repository="$1" expected_revision="$2" common_dir info_attributes \
    post_common_dir post_info_attributes
  common_dir="$(timeout --signal=KILL 30 git -C "$repository" rev-parse \
    --path-format=absolute --git-common-dir)" || return 1
  common_dir="$(timeout --signal=KILL 30 realpath -e -- "$common_dir")" || return 1
  info_attributes="$(timeout --signal=KILL 30 git -C "$repository" rev-parse --path-format=absolute \
    --git-path info/attributes)" || return 1
  info_attributes="$(timeout --signal=KILL 30 realpath -m -- "$info_attributes")" || return 1
  [[ "$info_attributes" == "$common_dir/info/attributes" && \
     ! -e "$info_attributes" && ! -L "$info_attributes" ]] || return 1
  (
    ulimit -v $((768 * 1024))
    set -o pipefail
    timeout --signal=KILL 60 git -C "$repository" ls-tree -rzt --name-only \
      "$expected_revision" | \
      timeout --signal=KILL 60 git -c core.attributesFile=/dev/null \
        -C "$repository" check-attr --source="$expected_revision" -z --stdin --all | \
      timeout --signal=KILL 60 python3 -c '
import sys

forbidden = {b"export-ignore", b"export-subst"}
pending = b""
field_index = 0
attribute = b""
record_count = 0
while True:
    chunk = sys.stdin.buffer.read(65536)
    if not chunk:
        break
    pending += chunk
    if len(pending) > 1114112:
        raise SystemExit(4)
    while b"\0" in pending:
        field, pending = pending.split(b"\0", 1)
        if len(field) > 1048576 or (field_index % 3 != 2 and not field):
            raise SystemExit(4)
        position = field_index % 3
        if position == 1:
            attribute = field
        elif position == 2:
            record_count += 1
            if record_count > 200000 or attribute in forbidden:
                raise SystemExit(3)
        field_index += 1
if pending or field_index % 3:
    raise SystemExit(5)
'
  ) || return 1
  post_common_dir="$(timeout --signal=KILL 30 git -C "$repository" rev-parse \
    --path-format=absolute --git-common-dir)" || return 1
  post_common_dir="$(timeout --signal=KILL 30 realpath -e -- "$post_common_dir")" || return 1
  post_info_attributes="$(timeout --signal=KILL 30 git -C "$repository" rev-parse \
    --path-format=absolute --git-path info/attributes)" || return 1
  post_info_attributes="$(timeout --signal=KILL 30 realpath -m -- \
    "$post_info_attributes")" || return 1
  [[ "$post_common_dir" == "$common_dir" && \
     "$post_info_attributes" == "$info_attributes" && \
     "$post_info_attributes" == "$post_common_dir/info/attributes" && \
     ! -e "$post_info_attributes" && ! -L "$post_info_attributes" ]]
}

[[ "$(id -u)" == "0" ]] || fail root-required
[[ "$revision" =~ ^[0-9a-f]{40}$ ]] || fail bad-revision
[[ "$archive_sha256" =~ ^[0-9a-f]{64}$ ]] || fail bad-archive-hash
[[ "$bundle_sha256" =~ ^[0-9a-f]{64}$ ]] || fail bad-bundle-hash
for command_name in id git python3 sha256sum realpath stat find awk install \
  mktemp rm mv chmod chown sync tar docker sort grep env du wc findmnt timeout; do
  command -v "$command_name" >/dev/null 2>&1 || fail missing-trusted-host-command
done
expected_archive="/var/tmp/lecturesift-$revision.tar"
expected_bundle="/var/tmp/lecturesift-$revision.bundle"
[[ "$archive" == "$expected_archive" && "$bundle" == "$expected_bundle" ]] || \
  fail unexpected-upload-path

consume_trusted_stage_handoff() {
  local binding state_mode handoff_mode candidate_path
  [[ "$trusted_stage_nonce" =~ ^[0-9a-f]{32}$ && \
     "$trusted_stage_handoff_sha256" =~ ^[0-9a-f]{64}$ ]] || \
    fail missing-trusted-stage-handoff
  trusted_stage_handoff_state="$(dirname -- "$trusted_stage_handoff")"
  [[ "$trusted_stage_handoff_state" =~ ^${trusted_stage_state_root}/${revision}\.[A-Za-z0-9]{8}$ && \
     "$trusted_stage_handoff" == "$trusted_stage_handoff_state/handoff.attestation" ]] || \
    fail invalid-trusted-stage-handoff-path
  [[ -d "$trusted_stage_state_root" && ! -L "$trusted_stage_state_root" && \
     "$(realpath -e -- "$trusted_stage_state_root")" == "$trusted_stage_state_root" && \
     "$(stat -c '%u:%g:%a' -- "$trusted_stage_state_root")" == "0:0:700" ]] || \
    fail unsafe-trusted-stage-state-root
  [[ -d "$trusted_stage_state_parent" && ! -L "$trusted_stage_state_parent" && \
     "$(realpath -e -- "$trusted_stage_state_parent")" == "$trusted_stage_state_parent" && \
     "$(stat -c '%u:%g:%a' -- "$trusted_stage_state_parent")" == "0:0:700" ]] || \
    fail unsafe-trusted-stage-state-parent
  [[ -d "$trusted_stage_state_base" && ! -L "$trusted_stage_state_base" && \
     "$(realpath -e -- "$trusted_stage_state_base")" == "$trusted_stage_state_base" && \
     "$(stat -c '%u:%g' -- "$trusted_stage_state_base")" == "0:0" ]] || \
    fail unsafe-trusted-stage-state-base
  (( (8#$(stat -c '%a' -- "$trusted_stage_state_base") & 8#022) == 0 )) || \
    fail writable-trusted-stage-state-base
  state_mode="$(stat -c '%u:%g:%a' -- "$trusted_stage_handoff_state" 2>/dev/null || true)"
  [[ -d "$trusted_stage_handoff_state" && ! -L "$trusted_stage_handoff_state" && \
     "$(realpath -e -- "$trusted_stage_handoff_state")" == "$trusted_stage_handoff_state" && \
     "$state_mode" == "0:0:700" ]] || fail unsafe-trusted-stage-handoff-state
  handoff_mode="$(stat -c '%u:%g:%a' -- "$trusted_stage_handoff" 2>/dev/null || true)"
  [[ -f "$trusted_stage_handoff" && ! -L "$trusted_stage_handoff" && \
     "$(realpath -e -- "$trusted_stage_handoff")" == "$trusted_stage_handoff" && \
     "$handoff_mode" == "0:0:600" ]] || fail unsafe-trusted-stage-handoff
  [[ -f "$trusted_stage_controller" && ! -L "$trusted_stage_controller" && \
     "$(realpath -e -- "$trusted_stage_controller")" == "$trusted_stage_controller" && \
     "$(stat -c '%u:%g' -- "$trusted_stage_controller")" == "0:0" ]] || \
    fail unsafe-trusted-stage-controller
  (( (8#$(stat -c '%a' -- "$trusted_stage_controller") & 8#022) == 0 )) || \
    fail writable-trusted-stage-controller
  [[ "$(sha256sum "$trusted_stage_handoff" | awk '{print $1}')" == \
     "$trusted_stage_handoff_sha256" ]] || fail trusted-stage-handoff-digest
  candidate_path="$(realpath -e -- "$0")" || fail unsafe-candidate-stager-path
  binding="$(python3 - "$trusted_stage_handoff" "$revision" "$trusted_stage_nonce" \
    "$archive_sha256" "$bundle_sha256" "$candidate_path" \
    "$trusted_stage_controller" <<'PY'
import hashlib
from pathlib import Path
import re
import sys

path = Path(sys.argv[1])
allowed = {
    "version", "revision", "nonce", "archive_sha256", "bundle_sha256",
    "source_tree_sha256", "candidate_stager_sha256",
    "trusted_stage_controller_sha256", "controller_path",
}
values = {}
for line in path.read_text(encoding="ascii").splitlines():
    if not line or "=" not in line:
        raise SystemExit("invalid trusted stage handoff syntax")
    key, value = line.split("=", 1)
    if key not in allowed or key in values or not value:
        raise SystemExit("invalid trusted stage handoff field")
    values[key] = value
if set(values) != allowed:
    raise SystemExit("incomplete trusted stage handoff")
sha = re.compile(r"[0-9a-f]{64}")
candidate_sha = hashlib.sha256(Path(sys.argv[6]).read_bytes()).hexdigest()
controller_sha = hashlib.sha256(Path(sys.argv[7]).read_bytes()).hexdigest()
if (
    values["version"] != "1"
    or values["revision"] != sys.argv[2]
    or values["nonce"] != sys.argv[3]
    or values["archive_sha256"] != sys.argv[4]
    or values["bundle_sha256"] != sys.argv[5]
    or values["candidate_stager_sha256"] != candidate_sha
    or values["trusted_stage_controller_sha256"] != controller_sha
    or values["controller_path"] != sys.argv[7]
    or not sha.fullmatch(values["source_tree_sha256"])
):
    raise SystemExit("trusted stage handoff binding mismatch")
print(values["source_tree_sha256"] + "|" + controller_sha)
PY
  )" || fail invalid-trusted-stage-handoff-binding
  trusted_stage_source_tree_sha256="${binding%%|*}"
  trusted_stage_controller_sha256="${binding#*|}"
  [[ "$trusted_stage_source_tree_sha256" =~ ^[0-9a-f]{64}$ && \
     "$trusted_stage_controller_sha256" =~ ^[0-9a-f]{64}$ ]] || \
    fail invalid-trusted-stage-handoff-binding
  trusted_stage_handoff_consumed="$trusted_stage_handoff_state/handoff.consumed"
  [[ ! -e "$trusted_stage_handoff_consumed" && \
     ! -L "$trusted_stage_handoff_consumed" ]] || fail replayed-trusted-stage-handoff
  mv -T -- "$trusted_stage_handoff" "$trusted_stage_handoff_consumed"
  sync -f "$trusted_stage_handoff_state"
  [[ ! -e "$trusted_stage_handoff" && ! -L "$trusted_stage_handoff" && \
     -f "$trusted_stage_handoff_consumed" && ! -L "$trusted_stage_handoff_consumed" && \
     "$(stat -c '%u:%g:%a' -- "$trusted_stage_handoff_consumed")" == "0:0:600" && \
     "$(sha256sum "$trusted_stage_handoff_consumed" | awk '{print $1}')" == \
       "$trusted_stage_handoff_sha256" ]] || fail trusted-stage-handoff-consumption
}

# No archive or bundle parser may run before this one-time authorization is
# validated and atomically moved to its consumed name.
consume_trusted_stage_handoff

check_upload() {
  local path="$1" mode
  [[ -f "$path" && ! -L "$path" ]] || fail missing-upload
  [[ "$(stat -c '%u:%g' -- "$path")" == "0:0" ]] || fail upload-owner
  mode="$(stat -c '%a' -- "$path")"
  [[ "$mode" == "400" || "$mode" == "600" ]] || fail upload-mode
  [[ "$(realpath -e -- "$path")" == "$path" ]] || fail upload-path-resolution
}
check_upload "$archive"
check_upload "$bundle"
for upload in "$archive" "$bundle"; do
  upload_bytes="$(stat -c '%s' -- "$upload")"
  [[ "$upload_bytes" =~ ^[0-9]+$ && "$upload_bytes" -le "$MAX_TRANSPORT_BYTES" ]] || \
    fail oversized-transport
done
printf '%s  %s\n' "$archive_sha256" "$archive" | sha256sum -c --status || fail archive-hash
printf '%s  %s\n' "$bundle_sha256" "$bundle" | sha256sum -c --status || fail bundle-hash

release_base=/srv/lecturesift/releases
worktree_base=/srv/lecturesift/worktrees
release="$release_base/$revision"
worktree="$worktree_base/$revision"
incoming_release="$release_base/.incoming-$revision"
incoming_worktree="$worktree_base/.incoming-$revision"
comparison_tree="$trusted_stage_handoff_state/comparison-tree"
app_image="lecturesift-backend:staged-$revision"
proxy_image="lecturesift-egress-proxy:staged-$revision"
evidence_root=/var/lib/lecturesift/release-candidates
evidence="$evidence_root/$revision.ok"
trusted_controller=/usr/local/sbin/lecturesift-exact-rehearsal-controller
created_release=false
created_worktree=false
created_incoming_release=false
created_incoming_worktree=false
created_comparison_tree=false
created_evidence=false
created_app_image=false
created_proxy_image=false
release_identity=""
worktree_identity=""
incoming_release_identity=""
incoming_worktree_identity=""
comparison_tree_identity=""
evidence_identity=""
app_image_id=""
proxy_image_id=""

remove_created_tree() {
  local path="$1" expected_identity="$2" allowed_parent="$3" resolved mounts target
  [[ ! -e "$path" && ! -L "$path" ]] && return 0
  [[ -n "$expected_identity" && -d "$path" && ! -L "$path" ]] || return 1
  resolved="$(realpath -e -- "$path")" || return 1
  [[ "$resolved" == "$allowed_parent/"* && "$resolved" != "$allowed_parent" && \
     "$(stat -c '%u:%g' -- "$path")" == "0:0" && \
     "$(stat -c '%d:%i' -- "$path")" == "$expected_identity" ]] || return 1
  mounts="$(findmnt -rn -o TARGET)" || return 1
  while IFS= read -r target; do
    [[ "$target" == "$resolved" || "$target" == "$resolved/"* ]] && return 1
  done <<<"$mounts"
  rm -rf --one-file-system -- "$resolved"
}

remove_created_evidence() {
  [[ "$created_evidence" == "true" ]] || return 0
  [[ -n "$evidence_identity" && -f "$evidence" && ! -L "$evidence" && \
     "$(realpath -e -- "$evidence")" == "$evidence" && \
     "$(stat -c '%u:%g:%a:%d:%i' -- "$evidence")" == \
       "0:0:600:$evidence_identity" ]] || return 1
  rm -f -- "$evidence"
  sync -f "$evidence_root"
}

cleanup() {
  local status="$?" cleanup_failed=false current_image_id=""
  trap - EXIT
  if [[ "$status" != "0" ]]; then
    if [[ "$created_app_image" == "true" ]]; then
      current_image_id="$(docker image inspect --format '{{.Id}}' "$app_image" 2>/dev/null || true)"
      [[ -n "$app_image_id" && "$current_image_id" == "$app_image_id" ]] && \
        docker image rm "$app_image" >/dev/null 2>&1 || cleanup_failed=true
    fi
    if [[ "$created_proxy_image" == "true" ]]; then
      current_image_id="$(docker image inspect --format '{{.Id}}' "$proxy_image" 2>/dev/null || true)"
      [[ -n "$proxy_image_id" && "$current_image_id" == "$proxy_image_id" ]] && \
        docker image rm "$proxy_image" >/dev/null 2>&1 || cleanup_failed=true
    fi
    [[ "$created_evidence" == "false" ]] || remove_created_evidence || cleanup_failed=true
    [[ "$created_release" == "false" ]] || \
      remove_created_tree "$release" "$release_identity" "$release_base" || cleanup_failed=true
    [[ "$created_worktree" == "false" ]] || \
      remove_created_tree "$worktree" "$worktree_identity" "$worktree_base" || cleanup_failed=true
  fi
  [[ "$created_incoming_release" == "false" ]] || \
    remove_created_tree "$incoming_release" "$incoming_release_identity" "$release_base" || cleanup_failed=true
  [[ "$created_incoming_worktree" == "false" ]] || \
    remove_created_tree "$incoming_worktree" "$incoming_worktree_identity" "$worktree_base" || cleanup_failed=true
  [[ "$created_comparison_tree" == "false" ]] || \
    remove_created_tree "$comparison_tree" "$comparison_tree_identity" \
      "$trusted_stage_handoff_state" || cleanup_failed=true
  if [[ "$cleanup_failed" == "true" ]]; then
    echo "EXACT_RELEASE_STAGE_FAILED|unsafe-created-resource-cleanup" >&2
    status=1
  fi
  exit "$status"
}
trap cleanup EXIT

for image in "$app_image" "$proxy_image"; do
  if docker image inspect "$image" >/dev/null 2>&1; then
    fail target-image-already-exists
  fi
done

python3 - "$archive" "$MAX_TREE_ENTRIES" "$MAX_EXPANDED_BYTES" <<'PY'
import pathlib
import sys
import tarfile

seen = set()
members = 0
expanded = 0
max_members = int(sys.argv[2])
max_expanded = int(sys.argv[3])
with tarfile.open(sys.argv[1], "r|") as source:
    for member in source:
        members += 1
        expanded += member.size
        if (
            members > max_members
            or member.size < 0
            or member.size > 1024 * 1024 * 1024
            or expanded > max_expanded
        ):
            raise SystemExit("archive exceeds reviewed expansion bounds")
        item = pathlib.PurePosixPath(member.name)
        if (
            item.is_absolute()
            or not item.parts
            or item.parts[0] != "source"
            or ".." in item.parts
            or member.name in seen
            or not (member.isfile() or member.isdir())
        ):
            raise SystemExit(f"unsafe archive entry: {member.name}")
        seen.add(member.name)
if members == 0:
    raise SystemExit("empty archive")
PY

[[ "$(git bundle list-heads "$bundle")" == "$revision HEAD" ]] || fail bundle-head

install -d -o root -g root -m 0755 /srv/lecturesift "$release_base" "$worktree_base"
for directory in /srv/lecturesift "$release_base" "$worktree_base"; do
  [[ -d "$directory" && ! -L "$directory" && "$(realpath -e -- "$directory")" == "$directory" ]] || \
    fail unsafe-stage-directory
  [[ "$(stat -c '%u:%g' -- "$directory")" == "0:0" ]] || fail stage-directory-owner
  (( (8#$(stat -c '%a' -- "$directory") & 8#022) == 0 )) || fail stage-directory-mode
done
for target in "$release" "$worktree" "$incoming_release" "$incoming_worktree" "$comparison_tree" "$evidence"; do
  [[ ! -e "$target" && ! -L "$target" ]] || fail target-already-exists
done

install -d -o root -g root -m 0755 "$incoming_release"
created_incoming_release=true
incoming_release_identity="$(stat -c '%d:%i' -- "$incoming_release")"
tar --extract --file "$archive" --directory "$incoming_release" \
  --strip-components=1 --no-same-owner --no-same-permissions
chown -R root:root "$incoming_release"
chmod -R go-w "$incoming_release"
[[ -z "$(find "$incoming_release" -xdev -type l -print -quit)" ]] || fail archive-symlink
[[ -z "$(find "$incoming_release" -xdev \( ! -user root -o -perm /022 \) -print -quit)" ]] || \
  fail archive-metadata

created_incoming_worktree=true
git clone --no-checkout --quiet "$bundle" "$incoming_worktree"
incoming_worktree_identity="$(stat -c '%d:%i' -- "$incoming_worktree")"
git -C "$incoming_worktree" checkout --detach --quiet "$revision"
[[ "$(git -C "$incoming_worktree" rev-parse --verify 'HEAD^{commit}')" == "$revision" ]] || \
  fail worktree-revision
git -C "$incoming_worktree" fsck --strict --no-progress >/dev/null || fail worktree-fsck
[[ -z "$(git -C "$incoming_worktree" status --porcelain=v1 --untracked-files=all)" ]] || \
  fail worktree-dirty
[[ -z "$(git -C "$incoming_worktree" ls-tree -r "$revision" | grep -E '^(120000|160000) ' || true)" ]] || \
  fail worktree-link-or-submodule
verify_no_git_export_attributes "$incoming_worktree" "$revision" || \
  fail export-attributes-forbidden
chown -R root:root "$incoming_worktree"
chmod -R go-w "$incoming_worktree"
[[ -z "$(find "$incoming_worktree" -xdev \( ! -user root -o -perm /022 \) -print -quit)" ]] || \
  fail worktree-metadata

install -d -o root -g root -m 0700 "$comparison_tree"
created_comparison_tree=true
comparison_tree_identity="$(stat -c '%d:%i' -- "$comparison_tree")"
git -c core.attributesFile=/dev/null -C "$incoming_worktree" \
  archive --format=tar "$revision" | tar -xf - -C "$comparison_tree"
tree_sha256="$(python3 - "$incoming_release" "$comparison_tree" <<'PY'
import hashlib
import json
import os
import pathlib
import stat
import sys

def inventory(root_text):
    root = pathlib.Path(root_text)
    result = []
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        relative = path.relative_to(root).as_posix()
        details = path.lstat()
        if stat.S_ISDIR(details.st_mode):
            result.append((relative, "d", 1, ""))
        elif stat.S_ISREG(details.st_mode):
            digest_builder = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest_builder.update(chunk)
            digest = digest_builder.hexdigest()
            result.append((relative, "f", int(bool(details.st_mode & 0o111)), digest))
        else:
            raise SystemExit("unsupported tree entry: " + relative)
    return result

archive_tree = inventory(sys.argv[1])
git_tree = inventory(sys.argv[2])
if archive_tree != git_tree:
    raise SystemExit("transport archive does not equal bundle commit")
payload = json.dumps(git_tree, separators=(",", ":"), ensure_ascii=True).encode()
print(hashlib.sha256(payload).hexdigest())
PY
)" || fail archive-bundle-tree-mismatch
[[ "$tree_sha256" =~ ^[0-9a-f]{64}$ ]] || fail tree-digest
[[ "$tree_sha256" == "$trusted_stage_source_tree_sha256" ]] || \
  fail trusted-stage-handoff-tree-mismatch
remove_created_tree "$comparison_tree" "$comparison_tree_identity" \
  "$trusted_stage_handoff_state" || \
  fail unsafe-comparison-tree-removal
created_comparison_tree=false

for tree in "$incoming_release" "$incoming_worktree"; do
  expanded_bytes="$(du -sb -- "$tree" | awk '{print $1}')"
  tree_entries="$(find "$tree" -xdev -print | wc -l)"
  [[ "$expanded_bytes" =~ ^[0-9]+$ && "$expanded_bytes" -le "$MAX_EXPANDED_BYTES" && \
     "$tree_entries" =~ ^[0-9]+$ && "$tree_entries" -le "$MAX_TREE_ENTRIES" ]] || \
    fail expanded-tree-bound
done

mv -T -- "$incoming_release" "$release"
created_release=true
created_incoming_release=false
release_identity="$incoming_release_identity"
mv -T -- "$incoming_worktree" "$worktree"
created_worktree=true
created_incoming_worktree=false
worktree_identity="$incoming_worktree_identity"

# Candidate Dockerfiles are host-root-equivalent.  Never evaluate one until
# the fixed, independently installed controller proves that the entire Git
# tree, the exact-rehearsal orchestrator and the controller itself match the
# root-only review allowlist for this revision.
[[ -f "$trusted_controller" && ! -L "$trusted_controller" && \
   "$(realpath -e -- "$trusted_controller")" == "$trusted_controller" && \
   "$(stat -c '%u:%g' -- "$trusted_controller")" == "0:0" ]] || \
  fail unsafe-trusted-controller
(( (8#$(stat -c '%a' -- "$trusted_controller") & 8#022) == 0 )) || \
  fail writable-trusted-controller
build_authorization="$(env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin LANG=C.UTF-8 \
  LECTURESIFT_EXPECTED_REHEARSAL_REVISION="$revision" \
  LECTURESIFT_TRUSTED_CONTROLLER_MODE=authorize-build \
  "$trusted_controller")" || fail candidate-build-not-authorized
[[ "$build_authorization" == \
   "TRUSTED_CANDIDATE_BUILD_AUTHORIZED|revision=$revision|tree=$tree_sha256" ]] || \
  fail invalid-candidate-build-authorization
supply_chain_digest="$(python3 "$release/deploy/supply_chain_lock.py" \
  --root "$release" --print-digest)" || fail invalid-supply-chain-lock
[[ "$supply_chain_digest" =~ ^[0-9a-f]{64}$ ]] || fail invalid-supply-chain-digest

container_state_before="$(docker ps -aq | sort | while read -r container; do
  [[ -n "$container" ]] || continue
  docker inspect --format '{{.Id}}|{{.Name}}|{{.State.Status}}|{{.State.StartedAt}}|{{.RestartCount}}|{{.Image}}' "$container"
done)"
listeners_before="$(ss -Hlnptu | LC_ALL=C sort)"
docker build --pull \
  --build-arg "LECTURESIFT_BUILD_REVISION=$revision" \
  --build-arg "LECTURESIFT_SUPPLY_CHAIN_LOCK_SHA256=$supply_chain_digest" \
  --tag "$app_image" "$release"
created_app_image=true
app_image_id="$(docker image inspect --format '{{.Id}}' "$app_image")"
docker build --pull \
  --build-arg "LECTURESIFT_BUILD_REVISION=$revision" \
  --build-arg "LECTURESIFT_SUPPLY_CHAIN_LOCK_SHA256=$supply_chain_digest" \
  --tag "$proxy_image" "$release/deploy/egress-proxy"
created_proxy_image=true
proxy_image_id="$(docker image inspect --format '{{.Id}}' "$proxy_image")"
for image in "$app_image" "$proxy_image"; do
  [[ "$(docker image inspect --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}' "$image")" == "$revision" ]] || fail image-label
  [[ "$(docker image inspect --format '{{ index .Config.Labels "io.lecturesift.supply-chain-lock-sha256" }}' "$image")" == "$supply_chain_digest" ]] || fail image-supply-chain-label
  docker image inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$image" \
    | grep -Fqx "LECTURESIFT_BUILD_REVISION=$revision" || fail image-environment
  docker image inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$image" \
    | grep -Fqx "LECTURESIFT_SUPPLY_CHAIN_LOCK_SHA256=$supply_chain_digest" || fail image-supply-chain-environment
  [[ -z "$(docker ps -aq --filter "ancestor=$image")" ]] || fail staged-image-has-container
done
[[ "$container_state_before" == "$(docker ps -aq | sort | while read -r container; do
  [[ -n "$container" ]] || continue
  docker inspect --format '{{.Id}}|{{.Name}}|{{.State.Status}}|{{.State.StartedAt}}|{{.RestartCount}}|{{.Image}}' "$container"
done)" ]] || fail container-state-changed
[[ "$listeners_before" == "$(ss -Hlnptu | LC_ALL=C sort)" ]] || fail listeners-changed

install -d -o root -g root -m 0700 "$evidence_root"
[[ -d "$evidence_root" && ! -L "$evidence_root" && \
   "$(realpath -e -- "$evidence_root")" == "$evidence_root" && \
   "$(stat -c '%u:%g:%a' -- "$evidence_root")" == "0:0:700" ]] || \
  fail unsafe-evidence-root
temporary="$(mktemp -- "$evidence_root/.release-candidate.XXXXXXXX")"
chmod 0600 "$temporary"
chown root:root "$temporary"
{
  printf 'status=verified\nrevision=%s\n' "$revision"
  printf 'archive_sha256=%s\nbundle_sha256=%s\ntree_sha256=%s\n' \
    "$archive_sha256" "$bundle_sha256" "$tree_sha256"
  printf 'app_image_id=%s\nproxy_image_id=%s\n' "$app_image_id" "$proxy_image_id"
  printf 'trusted_stage_controller_sha256=%s\ntrusted_stage_handoff_sha256=%s\n' \
    "$trusted_stage_controller_sha256" "$trusted_stage_handoff_sha256"
  printf 'trusted_stage_handoff_nonce=%s\n' "$trusted_stage_nonce"
  printf 'archive_equals_bundle_commit=true\ncontainers_unchanged=true\nlisteners_unchanged=true\n'
} >"$temporary"
sync -f "$temporary"
mv -T -- "$temporary" "$evidence"
created_evidence=true
evidence_identity="$(stat -c '%d:%i' -- "$evidence")"
chmod 0600 "$evidence"
chown root:root "$evidence"
sync -f "$evidence_root"

completion="$trusted_stage_handoff_state/handoff.completed"
[[ ! -e "$completion" && ! -L "$completion" ]] || fail replayed-stage-completion
completion_temporary="$(mktemp -- "$trusted_stage_handoff_state/.completion.XXXXXXXX")"
evidence_sha256="$(sha256sum "$evidence" | awk '{print $1}')"
{
  printf 'version=1\nstatus=staged\nrevision=%s\nnonce=%s\n' \
    "$revision" "$trusted_stage_nonce"
  printf 'handoff_sha256=%s\ntrusted_stage_controller_sha256=%s\n' \
    "$trusted_stage_handoff_sha256" "$trusted_stage_controller_sha256"
  printf 'candidate_evidence_sha256=%s\n' "$evidence_sha256"
} >"$completion_temporary"
chmod 0600 "$completion_temporary"
chown root:root "$completion_temporary"
sync -f "$completion_temporary"
mv -T -- "$completion_temporary" "$completion"
sync -f "$trusted_stage_handoff_state"

rm -f -- "$archive" "$bundle"
trap - EXIT
created_evidence=false
printf 'EXACT_RELEASE_STAGED_OK|revision=%s|tree=%s|app=%s|proxy=%s\n' \
  "$revision" "$tree_sha256" "$app_image_id" "$proxy_image_id"
