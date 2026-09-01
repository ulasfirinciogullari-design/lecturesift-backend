#!/usr/bin/env bash
set +x
set -euo pipefail

: "${POSTGRES_DB:?Missing POSTGRES_DB}"
: "${POSTGRES_USER:?Missing POSTGRES_USER}"
: "${LECTURESIFT_APP_DB_USER:?Missing LECTURESIFT_APP_DB_USER}"
: "${LECTURESIFT_APP_DB_PASSWORD:?Missing LECTURESIFT_APP_DB_PASSWORD}"
: "${LECTURESIFT_WORKER_DB_USER:?Missing LECTURESIFT_WORKER_DB_USER}"
: "${LECTURESIFT_WORKER_DB_PASSWORD:?Missing LECTURESIFT_WORKER_DB_PASSWORD}"

target_db="${LECTURESIFT_PROVISION_DATABASE:-$POSTGRES_DB}"
phase="${LECTURESIFT_PROVISION_PHASE:-bootstrap}"
rehearsal_role_comment="${LECTURESIFT_REHEARSAL_ROLE_COMMENT:-}"
schema_owner="${LECTURESIFT_SCHEMA_OWNER_USER:-$POSTGRES_USER}"
role_pattern='^[a-z_][a-z0-9_]{0,62}$'
case "$phase" in
  bootstrap|runtime) ;;
  *) echo "Unknown PostgreSQL provisioning phase." >&2; exit 1 ;;
esac
if [[ -n "$rehearsal_role_comment" &&
      ( ! "$target_db" =~ ^lecturesift_rehearsal_[0-9]{14}$ ||
        "$rehearsal_role_comment" != "lecturesift.rehearsal-role:v2:$target_db" ) ]]; then
  echo "Invalid rehearsal role provenance." >&2
  exit 1
fi
for identifier in \
  "$POSTGRES_USER" "$schema_owner" "$LECTURESIFT_APP_DB_USER" \
  "$LECTURESIFT_WORKER_DB_USER" "$target_db"; do
  if [[ ! "$identifier" =~ $role_pattern ]]; then
    echo "Unsafe PostgreSQL role or database identifier." >&2
    exit 1
  fi
done
if [[ "$schema_owner" == "$LECTURESIFT_APP_DB_USER" || \
      "$schema_owner" == "$LECTURESIFT_WORKER_DB_USER" || \
      "$POSTGRES_USER" == "$LECTURESIFT_APP_DB_USER" || \
      "$POSTGRES_USER" == "$LECTURESIFT_WORKER_DB_USER" || \
      "$LECTURESIFT_APP_DB_USER" == "$LECTURESIFT_WORKER_DB_USER" ]]; then
  echo "PostgreSQL owner, API and worker roles must be distinct." >&2
  exit 1
fi
if [[ ${#LECTURESIFT_APP_DB_PASSWORD} -lt 24 || \
      ${#LECTURESIFT_WORKER_DB_PASSWORD} -lt 24 || \
      "$LECTURESIFT_APP_DB_PASSWORD" == "$LECTURESIFT_WORKER_DB_PASSWORD" ]]; then
  echo "API and worker database passwords must be distinct and at least 24 characters." >&2
  exit 1
fi

# Create/rotate both runtime logins before the owner-only schema migration.
# Password values exist only inside the private database container; psql never
# echoes the generated SQL.
psql --no-psqlrc --quiet --set=ON_ERROR_STOP=1 \
  --username "$POSTGRES_USER" --dbname postgres \
  --variable=api_user="$LECTURESIFT_APP_DB_USER" \
  --variable=api_password="$LECTURESIFT_APP_DB_PASSWORD" \
  --variable=worker_user="$LECTURESIFT_WORKER_DB_USER" \
  --variable=worker_password="$LECTURESIFT_WORKER_DB_PASSWORD" \
  --variable=target_db="$target_db" \
  --variable=rehearsal_role_comment="$rehearsal_role_comment" <<'SQL'
BEGIN;
SELECT format(
  'CREATE ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD %L',
  :'api_user', :'api_password'
)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'api_user')
\gexec
SELECT format(
  'ALTER ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD %L',
  :'api_user', :'api_password'
)
\gexec
SELECT format('COMMENT ON ROLE %I IS %L', :'api_user', :'rehearsal_role_comment')
WHERE :'rehearsal_role_comment' <> ''
\gexec
SELECT format(
  'CREATE ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD %L',
  :'worker_user', :'worker_password'
)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'worker_user')
\gexec
SELECT format(
  'ALTER ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD %L',
  :'worker_user', :'worker_password'
)
\gexec
SELECT format('COMMENT ON ROLE %I IS %L', :'worker_user', :'rehearsal_role_comment')
WHERE :'rehearsal_role_comment' <> ''
\gexec
SELECT format('REVOKE ALL PRIVILEGES ON DATABASE %I FROM %I', :'target_db', :'api_user')
\gexec
SELECT format('REVOKE ALL PRIVILEGES ON DATABASE %I FROM %I', :'target_db', :'worker_user')
\gexec
SELECT format('REVOKE TEMPORARY ON DATABASE %I FROM PUBLIC', :'target_db')
\gexec
SELECT format('GRANT CONNECT ON DATABASE %I TO %I', :'target_db', :'api_user')
\gexec
SELECT format('GRANT CONNECT ON DATABASE %I TO %I', :'target_db', :'worker_user')
\gexec
SELECT format('ALTER ROLE %I IN DATABASE %I SET search_path TO public', :'api_user', :'target_db')
\gexec
SELECT format(
  'ALTER ROLE %I IN DATABASE %I SET search_path TO lecturesift_worker, public',
  :'worker_user', :'target_db'
)
\gexec
COMMIT;
SQL

