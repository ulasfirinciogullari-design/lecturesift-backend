#!/usr/bin/env bash
set -euo pipefail
umask 077
set +x

# Arm the one-time provider first-start fence from fresh target evidence, then
# consume it only after systemd's full-stack health wait succeeds.

[[ "$(id -u)" == "0" ]] || {
  echo "Provider first-start verification must run as root." >&2
  exit 1
}
MODE="${1:-}"
[[ "$MODE" == "arm" || "$MODE" == "complete" ]] || {
  echo "Usage: $0 arm|complete" >&2
  exit 1
}

ROOT_DIR="${LECTURESIFT_ROOT:-/opt/lecturesift}"
DB_ENV_FILE="${LECTURESIFT_DB_ENV_FILE:-/etc/lecturesift/postgres.env}"
RELEASE_ENV_FILE="${LECTURESIFT_RELEASE_ENV_FILE:-/run/lecturesift/release.env}"
EVIDENCE_TOOL="$ROOT_DIR/deploy/provider_cutover_evidence.py"
DATA_MANIFEST="$ROOT_DIR/deploy/rehearsal_manifest.sql"
SCHEMA_CONTRACT="$ROOT_DIR/deploy/schema_contract_payment_provider_sessions_v1.txt"
SCHEMA_VERIFIER="$ROOT_DIR/deploy/verify_schema_transition.py"
SECURITY_MANIFEST="$ROOT_DIR/deploy/postgres_security_manifest.sql"
SECURITY_VALIDATOR="$ROOT_DIR/deploy/validate_postgres_security_manifest.py"
ROLE_LOGIN_PROBE="$ROOT_DIR/deploy/postgres_role_login_probe.sh"
REDIS_MANIFEST_TOOL="$ROOT_DIR/deploy/target_redis_manifest.sh"
LOCK_ROOT="/var/backups/lecturesift"
WORK_DIR=""

fail() {
  echo "Provider first-start verification failed: $*" >&2
  exit 1
}

check_private() {
  local path="$1" label="$2" mode
  [[ -f "$path" && ! -L "$path" && "$(stat -c '%u' -- "$path")" == "0" ]] ||
    fail "$label must be a root-owned regular non-symlink file"
  mode="$(stat -c '%a' -- "$path")"
  [[ "$mode" == "400" || "$mode" == "600" ]] ||
    fail "$label must have mode 0400 or 0600"
}

[[ "$DB_ENV_FILE" == "/etc/lecturesift/postgres.env" ]] ||
  fail "the database environment path is fixed"
[[ "$RELEASE_ENV_FILE" == "/run/lecturesift/release.env" ]] ||
  fail "the release identity path is fixed"
check_private "$DB_ENV_FILE" "Database environment"
check_private "$RELEASE_ENV_FILE" "Release identity"
for helper in "$EVIDENCE_TOOL" "$DATA_MANIFEST" "$SECURITY_MANIFEST" \
  "$SCHEMA_CONTRACT" "$SCHEMA_VERIFIER" "$SECURITY_VALIDATOR" \
  "$ROLE_LOGIN_PROBE" "$REDIS_MANIFEST_TOOL" \
  "$ROOT_DIR/compose.yaml"; do
  [[ -f "$helper" && ! -L "$helper" ]] || fail "a first-start helper is missing or unsafe"
done

EXPECTED_REVISION="$(sed -n 's/^LECTURESIFT_EXPECTED_BUILD_REVISION=//p' "$RELEASE_ENV_FILE")"
[[ "$(wc -l <"$RELEASE_ENV_FILE")" == "1" &&
   "$EXPECTED_REVISION" =~ ^[0-9a-f]{40}$ ]] ||
  fail "the release identity is invalid"

if [[ "$MODE" == "complete" ]]; then
  completion="$(
    python3 "$EVIDENCE_TOOL" complete-first-start --expected-revision "$EXPECTED_REVISION"
  )" || fail "the armed first-start proof could not be consumed"
  [[ "$completion" == "consumed" ]] || fail "the first-start completion output is invalid"
  echo "Provider first-start gate is consumed."
  exit 0
fi

status="$(python3 "$EVIDENCE_TOOL" first-start-status --expected-revision "$EXPECTED_REVISION")" ||
  fail "the first-start status is ambiguous or crash-fenced"
[[ "$status" == "required" || "$status" == "consumed" ]] ||
  fail "the first-start status output is invalid"

if [[ "$status" == "consumed" ]]; then
  echo "Provider first-start gate was already consumed."
  exit 0
fi

set -a
# shellcheck disable=SC1090
source "$DB_ENV_FILE" >/dev/null 2>&1
set +a
set +x
for required in POSTGRES_DB POSTGRES_USER LECTURESIFT_APP_DB_USER \
  LECTURESIFT_WORKER_DB_USER; do
  [[ -n "${!required:-}" ]] || fail "the database environment is incomplete"
done

[[ -d "$LOCK_ROOT" && ! -L "$LOCK_ROOT" &&
   "$(realpath -e -- "$LOCK_ROOT")" == "$LOCK_ROOT" &&
   "$(stat -c '%u' -- "$LOCK_ROOT")" == "0" ]] ||
  fail "the shared backup/start lock root is unsafe"
