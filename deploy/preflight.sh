#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="${LECTURESIFT_ROOT:-/opt/lecturesift}"
ENV_FILE="${LECTURESIFT_ENV_FILE:-/etc/lecturesift/runtime.env}"
API_ENV_FILE="${LECTURESIFT_API_ENV_FILE:-/etc/lecturesift/api.env}"
WORKER_ENV_FILE="${LECTURESIFT_WORKER_ENV_FILE:-/etc/lecturesift/worker.env}"
INSTAGRAM_ENV_FILE="${LECTURESIFT_INSTAGRAM_ENV_FILE:-/etc/lecturesift/instagram.env}"
DB_ENV_FILE="${LECTURESIFT_DB_ENV_FILE:-/etc/lecturesift/postgres.env}"
RESTIC_ENV_FILE="${LECTURESIFT_RESTIC_ENV_FILE:-/etc/lecturesift/restic.env}"
RELEASE_ENV_FILE="${LECTURESIFT_RELEASE_ENV_FILE:-/run/lecturesift/release.env}"
ROLE_ENV_GENERATOR="$ROOT_DIR/deploy/generate_role_envs.py"
RELEASE_HELPER="$ROOT_DIR/deploy/release.sh"
CUTOVER_EVIDENCE_TOOL="$ROOT_DIR/deploy/provider_cutover_evidence.py"
PROVIDER_CUTOVER_ROOT="/var/lib/lecturesift/provider-cutover"
PROVIDER_CUTOVER_IN_PROGRESS="$PROVIDER_CUTOVER_ROOT/provider-cutover.in-progress"
PROVIDER_CUTOVER_FINAL="$PROVIDER_CUTOVER_ROOT/provider-cutover.ok"
RECOVERY_EVIDENCE_ROOT="/var/lib/lecturesift/recovery-drills"
RECOVERY_DRILL_MAX_AGE_DAYS=90
RECOVERY_SNAPSHOT_MAX_AGE_SECONDS=172800
RESTIC_ESCROW_MARKER="$RECOVERY_EVIDENCE_ROOT/restic-password-escrow.ok"
REDIS_FAIL_STOP_MARKER="/var/lib/lecturesift/migration-fail-stop/redis-state-unproven"
POSTGRES_CUTOVER_FAIL_STOP_MARKER="/var/lib/lecturesift/migration-fail-stop/postgres-cutover-unproven"
POSTGRES_ROLLBACK_FAIL_STOP_MARKER="/var/lib/lecturesift/migration-fail-stop/postgres-rollback-unproven"
REQUIRED_BACKUP_SET_SHA256="${LECTURESIFT_REQUIRED_BACKUP_SET_SHA256:-}"
BOOTSTRAP_OVERRIDE="${LECTURESIFT_RECOVERY_BOOTSTRAP_OVERRIDE:-}"
PREFLIGHT_CONTEXT="${LECTURESIFT_PREFLIGHT_CONTEXT:-production}"
BOOTSTRAP_INFRASTRUCTURE_ONLY="${LECTURESIFT_BOOTSTRAP_INFRASTRUCTURE_ONLY:-}"

fail() {
  echo "Production preflight failed: $*" >&2
  exit 1
}

check_private_env() {
  local path="$1"
  if [[ ! -f "$path" || -L "$path" ]]; then
    echo "Missing private environment file: $path" >&2
    exit 1
  fi
  if [[ "$(stat -c '%u' "$path")" != "0" ]]; then
    echo "Private environment file must be owned by root: $path" >&2
    exit 1
  fi
  case "$(stat -c '%a' "$path")" in
    400|600) ;;
    *)
      echo "Private environment file must have mode 0400 or 0600: $path" >&2
      exit 1
      ;;
  esac
}

check_private_env "$ENV_FILE"
check_private_env "$DB_ENV_FILE"
if [[ -e "$REDIS_FAIL_STOP_MARKER" || -L "$REDIS_FAIL_STOP_MARKER" ]]; then
  fail "Redis migration state is unproven; manual recovery and fail-stop marker clearance are required"