psql --no-psqlrc --quiet --set=ON_ERROR_STOP=1 \
  --username "$POSTGRES_USER" --dbname "$target_db" \
  --variable=api_user="$LECTURESIFT_APP_DB_USER" \
  --variable=worker_user="$LECTURESIFT_WORKER_DB_USER" \
  --variable=owner_user="$schema_owner" <<'SQL'
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
SELECT format('REVOKE ALL ON SCHEMA public FROM %I', :'api_user')
\gexec
SELECT format('REVOKE ALL ON SCHEMA public FROM %I', :'worker_user')
\gexec
SELECT format('GRANT USAGE ON SCHEMA public TO %I', :'api_user')
\gexec
SELECT format('GRANT USAGE ON SCHEMA public TO %I', :'worker_user')
\gexec

CREATE SCHEMA IF NOT EXISTS lecturesift_worker;
SELECT format('ALTER SCHEMA lecturesift_worker OWNER TO %I', :'owner_user')
\gexec
REVOKE ALL ON SCHEMA lecturesift_worker FROM PUBLIC;
SELECT format('REVOKE ALL ON SCHEMA lecturesift_worker FROM %I', :'api_user')
\gexec
SELECT format('REVOKE ALL ON SCHEMA lecturesift_worker FROM %I', :'worker_user')
\gexec
SELECT format('GRANT USAGE ON SCHEMA lecturesift_worker TO %I', :'worker_user')
\gexec

# The API can read and mutate application rows, but it cannot create, replace
# or drop schema objects. Only the owner-run migration container performs DDL.
SELECT format('REVOKE ALL ON ALL TABLES IN SCHEMA public FROM %I', :'api_user')
\gexec
SELECT format('REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM %I', :'api_user')
\gexec
SELECT format('REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM %I', :'api_user')
\gexec
SELECT format('GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO %I', :'api_user')
\gexec
SELECT format('GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO %I', :'api_user')
\gexec
SELECT format('GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO %I', :'api_user')
\gexec
SELECT format(
  'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO %I',
  :'owner_user', :'api_user'
)
\gexec
SELECT format(
  'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO %I',
  :'owner_user', :'api_user'
)
\gexec
SELECT format(
  'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public GRANT EXECUTE ON FUNCTIONS TO %I',
  :'owner_user', :'api_user'
)
\gexec

# The worker receives no privilege on owner tables, sequences or functions.
# Its only database surface is rebuilt below as masked, owner-backed views.
SELECT format('REVOKE ALL ON ALL TABLES IN SCHEMA public FROM %I', :'worker_user')
\gexec
SELECT format('REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM %I', :'worker_user')
\gexec
SELECT format('REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM %I', :'worker_user')
\gexec
SELECT format(
  'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public REVOKE ALL ON TABLES FROM %I',
  :'owner_user', :'worker_user'
)
\gexec
SELECT format(
  'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public REVOKE ALL ON SEQUENCES FROM %I',
  :'owner_user', :'worker_user'
)
\gexec
SELECT format(
  'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public REVOKE ALL ON FUNCTIONS FROM %I',
  :'owner_user', :'worker_user'
)
\gexec
SQL