lock_mode="$(stat -c '%a' -- "$LOCK_ROOT")"
(( (8#$lock_mode & 8#077) == 0 )) || fail "the shared lock root is not root-private"
exec 9>"$LOCK_ROOT/.backup.lock"
flock -n 9 || fail "a backup, restore, rehearsal or migration is active"
WORK_DIR="$(mktemp -d -- "$LOCK_ROOT/.provider-first-start-XXXXXXXX")"
chmod 0700 "$WORK_DIR"
cleanup() {
  local resolved=""
  trap - EXIT
  if [[ -n "$WORK_DIR" && -d "$WORK_DIR" && ! -L "$WORK_DIR" ]]; then
    resolved="$(realpath -e -- "$WORK_DIR" 2>/dev/null || true)"
    if [[ "$resolved" == "$LOCK_ROOT"/.provider-first-start-* ]]; then
      rm -rf --one-file-system -- "$resolved"
    fi
  fi
}
trap cleanup EXIT

compose=(docker compose --project-directory "$ROOT_DIR" --file "$ROOT_DIR/compose.yaml")
for service in api worker; do
  writer="$("${compose[@]}" ps -q "$service")"
  if [[ -n "$writer" ]]; then
    [[ "$(docker inspect -f '{{.State.Running}}' "$writer" 2>/dev/null)" == "false" ]] ||
      fail "the target $service writer is already running"
  fi
done

"${compose[@]}" exec -T postgres psql --no-psqlrc --quiet \
  --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" -v ON_ERROR_STOP=1 \
  -f - <"$DATA_MANIFEST" >"$WORK_DIR/data.raw" ||
  fail "the strict target data manifest could not be captured"
if grep -Eq '^(TABLE_DIFF|UNVALIDATED_FK)\|' "$WORK_DIR/data.raw" ||
   awk -F'|' '$1 == "ANOMALY" && $3 != "0" {bad=1} END {exit(bad ? 0 : 1)}' \
     "$WORK_DIR/data.raw"; then
  fail "the strict target data manifest contains an anomaly"
fi
python3 "$SCHEMA_VERIFIER" current \
  --manifest "$WORK_DIR/data.raw" --contract "$SCHEMA_CONTRACT" >/dev/null ||
  fail "the strict target data manifest violates the exact current schema contract"
tr -d '\r' <"$WORK_DIR/data.raw" |
  grep -E '^(DATABASE|SCHEMA|SCHEMA_OBJECT|TABLE|ANOMALY|STATUS|SCHEMA_COMPAT|UNVALIDATED_FK|MANIFEST_COMPLETE)\|' |
  LC_ALL=C sort >"$WORK_DIR/data.safe"
[[ "$(grep -c '^DATABASE|' "$WORK_DIR/data.safe")" == "1" &&
   "$(grep -c '^SCHEMA|' "$WORK_DIR/data.safe")" == "1" &&
   "$(grep -c '^TABLE|' "$WORK_DIR/data.safe")" -gt 0 &&
   "$(grep -c '^SCHEMA_COMPAT|' "$WORK_DIR/data.safe")" == "0" ]] ||
  fail "the strict target data manifest is incomplete"

"${compose[@]}" exec -T postgres psql --no-psqlrc --quiet \
  --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" -v ON_ERROR_STOP=1 \
  --variable=owner_user="$POSTGRES_USER" \
  --variable=api_user="$LECTURESIFT_APP_DB_USER" \
  --variable=worker_user="$LECTURESIFT_WORKER_DB_USER" \
  <"$SECURITY_MANIFEST" >"$WORK_DIR/security.raw" ||
  fail "the target PostgreSQL authority manifest could not be captured"
python3 "$SECURITY_VALIDATOR" "$WORK_DIR/security.raw" >"$WORK_DIR/security.safe" ||
  fail "the target PostgreSQL authority manifest is invalid"

DATA_SHA256="$(sha256sum "$WORK_DIR/data.safe" | awk '{print $1}')"
SECURITY_SHA256="$(sha256sum "$WORK_DIR/security.safe" | awk '{print $1}')"
ROLE_LOGIN_SHA256="$(bash "$ROLE_LOGIN_PROBE")" ||
  fail "the trusted PostgreSQL role login proof failed"
REDIS_SHA256="$(bash "$REDIS_MANIFEST_TOOL" manifest steady full)" ||
  fail "the complete target Redis manifest could not be proved"
for digest in "$DATA_SHA256" "$SECURITY_SHA256" "$ROLE_LOGIN_SHA256" "$REDIS_SHA256"; do
  [[ "$digest" =~ ^[0-9a-f]{64}$ ]] || fail "a first-start target digest is invalid"
done

armed="$(
  python3 "$EVIDENCE_TOOL" arm-first-start \
    --expected-revision "$EXPECTED_REVISION" \
    --migrated-target-manifest-sha256 "$DATA_SHA256" \
    --postgres-role-login-probe-sha256 "$ROLE_LOGIN_SHA256" \
    --postgres-security-manifest-sha256 "$SECURITY_SHA256" \
    --target-redis-manifest-sha256 "$REDIS_SHA256"
)" || fail "the target state does not match the finalized provider proof"
[[ "$armed" == "armed" || "$armed" == "consumed" ]] ||
  fail "the first-start arm output is invalid"
echo "Provider first-start gate: $armed."
