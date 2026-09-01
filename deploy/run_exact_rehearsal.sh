#!/usr/bin/env bash
set -Eeuo pipefail
set +x
umask 077
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
export GIT_ATTR_NOSYSTEM=1
export GIT_CONFIG_NOSYSTEM=1
export GIT_CONFIG_GLOBAL=/dev/null
IFS=$' \t\n'

revision="${LECTURESIFT_EXPECTED_REHEARSAL_REVISION:-}"
root="/srv/lecturesift/worktrees/$revision"
staging_base=/run/lecturesift-exact-rehearsal
candidate_evidence="/var/lib/lecturesift/release-candidates/$revision.ok"
admission_root=/var/lib/lecturesift/rehearsal-admissions
admission="$admission_root/$revision.ok"
provenance_root=/var/lib/lecturesift/rehearsal-provenance
rehearsal_run_root=/var/backups/lecturesift/rehearsal
postgres_env=/etc/lecturesift/postgres.env
main_manifest_in_container=/tmp/lecturesift-main-db-rehearsal-manifest.sql
baseline_ready=false
rehearsal_succeeded=false
inner_run_dir=""
artifact_hashes=""
trusted_controller=/usr/local/sbin/lecturesift-exact-rehearsal-controller
trusted_controller_state_base=/var/lib/lecturesift
trusted_controller_state_parent=$trusted_controller_state_base/controller-state
trusted_controller_state_root=$trusted_controller_state_parent/exact-rehearsal
trusted_handoff="${LECTURESIFT_TRUSTED_REHEARSAL_HANDOFF:-}"
trusted_handoff_nonce="${LECTURESIFT_TRUSTED_REHEARSAL_NONCE:-}"
trusted_handoff_sha256="${LECTURESIFT_TRUSTED_REHEARSAL_HANDOFF_SHA256:-}"
trusted_handoff_state=""
trusted_handoff_consumed=""
trusted_source_tree_sha256=""
trusted_controller_sha256=""
admission_written=false
expected_rehearsal_run_id="$(date -u +%Y%m%dT%H%M%SZ)"
[[ "$expected_rehearsal_run_id" =~ ^[0-9]{8}T[0-9]{6}Z$ ]] || \
  fail invalid-expected-rehearsal-run
expected_rehearsal_suffix="${expected_rehearsal_run_id//[^0-9]/}"

