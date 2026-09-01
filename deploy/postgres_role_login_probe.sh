#!/usr/bin/env bash
set -euo pipefail
umask 077
set +x

# Prove all three production PostgreSQL logins with the trusted official data
# container. Passwords travel only over stdin to that container and never
# enter argv, output, or evidence.

[[ "$(id -u)" == "0" ]] || {
  echo "PostgreSQL role login proof must run as root." >&2
  exit 1
}

ROOT_DIR="${LECTURESIFT_ROOT:-/opt/lecturesift}"
DB_ENV_FILE="${LECTURESIFT_DB_ENV_FILE:-/etc/lecturesift/postgres.env}"
VALIDATOR="$ROOT_DIR/deploy/validate_postgres_role_login_probe.py"
[[ "$DB_ENV_FILE" == "/etc/lecturesift/postgres.env" ]] || {
  echo "The PostgreSQL role login proof uses one fixed database env path." >&2
  exit 1
}
[[ -f "$DB_ENV_FILE" && ! -L "$DB_ENV_FILE" &&
   "$(stat -c '%u' -- "$DB_ENV_FILE")" == "0" &&
   "$(stat -c '%a' -- "$DB_ENV_FILE")" == "600" ]] || {
  echo "The PostgreSQL database env is not a private root-owned file." >&2
  exit 1
}
[[ -f "$VALIDATOR" && ! -L "$VALIDATOR" ]] || {
  echo "The PostgreSQL role login evidence validator is missing or unsafe." >&2
  exit 1
}

set -a
# shellcheck disable=SC1090
source "$DB_ENV_FILE" >/dev/null 2>&1
set +a
set +x
for required in POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD \
  LECTURESIFT_APP_DB_USER LECTURESIFT_APP_DB_PASSWORD \
  LECTURESIFT_WORKER_DB_USER LECTURESIFT_WORKER_DB_PASSWORD; do
  [[ -n "${!required:-}" && "${!required}" != *$'\n'* && "${!required}" != *$'\r'* ]] || {
    echo "The PostgreSQL role login proof is missing a safe required field." >&2
    exit 1
  }
done
for role in "$POSTGRES_USER" "$LECTURESIFT_APP_DB_USER" "$LECTURESIFT_WORKER_DB_USER"; do
  [[ "$role" =~ ^[a-z_][a-z0-9_]{0,62}$ ]] || {
    echo "A PostgreSQL proof role name is invalid." >&2
    exit 1
  }
done
[[ "$POSTGRES_DB" =~ ^[a-z_][a-z0-9_]{0,62}$ ]] || {
  echo "The PostgreSQL proof database name is invalid." >&2
  exit 1
}

compose=(docker compose --project-directory "$ROOT_DIR" --file "$ROOT_DIR/compose.yaml")
container="$("${compose[@]}" ps -q postgres)"
[[ "$container" =~ ^[0-9a-f]{64}$ ]] || {
  echo "The exact target PostgreSQL container could not be identified." >&2
  exit 1
}

postgres_identity() {
  docker inspect --format \
    '{{.Id}}|{{.Config.Image}}|{{index .Config.Labels "com.docker.compose.project"}}|{{index .Config.Labels "com.docker.compose.service"}}|{{.State.Running}}|{{if .State.Health}}{{.State.Health.Status}}{{end}}|{{.RestartCount}}|{{.State.StartedAt}}' \
    "$container"
}

identity_before="$(postgres_identity)" || exit 1
IFS='|' read -r postgres_id postgres_image postgres_project postgres_service \
  postgres_running postgres_health postgres_restarts postgres_started <<<"$identity_before"
[[ "$postgres_id" == "$container" && "$postgres_image" == "postgres:18-bookworm@sha256:1c59e2c3c818eaa0f0628f695b36e7c9e362d6b219b36a54a32df645cbd7e1af" &&
   "$postgres_project" == "lecturesift" && "$postgres_service" == "postgres" &&
   "$postgres_running" == "true" && "$postgres_health" == "healthy" &&
   "$postgres_restarts" =~ ^[0-9]+$ && -n "$postgres_started" ]] || {
  echo "The target PostgreSQL container identity or health is invalid." >&2
  exit 1
}