fi
if [[ -e "$POSTGRES_CUTOVER_FAIL_STOP_MARKER" || -L "$POSTGRES_CUTOVER_FAIL_STOP_MARKER" ]]; then
  fail "PostgreSQL cutover state is unproven; manual recovery and fail-stop marker clearance are required"
fi
if [[ -e "$POSTGRES_ROLLBACK_FAIL_STOP_MARKER" || -L "$POSTGRES_ROLLBACK_FAIL_STOP_MARKER" ]]; then
  fail "PostgreSQL rollback state is unproven; manual recovery and fail-stop marker clearance are required"
fi
if [[ "$PREFLIGHT_CONTEXT" == "production" &&
      ( -e "$PROVIDER_CUTOVER_IN_PROGRESS" || -L "$PROVIDER_CUTOVER_IN_PROGRESS" ) ]]; then
  fail "the provider cutover is still in progress; API/worker startup remains blocked"
fi
if [[ "$PREFLIGHT_CONTEXT" == "production" &&
      ( ! -f "$PROVIDER_CUTOVER_FINAL" || -L "$PROVIDER_CUTOVER_FINAL" ) ]]; then
  fail "the atomic provider-cutover approval is missing; finalize PostgreSQL, Redis and R2 gates first"
fi
if [[ -e "$RESTIC_ENV_FILE" ]]; then
  check_private_env "$RESTIC_ENV_FILE"
fi

command -v python3 >/dev/null 2>&1 || fail "python3 is not installed"
[[ -f "$ROLE_ENV_GENERATOR" && ! -L "$ROLE_ENV_GENERATOR" ]] || \
  fail "the role environment generator is missing or unsafe"
export LECTURESIFT_ENV_FILE="$ENV_FILE"
export LECTURESIFT_API_ENV_FILE="$API_ENV_FILE"
export LECTURESIFT_WORKER_ENV_FILE="$WORKER_ENV_FILE"
export LECTURESIFT_INSTAGRAM_ENV_FILE="$INSTAGRAM_ENV_FILE"

# Every non-production path is explicit, one-shot and forbidden in the master
# environment. The normal systemd service supplies none of these variables.
for host_only_name in \
  LECTURESIFT_RECOVERY_BOOTSTRAP_OVERRIDE \
  LECTURESIFT_PREFLIGHT_CONTEXT \
  LECTURESIFT_BOOTSTRAP_INFRASTRUCTURE_ONLY; do
  if grep -Eq "^[[:space:]]*(export[[:space:]]+)?${host_only_name}=" "$ENV_FILE"; then
    fail "$host_only_name must not be stored in the runtime environment file"
  fi
done
case "$PREFLIGHT_CONTEXT" in
  production)
    [[ -z "$BOOTSTRAP_OVERRIDE" || "$BOOTSTRAP_OVERRIDE" == "NO" ]] ||
      fail "the recovery bootstrap override is forbidden in normal production preflight"
    [[ -z "$BOOTSTRAP_INFRASTRUCTURE_ONLY" ]] ||
      fail "the infrastructure-only flag is forbidden in normal production preflight"
    ;;
  bootstrap-infrastructure)
    [[ "$BOOTSTRAP_OVERRIDE" == "YES" && "$BOOTSTRAP_INFRASTRUCTURE_ONLY" == "YES" ]] ||
      fail "bootstrap-infrastructure requires both explicit one-time confirmations"
    [[ -z "${INVOCATION_ID:-}" ]] ||
      fail "bootstrap-infrastructure cannot run from a persistent systemd service"
    ;;
  disaster-restore-validation)
    [[ -z "$BOOTSTRAP_OVERRIDE" || "$BOOTSTRAP_OVERRIDE" == "NO" ]] ||
      fail "disaster restore validation refuses the bootstrap override"
    [[ -n "$REQUIRED_BACKUP_SET_SHA256" ]] ||
      fail "disaster restore validation requires one exact selected backup set"
    ;;
  *) fail "LECTURESIFT_PREFLIGHT_CONTEXT is invalid" ;;