if [[ "$phase" == "runtime" ]]; then
  # Compatibility views preserve the SQLAlchemy table shape while masking
  # credentials, PII, order details and provider diagnostics. The worker sees
  # only the fields needed to decide an entitlement or charge a completed job.
  psql --no-psqlrc --quiet --set=ON_ERROR_STOP=1 \
    --username "$POSTGRES_USER" --dbname "$target_db" \
    --variable=worker_user="$LECTURESIFT_WORKER_DB_USER" <<'SQL'
CREATE OR REPLACE VIEW lecturesift_worker.billing_users
WITH (security_barrier=true, security_invoker=false) AS
SELECT id,
       ''::varchar(320) AS email,
       ''::varchar(64) AS password_salt,
       ''::varchar(64) AS password_hash,
       credit_minutes,
       timestamptz '1970-01-01 00:00:00+00' AS created_at
FROM public.billing_users;

CREATE OR REPLACE VIEW lecturesift_worker.billing_user_profiles
WITH (security_barrier=true, security_invoker=false) AS
SELECT user_id,
       ''::varchar(80) AS first_name,
       ''::varchar(80) AS last_name,
       NULL::varchar(32) AS phone,
       'ZZ'::varchar(2) AS country_code,
       NULL::timestamptz AS email_verified_at,
       NULL::timestamptz AS phone_verified_at,
       0::integer AS session_version,
       timestamptz '1970-01-01 00:00:00+00' AS created_at,
       timestamptz '1970-01-01 00:00:00+00' AS updated_at
FROM public.billing_user_profiles;

CREATE OR REPLACE VIEW lecturesift_worker.billing_user_preferences
WITH (security_barrier=true, security_invoker=false) AS
SELECT user_id,
       'tr'::varchar(8) AS preferred_language,
       timestamptz '1970-01-01 00:00:00+00' AS updated_at
FROM public.billing_user_preferences;

CREATE OR REPLACE VIEW lecturesift_worker.billing_subscriptions
WITH (security_barrier=true, security_invoker=false) AS
SELECT ''::varchar(36) AS id,
       user_id, plan_code, interval, status, starts_at, ends_at,
       ''::varchar(40) AS source_reference,
       timestamptz '1970-01-01 00:00:00+00' AS created_at
FROM public.billing_subscriptions;

CREATE OR REPLACE VIEW lecturesift_worker.billing_manual_orders
WITH (security_barrier=true, security_invoker=false) AS
SELECT ''::varchar(40) AS reference,
       user_id,
       plan_code,
       ''::varchar(16) AS interval,
       0::integer AS amount_minor,
       ''::varchar(3) AS currency,
       status,
       timestamptz '1970-01-01 00:00:00+00' AS created_at,
       timestamptz '1970-01-01 00:00:00+00' AS updated_at
FROM public.billing_manual_orders
WHERE plan_code = 'credit' AND status = 'paid';

CREATE OR REPLACE VIEW lecturesift_worker.billing_payment_orders
WITH (security_barrier=true, security_invoker=false) AS
SELECT ''::varchar(64) AS reference,
       user_id,
       ''::varchar(24) AS provider,
       plan_code,
       ''::varchar(16) AS interval,
       0::integer AS amount_minor,
       ''::varchar(3) AS currency,
       status,
       0::integer AS provider_amount_minor,
       NULL::varchar(32) AS failure_code,
       NULL::varchar(240) AS failure_message,
       timestamptz '1970-01-01 00:00:00+00' AS created_at,
       timestamptz '1970-01-01 00:00:00+00' AS updated_at
FROM public.billing_payment_orders
WHERE plan_code = 'credit' AND status = 'paid';

CREATE OR REPLACE VIEW lecturesift_worker.billing_usage_events
WITH (security_barrier=true, security_invoker=false) AS
SELECT id, user_id, job_id, plan_code, minutes, occurred_at
FROM public.billing_usage_events;

