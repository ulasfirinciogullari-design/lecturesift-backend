#!/usr/bin/env bash
set -euo pipefail

# Recover one encrypted configuration payload into an isolated root-only
# directory.  This script never writes to /etc/lecturesift or /opt/lecturesift;
# installation remains a deliberate operator action after review.

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Configuration recovery must be run as root." >&2
  exit 1
fi
if [[ $# -ne 1 || ! "$1" =~ ^[[:xdigit:]]{8,64}$ ]]; then
  echo "Usage: $0 RESTIC_SNAPSHOT_ID" >&2
  exit 1
fi

umask 077
set +x

SNAPSHOT_ID="$1"
ROOT_DIR="${LECTURESIFT_ROOT:-/opt/lecturesift}"
RESTIC_ENV_FILE="/etc/lecturesift/restic.env"
SNAPSHOT_TOOL="$ROOT_DIR/deploy/configuration_snapshot.py"
RECOVERY_ROOT="/var/lib/lecturesift/configuration-recovery"
BACKUP_LOCK_ROOT="/var/backups/lecturesift"
RESTIC_HOST="lecturesift-production"
RESTIC_CONFIGURATION_PATH="/var/backups/lecturesift/.restic-staging/current/configuration-snapshot-v1"
MAX_CONFIGURATION_BYTES=134217728
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
RUN_DIR=""
FINAL_DIR=""
FINAL_STAGING=""

fail() {
  echo "Configuration recovery failed: $*" >&2
  exit 1
}

for command_name in restic realpath stat install mktemp find python3 flock mv rm; do
  command -v "$command_name" >/dev/null 2>&1 || fail "missing required command: $command_name"
done

[[ -f "$RESTIC_ENV_FILE" && ! -L "$RESTIC_ENV_FILE" ]] || \
  fail "$RESTIC_ENV_FILE must be a regular non-symlink file"
[[ "$(stat -c '%u' -- "$RESTIC_ENV_FILE")" == "0" && \
   "$(stat -c '%a' -- "$RESTIC_ENV_FILE")" == "600" ]] || \
  fail "$RESTIC_ENV_FILE must be root-owned with mode 0600"
[[ -f "$SNAPSHOT_TOOL" && ! -L "$SNAPSHOT_TOOL" && \
   "$(stat -c '%u' -- "$SNAPSHOT_TOOL")" == "0" ]] || \
  fail "the configuration snapshot verifier is missing or unsafe"
snapshot_tool_mode="$(stat -c '%a' -- "$SNAPSHOT_TOOL")"
(( (8#$snapshot_tool_mode & 8#022) == 0 )) || \
  fail "the configuration snapshot verifier must not be group/other writable"

recovery_parent="$(dirname -- "$RECOVERY_ROOT")"
[[ -d "$recovery_parent" && ! -L "$recovery_parent" && \
   "$(realpath -e -- "$recovery_parent")" == "$recovery_parent" ]] || \
  fail "the configuration recovery parent is unsafe"
[[ "$(realpath -m -- "$RECOVERY_ROOT")" == "$RECOVERY_ROOT" ]] || \
  fail "the configuration recovery root resolves through an unsafe path"
install -d -o root -g root -m 0700 -- "$RECOVERY_ROOT"
[[ ! -L "$RECOVERY_ROOT" && "$(realpath -e -- "$RECOVERY_ROOT")" == "$RECOVERY_ROOT" && \
   "$(stat -c '%u' -- "$RECOVERY_ROOT")" == "0" && \
   "$(stat -c '%a' -- "$RECOVERY_ROOT")" == "700" ]] || \
  fail "the configuration recovery root must be a private root-owned real directory"

install -d -o root -g root -m 0700 -- "$BACKUP_LOCK_ROOT"
[[ ! -L "$BACKUP_LOCK_ROOT" && "$(realpath -e -- "$BACKUP_LOCK_ROOT")" == "$BACKUP_LOCK_ROOT" && \
   "$(stat -c '%u' -- "$BACKUP_LOCK_ROOT")" == "0" ]] || \
  fail "the shared backup/recovery lock root is unsafe"
backup_lock_mode="$(stat -c '%a' -- "$BACKUP_LOCK_ROOT")"
(( (8#$backup_lock_mode & 8#077) == 0 )) || \
  fail "the shared backup/recovery lock root must be private to root"
exec 9>"$BACKUP_LOCK_ROOT/.backup.lock"
flock -n 9 || fail "a backup, restore, or configuration recovery is already active"

cleanup() {
  local resolved
  set +e
  unset RESTIC_REPOSITORY RESTIC_PASSWORD RESTIC_CACHE_DIR
  unset RESTIC_AWS_ACCESS_KEY_ID RESTIC_AWS_SECRET_ACCESS_KEY
  unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY
  if [[ -n "${RUN_DIR:-}" && -e "$RUN_DIR" ]]; then
    resolved="$(realpath -e -- "$RUN_DIR")" || return 0
    case "$resolved" in
      "$RECOVERY_ROOT"/.incomplete-configuration-recovery-*)
        rm -rf --one-file-system -- "$resolved"
        ;;
      *)
        echo "Refusing unsafe configuration recovery cleanup target." >&2
        ;;
    esac
  fi
}
trap cleanup EXIT

RUN_DIR="$(mktemp -d -- "$RECOVERY_ROOT/.incomplete-configuration-recovery-$STAMP-XXXXXXXX")"
chmod 0700 "$RUN_DIR"
EXTRACT_ROOT="$RUN_DIR/extracted"
RESTIC_CACHE_DIR="$RUN_DIR/restic-cache"
RESTIC_LOG="$RUN_DIR/restic.log"
install -d -o root -g root -m 0700 -- "$EXTRACT_ROOT" "$RESTIC_CACHE_DIR"

# A malicious shell statement in a root-only env file must not print secrets to
# either the operator terminal or a plaintext diagnostic.
# shellcheck disable=SC1090
if ! source "$RESTIC_ENV_FILE" >/dev/null 2>&1; then
  set +x
  fail "$RESTIC_ENV_FILE could not be loaded"
fi
set +x
[[ -n "${RESTIC_REPOSITORY:-}" && -n "${RESTIC_PASSWORD:-}" ]] || \
  fail "the Restic repository or password is not configured"
case "$RESTIC_REPOSITORY" in
  s3:*/lecturesift-production-backups/restic) ;;
  *) fail "configuration recovery requires the dedicated production Restic repository" ;;
esac
[[ -n "${RESTIC_AWS_ACCESS_KEY_ID:-}" && \
   -n "${RESTIC_AWS_SECRET_ACCESS_KEY:-}" ]] || \
  fail "the production Restic storage credentials are not configured"
export RESTIC_REPOSITORY RESTIC_PASSWORD RESTIC_CACHE_DIR
export AWS_ACCESS_KEY_ID="$RESTIC_AWS_ACCESS_KEY_ID"
export AWS_SECRET_ACCESS_KEY="$RESTIC_AWS_SECRET_ACCESS_KEY"

restic cat config >"$RUN_DIR/restic-config.json" 2>"$RESTIC_LOG" || \
  fail "the configured Restic repository is inaccessible"
restic snapshots --json --host "$RESTIC_HOST" --tag lecturesift --tag production \
  "$SNAPSHOT_ID" >"$RUN_DIR/snapshot.json" 2>"$RESTIC_LOG" || \
  fail "the selected production snapshot could not be read"
python3 - "$RUN_DIR/snapshot.json" "$SNAPSHOT_ID" <<'PY' || \
  fail "the selected snapshot identity, host, or tags are invalid"
import json
from pathlib import Path
import sys

items = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
requested = sys.argv[2].lower()
if not isinstance(items, list) or len(items) != 1:
    raise SystemExit(1)
item = items[0]
snapshot_id = str(item.get("id") or "").lower()
tags = set(item.get("tags") or [])
if not snapshot_id.startswith(requested) or item.get("hostname") != "lecturesift-production":
    raise SystemExit(1)
if not {"lecturesift", "production"}.issubset(tags):
    raise SystemExit(1)
PY

# Inspect the selected subtree before materializing it.  A crafted snapshot
# cannot use the extraction helper to consume unbounded host storage.
restic ls --json "$SNAPSHOT_ID" >"$RUN_DIR/snapshot-files.jsonl" 2>"$RESTIC_LOG" || \
  fail "the selected snapshot file list could not be inspected"
configuration_size="$(python3 - "$RUN_DIR/snapshot-files.jsonl" \
  "$RESTIC_CONFIGURATION_PATH" "$MAX_CONFIGURATION_BYTES" <<'PY'
import json
from pathlib import Path
import sys

listing = Path(sys.argv[1])
prefix = sys.argv[2].rstrip("/") + "/"
maximum = int(sys.argv[3])
count = 0
total = 0
for raw_line in listing.read_text(encoding="utf-8").splitlines():
    if not raw_line.strip():
        continue
    item = json.loads(raw_line)
    node = item.get("node") if isinstance(item, dict) else None
    if not isinstance(node, dict) and isinstance(item, dict) and item.get("struct_type") == "node":
        node = item
    if not isinstance(node, dict) or node.get("type") != "file":
        continue
    path = str(node.get("path") or item.get("path") or "")
    if not path.startswith(prefix):
        continue
    size = node.get("size")
    if not isinstance(size, int) or isinstance(size, bool) or size < 0:
        raise SystemExit(1)
    count += 1
    total += size
if count < 3 or total <= 0 or total > maximum:
    raise SystemExit(1)
print(total)
PY
)" || fail "the encrypted configuration subtree is missing or exceeds 128 MiB"
[[ "$configuration_size" =~ ^[1-9][0-9]*$ ]] || \
  fail "the encrypted configuration subtree size is invalid"

restic restore "$SNAPSHOT_ID" --target "$EXTRACT_ROOT" --verify \
  --include "$RESTIC_CONFIGURATION_PATH/**" >"$RESTIC_LOG" 2>&1 || \
  fail "the encrypted configuration subtree could not be restored and verified"

# Credentials are no longer needed.  They are removed before parsing or moving
# any recovered material and are never written into operator evidence.
unset RESTIC_REPOSITORY RESTIC_PASSWORD
export -n RESTIC_CACHE_DIR
unset RESTIC_AWS_ACCESS_KEY_ID RESTIC_AWS_SECRET_ACCESS_KEY
unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY

SNAPSHOT_ROOT="$EXTRACT_ROOT${RESTIC_CONFIGURATION_PATH}"
[[ -d "$SNAPSHOT_ROOT" && ! -L "$SNAPSHOT_ROOT" ]] || \
  fail "the expected configuration snapshot directory was not restored"
python3 "$SNAPSHOT_TOOL" verify --snapshot-root "$SNAPSHOT_ROOT" \
  --deploy-root "$ROOT_DIR" --quiet || \
  fail "the recovered configuration snapshot failed its manifest verification"

FINAL_DIR="$RECOVERY_ROOT/configuration-recovery-$STAMP-${SNAPSHOT_ID:0:12}"
[[ ! -e "$FINAL_DIR" && ! -L "$FINAL_DIR" ]] || \
  fail "the final configuration recovery directory already exists"
FINAL_STAGING="$RUN_DIR/final"
install -d -o root -g root -m 0700 -- "$FINAL_STAGING"
mv -- "$SNAPSHOT_ROOT" "$FINAL_STAGING/snapshot"
cat >"$FINAL_STAGING/OPERATOR_STEPS.txt" <<EOF
This directory is an isolated, verified recovery copy. No live configuration was changed.

Review the manifest and compare the recovered identity files with the checked-out release.
Install each required environment file explicitly, for example:
  install -o root -g root -m 0600 -- \
    '$FINAL_DIR/snapshot/files/etc/lecturesift/runtime.env' \
    '/etc/lecturesift/runtime.env'

Repeat that explicit install step only for the six allowlisted *.env files you intend to recover.
Never copy the entire snapshot tree over /etc or /opt. After review, run:
  python3 '$ROOT_DIR/deploy/generate_role_envs.py' --check
  '$ROOT_DIR/deploy/preflight.sh'
Then use the documented validated reload procedure; do not blindly restart services.
EOF
chown root:root "$FINAL_STAGING/OPERATOR_STEPS.txt"
chmod 0600 "$FINAL_STAGING/OPERATOR_STEPS.txt"
rm -rf --one-file-system -- "$EXTRACT_ROOT"
rm -rf --one-file-system -- "$RESTIC_CACHE_DIR" "$RESTIC_LOG" \
  "$RUN_DIR/restic-config.json" \
  "$RUN_DIR/snapshot.json" "$RUN_DIR/snapshot-files.jsonl"
mv -- "$FINAL_STAGING" "$FINAL_DIR"
rm -rf --one-file-system -- "$RUN_DIR"
RUN_DIR=""

echo "Verified configuration extracted to: $FINAL_DIR"
echo "No file under /etc/lecturesift or /opt/lecturesift was modified."