esac
if [[ -n "$REQUIRED_BACKUP_SET_SHA256" && \
      ! "$REQUIRED_BACKUP_SET_SHA256" =~ ^[[:xdigit:]]{64}$ ]]; then
  fail "LECTURESIFT_REQUIRED_BACKUP_SET_SHA256 must be one SHA-256 value"
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
# shellcheck disable=SC1090
source "$DB_ENV_FILE"
if [[ -f "$RESTIC_ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$RESTIC_ENV_FILE"
fi
set +a

required=(
  POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD
  LECTURESIFT_APP_DB_USER LECTURESIFT_APP_DB_PASSWORD DATABASE_URL
  LECTURESIFT_WORKER_DB_USER LECTURESIFT_WORKER_DB_PASSWORD
  LECTURESIFT_WORKER_DATABASE_URL
  CELERY_BROKER_URL REDIS_URL PUBLIC_BASE_URL FRONTEND_BASE_URL
  BILLING_SESSION_SECRET PAYMENT_TOKEN_BINDING_SECRET ADMIN_ADMIN OPENAI_API_KEY
  LECTURESIFT_OPS_ALERT_EMAIL
  S3_ENDPOINT_URL S3_BUCKET S3_ACCESS_KEY_ID S3_SECRET_ACCESS_KEY
)

missing=()
for name in "${required[@]}"; do
  if [[ -z "${!name:-}" ]]; then
    missing+=("$name")
  fi
done

if ((${#missing[@]})); then
  printf 'Missing required environment values: %s\n' "${missing[*]}" >&2
  exit 1
fi

if [[ "${INSTAGRAM_DAILY_AUTOMATION_ENABLED:-false}" == "true" ]]; then
  instagram_required=(
    INSTAGRAM_ACCESS_TOKEN INSTAGRAM_ACCOUNT_ID INSTAGRAM_APP_SECRET
    INSTAGRAM_GRAPH_API_VERSION INSTAGRAM_DAILY_MEDIA_TYPE
  )
  instagram_missing=()
  for name in "${instagram_required[@]}"; do
    if [[ -z "${!name:-}" ]]; then
      instagram_missing+=("$name")
    fi
  done
  if ((${#instagram_missing[@]})); then
    printf 'Missing enabled Instagram job values: %s\n' "${instagram_missing[*]}" >&2
    exit 1
  fi
fi

if [[ "$DATABASE_URL" != postgresql* || \
      "$LECTURESIFT_WORKER_DATABASE_URL" != postgresql* ]]; then
  echo "API and worker database URLs must use PostgreSQL in production." >&2
  exit 1
fi

if ! python3 - <<'PY'
import os
import re
from urllib.parse import unquote, urlsplit

api_url = urlsplit(os.environ["DATABASE_URL"])
worker_url = urlsplit(os.environ["LECTURESIFT_WORKER_DATABASE_URL"])
api_user = os.environ["LECTURESIFT_APP_DB_USER"]
api_password = os.environ["LECTURESIFT_APP_DB_PASSWORD"]
worker_user = os.environ["LECTURESIFT_WORKER_DB_USER"]
worker_password = os.environ["LECTURESIFT_WORKER_DB_PASSWORD"]
owner_user = os.environ["POSTGRES_USER"]
database = os.environ["POSTGRES_DB"]
identifier = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
def valid_url(url, user, password):
    return (
        url.scheme in {"postgresql", "postgresql+psycopg"}
        and url.hostname == "postgres"
        and (url.port or 5432) == 5432
        and unquote(url.username or "") == user
        and unquote(url.password or "") == password
        and url.path == f"/{database}"
        and not url.query
        and not url.fragment
    )

valid = (
    valid_url(api_url, api_user, api_password)
    and valid_url(worker_url, worker_user, worker_password)
    and len({owner_user, api_user, worker_user}) == 3
    and api_password != worker_password
    and all(identifier.fullmatch(value) for value in (owner_user, api_user, worker_user, database))
)
raise SystemExit(0 if valid else 1)
PY
then
  echo "API and worker database URLs must use distinct least-privilege roles on the private Compose database." >&2
  exit 1
fi

if [[ "$CELERY_BROKER_URL" != redis://redis:6379/* || "$REDIS_URL" != redis://redis:6379/* ]]; then
  echo "Celery and application state must target the private Compose Redis service." >&2
  exit 1
fi

if [[ "$PUBLIC_BASE_URL" != "https://api.lecturesift.com" ]]; then
  echo "PUBLIC_BASE_URL must be https://api.lecturesift.com" >&2
  exit 1
fi

if [[ "$FRONTEND_BASE_URL" != "https://lecturesift.com" ]]; then
  echo "FRONTEND_BASE_URL must be https://lecturesift.com" >&2
  exit 1
fi

if [[ "$S3_ENDPOINT_URL" != https://* ]]; then
  echo "S3_ENDPOINT_URL must use HTTPS." >&2
  exit 1
fi

if [[ "$POSTGRES_PASSWORD" == *CHANGE_ME* || \
      "$LECTURESIFT_APP_DB_PASSWORD" == *CHANGE_ME* || \
      "$LECTURESIFT_WORKER_DB_PASSWORD" == *CHANGE_ME* || \
      "$DATABASE_URL" == *CHANGE_ME* || \
      "$LECTURESIFT_WORKER_DATABASE_URL" == *CHANGE_ME* ]]; then
  echo "Database placeholders must be replaced before production startup." >&2
  exit 1
fi

if [[ ${#POSTGRES_PASSWORD} -lt 24 ]]; then
  echo "POSTGRES_PASSWORD must be at least 24 characters." >&2
  exit 1
fi

if [[ ${#LECTURESIFT_APP_DB_PASSWORD} -lt 24 ]]; then
  echo "LECTURESIFT_APP_DB_PASSWORD must be at least 24 characters." >&2
  exit 1
fi

if [[ ${#LECTURESIFT_WORKER_DB_PASSWORD} -lt 24 || \
      "$LECTURESIFT_APP_DB_PASSWORD" == "$LECTURESIFT_WORKER_DB_PASSWORD" ]]; then
  echo "LECTURESIFT_WORKER_DB_PASSWORD must be distinct and at least 24 characters." >&2
  exit 1
fi

if [[ ${#BILLING_SESSION_SECRET} -lt 32 ]]; then
  echo "BILLING_SESSION_SECRET must be at least 32 characters." >&2
  exit 1
fi

if [[ ${#PAYMENT_TOKEN_BINDING_SECRET} -lt 32 ]]; then
  echo "PAYMENT_TOKEN_BINDING_SECRET must be at least 32 characters." >&2
  exit 1
fi

payment_token_binding_legacy_secret="${PAYMENT_TOKEN_BINDING_LEGACY_SECRET:-}"
if [[ -n "$payment_token_binding_legacy_secret" && \
      ${#payment_token_binding_legacy_secret} -lt 32 ]]; then
  echo "PAYMENT_TOKEN_BINDING_LEGACY_SECRET must be empty or at least 32 characters." >&2
  exit 1
fi

if [[ -n "$payment_token_binding_legacy_secret" && \
      "$PAYMENT_TOKEN_BINDING_SECRET" == "$payment_token_binding_legacy_secret" ]]; then
  echo "The active and legacy payment-token binding keys must differ." >&2
  exit 1
fi

if [[ ${#ADMIN_ADMIN} -lt 32 ]]; then
  echo "ADMIN_ADMIN must be at least 32 characters." >&2
  exit 1
fi

if [[ ! "$LECTURESIFT_OPS_ALERT_EMAIL" =~ ^[^[:space:]@]+@[^[:space:]@]+[.][^[:space:]@]+$ ]]; then
  echo "LECTURESIFT_OPS_ALERT_EMAIL must be a valid operations mailbox." >&2
  exit 1
fi

validate_recovery_marker() {
  local marker="$1"
  local marker_mode marker_mtime now oldest_allowed newest_allowed
  local completed_time_text completed_time_epoch snapshot_time_text snapshot_time_epoch drill_scope

  [[ -f "$marker" && ! -L "$marker" ]] || return 1
  [[ "$(stat -c '%u' -- "$marker")" == "0" ]] || return 1
  marker_mode="$(stat -c '%a' -- "$marker")"
  (( (8#$marker_mode & 8#022) == 0 )) || return 1

  marker_mtime="$(stat -c '%Y' -- "$marker")"
  now="$(date -u +%s)"
  oldest_allowed=$((now - RECOVERY_DRILL_MAX_AGE_DAYS * 86400))
  newest_allowed=$((now + 300))
  (( marker_mtime >= oldest_allowed && marker_mtime <= newest_allowed )) || return 1

  grep -Fqx 'status=success' "$marker" || return 1
  grep -Fqx 'snapshot_selector=host-lecturesift-production-tags-lecturesift-production' "$marker" || return 1
  drill_scope="$(sed -n 's/^drill_scope=//p' "$marker")"
  [[ "$drill_scope" == "current-latest" || "$drill_scope" == "explicit-backup" ]] || return 1
  [[ "$(grep -c '^drill_scope=' "$marker")" == "1" ]] || return 1
  if [[ -z "$REQUIRED_BACKUP_SET_SHA256" && "$drill_scope" != "current-latest" ]]; then
    return 1
  fi
  grep -Eq '^snapshot_id=[[:xdigit:]]{8,64}$' "$marker" || return 1
  grep -Eq '^backup_set_sha256=[[:xdigit:]]{64}$' "$marker" || return 1
  grep -Eq '^snapshot_time_utc=[0-9]{4}-[0-9]{2}-[0-9]{2}T' "$marker" || return 1
  grep -Fqx "repository_id_sha256=$CURRENT_REPOSITORY_ID_SHA256" "$marker" || return 1
  if [[ -n "$REQUIRED_BACKUP_SET_SHA256" ]]; then
    grep -Fqx "backup_set_sha256=$REQUIRED_BACKUP_SET_SHA256" "$marker" || return 1
  fi
  completed_time_text="$(sed -n 's/^completed_at_utc=//p' "$marker")"
  [[ -n "$completed_time_text" && \
     "$(grep -c '^completed_at_utc=' "$marker")" == "1" ]] || return 1
  completed_time_epoch="$(date -u -d "$completed_time_text" +%s 2>/dev/null)" || return 1
  (( completed_time_epoch >= marker_mtime - 300 && \
     completed_time_epoch <= marker_mtime + 300 )) || return 1
  snapshot_time_text="$(sed -n 's/^snapshot_time_utc=//p' "$marker")"
  [[ -n "$snapshot_time_text" && \
     "$(grep -c '^snapshot_time_utc=' "$marker")" == "1" ]] || return 1
  snapshot_time_epoch="$(date -u -d "$snapshot_time_text" +%s 2>/dev/null)" || return 1
  # A normal drill remains valid for its own 90-day cadence and must have used
  # a snapshot fresh at drill time. A deliberately selected historical backup
  # may authorize only an exact restore-set hash; it can never satisfy the
  # ordinary startup drill gate above.
  if [[ "$drill_scope" == "current-latest" ]]; then
    (( snapshot_time_epoch >= completed_time_epoch - RECOVERY_SNAPSHOT_MAX_AGE_SECONDS && \
       snapshot_time_epoch <= completed_time_epoch + 300 )) || return 1
  else
    (( snapshot_time_epoch <= completed_time_epoch + 300 )) || return 1
  fi
  grep -Fqx 'checksums=verified' "$marker" || return 1
  grep -Fqx 'postgres_restore=verified' "$marker" || return 1
  grep -Fqx 'redis_restore=verified' "$marker" || return 1
  grep -Fqx 'restored_payload_removed=true' "$marker" || return 1
  grep -Fqx 'live_services_touched=false' "$marker" || return 1
}

validate_escrow_marker() {
  local marker="$1" marker_mode verified_text verified_epoch now
  [[ -f "$marker" && ! -L "$marker" ]] || return 1
  [[ "$(stat -c '%u' -- "$marker")" == "0" ]] || return 1
  marker_mode="$(stat -c '%a' -- "$marker")"
  (( (8#$marker_mode & 8#022) == 0 )) || return 1
  grep -Fqx 'status=verified' "$marker" || return 1
  grep -Fqx 'escrow_type=encrypted-off-host' "$marker" || return 1
  grep -Fqx 'recovery_test=decrypt-and-repository-opened' "$marker" || return 1
  grep -Eq '^ciphertext_sha256=[[:xdigit:]]{64}$' "$marker" || return 1
  grep -Fqx "restic_key_id=$CURRENT_RESTIC_KEY_ID" "$marker" || return 1
  grep -Fqx "repository_id_sha256=$CURRENT_REPOSITORY_ID_SHA256" "$marker" || return 1
  verified_text="$(sed -n 's/^verified_at_utc=//p' "$marker")"
  [[ -n "$verified_text" && "$(grep -c '^verified_at_utc=' "$marker")" == "1" ]] || return 1
  verified_epoch="$(date -u -d "$verified_text" +%s 2>/dev/null)" || return 1
  now="$(date -u +%s)"
  (( verified_epoch <= now + 300 )) || return 1
}

if [[ "$BOOTSTRAP_OVERRIDE" != "YES" ]]; then
  [[ -f "$RESTIC_ENV_FILE" ]] || fail "off-site restic configuration is required"
  [[ -n "${RESTIC_REPOSITORY:-}" ]] || fail "RESTIC_REPOSITORY is required"
  [[ -n "${RESTIC_PASSWORD:-}" ]] || fail "RESTIC_PASSWORD is required"
  case "$RESTIC_REPOSITORY" in
    s3:*) ;;
    *) fail "production recovery requires an off-site S3-compatible restic repository" ;;
  esac
  case "$RESTIC_REPOSITORY" in
    s3:*/lecturesift-production-backups/restic) ;;
    *) fail "production restic must use the dedicated lecturesift-production-backups/restic target" ;;
  esac
  [[ -n "${RESTIC_AWS_ACCESS_KEY_ID:-}" ]] || \
    fail "RESTIC_AWS_ACCESS_KEY_ID is required"
  [[ -n "${RESTIC_AWS_SECRET_ACCESS_KEY:-}" ]] || \
    fail "RESTIC_AWS_SECRET_ACCESS_KEY is required"
  command -v restic >/dev/null 2>&1 || fail "restic is not installed"
  command -v python3 >/dev/null 2>&1 || fail "python3 is not installed"

  export AWS_ACCESS_KEY_ID="${RESTIC_AWS_ACCESS_KEY_ID:-}"
  export AWS_SECRET_ACCESS_KEY="${RESTIC_AWS_SECRET_ACCESS_KEY:-}"
  CURRENT_REPOSITORY_ID_SHA256="$(restic cat config 2>/dev/null | python3 -c \
    'import hashlib,json,sys; value=str(json.load(sys.stdin).get("id") or ""); raise SystemExit(1) if not value else None; print(hashlib.sha256(value.encode("ascii")).hexdigest())')" || \
    fail "the configured restic repository is inaccessible, uninitialized, or has no stable id"
  current_restic_key_hint="$(restic key list --json 2>/dev/null | python3 -c '
import json, re, sys
keys = json.load(sys.stdin)
current = [str(item.get("id") or "") for item in keys if item.get("current") is True]
if len(current) != 1 or re.fullmatch(r"[0-9a-fA-F]{8,64}", current[0]) is None:
    raise SystemExit(1)
print(current[0].lower())
')" || fail "the current restic encryption-key hint could not be determined"
  CURRENT_RESTIC_KEY_ID="$(restic list keys --quiet 2>/dev/null | python3 -c '
import re, sys
hint = sys.argv[1].lower()
matches = [line.strip().lower() for line in sys.stdin if re.fullmatch(r"[0-9a-fA-F]{64}", line.strip()) and line.strip().lower().startswith(hint)]
if len(matches) != 1:
    raise SystemExit(1)
print(matches[0])
' "$current_restic_key_hint")" || fail "the current restic encryption-key identity could not be resolved uniquely"

  # Backup freshness and restore-drill freshness are independent gates. The
  # latest off-site production snapshot must be <=48h old on every boot, while
  # a successful isolated drill is allowed its separate 90-day cadence.
  # Normal public startup requires a <=48h latest snapshot. An explicitly
  # selected disaster restore instead relies on its freshly generated exact
  # backup-set drill marker below; requiring a fresh *latest* snapshot in that
  # mode would make recovery impossible precisely during a backup outage.
  if [[ -z "$REQUIRED_BACKUP_SET_SHA256" ]]; then
    restic snapshots --json --latest 1 --host lecturesift-production \
      --tag lecturesift --tag production 2>/dev/null | \
      python3 -c '
from datetime import datetime, timezone
import json, sys
snapshots = json.load(sys.stdin)
if not isinstance(snapshots, list) or len(snapshots) != 1:
    raise SystemExit(1)
snapshot = snapshots[0]
tags = set(snapshot.get("tags") or [])
if snapshot.get("hostname") != "lecturesift-production" or not {"lecturesift", "production"}.issubset(tags):
    raise SystemExit(1)
created = datetime.fromisoformat(str(snapshot.get("time") or "").replace("Z", "+00:00"))
age = (datetime.now(timezone.utc) - created.astimezone(timezone.utc)).total_seconds()
raise SystemExit(0 if -300 <= age <= int(sys.argv[1]) else 1)
' "$RECOVERY_SNAPSHOT_MAX_AGE_SECONDS" || \
      fail "the latest off-site production snapshot is missing or older than 48 hours"
  fi
  unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY

  for confirmation in \
    LECTURESIFT_DATABASE_RECOVERY_CONFIRMED \
    LECTURESIFT_OBJECT_RETENTION_CONFIRMED \
    LECTURESIFT_RECOVERY_DRILL_CONFIRMED; do
    [[ "${!confirmation:-}" == "true" ]] || \
      fail "$confirmation must be true before production startup"
  done

  evidence_root_normalized="$(realpath -m -- "$RECOVERY_EVIDENCE_ROOT")"
  [[ "$evidence_root_normalized" == "$RECOVERY_EVIDENCE_ROOT" ]] || \
    fail "the recovery evidence root resolves through a symlink"
  [[ -d "$RECOVERY_EVIDENCE_ROOT" && ! -L "$RECOVERY_EVIDENCE_ROOT" ]] || \
    fail "the fixed recovery evidence directory is missing"
  [[ "$(realpath -e -- "$RECOVERY_EVIDENCE_ROOT")" == "$RECOVERY_EVIDENCE_ROOT" ]] || \
    fail "the recovery evidence directory escaped its fixed path"
  validate_escrow_marker "$RESTIC_ESCROW_MARKER" || \
    fail "no repository-bound proof of tested encrypted off-host restic password escrow was found"

  valid_recovery_marker=""
  shopt -s nullglob
  recovery_markers=("$RECOVERY_EVIDENCE_ROOT"/restic-restore-*.ok)
  shopt -u nullglob
  for recovery_marker in "${recovery_markers[@]}"; do
    if validate_recovery_marker "$recovery_marker"; then
      valid_recovery_marker="$recovery_marker"
    fi
  done
  [[ -n "$valid_recovery_marker" ]] || \
    fail "no recent, root-owned successful isolated restore-drill marker was found"
else
  echo "WARNING: one-time recovery bootstrap override is active; production cutover is not yet approved." >&2
fi

case "${EMAIL_PROVIDER:-none}" in
  resend)
    if [[ -z "${EMAIL_FROM:-}" || -z "${RESEND_API_KEY:-}" ]]; then
      echo "Resend email delivery requires EMAIL_FROM and RESEND_API_KEY." >&2
      exit 1
    fi
    ;;
  smtp)
    if [[ -z "${EMAIL_FROM:-}" || -z "${SMTP_HOST:-}" || -z "${SMTP_USERNAME:-}" || -z "${SMTP_PASSWORD:-}" ]]; then
      echo "SMTP email delivery is incomplete." >&2
      exit 1
    fi
    ;;
  *)
    echo "Production email delivery must use resend or smtp." >&2
    exit 1
    ;;
esac

# Publish service-role environment files only after every source value and
# recovery/security invariant above has passed. A failed reload therefore
# leaves the last known-good role files untouched instead of poisoning a later
# backup or restart. Generation itself stages all roles before atomic renames.
python3 "$ROLE_ENV_GENERATOR"
check_private_env "$API_ENV_FILE"
check_private_env "$WORKER_ENV_FILE"
check_private_env "$INSTAGRAM_ENV_FILE"
python3 "$ROLE_ENV_GENERATOR" --check

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is not installed." >&2
  exit 1
fi

if [[ ! -d "$ROOT_DIR" ]]; then
  echo "Missing deployment root: $ROOT_DIR" >&2
  exit 1
fi

[[ -f "$RELEASE_HELPER" && ! -L "$RELEASE_HELPER" ]] || \
  fail "the exact release helper is missing or unsafe"
export LECTURESIFT_RELEASE_ENV_FILE="$RELEASE_ENV_FILE"
bash "$RELEASE_HELPER" prepare

[[ -f "$CUTOVER_EVIDENCE_TOOL" && ! -L "$CUTOVER_EVIDENCE_TOOL" ]] || \
  fail "the provider cutover evidence validator is missing or unsafe"
case "$PREFLIGHT_CONTEXT" in
  production)
    prepared_revision="$(sed -n 's/^LECTURESIFT_EXPECTED_BUILD_REVISION=//p' "$RELEASE_ENV_FILE")"
    [[ "$prepared_revision" =~ ^[0-9a-f]{40}$ ]] ||
      fail "the prepared release revision is invalid"
    python3 "$CUTOVER_EVIDENCE_TOOL" validate-final \
      --expected-revision "$prepared_revision" ||
      fail "the provider cutover proof is absent, changed or belongs to another release"
    ;;
  bootstrap-infrastructure)
    echo "WARNING: infrastructure-only bootstrap passed; API/worker production startup is not approved." >&2
    ;;
  disaster-restore-validation)
    echo "Disaster-restore validation uses its selected backup proof instead of a provider-cutover marker." >&2
    ;;
esac

[[ -f "$ROOT_DIR/deploy/resource_guard.sh" && \
   ! -L "$ROOT_DIR/deploy/resource_guard.sh" ]] || \
  fail "the host resource guard is missing or unsafe"
bash "$ROOT_DIR/deploy/resource_guard.sh"

export LECTURESIFT_DB_ENV_FILE="$DB_ENV_FILE"
export LECTURESIFT_RESTIC_ENV_FILE="$RESTIC_ENV_FILE"
docker compose --project-directory "$ROOT_DIR" --file "$ROOT_DIR/compose.yaml" config --quiet

echo "LectureSift production preflight passed."