CREATE OR REPLACE VIEW lecturesift_worker.lecturesift_guest_trials
WITH (security_barrier=true, security_invoker=false) AS
SELECT ''::varchar(64) AS fingerprint_hash,
       user_id,
       job_id,
       media_minutes,
       timestamptz '1970-01-01 00:00:00+00' AS created_at,
       last_seen_at
FROM public.lecturesift_guest_trials;

CREATE OR REPLACE VIEW lecturesift_worker.lecturesift_runtime_metrics
WITH (security_barrier=true, security_invoker=false) AS
SELECT job_id, media_minutes, elapsed_seconds, size_bytes, created_at
FROM public.lecturesift_runtime_metrics;

REVOKE ALL ON ALL TABLES IN SCHEMA lecturesift_worker FROM PUBLIC;
SELECT format('REVOKE ALL ON ALL TABLES IN SCHEMA lecturesift_worker FROM %I', :'worker_user')
\gexec
SELECT format(
  'GRANT SELECT ON lecturesift_worker.billing_users, lecturesift_worker.billing_user_profiles, lecturesift_worker.billing_user_preferences, lecturesift_worker.billing_subscriptions, lecturesift_worker.billing_manual_orders, lecturesift_worker.billing_payment_orders TO %I',
  :'worker_user'
)
\gexec
SELECT format('GRANT UPDATE (credit_minutes) ON lecturesift_worker.billing_users TO %I', :'worker_user')
\gexec
SELECT format('GRANT SELECT, INSERT ON lecturesift_worker.billing_usage_events TO %I', :'worker_user')
\gexec
SELECT format('GRANT SELECT, INSERT ON lecturesift_worker.lecturesift_runtime_metrics TO %I', :'worker_user')
\gexec
SELECT format('GRANT SELECT ON lecturesift_worker.lecturesift_guest_trials TO %I', :'worker_user')
\gexec
SELECT format(
  'GRANT UPDATE (job_id, media_minutes, last_seen_at) ON lecturesift_worker.lecturesift_guest_trials TO %I',
  :'worker_user'
)
\gexec

# Prove the views are not merely granted but actually support the exact
# entitlement/metering writes. Every probe row is rolled back in the same
# transaction; a crash also rolls the open transaction back server-side.
BEGIN;
SELECT 'role-probe-' || substr(md5(random()::text || clock_timestamp()::text), 1, 24) AS probe_user,
       'usage-probe-' || md5(random()::text || clock_timestamp()::text) AS usage_job,
       'runtime-probe-' || md5(random()::text || clock_timestamp()::text) AS runtime_job,
       md5(random()::text) || md5(clock_timestamp()::text) AS probe_fingerprint
\gset
INSERT INTO public.billing_users (
  id, email, password_salt, password_hash, credit_minutes, created_at
) VALUES (
  :'probe_user', :'probe_user' || '@invalid.example', repeat('0', 64), repeat('1', 64), 5, now()
);
INSERT INTO public.lecturesift_guest_trials (
  fingerprint_hash, user_id, job_id, media_minutes, created_at, last_seen_at
) VALUES (:'probe_fingerprint', :'probe_user', NULL, NULL, now(), now());
SET LOCAL ROLE :"worker_user";
SET LOCAL search_path TO lecturesift_worker, public;
SELECT 1 / CASE WHEN email = '' AND password_salt = '' AND password_hash = ''
                     AND credit_minutes = 5
                THEN 1 ELSE 0 END
FROM billing_users WHERE id = :'probe_user';
UPDATE billing_users SET credit_minutes = 4 WHERE id = :'probe_user';
INSERT INTO billing_usage_events (
  id, user_id, job_id, plan_code, minutes, occurred_at
) VALUES (md5(random()::text), :'probe_user', :'usage_job', 'free', 1, now());
UPDATE lecturesift_guest_trials
SET job_id = :'usage_job', media_minutes = 1, last_seen_at = now()
WHERE user_id = :'probe_user';
INSERT INTO lecturesift_runtime_metrics (
  job_id, media_minutes, elapsed_seconds, size_bytes, created_at
) VALUES (:'runtime_job', 1, 1, 1, now());
ROLLBACK;
SQL
fi