raw="$(mktemp -- /var/tmp/lecturesift-role-login-proof.XXXXXXXX)"
safe="$(mktemp -- /var/tmp/lecturesift-role-login-proof-safe.XXXXXXXX)"
chmod 0600 "$raw" "$safe"
cleanup() {
  rm -f -- "$raw" "$safe"
  unset POSTGRES_PASSWORD LECTURESIFT_APP_DB_PASSWORD LECTURESIFT_WORKER_DB_PASSWORD
}
trap cleanup EXIT
printf 'ROLE_LOGIN_MANIFEST|v1\n' >"$raw"

probe() {
  local kind="$1" role="$2" password="$3" result
  result="$(
    {
      printf '%s\n' "$password"
      cat <<'SQL'
SELECT jsonb_build_object(
  'kind', :'expected_kind',
  'current_user', current_user,
  'session_user', session_user,
  'database', current_database(),
  'server_version_num', current_setting('server_version_num'),
  'login', r.rolcanlogin,
  'superuser', r.rolsuper,
  'inherit', r.rolinherit,
  'createdb', r.rolcreatedb,
  'createrole', r.rolcreaterole,
  'replication', r.rolreplication,
  'bypassrls', r.rolbypassrls,
  'connect', has_database_privilege(current_user, current_database(), 'CONNECT'),
  'temporary', has_database_privilege(current_user, current_database(), 'TEMP'),
  'public_create', has_schema_privilege(current_user, 'public', 'CREATE'),
  'search_path', regexp_replace(current_setting('search_path'), '[[:space:]]+', '', 'g'),
  'transaction_read_only', current_setting('transaction_read_only') = 'on'
)::text
FROM pg_roles r
WHERE r.rolname = current_user;
SQL
    } |
      docker exec -i "$container" sh -euc '
        IFS= read -r PGPASSWORD
        export PGPASSWORD
        export PGOPTIONS="-c default_transaction_read_only=on -c statement_timeout=10000 -c lock_timeout=3000"
        exec psql --no-psqlrc --quiet --tuples-only --no-align \
          --set ON_ERROR_STOP=1 --host 127.0.0.1 --port 5432 \
          --username "$1" --dbname "$2" --variable=expected_kind="$3" --file -
      ' sh "$role" "$POSTGRES_DB" "$kind"
  )" || return 1
  result="$(printf '%s' "$result" | tr -d '\r')"
  [[ "$result" == \{*\} && "$result" != *$'\n'* ]] || return 1
  printf 'ROLE|%s\n' "$result" >>"$raw"
}

probe owner "$POSTGRES_USER" "$POSTGRES_PASSWORD" || {
  echo "The PostgreSQL owner login proof failed." >&2
  exit 1
}
probe api "$LECTURESIFT_APP_DB_USER" "$LECTURESIFT_APP_DB_PASSWORD" || {
  echo "The PostgreSQL API login proof failed." >&2
  exit 1
}
probe worker "$LECTURESIFT_WORKER_DB_USER" "$LECTURESIFT_WORKER_DB_PASSWORD" || {
  echo "The PostgreSQL worker login proof failed." >&2
  exit 1
}
printf 'ROLE_LOGIN_COMPLETE|v1|3\n' >>"$raw"
python3 "$VALIDATOR" "$raw" \
  --database "$POSTGRES_DB" \
  --owner-user "$POSTGRES_USER" \
  --api-user "$LECTURESIFT_APP_DB_USER" \
  --worker-user "$LECTURESIFT_WORKER_DB_USER" >"$safe" || {
  echo "The PostgreSQL role login transcript is invalid." >&2
  exit 1
}
identity_after="$(postgres_identity)" || exit 1
[[ "$identity_after" == "$identity_before" ]] || {
  echo "The target PostgreSQL container changed during role login proofs." >&2
  exit 1
}
digest="$(sha256sum "$safe" | awk '{print $1}')"
[[ "$digest" =~ ^[0-9a-f]{64}$ ]] || exit 1
printf '%s\n' "$digest"
