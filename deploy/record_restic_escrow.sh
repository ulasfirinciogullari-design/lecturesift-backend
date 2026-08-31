#!/usr/bin/env bash
set -euo pipefail

# Record non-secret, repository-bound evidence only after an operator has
# recovered the encrypted password from a separate host and used it to open
# this exact restic repository. The ciphertext and plaintext stay off the VPS.

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run this escrow evidence command as root." >&2
  exit 1
fi

umask 077
set +x

ENV_FILE="${LECTURESIFT_RESTIC_ENV_FILE:-/etc/lecturesift/restic.env}"
EVIDENCE_ROOT="/var/lib/lecturesift/recovery-drills"
MARKER="$EVIDENCE_ROOT/restic-password-escrow.ok"
CONFIRM="${LECTURESIFT_RESTIC_ESCROW_CONFIRM:-}"
RECOVERY_TESTED="${LECTURESIFT_RESTIC_ESCROW_RECOVERY_TESTED:-}"
CIPHERTEXT_SHA256="${LECTURESIFT_RESTIC_ESCROW_CIPHERTEXT_SHA256:-}"
RECOVERED_KEY_ID="${LECTURESIFT_RESTIC_ESCROW_KEY_ID:-}"

fail() {
  echo "Restic escrow evidence failed: $*" >&2
  exit 1
}

[[ "$CONFIRM" == "YES" ]] || \
  fail "set LECTURESIFT_RESTIC_ESCROW_CONFIRM=YES only after off-host escrow is complete"
[[ "$RECOVERY_TESTED" == "YES" ]] || \
  fail "set LECTURESIFT_RESTIC_ESCROW_RECOVERY_TESTED=YES only after a decrypt/open recovery test"
[[ "$CIPHERTEXT_SHA256" =~ ^[[:xdigit:]]{64}$ ]] || \
  fail "LECTURESIFT_RESTIC_ESCROW_CIPHERTEXT_SHA256 must hash the encrypted off-host artifact"
[[ "$RECOVERED_KEY_ID" =~ ^[[:xdigit:]]{64}$ ]] || \
  fail "LECTURESIFT_RESTIC_ESCROW_KEY_ID must be the current key opened during the off-host test"

for command_name in restic python3 realpath stat install mktemp; do
  command -v "$command_name" >/dev/null 2>&1 || fail "missing required command: $command_name"
done

[[ -f "$ENV_FILE" && ! -L "$ENV_FILE" ]] || fail "$ENV_FILE must be a regular non-symlink file"
[[ "$(stat -c '%u' -- "$ENV_FILE")" == "0" ]] || fail "$ENV_FILE must be owned by root"
env_mode="$(stat -c '%a' -- "$ENV_FILE")"
(( (8#$env_mode & 8#077) == 0 )) || fail "$ENV_FILE must be private to root"

# shellcheck disable=SC1090
source "$ENV_FILE" >/dev/null 2>&1 || fail "$ENV_FILE could not be loaded"
[[ -n "${RESTIC_REPOSITORY:-}" && -n "${RESTIC_PASSWORD:-}" ]] || \
  fail "restic repository/password is not configured"
case "$RESTIC_REPOSITORY" in
  s3:*) ;;
  *) fail "the production repository must be off-site S3-compatible storage" ;;
esac
[[ -n "${RESTIC_AWS_ACCESS_KEY_ID:-}" && -n "${RESTIC_AWS_SECRET_ACCESS_KEY:-}" ]] || \
  fail "restic S3 credentials are incomplete"
export RESTIC_REPOSITORY RESTIC_PASSWORD
export AWS_ACCESS_KEY_ID="$RESTIC_AWS_ACCESS_KEY_ID"
export AWS_SECRET_ACCESS_KEY="$RESTIC_AWS_SECRET_ACCESS_KEY"

repository_id_sha256="$(restic cat config 2>/dev/null | python3 -c \
  'import hashlib,json,sys; value=str(json.load(sys.stdin).get("id") or ""); raise SystemExit(1) if not value else None; print(hashlib.sha256(value.encode("ascii")).hexdigest())')" || \
  fail "the configured repository could not be opened"
current_key_hint="$(restic key list --json 2>/dev/null | python3 -c '
import json, re, sys
keys = json.load(sys.stdin)
current = [str(item.get("id") or "") for item in keys if item.get("current") is True]
if len(current) != 1 or re.fullmatch(r"[0-9a-fA-F]{8,64}", current[0]) is None:
    raise SystemExit(1)
print(current[0].lower())
')" || fail "the current restic key hint could not be determined"
current_key_id="$(restic list keys --quiet 2>/dev/null | python3 -c '
import re, sys
hint = sys.argv[1].lower()
matches = [line.strip().lower() for line in sys.stdin if re.fullmatch(r"[0-9a-fA-F]{64}", line.strip()) and line.strip().lower().startswith(hint)]
if len(matches) != 1:
    raise SystemExit(1)
print(matches[0])
' "$current_key_hint")" || fail "the current restic key identity could not be resolved uniquely"
[[ "${RECOVERED_KEY_ID,,}" == "$current_key_id" ]] || \
  fail "the off-host recovered key does not match the key used by this production password"
unset RESTIC_REPOSITORY RESTIC_PASSWORD RESTIC_AWS_ACCESS_KEY_ID RESTIC_AWS_SECRET_ACCESS_KEY
unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY

root_normalized="$(realpath -m -- "$EVIDENCE_ROOT")"
[[ "$root_normalized" == "$EVIDENCE_ROOT" ]] || fail "the evidence root resolves through a symlink"
install -d -o root -g root -m 0750 -- "$EVIDENCE_ROOT"
[[ ! -L "$EVIDENCE_ROOT" && "$(realpath -e -- "$EVIDENCE_ROOT")" == "$EVIDENCE_ROOT" ]] || \
  fail "the evidence root is unsafe"

marker_tmp="$(mktemp -- "$EVIDENCE_ROOT/.restic-password-escrow-XXXXXXXX")"
{
  printf 'status=verified\n'
  printf 'verified_at_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf 'escrow_type=encrypted-off-host\n'
  printf 'recovery_test=decrypt-and-repository-opened\n'
  printf 'ciphertext_sha256=%s\n' "${CIPHERTEXT_SHA256,,}"
  printf 'restic_key_id=%s\n' "$current_key_id"
  printf 'repository_id_sha256=%s\n' "$repository_id_sha256"
} >"$marker_tmp"
chmod 0644 "$marker_tmp"
mv -- "$marker_tmp" "$MARKER"

echo "Repository-bound off-host restic escrow evidence recorded without storing a secret."