fail() {
  echo "EXACT_REHEARSAL_FAILED|$*" >&2
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
[[ -d "$root" && ! -L "$root" && "$(realpath -e -- "$root")" == "$root" ]] || \
  fail unsafe-worktree
[[ -f "$root/deploy/release.sh" && ! -L "$root/deploy/release.sh" ]] || fail missing-release-helper
[[ -f "$root/deploy/check_shell_syntax.sh" && \
   ! -L "$root/deploy/check_shell_syntax.sh" ]] || fail missing-shell-syntax-gate
[[ -f "$root/deploy/assert_rehearsal_production_stopped.sh" && \
   ! -L "$root/deploy/assert_rehearsal_production_stopped.sh" ]] || \
  fail missing-production-stop-gate
[[ "$(git -C "$root" rev-parse --verify 'HEAD^{commit}')" == "$revision" ]] || fail revision-mismatch

consume_trusted_controller_handoff() {
  local binding state_mode handoff_mode
  [[ "$trusted_handoff_nonce" =~ ^[0-9a-f]{32}$ && \
     "$trusted_handoff_sha256" =~ ^[0-9a-f]{64}$ ]] || fail missing-trusted-handoff
  trusted_handoff_state="$(dirname -- "$trusted_handoff")"
  [[ "$trusted_handoff_state" =~ ^${trusted_controller_state_root}/${revision}\.[A-Za-z0-9]{8}$ && \
     "$trusted_handoff" == "$trusted_handoff_state/handoff.attestation" ]] || \
    fail invalid-trusted-handoff-path
  [[ -d "$trusted_controller_state_root" && ! -L "$trusted_controller_state_root" && \
     "$(realpath -e -- "$trusted_controller_state_root")" == "$trusted_controller_state_root" && \
     "$(stat -c '%u:%g:%a' -- "$trusted_controller_state_root")" == "0:0:700" ]] || \
    fail unsafe-trusted-controller-state-root
  [[ -d "$trusted_controller_state_parent" && ! -L "$trusted_controller_state_parent" && \
     "$(realpath -e -- "$trusted_controller_state_parent")" == "$trusted_controller_state_parent" && \
     "$(stat -c '%u:%g:%a' -- "$trusted_controller_state_parent")" == "0:0:700" ]] || \
    fail unsafe-trusted-controller-state-parent
  [[ -d "$trusted_controller_state_base" && ! -L "$trusted_controller_state_base" && \
     "$(realpath -e -- "$trusted_controller_state_base")" == "$trusted_controller_state_base" && \
     "$(stat -c '%u:%g' -- "$trusted_controller_state_base")" == "0:0" ]] || \
    fail unsafe-trusted-controller-state-base
  (( (8#$(stat -c '%a' -- "$trusted_controller_state_base") & 8#022) == 0 )) || \
    fail writable-trusted-controller-state-base
  state_mode="$(stat -c '%u:%g:%a' -- "$trusted_handoff_state" 2>/dev/null || true)"
  [[ -d "$trusted_handoff_state" && ! -L "$trusted_handoff_state" && \
     "$(realpath -e -- "$trusted_handoff_state")" == "$trusted_handoff_state" && \
     "$state_mode" == "0:0:700" ]] || fail unsafe-trusted-handoff-state
  handoff_mode="$(stat -c '%u:%g:%a' -- "$trusted_handoff" 2>/dev/null || true)"
  [[ -f "$trusted_handoff" && ! -L "$trusted_handoff" && \
     "$(realpath -e -- "$trusted_handoff")" == "$trusted_handoff" && \
     "$handoff_mode" == "0:0:600" ]] || fail unsafe-trusted-handoff
  [[ -f "$trusted_controller" && ! -L "$trusted_controller" && \
     "$(realpath -e -- "$trusted_controller")" == "$trusted_controller" && \
     "$(stat -c '%u:%g' -- "$trusted_controller")" == "0:0" ]] || \
    fail unsafe-trusted-controller
  (( (8#$(stat -c '%a' -- "$trusted_controller") & 8#022) == 0 )) || \
    fail writable-trusted-controller
  [[ "$(sha256sum "$trusted_handoff" | awk '{print $1}')" == \
     "$trusted_handoff_sha256" ]] || fail trusted-handoff-digest
  binding="$(python3 - "$trusted_handoff" "$revision" "$trusted_handoff_nonce" \
    "$root/deploy/run_exact_rehearsal.sh" "$trusted_controller" <<'PY'
import hashlib
from pathlib import Path
import re
import sys

path = Path(sys.argv[1])
allowed = {
    "version", "revision", "nonce", "source_tree_sha256",
    "orchestrator_sha256", "trusted_controller_sha256", "controller_path",
}
values = {}
for line in path.read_text(encoding="ascii").splitlines():
    if not line or "=" not in line:
        raise SystemExit("invalid trusted handoff syntax")
    key, value = line.split("=", 1)
    if key not in allowed or key in values or not value:
        raise SystemExit("invalid trusted handoff field")
    values[key] = value
if set(values) != allowed:
    raise SystemExit("incomplete trusted handoff")
sha = re.compile(r"[0-9a-f]{64}")
helper_sha = hashlib.sha256(Path(sys.argv[4]).read_bytes()).hexdigest()
controller_sha = hashlib.sha256(Path(sys.argv[5]).read_bytes()).hexdigest()
if (
    values["version"] != "1"
    or values["revision"] != sys.argv[2]
    or values["nonce"] != sys.argv[3]
    or values["controller_path"] != sys.argv[5]
    or not sha.fullmatch(values["source_tree_sha256"])
    or values["orchestrator_sha256"] != helper_sha
    or values["trusted_controller_sha256"] != controller_sha
):
    raise SystemExit("trusted handoff binding mismatch")
print(values["source_tree_sha256"] + "|" + controller_sha)
PY
  )" || fail invalid-trusted-handoff-binding
  trusted_source_tree_sha256="${binding%%|*}"
  trusted_controller_sha256="${binding#*|}"
  [[ "$trusted_source_tree_sha256" =~ ^[0-9a-f]{64}$ && \
     "$trusted_controller_sha256" =~ ^[0-9a-f]{64}$ ]] || \
    fail invalid-trusted-handoff-binding
  trusted_handoff_consumed="$trusted_handoff_state/handoff.consumed"
  [[ ! -e "$trusted_handoff_consumed" && ! -L "$trusted_handoff_consumed" ]] || \
    fail replayed-trusted-handoff
  mv -T -- "$trusted_handoff" "$trusted_handoff_consumed"
  sync -f "$trusted_handoff_state"
  [[ ! -e "$trusted_handoff" && ! -L "$trusted_handoff" && \
     -f "$trusted_handoff_consumed" && ! -L "$trusted_handoff_consumed" && \
     "$(stat -c '%u:%g:%a' -- "$trusted_handoff_consumed")" == "0:0:600" && \
     "$(sha256sum "$trusted_handoff_consumed" | awk '{print $1}')" == \
       "$trusted_handoff_sha256" ]] || fail trusted-handoff-consumption
}

consume_trusted_controller_handoff
bash "$root/deploy/check_shell_syntax.sh" || fail shell-syntax-gate

if [[ -e "$staging_base" || -L "$staging_base" ]]; then
  [[ -d "$staging_base" && ! -L "$staging_base" && \
     "$(realpath -e -- "$staging_base")" == "$staging_base" && \
     "$(stat -c '%u:%g:%a' -- "$staging_base")" == "0:0:700" ]] || fail unsafe-staging-base
else
  install -d -o root -g root -m 0700 -- "$staging_base"
fi
state="$(mktemp -d -- "$staging_base/$revision.XXXXXXXX")"
[[ "$(stat -c '%u:%g:%a' -- "$state")" == "0:0:700" && ! -L "$state" ]] || fail unsafe-state
release_env="$state/rehearsal-release.env"
export LECTURESIFT_ROOT="$root" LECTURESIFT_RELEASE_ENV_FILE="$release_env"
bash "$root/deploy/release.sh" prepare >/dev/null

check_private_file() {
  local path="$1" mode
  [[ -f "$path" && ! -L "$path" && "$(realpath -e -- "$path")" == "$path" ]] || \
    fail unsafe-private-file
  [[ "$(stat -c '%u:%g' -- "$path")" == "0:0" ]] || fail unsafe-private-owner
  mode="$(stat -c '%a' -- "$path")"
  [[ "$mode" == "400" || "$mode" == "600" ]] || fail unsafe-private-mode
}
check_private_file "$postgres_env"

# Extract only four non-secret identifiers. The master dotenv is parsed as
# data; it is never sourced by this root wrapper.
python3 - "$postgres_env" "$state/postgres-identifiers.env" <<'PY'
import os
import re
import shlex
import sys
import tempfile
from pathlib import Path

required = {"POSTGRES_USER", "POSTGRES_DB", "LECTURESIFT_APP_DB_USER", "LECTURESIFT_WORKER_DB_USER"}
identifier = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
assignment = re.compile(r"^[ \t]*(?:export[ \t]+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
values = {}
text = Path(sys.argv[1]).read_text(encoding="utf-8")
if "\x00" in text:
    raise SystemExit("POSTGRES_DOTENV_NUL")
for number, raw in enumerate(text.splitlines(), 1):
    if not raw.strip() or raw.lstrip().startswith("#"):
        continue
    match = assignment.fullmatch(raw)
    if not match:
        raise SystemExit(f"POSTGRES_DOTENV_SYNTAX|line={number}")
    key, rhs = match.groups()
    if key in values:
        raise SystemExit(f"POSTGRES_DOTENV_DUPLICATE|key={key}")
    lexer = shlex.shlex(rhs, posix=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    words = list(lexer)
    if len(words) != 1:
        raise SystemExit(f"POSTGRES_DOTENV_VALUE|line={number}|key={key}")
    values[key] = words[0]
missing = required - values.keys()
if missing or any(not identifier.fullmatch(values[key]) for key in required):
    raise SystemExit("POSTGRES_IDENTIFIER_CONTRACT")
destination = Path(sys.argv[2])
fd, temporary_name = tempfile.mkstemp(prefix=".postgres-identifiers.", dir=destination.parent)
temporary = Path(temporary_name)
try:
    os.fchmod(fd, 0o600)
    os.fchown(fd, 0, 0)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
        for key in sorted(required):
            stream.write(f"{key}={shlex.quote(values[key])}\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, destination)
finally:
    temporary.unlink(missing_ok=True)
PY
# This file was generated from an identifier-only allowlist above.
# shellcheck disable=SC1090
source "$state/postgres-identifiers.env"
supply_chain_digest="$(python3 "$root/deploy/supply_chain_lock.py" \
  --root "$root" --print-digest)" || fail invalid-supply-chain-lock
[[ "$supply_chain_digest" =~ ^[0-9a-f]{64}$ ]] || fail invalid-supply-chain-digest

validate_candidate() {
  check_private_file "$candidate_evidence"
  python3 - "$candidate_evidence" "$revision" <<'PY'
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
        raise SystemExit("candidate evidence syntax")
    key, value = line.split("=", 1)
    if key not in allowed or key in values or "\x00" in value:
        raise SystemExit("candidate evidence key")
    values[key] = value
required_true = {"archive_equals_bundle_commit", "containers_unchanged", "listeners_unchanged"}
if values.get("status") != "verified" or values.get("revision") != sys.argv[2]:
    raise SystemExit("candidate evidence identity")
if any(values.get(key) != "true" for key in required_true):
    raise SystemExit("candidate evidence safety")
for key in ("archive_sha256", "bundle_sha256", "tree_sha256"):
    if not re.fullmatch(r"[0-9a-f]{64}", values.get(key, "")):
        raise SystemExit("candidate evidence digest")
for key in ("app_image_id", "proxy_image_id"):
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", values.get(key, "")):
        raise SystemExit("candidate evidence image")
for key in ("trusted_stage_controller_sha256", "trusted_stage_handoff_sha256"):
    if not re.fullmatch(r"[0-9a-f]{64}", values.get(key, "")):
        raise SystemExit("candidate evidence trusted-stage digest")
if not re.fullmatch(r"[0-9a-f]{32}", values.get("trusted_stage_handoff_nonce", "")):
    raise SystemExit("candidate evidence trusted-stage nonce")
PY
  for pair in \
    "lecturesift-backend:staged-$revision|app_image_id" \
    "lecturesift-egress-proxy:staged-$revision|proxy_image_id"; do
    image="${pair%%|*}"
    evidence_key="${pair#*|}"
    expected_id="$(sed -n "s/^${evidence_key}=//p" "$candidate_evidence")"
    [[ "$(docker image inspect --format '{{.Id}}' "$image")" == "$expected_id" ]] || fail staged-image-id
    [[ "$(docker image inspect --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}' "$image")" == "$revision" ]] || fail staged-image-label
    [[ "$(docker image inspect --format '{{ index .Config.Labels "io.lecturesift.supply-chain-lock-sha256" }}' "$image")" == "$supply_chain_digest" ]] || fail staged-image-supply-chain-label
    docker image inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$image" \
      | grep -Fqx "LECTURESIFT_BUILD_REVISION=$revision" || fail staged-image-environment
    docker image inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$image" \
      | grep -Fqx "LECTURESIFT_SUPPLY_CHAIN_LOCK_SHA256=$supply_chain_digest" || fail staged-image-supply-chain-environment
  done
}
validate_candidate
[[ "$(sed -n 's/^tree_sha256=//p' "$candidate_evidence")" == \
   "$trusted_source_tree_sha256" ]] || fail trusted-handoff-tree-mismatch

check_provenance_residue() {
  if [[ ! -e "$provenance_root" && ! -L "$provenance_root" ]]; then
    return 0
  fi
  [[ -d "$provenance_root" && ! -L "$provenance_root" && \
     "$(realpath -e -- "$provenance_root")" == "$provenance_root" && \
     "$(stat -c '%u:%g:%a' -- "$provenance_root")" == "0:0:700" ]] || return 1
  [[ -z "$(find "$provenance_root" -mindepth 1 -maxdepth 1 -print -quit)" ]]
}

for unit in lecturesift.service lecturesift-api.service lecturesift-worker.service \
  lecturesift-instagram.service lecturesift-instagram.timer \
  lecturesift-caddy-staging.service caddy.service; do
  systemctl is-active --quiet "$unit" && fail "active-unit:$unit"
done
bash "$root/deploy/assert_rehearsal_production_stopped.sh" >/dev/null || \
  fail active-production-runtime

exec 7>"$staging_base/.exact-rehearsal.lock"
chmod 0600 "$staging_base/.exact-rehearsal.lock"
flock -n 7 || fail another-exact-rehearsal-active

reconcile_stale_labeled_resources() {
  local identifier record rehearsal_label run_label purpose_label name internal driver scope endpoint endpoints
  local stale_run="" stale_age_ok postgres_identity network_name
  local container_output volume_output network_output
  local -a containers=() volumes=() networks=()
  local -a allowed_containers=(
    lecturesift-api-rehearsal lecturesift-worker-rehearsal
    lecturesift-redis-rehearsal lecturesift-egress-proxy-api-rehearsal
    lecturesift-egress-proxy-worker-rehearsal lecturesift-migration-rehearsal
    lecturesift-source-postgres-rehearsal
  )
  local -a allowed_volumes=(
    lecturesift-api-rehearsal-work lecturesift-worker-rehearsal-work
  )
  local -a allowed_networks=(
    lecturesift_rehearsal_backend lecturesift_rehearsal_api_proxy
    lecturesift_rehearsal_worker_proxy lecturesift_rehearsal_migration
  )
  docker info >/dev/null 2>&1 || return 1
  container_output="$(docker ps -aq --filter label=lecturesift.rehearsal=true)" || return 1
  volume_output="$(docker volume ls -q --filter label=lecturesift.rehearsal=true)" || return 1
  network_output="$(docker network ls -q --filter label=lecturesift.rehearsal=true)" || return 1
  [[ -z "$container_output" ]] || mapfile -t containers <<<"$container_output"
  [[ -z "$volume_output" ]] || mapfile -t volumes <<<"$volume_output"
  [[ -z "$network_output" ]] || mapfile -t networks <<<"$network_output"
  # Reject mixed labeled/unlabeled fixed-name state before considering any
  # deletion. An allowlisted endpoint name is insufficient unless its exact
  # Docker identity was returned by the same dual-label enumeration.
  for name in "${allowed_containers[@]}"; do
    if docker container inspect "$name" >/dev/null 2>&1; then
      identifier="$(docker container inspect --format '{{.Id}}' "$name")" || return 1
      [[ " ${containers[*]} " == *" $identifier "* ]] || return 1
    fi
  done
  for name in "${allowed_volumes[@]}"; do
    if docker volume inspect "$name" >/dev/null 2>&1; then
      [[ " ${volumes[*]} " == *" $name "* ]] || return 1
    fi
  done
  for name in "${allowed_networks[@]}"; do
    if docker network inspect "$name" >/dev/null 2>&1; then
      identifier="$(docker network inspect --format '{{.Id}}' "$name")" || return 1
      [[ " ${networks[*]} " == *" $identifier "* ]] || return 1
    fi
  done
  if ((${#containers[@]} == 0 && ${#volumes[@]} == 0 && ${#networks[@]} == 0)); then
    return 0
  fi
  validate_run() {
    local candidate="$1"
    [[ "$candidate" =~ ^[0-9]{14}$ ]] || return 1
    if [[ -z "$stale_run" ]]; then
      stale_run="$candidate"
      stale_age_ok="$(python3 - "$stale_run" <<'PY'
from datetime import datetime, timezone
import sys, time
try:
    created = datetime.strptime(sys.argv[1], "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc).timestamp()
except ValueError:
    raise SystemExit(2)
print("true" if time.time() - created > 3600 else "false")
PY
)" || return 1
      [[ "$stale_age_ok" == "true" ]] || return 1
    fi
    [[ "$candidate" == "$stale_run" ]]
  }

  # First pass validates every possible deletion target and attachment. Nothing
  # is removed when one name, label, age or topology check is ambiguous.
  for identifier in "${containers[@]}"; do
    [[ -n "$identifier" ]] || continue
    record="$(docker container inspect --format \
      '{{ index .Config.Labels "lecturesift.rehearsal" }}|{{ index .Config.Labels "lecturesift.rehearsal.run" }}|{{ index .Config.Labels "lecturesift.rehearsal.purpose" }}|{{.Name}}' \
      "$identifier")" || return 1
    IFS='|' read -r rehearsal_label run_label purpose_label name <<<"$record"
    name="${name#/}"
    [[ "$rehearsal_label" == "true" && \
       " ${allowed_containers[*]} " == *" $name "* ]] || return 1
    [[ "$name" != "lecturesift-migration-rehearsal" || \
       "$purpose_label" == "candidate-database-migration" ]] || return 1
    [[ "$name" != "lecturesift-source-postgres-rehearsal" || \
       "$purpose_label" == "source-postgres-client" ]] || return 1
    validate_run "$run_label" || return 1
  done
  for identifier in "${volumes[@]}"; do
    [[ -n "$identifier" ]] || continue
    record="$(docker volume inspect --format \
      '{{ index .Labels "lecturesift.rehearsal" }}|{{ index .Labels "lecturesift.rehearsal.run" }}|{{.Name}}' \
      "$identifier")" || return 1
    IFS='|' read -r rehearsal_label run_label name <<<"$record"
    [[ "$rehearsal_label" == "true" && \
       " ${allowed_volumes[*]} " == *" $name "* ]] || return 1
    validate_run "$run_label" || return 1
  done
  for identifier in "${networks[@]}"; do
    [[ -n "$identifier" ]] || continue
    record="$(docker network inspect --format \
      '{{ index .Labels "lecturesift.rehearsal" }}|{{ index .Labels "lecturesift.rehearsal.run" }}|{{ index .Labels "lecturesift.rehearsal.purpose" }}|{{.Name}}|{{.Internal}}|{{.Driver}}|{{.Scope}}' \
      "$identifier")" || return 1
    IFS='|' read -r rehearsal_label run_label purpose_label name internal driver scope <<<"$record"
    [[ "$rehearsal_label" == "true" && "$internal" == "true" && \
       "$driver" == "bridge" && "$scope" == "local" && \
       " ${allowed_networks[*]} " == *" $name "* ]] || return 1
    [[ "$name" != "lecturesift_rehearsal_migration" || \
       "$purpose_label" == "candidate-database-migration" ]] || return 1
    validate_run "$run_label" || return 1
    endpoints="$(docker network inspect --format \
      '{{range .Containers}}{{println .Name}}{{end}}' "$identifier")" || return 1
    while IFS= read -r endpoint; do
      [[ -z "$endpoint" || " ${allowed_containers[*]} " == *" $endpoint "* || \
         ( ( "$name" == "lecturesift_rehearsal_backend" || \
             "$name" == "lecturesift_rehearsal_migration" ) && \
           "$endpoint" == "lecturesift-postgres-1" ) ]] || return 1
    done <<<"$endpoints"
  done
  for network_name in lecturesift_rehearsal_backend lecturesift_rehearsal_migration; do
    if docker network inspect "$network_name" >/dev/null 2>&1; then
      [[ " ${networks[*]} " == *" $(docker network inspect --format '{{.Id}}' "$network_name") "* ]] || \
        return 1
      postgres_identity="$(docker container inspect --format \
        '{{ index .Config.Labels "com.docker.compose.project" }}|{{ index .Config.Labels "com.docker.compose.service" }}|{{.State.Running}}' \
        lecturesift-postgres-1 2>/dev/null || true)"
      [[ "$postgres_identity" == "lecturesift|postgres|true" ]] || return 1
    fi
  done

  for identifier in "${containers[@]}"; do
    [[ -z "$identifier" ]] || docker rm -f "$identifier" >/dev/null || return 1
  done
  for identifier in "${volumes[@]}"; do
    [[ -z "$identifier" ]] || docker volume rm "$identifier" >/dev/null || return 1
  done
  for identifier in "${networks[@]}"; do
    [[ -n "$identifier" ]] || continue
    network_name="$(docker network inspect --format '{{.Name}}' "$identifier")" || return 1
    if [[ "$network_name" == "lecturesift_rehearsal_backend" || \
          "$network_name" == "lecturesift_rehearsal_migration" ]]; then
      endpoints="$(docker network inspect --format \
        '{{range .Containers}}{{println .Name}}{{end}}' "$identifier")" || return 1
      if grep -Fqx lecturesift-postgres-1 <<<"$endpoints"; then
        docker network disconnect "$identifier" lecturesift-postgres-1 >/dev/null || return 1
      fi
    fi
    docker network rm "$identifier" >/dev/null || return 1
  done
  container_output="$(docker ps -aq --filter label=lecturesift.rehearsal=true)" || return 1
  volume_output="$(docker volume ls -q --filter label=lecturesift.rehearsal=true)" || return 1
  network_output="$(docker network ls -q --filter label=lecturesift.rehearsal=true)" || return 1
  [[ -z "$container_output" && -z "$volume_output" && -z "$network_output" ]]
}

reconcile_stale_labeled_resources || fail unsafe-or-recent-rehearsal-runtime-residue
[[ -z "$(ss -Hlnut 'sport = :18000')" ]] || fail rehearsal-port-in-use

run_inner_rehearsal() {
  local -a run_identity=()
  if [[ "${1:-}" != "--reconcile-only" ]]; then
    run_identity=(LECTURESIFT_EXPECTED_REHEARSAL_RUN_ID="$expected_rehearsal_run_id")
  fi
  env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin LANG=C.UTF-8 \
    "${run_identity[@]}" \
    LECTURESIFT_ROOT="$root" \
    LECTURESIFT_SOURCE_DB_ENV=/root/.lecturesift-render-source.env \
    LECTURESIFT_DB_ENV_FILE=/etc/lecturesift/postgres.env \
    LECTURESIFT_ENV_FILE=/etc/lecturesift/runtime.env \
    LECTURESIFT_API_ENV_FILE=/etc/lecturesift/api.env \
    LECTURESIFT_WORKER_ENV_FILE=/etc/lecturesift/worker.env \
    LECTURESIFT_INSTAGRAM_ENV_FILE=/etc/lecturesift/instagram.env \
    LECTURESIFT_REHEARSAL_ENV_FILE=/etc/lecturesift/rehearsal.env \
    LECTURESIFT_RELEASE_ENV_FILE="$release_env" \
    bash "$root/deploy/rehearsal_restore.sh" "$@"
}

snapshot_rehearsal_runs() {
  local output="$1"
  [[ -d "$rehearsal_run_root" && ! -L "$rehearsal_run_root" ]] || fail unsafe-rehearsal-run-root
  [[ "$(realpath -e -- "$rehearsal_run_root")" == "$rehearsal_run_root" ]] || fail unsafe-rehearsal-run-root
  [[ "$(stat -c '%u:%g:%a' -- "$rehearsal_run_root")" == "0:0:700" ]] || fail unsafe-rehearsal-run-root
  find "$rehearsal_run_root" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' |
    LC_ALL=C sort >"$output"
}

# Reconcile only durable pre-created markers from interrupted runs while the
# exact-rehearsal lock is held. The inner helper owns its fixed database and
# backup locks and fails closed for recent, malformed or still-bound markers.
# Only after it succeeds may the empty registry and base-state baselines be
# accepted as the starting point for this exact rehearsal.
stale_reconcile_evidence="$(run_inner_rehearsal --reconcile-stale)" || \
  fail rehearsal-stale-state-reconcile-failed
[[ "$stale_reconcile_evidence" == \
  "REHEARSAL_STALE_RECONCILE_OK|validated_old_database_and_role_cleanup=true|provenance_empty=true" ]] || \
  fail invalid-rehearsal-stale-reconcile-evidence
reconcile_evidence="$(run_inner_rehearsal --reconcile-only)" || \
  fail rehearsal-provenance-reconcile-failed
[[ "$reconcile_evidence" == \
  "REHEARSAL_RECONCILE_OK|database_or_role_modified=false|provenance_empty=true" ]] || \
  fail invalid-rehearsal-provenance-reconcile-evidence
check_provenance_residue || fail stale-rehearsal-provenance

snapshot_inventory() {
  local prefix="$1"
  docker ps -aq | LC_ALL=C sort | while read -r container; do
    [[ -n "$container" ]] || continue
    docker inspect --format '{{.Id}}|{{.Name}}|{{.State.Status}}|{{.State.StartedAt}}|{{.RestartCount}}|{{.Image}}' "$container"
  done >"$prefix.containers"
  docker volume ls -q | LC_ALL=C sort | while read -r volume; do
    [[ -n "$volume" ]] || continue
    docker volume inspect --format '{{.Name}}|{{.Driver}}|{{json .Labels}}' "$volume"
  done >"$prefix.volumes"
  docker network ls -q | LC_ALL=C sort | while read -r network; do
    [[ -n "$network" ]] || continue
    docker network inspect --format '{{.Id}}|{{.Name}}|{{.Driver}}|{{.Scope}}|{{json .Labels}}' "$network"
  done >"$prefix.networks"
  ss -Hlnut | LC_ALL=C sort >"$prefix.listeners"
  docker inspect --format '{{.Id}}|{{.State.Status}}|{{.State.StartedAt}}|{{.RestartCount}}|{{.Image}}' \
    lecturesift-postgres-1 >"$prefix.postgres-container"
  docker inspect --format '{{.Id}}|{{.State.Status}}|{{.State.StartedAt}}|{{.RestartCount}}|{{.Image}}' \
    lecturesift-redis-1 >"$prefix.redis-container"
}

snapshot_roles() {
  local output="$1"
  docker exec lecturesift-postgres-1 psql --no-psqlrc --quiet --tuples-only --no-align \
    --username "$POSTGRES_USER" --dbname postgres <<'SQL' | LC_ALL=C sort >"$output"
SELECT 'ROLE|' || rolname || '|' || rolsuper || '|' || rolinherit || '|' ||
       rolcreaterole || '|' || rolcreatedb || '|' || rolcanlogin || '|' ||
       rolreplication || '|' || rolbypassrls || '|' || rolconnlimit || '|' ||
       coalesce(rolvaliduntil::text, '') || '|' ||
       md5(coalesce(rolpassword, '')) || '|' ||
       md5(coalesce(array_to_string(rolconfig, E'\n'), ''))
FROM pg_authid
UNION ALL
SELECT 'SETTING|' || setdatabase || '|' || setrole || '|' ||
       md5(coalesce(array_to_string(setconfig, E'\n'), ''))
FROM pg_db_role_setting
UNION ALL
SELECT 'MEMBERSHIP|' || pg_get_userbyid(roleid) || '|' ||
       pg_get_userbyid(member) || '|' || pg_get_userbyid(grantor) || '|' ||
       admin_option || '|' || inherit_option || '|' || set_option
FROM pg_auth_members
UNION ALL
SELECT 'PARAMETER_ACL|' || parname || '|' ||
       md5(coalesce(array_to_string(paracl, E'\n'), ''))
FROM pg_parameter_acl
UNION ALL
SELECT 'TABLESPACE_ACL|' || spcname || '|' || pg_get_userbyid(spcowner) || '|' ||
       md5(coalesce(array_to_string(spcacl, E'\n'), '')) || '|' ||
       md5(coalesce(array_to_string(spcoptions, E'\n'), ''))
FROM pg_tablespace
ORDER BY 1;
SQL
}

snapshot_databases() {
  local output="$1"
  docker exec lecturesift-postgres-1 psql --no-psqlrc --quiet --tuples-only --no-align \
    --username "$POSTGRES_USER" --dbname postgres <<'SQL' | LC_ALL=C sort >"$output"
SELECT 'DATABASE_INVENTORY|' || datname || '|' || pg_get_userbyid(datdba) || '|' ||
       pg_encoding_to_char(encoding) || '|' || datcollate || '|' || datctype || '|' ||
       datlocprovider || '|' || coalesce(datcollversion, '') || '|' ||
       datistemplate || '|' || datallowconn || '|' || datconnlimit || '|' ||
       length(coalesce(shobj_description(oid, 'pg_database'), '')) || '|' ||
       md5(coalesce(shobj_description(oid, 'pg_database'), '')) || '|' ||
       md5(coalesce(array_to_string(datacl, E'\n'), ''))
FROM pg_database
ORDER BY datname;
SQL
}

snapshot_grants() {
  local output="$1"
  docker exec lecturesift-postgres-1 psql --no-psqlrc --quiet --tuples-only --no-align \
    --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<'SQL' | LC_ALL=C sort >"$output"
SELECT 'SCHEMA_ACL|' || nspname || '|' || pg_get_userbyid(nspowner) || '|' ||
       md5(coalesce(array_to_string(nspacl, E'\n'), ''))
FROM pg_namespace
UNION ALL
SELECT 'RELATION_ACL|' || n.nspname || '.' || c.relname || '|' ||
       pg_get_userbyid(c.relowner) || '|' ||
       md5(coalesce(array_to_string(c.relacl, E'\n'), ''))
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname NOT IN ('pg_toast', 'information_schema')
  AND n.nspname !~ '^pg_temp_' AND n.nspname !~ '^pg_toast_temp_'
UNION ALL
SELECT 'FUNCTION_ACL|' || n.nspname || '.' || p.oid::regprocedure::text || '|' ||
       pg_get_userbyid(p.proowner) || '|' ||
       md5(coalesce(array_to_string(p.proacl, E'\n'), ''))
FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
WHERE n.nspname NOT IN ('pg_catalog', 'information_schema')
UNION ALL
SELECT 'TYPE_ACL|' || n.nspname || '.' || t.typname || '|' ||
       pg_get_userbyid(t.typowner) || '|' ||
       md5(coalesce(array_to_string(t.typacl, E'\n'), ''))
FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace
WHERE n.nspname NOT IN ('pg_catalog', 'information_schema')
UNION ALL
SELECT 'DEFAULT_ACL|' || pg_get_userbyid(d.defaclrole) || '|' ||
       coalesce(n.nspname, '') || '|' || d.defaclobjtype || '|' ||
       md5(coalesce(array_to_string(d.defaclacl, E'\n'), ''))
FROM pg_default_acl d LEFT JOIN pg_namespace n ON n.oid = d.defaclnamespace
UNION ALL
SELECT 'LARGE_OBJECT_ACL|' || oid || '|' || pg_get_userbyid(lomowner) || '|' ||
       md5(coalesce(array_to_string(lomacl, E'\n'), ''))
FROM pg_largeobject_metadata
ORDER BY 1;
SQL
}

redis_lua='local salt=ARGV[1]; local cursor="0"; local keys={}; repeat local page=redis.call("SCAN",cursor); cursor=page[1]; for _,key in ipairs(page[2]) do table.insert(keys,key) end until cursor=="0"; table.sort(keys); local out={}; for _,key in ipairs(keys) do local kind=redis.call("TYPE",key); if type(kind)=="table" then kind=kind["ok"] end; local value=redis.call("DUMP",key); local ttl=redis.call("PTTL",key); table.insert(out,redis.sha1hex(salt..key).."|"..kind.."|"..redis.sha1hex(salt..value).."|"..ttl) end; return out'
snapshot_redis() {
  local output="$1"
  # Redis enforces the script's read-only contract in addition to the static
  # command allowlist embedded above.
  docker exec lecturesift-redis-1 redis-cli --json EVAL_RO "$redis_lua" 0 "$redis_salt" >"$output"
}

snapshot_manifest() {
  local output="$1" canonical="$2"
  docker cp "$root/deploy/rehearsal_manifest.sql" \
    "lecturesift-postgres-1:$main_manifest_in_container"
  docker exec lecturesift-postgres-1 psql --no-psqlrc --quiet \
    --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
    -v ON_ERROR_STOP=1 -f "$main_manifest_in_container" >"$output"
  python3 "$root/deploy/verify_schema_transition.py" current \
    --manifest "$output" \
    --contract "$root/deploy/schema_contract_payment_provider_sessions_v1.txt" \
    --preserved-contract "$root/deploy/schema_contract_billing_email_verifications_v1.txt" \
    >"$output.schema-contract"
  grep -E '^(DATABASE|SCHEMA|SCHEMA_OBJECT|SCHEMA_COMPAT|TABLE|TABLE_DIFF|ANOMALY|STATUS|UNVALIDATED_FK|MANIFEST_COMPLETE)\|' \
    "$output" | LC_ALL=C sort >"$canonical"
}

redis_salt="$(openssl rand -hex 32)"
date -u +%s%3N >"$state/before.epoch-ms"
snapshot_inventory "$state/before"
snapshot_roles "$state/before.roles"
snapshot_databases "$state/before.databases"
snapshot_grants "$state/before.grants"
snapshot_redis "$state/before.redis.json"
snapshot_manifest "$state/before.manifest" "$state/before.manifest.canonical"
baseline_ready=true

cleanup_labeled_residue() {
  local identifier labels name internal driver scope endpoint endpoint_list postgres_identity purpose_label
  local container_output volume_output network_output
  local -a containers=() volumes=() networks=()
  local -a allowed_containers=(
    lecturesift-api-rehearsal lecturesift-worker-rehearsal
    lecturesift-redis-rehearsal lecturesift-egress-proxy-api-rehearsal
    lecturesift-egress-proxy-worker-rehearsal lecturesift-migration-rehearsal
    lecturesift-source-postgres-rehearsal
  )
  local -a allowed_volumes=(
    lecturesift-api-rehearsal-work lecturesift-worker-rehearsal-work
  )
  local -a allowed_networks=(
    lecturesift_rehearsal_backend lecturesift_rehearsal_api_proxy
    lecturesift_rehearsal_worker_proxy lecturesift_rehearsal_migration
  )
  docker info >/dev/null 2>&1 || return 1
  container_output="$(docker ps -aq \
    --filter label=lecturesift.rehearsal=true \
    --filter "label=lecturesift.rehearsal.run=$expected_rehearsal_suffix")" || return 1
  volume_output="$(docker volume ls -q \
    --filter label=lecturesift.rehearsal=true \
    --filter "label=lecturesift.rehearsal.run=$expected_rehearsal_suffix")" || return 1
  network_output="$(docker network ls -q \
    --filter label=lecturesift.rehearsal=true \
    --filter "label=lecturesift.rehearsal.run=$expected_rehearsal_suffix")" || return 1
  [[ -z "$container_output" ]] || mapfile -t containers <<<"$container_output"
  [[ -z "$volume_output" ]] || mapfile -t volumes <<<"$volume_output"
  [[ -z "$network_output" ]] || mapfile -t networks <<<"$network_output"

  # Validate every candidate first. A forged label on an unknown resource must
  # fail closed without deleting even the legitimate resources from this run.
  for identifier in "${containers[@]}"; do
    [[ -n "$identifier" ]] || continue
    labels="$(docker container inspect --format \
      '{{ index .Config.Labels "lecturesift.rehearsal" }}|{{ index .Config.Labels "lecturesift.rehearsal.run" }}|{{ index .Config.Labels "lecturesift.rehearsal.purpose" }}|{{.Name}}' \
      "$identifier")" || return 1
    IFS='|' read -r rehearsal_label run_label purpose_label name <<<"$labels"
    name="${name#/}"
    [[ "$rehearsal_label" == "true" && "$run_label" == "$expected_rehearsal_suffix" && \
       " ${allowed_containers[*]} " == *" $name "* ]] || return 1
    [[ "$name" != "lecturesift-migration-rehearsal" || \
       "$purpose_label" == "candidate-database-migration" ]] || return 1
    [[ "$name" != "lecturesift-source-postgres-rehearsal" || \
       "$purpose_label" == "source-postgres-client" ]] || return 1
  done
  for identifier in "${volumes[@]}"; do
    [[ -n "$identifier" ]] || continue
    labels="$(docker volume inspect --format \
      '{{ index .Labels "lecturesift.rehearsal" }}|{{ index .Labels "lecturesift.rehearsal.run" }}|{{.Name}}' \
      "$identifier")" || return 1
    IFS='|' read -r rehearsal_label run_label name <<<"$labels"
    [[ "$rehearsal_label" == "true" && "$run_label" == "$expected_rehearsal_suffix" && \
       " ${allowed_volumes[*]} " == *" $name "* ]] || return 1
  done
  for identifier in "${networks[@]}"; do
    [[ -n "$identifier" ]] || continue
    labels="$(docker network inspect --format \
      '{{ index .Labels "lecturesift.rehearsal" }}|{{ index .Labels "lecturesift.rehearsal.run" }}|{{ index .Labels "lecturesift.rehearsal.purpose" }}|{{.Name}}|{{.Internal}}|{{.Driver}}|{{.Scope}}' \
      "$identifier")" || return 1
    IFS='|' read -r rehearsal_label run_label purpose_label name internal driver scope <<<"$labels"
    [[ "$rehearsal_label" == "true" && "$run_label" == "$expected_rehearsal_suffix" && \
       "$internal" == "true" && "$driver" == "bridge" && "$scope" == "local" && \
       " ${allowed_networks[*]} " == *" $name "* ]] || return 1
    [[ "$name" != "lecturesift_rehearsal_migration" || \
       "$purpose_label" == "candidate-database-migration" ]] || return 1
    if [[ "$name" == "lecturesift_rehearsal_backend" || \
          "$name" == "lecturesift_rehearsal_migration" ]]; then
      endpoint_list="$(docker network inspect --format \
        '{{range .Containers}}{{println .Name}}{{end}}' "$identifier")" || return 1
      while IFS= read -r endpoint; do
        [[ -z "$endpoint" || " ${allowed_containers[*]} lecturesift-postgres-1 " == \
          *" $endpoint "* ]] || return 1
      done <<<"$endpoint_list"
      postgres_identity="$(docker container inspect --format \
        '{{ index .Config.Labels "com.docker.compose.project" }}|{{ index .Config.Labels "com.docker.compose.service" }}|{{.State.Running}}' \
        lecturesift-postgres-1 2>/dev/null || true)"
      [[ "$postgres_identity" == "lecturesift|postgres|true" ]] || return 1
    fi
  done
  for identifier in "${containers[@]}"; do
    [[ -z "$identifier" ]] || docker rm -f "$identifier" >/dev/null || return 1
  done
  for identifier in "${volumes[@]}"; do
    [[ -z "$identifier" ]] || docker volume rm "$identifier" >/dev/null || return 1
  done
  for identifier in "${networks[@]}"; do
    [[ -z "$identifier" ]] && continue
    name="$(docker network inspect --format '{{.Name}}' "$identifier")" || return 1
    if [[ "$name" == "lecturesift_rehearsal_backend" || \
          "$name" == "lecturesift_rehearsal_migration" ]]; then
      endpoint_list="$(docker network inspect --format \
        '{{range .Containers}}{{println .Name}}{{end}}' "$identifier")" || return 1
      if grep -Fqx lecturesift-postgres-1 <<<"$endpoint_list"; then
        docker network disconnect "$identifier" lecturesift-postgres-1 >/dev/null || return 1
      fi
    fi
    docker network rm "$identifier" >/dev/null || return 1
  done
  container_output="$(docker ps -aq --filter label=lecturesift.rehearsal=true)" || return 1
  volume_output="$(docker volume ls -q --filter label=lecturesift.rehearsal=true)" || return 1
  network_output="$(docker network ls -q --filter label=lecturesift.rehearsal=true)" || return 1
  [[ -z "$container_output" && -z "$volume_output" && -z "$network_output" ]]
}

verify_redis() {
  local after_epoch_ms="$1"
  python3 - "$state/before.redis.json" "$state/after.redis.json" \
    "$(<"$state/before.epoch-ms")" "$after_epoch_ms" <<'PY'
import json
import sys

def read(path):
    rows = json.load(open(path, encoding="utf-8"))
    parsed = {}
    for row in rows:
        key, kind, digest, ttl = row.split("|", 3)
        if key in parsed:
            raise SystemExit("duplicate confidential Redis key digest")
        parsed[key] = (kind, digest, int(ttl))
    return parsed

before, after = read(sys.argv[1]), read(sys.argv[2])
elapsed = max(0, int(sys.argv[4]) - int(sys.argv[3]))
tolerance = 5000
unexpected = set(after) - set(before)
if unexpected:
    raise SystemExit("Redis gained keys during rehearsal")
for key, (kind, digest, ttl) in before.items():
    expected = ttl - elapsed if ttl >= 0 else ttl
    current = after.get(key)
    if current is None:
        if ttl < 0 or expected > tolerance:
            raise SystemExit("Redis lost a non-expiring key during rehearsal")
        continue
    current_kind, current_digest, current_ttl = current
    if (kind, digest) != (current_kind, current_digest):
        raise SystemExit("Redis value/type changed during rehearsal")
    if ttl == -1 and current_ttl != -1:
        raise SystemExit("Redis persistence changed during rehearsal")
    if ttl >= 0 and (current_ttl < 0 or abs(current_ttl - expected) > tolerance):
        raise SystemExit("Redis expiry changed outside elapsed-time tolerance")
PY
}

write_admission() {
  local staged_app staged_proxy local_app local_proxy evidence_sha temporary
  local candidate_tree_sha local_tree_sha local_tree
  local stage_controller_sha stage_handoff_sha stage_handoff_nonce
  verify_trusted_handoff_for_admission || return 1
  staged_app="$(docker image inspect --format '{{.Id}}' "lecturesift-backend:staged-$revision")"
  staged_proxy="$(docker image inspect --format '{{.Id}}' "lecturesift-egress-proxy:staged-$revision")"
  local_app="$(docker image inspect --format '{{.Id}}' lecturesift-backend:local)"
  local_proxy="$(docker image inspect --format '{{.Id}}' lecturesift-egress-proxy:local)"
  for image in lecturesift-backend:local lecturesift-egress-proxy:local; do
    [[ "$(docker image inspect --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}' "$image")" == "$revision" ]] || return 1
    docker image inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$image" \
      | grep -Fqx "LECTURESIFT_BUILD_REVISION=$revision" || return 1
  done
  candidate_tree_sha="$(sed -n 's/^tree_sha256=//p' "$candidate_evidence")"
  local_tree="$trusted_handoff_state/rehearsed-local-source-tree"
  [[ ! -e "$local_tree" && ! -L "$local_tree" ]] || return 1
  install -d -o root -g root -m 0700 "$local_tree"
  verify_no_git_export_attributes "$root" "$revision" || return 1
  git -c core.attributesFile=/dev/null -c core.autocrlf=false -c core.eol=lf \
    -c tar.umask=0002 -C "$root" archive --format=tar "$revision" | \
    tar -xf - -C "$local_tree" || return 1
  local_tree_sha="$(python3 - "$local_tree" <<'PY'
import hashlib
import json
import pathlib
import stat
import sys

root = pathlib.Path(sys.argv[1])
inventory = []
for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
    relative = path.relative_to(root).as_posix()
    details = path.lstat()
    if stat.S_ISDIR(details.st_mode):
        inventory.append((relative, "d", 1, ""))
    elif stat.S_ISREG(details.st_mode):
        digest_builder = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest_builder.update(chunk)
        digest = digest_builder.hexdigest()
        inventory.append((relative, "f", int(bool(details.st_mode & 0o111)), digest))
    else:
        raise SystemExit("unsupported Git tree entry: " + relative)
payload = json.dumps(inventory, separators=(",", ":"), ensure_ascii=True).encode()
print(hashlib.sha256(payload).hexdigest())
PY
)" || return 1
  rm -rf -- "$local_tree"
  [[ "$candidate_tree_sha" =~ ^[0-9a-f]{64}$ && "$local_tree_sha" == "$candidate_tree_sha" ]] || \
    return 1
  evidence_sha="$(sha256sum "$candidate_evidence" | awk '{print $1}')"
  stage_controller_sha="$(sed -n 's/^trusted_stage_controller_sha256=//p' "$candidate_evidence")"
  stage_handoff_sha="$(sed -n 's/^trusted_stage_handoff_sha256=//p' "$candidate_evidence")"
  stage_handoff_nonce="$(sed -n 's/^trusted_stage_handoff_nonce=//p' "$candidate_evidence")"
  [[ "$stage_controller_sha" =~ ^[0-9a-f]{64}$ && \
     "$stage_handoff_sha" =~ ^[0-9a-f]{64}$ && \
     "$stage_handoff_nonce" =~ ^[0-9a-f]{32}$ ]] || return 1
  install -d -o root -g root -m 0700 "$admission_root"
  temporary="$(mktemp -- "$admission_root/.admission.XXXXXXXX")"
  chmod 0600 "$temporary"
  chown root:root "$temporary"
  {
    printf 'version=5\nstatus=verified\nrevision=%s\ncandidate_evidence_sha256=%s\n' "$revision" "$evidence_sha"
    printf 'trusted_controller_sha256=%s\ntrusted_controller_handoff_sha256=%s\n' \
      "$trusted_controller_sha256" "$trusted_handoff_sha256"
    printf 'trusted_stage_controller_sha256=%s\ntrusted_stage_handoff_sha256=%s\n' \
      "$stage_controller_sha" "$stage_handoff_sha"
    printf 'trusted_stage_handoff_nonce=%s\n' "$stage_handoff_nonce"
    printf 'staged_app_image_id=%s\nstaged_proxy_image_id=%s\n' "$staged_app" "$staged_proxy"
    printf 'rehearsed_local_app_image_id=%s\nrehearsed_local_proxy_image_id=%s\n' "$local_app" "$local_proxy"
    printf 'candidate_tree_sha256=%s\nrehearsed_local_source_tree_sha256=%s\n' \
      "$candidate_tree_sha" "$local_tree_sha"
    printf 'source_tree_equivalent=true\n'
    printf 'base_postgres_unchanged=true\nbase_redis_unchanged=true\nroles_unchanged=true\n'
    printf 'database_inventory_unchanged=true\ngrants_unchanged=true\n'
    printf 'containers_unchanged=true\nvolumes_unchanged=true\nnetworks_unchanged=true\nlisteners_unchanged=true\n'
    cat "$artifact_hashes"
  } >"$temporary"
  sync -f "$temporary"
  mv -fT -- "$temporary" "$admission"
  chmod 0600 "$admission"
  chown root:root "$admission"
  sync -f "$admission_root"
  if ! python3 "$root/deploy/validate_rehearsal_admission.py" --root "$root" --expected-revision "$revision" >/dev/null; then
    rm -f -- "$admission"
    sync -f "$admission_root"
    return 1
  fi
  admission_written=true
}

verify_trusted_handoff_for_admission() {
  [[ -n "$trusted_handoff_state" && -n "$trusted_handoff_consumed" && \
     ! -e "$trusted_handoff" && ! -L "$trusted_handoff" && \
     -f "$trusted_handoff_consumed" && ! -L "$trusted_handoff_consumed" && \
     "$(realpath -e -- "$trusted_handoff_consumed")" == "$trusted_handoff_consumed" && \
     "$(stat -c '%u:%g:%a' -- "$trusted_handoff_consumed")" == "0:0:600" && \
     "$(sha256sum "$trusted_handoff_consumed" | awk '{print $1}')" == \
       "$trusted_handoff_sha256" && \
     "$(sha256sum "$root/deploy/run_exact_rehearsal.sh" | awk '{print $1}')" == \
       "$(python3 - "$trusted_handoff_consumed" <<'PY'
from pathlib import Path
import sys
for line in Path(sys.argv[1]).read_text(encoding="ascii").splitlines():
    if line.startswith("orchestrator_sha256="):
        print(line.split("=", 1)[1])
        break
PY
)" && \
     "$(sha256sum "$trusted_controller" | awk '{print $1}')" == \
       "$trusted_controller_sha256" ]]
}

write_handoff_completion() {
  local completion="$trusted_handoff_state/handoff.completed"
  local temporary admission_sha256
  verify_trusted_handoff_for_admission || return 1
  [[ "$admission_written" == "true" && -f "$admission" && ! -L "$admission" ]] || \
    return 1
  admission_sha256="$(sha256sum "$admission" | awk '{print $1}')"
  [[ "$admission_sha256" =~ ^[0-9a-f]{64}$ ]] || return 1
  [[ ! -e "$completion" && ! -L "$completion" ]] || return 1
  temporary="$(mktemp -- "$trusted_handoff_state/.completion.XXXXXXXX")"
  chmod 0600 "$temporary"
  chown root:root "$temporary"
  {
    printf 'version=1\nstatus=admitted\nrevision=%s\n' "$revision"
    printf 'nonce=%s\nhandoff_sha256=%s\nadmission_sha256=%s\n' \
      "$trusted_handoff_nonce" "$trusted_handoff_sha256" "$admission_sha256"
  } >"$temporary"
  sync -f "$temporary"
  mv -T -- "$temporary" "$completion"
  sync -f "$trusted_handoff_state"
}

outer_exit() {
  local original_status="$?" post_failed=false after_epoch_ms
  trap - EXIT
  set +e
  docker exec lecturesift-postgres-1 rm -f -- "$main_manifest_in_container" >/dev/null 2>&1 || true
  cleanup_labeled_residue || post_failed=true
  check_provenance_residue || post_failed=true
  if [[ "$baseline_ready" == "true" ]]; then
    date -u +%s%3N >"$state/after.epoch-ms"
    after_epoch_ms="$(<"$state/after.epoch-ms")"
    snapshot_inventory "$state/after" || post_failed=true
    snapshot_roles "$state/after.roles" || post_failed=true
    snapshot_databases "$state/after.databases" || post_failed=true
    snapshot_grants "$state/after.grants" || post_failed=true
    snapshot_redis "$state/after.redis.json" || post_failed=true
    snapshot_manifest "$state/after.manifest" "$state/after.manifest.canonical" || post_failed=true
    cmp --silent "$state/before.containers" "$state/after.containers" || post_failed=true
    cmp --silent "$state/before.volumes" "$state/after.volumes" || post_failed=true
    cmp --silent "$state/before.networks" "$state/after.networks" || post_failed=true
    cmp --silent "$state/before.listeners" "$state/after.listeners" || post_failed=true
    cmp --silent "$state/before.postgres-container" "$state/after.postgres-container" || post_failed=true
    cmp --silent "$state/before.redis-container" "$state/after.redis-container" || post_failed=true
    cmp --silent "$state/before.roles" "$state/after.roles" || post_failed=true
    cmp --silent "$state/before.databases" "$state/after.databases" || post_failed=true
    cmp --silent "$state/before.grants" "$state/after.grants" || post_failed=true
    cmp --silent "$state/before.manifest.canonical" "$state/after.manifest.canonical" || post_failed=true
    cmp --silent "$state/before.manifest.schema-contract" "$state/after.manifest.schema-contract" || post_failed=true
    verify_redis "$after_epoch_ms" || post_failed=true
  fi
  docker exec lecturesift-postgres-1 rm -f -- "$main_manifest_in_container" >/dev/null 2>&1 || true
  if [[ "$original_status" == "0" && "$rehearsal_succeeded" == "true" && "$post_failed" == "false" ]]; then
    if ! write_admission || ! write_handoff_completion; then
      post_failed=true
    fi
  fi
  if [[ "$original_status" != "0" || "$post_failed" == "true" ]]; then
    if [[ "$admission_written" == "true" ]]; then
      rm -f -- "$admission"
      sync -f "$admission_root" >/dev/null 2>&1 || true
    fi
    echo "EXACT_REHEARSAL_POSTCONDITION_FAILED|state=$state" >&2
    exit 1
  fi
  rm -rf -- "$state"
  echo "EXACT_REHEARSAL_ADMITTED|revision=$revision|evidence=$admission"
  exit 0
}
trap outer_exit EXIT

snapshot_rehearsal_runs "$state/rehearsal-runs.before"
run_inner_rehearsal
snapshot_rehearsal_runs "$state/rehearsal-runs.after"
mapfile -t new_rehearsal_runs < <(comm -13 "$state/rehearsal-runs.before" "$state/rehearsal-runs.after")
[[ "${#new_rehearsal_runs[@]}" == "1" ]] || fail ambiguous-inner-rehearsal-artifacts
[[ "${new_rehearsal_runs[0]}" =~ ^[0-9]{8}T[0-9]{6}Z$ ]] || fail unsafe-inner-rehearsal-artifact-name
[[ "${new_rehearsal_runs[0]}" == "$expected_rehearsal_run_id" ]] || \
  fail unexpected-inner-rehearsal-run
inner_run_dir="$rehearsal_run_root/${new_rehearsal_runs[0]}"
artifact_hashes="$state/rehearsal-artifact-hashes.env"
python3 "$root/deploy/validate_rehearsal_artifacts.py" --root "$root" --run-dir "$inner_run_dir" --revision "$revision" >"$artifact_hashes" || fail invalid-inner-rehearsal-artifacts
[[ -s "$artifact_hashes" && ! -L "$artifact_hashes" ]] || fail missing-inner-rehearsal-artifact-hashes

role_login_digest="$(
  LECTURESIFT_ROOT="$root" \
    LECTURESIFT_DB_ENV_FILE="$postgres_env" \
    bash "$root/deploy/postgres_role_login_probe.sh"
)" || fail trusted-postgres-role-login-proof
[[ "$role_login_digest" =~ ^[0-9a-f]{64}$ ]] || \
  fail invalid-postgres-role-login-proof-digest
rehearsal_succeeded=true