safe_role_count="$(
  psql --no-psqlrc --quiet --tuples-only --no-align \
    --username "$POSTGRES_USER" --dbname postgres \
    --variable=api_user="$LECTURESIFT_APP_DB_USER" \
    --variable=worker_user="$LECTURESIFT_WORKER_DB_USER" <<'SQL'
SELECT count(*)
FROM pg_roles
WHERE rolname IN (:'api_user', :'worker_user')
  AND NOT (rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls)
  AND NOT rolinherit
  AND rolcanlogin;
SQL
)"
[[ "$safe_role_count" == "2" ]] || {
  echo "A PostgreSQL runtime role is missing or privileged." >&2
  exit 1
}

probe_role_login() {
  local label="$1" role="$2" password="$3" observed
  observed="$(
    PGPASSWORD="$password" PGCONNECT_TIMEOUT=5 \
      psql --no-psqlrc --quiet --tuples-only --no-align \
      --host 127.0.0.1 --port 5432 --username "$role" --dbname "$target_db" \
      --command 'SELECT current_user'
  )" || {
    echo "The PostgreSQL $label password/login probe failed." >&2
    exit 1
  }
  [[ "$observed" == "$role" ]] || {
    echo "The PostgreSQL $label login resolved to an unexpected role." >&2
    exit 1
  }
}

if [[ "$phase" == "runtime" ]]; then
  # Use TCP rather than the image's local socket so pg_hba password
  # authentication proves that the freshly rotated credentials really work.
  # The initdb bootstrap phase deliberately skips this because the official
  # image's temporary bootstrap server listens on a Unix socket only.
  probe_role_login API "$LECTURESIFT_APP_DB_USER" "$LECTURESIFT_APP_DB_PASSWORD"
  probe_role_login worker "$LECTURESIFT_WORKER_DB_USER" "$LECTURESIFT_WORKER_DB_PASSWORD"

  privilege_probe="$(
    psql --no-psqlrc --quiet --tuples-only --no-align \
      --username "$POSTGRES_USER" --dbname "$target_db" \
      --variable=api_user="$LECTURESIFT_APP_DB_USER" \
      --variable=worker_user="$LECTURESIFT_WORKER_DB_USER" <<'SQL'
SELECT (
  NOT has_schema_privilege(:'api_user', 'public', 'CREATE')
  AND NOT has_schema_privilege(:'worker_user', 'public', 'CREATE')
  AND NOT has_schema_privilege(:'worker_user', 'lecturesift_worker', 'CREATE')
  AND has_table_privilege(:'api_user', 'public.billing_users', 'SELECT')
  AND has_table_privilege(:'api_user', 'public.billing_users', 'INSERT')
  AND has_table_privilege(:'api_user', 'public.billing_users', 'UPDATE')
  AND has_table_privilege(:'api_user', 'public.billing_users', 'DELETE')
  AND NOT has_table_privilege(:'worker_user', 'public.billing_users', 'SELECT')
  AND NOT has_table_privilege(:'worker_user', 'public.billing_subscriptions', 'SELECT')
  AND NOT has_table_privilege(:'worker_user', 'public.billing_payment_orders', 'SELECT')
  AND NOT has_table_privilege(:'worker_user', 'public.billing_auth_tokens', 'SELECT')
  AND NOT has_table_privilege(:'worker_user', 'public.lecturesift_admin_account_events', 'SELECT')
  AND NOT has_table_privilege(:'worker_user', 'public.lecturesift_contact_messages', 'SELECT')
  AND has_table_privilege(:'worker_user', 'lecturesift_worker.billing_users', 'SELECT')
  AND has_column_privilege(:'worker_user', 'lecturesift_worker.billing_users', 'credit_minutes', 'UPDATE')
  AND has_table_privilege(:'worker_user', 'lecturesift_worker.billing_usage_events', 'SELECT')
  AND has_table_privilege(:'worker_user', 'lecturesift_worker.billing_usage_events', 'INSERT')
  AND NOT has_table_privilege(:'worker_user', 'lecturesift_worker.billing_usage_events', 'UPDATE,DELETE')
)::int;
SQL
  )"
  [[ "$privilege_probe" == "1" ]] || {
    echo "PostgreSQL runtime privilege verification failed." >&2
    exit 1
  }
fi

echo "PostgreSQL $phase roles and grants verified for $target_db."
