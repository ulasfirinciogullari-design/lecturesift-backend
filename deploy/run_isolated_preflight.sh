#!/usr/bin/env bash
set -Eeuo pipefail
set +x
umask 077

revision="${LECTURESIFT_EXPECTED_PREFLIGHT_REVISION:-}"
runtime_source="${LECTURESIFT_MASTER_RUNTIME_ENV:-/etc/lecturesift/runtime.env}"
database_source="${LECTURESIFT_MASTER_DATABASE_ENV:-/etc/lecturesift/postgres.env}"
staging_base=/run/lecturesift-staging

fail() {
  echo "ISOLATED_PREFLIGHT_FAILED|$*" >&2
  exit 1
}

[[ "$(id -u)" == "0" ]] || fail root-required
[[ "$revision" =~ ^[0-9a-f]{40}$ ]] || fail bad-revision
script_path="$(realpath -e -- "${BASH_SOURCE[0]}")"
root="$(realpath -e -- "$(dirname -- "$script_path")/..")"
[[ "$root" == "/srv/lecturesift/worktrees/$revision" ]] || fail unexpected-worktree
[[ ! -L "$root" && "$(git -C "$root" rev-parse --show-toplevel)" == "$root" ]] || \
  fail unsafe-worktree
[[ "$(git -C "$root" rev-parse --verify 'HEAD^{commit}')" == "$revision" ]] || fail bad-head
[[ -z "$(git -C "$root" status --porcelain=v1 --untracked-files=all)" ]] || fail dirty-source
[[ -z "$(find "$root" -xdev \( ! -user root -o -perm /022 \) -print -quit)" ]] || \
  fail unsafe-source-metadata

check_private_source() {
  local source="$1" mode
  [[ -f "$source" && ! -L "$source" && "$(realpath -e -- "$source")" == "$source" ]] || \
    fail unsafe-source
  [[ "$(stat -c '%u:%g' -- "$source")" == "0:0" ]] || fail unsafe-source-owner
  mode="$(stat -c '%a' -- "$source")"
  [[ "$mode" == "400" || "$mode" == "600" ]] || fail unsafe-source-mode
}
check_private_source "$runtime_source"
check_private_source "$database_source"

if [[ -e "$staging_base" || -L "$staging_base" ]]; then
  [[ -d "$staging_base" && ! -L "$staging_base" && \
     "$(realpath -e -- "$staging_base")" == "$staging_base" ]] || fail unsafe-staging-base
  [[ "$(stat -c '%u:%g:%a' -- "$staging_base")" == "0:0:700" ]] || \
    fail unsafe-staging-base-metadata
else
  install -d -o root -g root -m 0700 -- "$staging_base"
fi
state="$(mktemp -d -- "$staging_base/$revision.XXXXXXXX")"
[[ ! -L "$state" && "$(realpath -e -- "$state")" == "$state" && \
   "$(stat -c '%u:%g:%a' -- "$state")" == "0:0:700" ]] || fail unsafe-staging-state
cleanup() {
  local status="$?"
  trap - EXIT
  case "$(realpath -m -- "$state")" in
    "$staging_base/$revision."????????) rm -rf -- "$state" ;;
    *) echo "ISOLATED_PREFLIGHT_REFUSED_UNSAFE_CLEANUP" >&2; status=1 ;;
  esac
  exit "$status"
}
trap cleanup EXIT

# Parse as data, never as shell.  Generated values are shell-quoted only so the
# already-tracked preflight can consume its normal dotenv contract.
python3 - "$runtime_source" "$state/runtime.env" "$database_source" "$state/postgres.env" <<'PY'
import os
import re
import shlex
import sys
import tempfile
from pathlib import Path

blocked = {
    "LECTURESIFT_ROOT", "LECTURESIFT_ENV_FILE", "LECTURESIFT_API_ENV_FILE",
    "LECTURESIFT_WORKER_ENV_FILE", "LECTURESIFT_INSTAGRAM_ENV_FILE",
    "LECTURESIFT_DB_ENV_FILE", "LECTURESIFT_RESTIC_ENV_FILE",
    "LECTURESIFT_RELEASE_ENV_FILE", "ROOT_DIR", "ENV_FILE", "API_ENV_FILE",
    "WORKER_ENV_FILE", "INSTAGRAM_ENV_FILE", "DB_ENV_FILE", "RESTIC_ENV_FILE",
    "RELEASE_ENV_FILE", "ROLE_ENV_GENERATOR", "RELEASE_HELPER",
    "CUTOVER_EVIDENCE_TOOL", "LECTURESIFT_PREFLIGHT_CONTEXT",
    "LECTURESIFT_RECOVERY_BOOTSTRAP_OVERRIDE", "LECTURESIFT_BOOTSTRAP_INFRASTRUCTURE_ONLY",
}
assignment = re.compile(r"^[ \t]*(?:export[ \t]+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$")

def sanitize(source: Path) -> str:
    seen = set()
    output = []
    text = source.read_text(encoding="utf-8")
    if "\x00" in text:
        raise SystemExit("UNSAFE_DOTENV_NUL")
    for number, raw in enumerate(text.splitlines(), 1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        match = assignment.fullmatch(raw)
        if not match:
            raise SystemExit(f"UNSAFE_DOTENV_SYNTAX|line={number}")
        name, rhs = match.groups()
        if name in seen or name in blocked:
            raise SystemExit(f"UNSAFE_DOTENV_KEY|line={number}|key={name}")
        lexer = shlex.shlex(rhs, posix=True)
        lexer.whitespace_split = True
        lexer.commenters = ""
        words = list(lexer)
        if len(words) != 1:
            raise SystemExit(f"UNSAFE_DOTENV_VALUE|line={number}|key={name}")
        seen.add(name)
        output.append(f"{name}={shlex.quote(words[0])}")
    if not output:
        raise SystemExit("EMPTY_DOTENV")
    return "\n".join(output) + "\n"

for source_name, destination_name in zip(sys.argv[1::2], sys.argv[2::2]):
    destination = Path(destination_name)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        os.fchown(descriptor, 0, 0)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(sanitize(Path(source_name)))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
PY

env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin LANG=C.UTF-8 \
  LECTURESIFT_ROOT="$root" \
  LECTURESIFT_ENV_FILE="$state/runtime.env" \
  LECTURESIFT_API_ENV_FILE="$state/api.env" \
  LECTURESIFT_WORKER_ENV_FILE="$state/worker.env" \
  LECTURESIFT_INSTAGRAM_ENV_FILE="$state/instagram.env" \
  LECTURESIFT_DB_ENV_FILE="$state/postgres.env" \
  LECTURESIFT_RESTIC_ENV_FILE="$state/restic.absent" \
  LECTURESIFT_RELEASE_ENV_FILE="$state/release.env" \
  LECTURESIFT_PREFLIGHT_CONTEXT=bootstrap-infrastructure \
  LECTURESIFT_RECOVERY_BOOTSTRAP_OVERRIDE=YES \
  LECTURESIFT_BOOTSTRAP_INFRASTRUCTURE_ONLY=YES \
  bash "$root/deploy/preflight.sh"
echo "ISOLATED_PREFLIGHT_OK|revision=$revision"
