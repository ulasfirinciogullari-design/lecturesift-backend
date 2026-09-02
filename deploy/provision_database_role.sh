#!/usr/bin/env bash
set +x
set -euo pipefail

ROOT_DIR="${LECTURESIFT_ROOT:-/opt/lecturesift}"
DB_ENV_FILE="${LECTURESIFT_DB_ENV_FILE:-/etc/lecturesift/postgres.env}"
rehearsal_role_mode="${LECTURESIFT_REHEARSAL_ROLE_MODE:-NO}"
requested_owner_user="${LECTURESIFT_REHEARSAL_OWNER_DB_USER:-}"
requested_owner_password="${LECTURESIFT_REHEARSAL_OWNER_DB_PASSWORD:-}"
requested_owner_database_url="${LECTURESIFT_REHEARSAL_OWNER_DATABASE_URL:-}"
requested_api_user="${LECTURESIFT_REHEARSAL_APP_DB_USER:-}"
requested_api_password="${LECTURESIFT_REHEARSAL_APP_DB_PASSWORD:-}"
requested_worker_user="${LECTURESIFT_REHEARSAL_WORKER_DB_USER:-}"
requested_worker_password="${LECTURESIFT_REHEARSAL_WORKER_DB_PASSWORD:-}"

if [[ "$(id -u)" != "0" ]]; then
  echo "Run PostgreSQL role provisioning as root." >&2
  exit 1
fi
if [[ ! -f "$DB_ENV_FILE" || -L "$DB_ENV_FILE" || \
      "$(stat -c '%u' -- "$DB_ENV_FILE")" != "0" ]]; then
  echo "The PostgreSQL environment file is missing or unsafe." >&2
  exit 1
fi
case "$(stat -c '%a' -- "$DB_ENV_FILE")" in
  400|600) ;;
  *) echo "The PostgreSQL environment file must have mode 0400 or 0600." >&2; exit 1 ;;
esac

set -a
# shellcheck disable=SC1090
source "$DB_ENV_FILE"
set +a
: "${POSTGRES_DB:?Missing POSTGRES_DB}"
: "${POSTGRES_USER:?Missing POSTGRES_USER}"
: "${POSTGRES_PASSWORD:?Missing POSTGRES_PASSWORD}"
: "${LECTURESIFT_APP_DB_USER:?Missing LECTURESIFT_APP_DB_USER}"
: "${LECTURESIFT_APP_DB_PASSWORD:?Missing LECTURESIFT_APP_DB_PASSWORD}"
: "${LECTURESIFT_WORKER_DB_USER:?Missing LECTURESIFT_WORKER_DB_USER}"
: "${LECTURESIFT_WORKER_DB_PASSWORD:?Missing LECTURESIFT_WORKER_DB_PASSWORD}"

compose=(docker compose --project-directory "$ROOT_DIR" --file "$ROOT_DIR/compose.yaml")
target_db="${LECTURESIFT_PROVISION_DATABASE:-$POSTGRES_DB}"
migration_network="lecturesift_rehearsal_migration"
migration_container="lecturesift-migration-rehearsal"
migration_purpose="candidate-database-migration"
migration_network_id=""
migration_container_id=""
migration_postgres_id=""
migration_redis_id=""
migration_redis_ip=""

resolve_rehearsal_production_endpoints() {
  local postgres_record redis_record redis_networks
  migration_postgres_id="$("${compose[@]}" ps -q postgres)" || return 1
  migration_redis_id="$("${compose[@]}" ps -q redis)" || return 1
  [[ "$migration_postgres_id" =~ ^[0-9a-f]{64}$ && \
     "$migration_redis_id" =~ ^[0-9a-f]{64}$ && \
     "$migration_postgres_id" != "$migration_redis_id" ]] || return 1
  postgres_record="$(docker container inspect --format \
    '{{.Id}}|{{.Name}}|{{ index .Config.Labels "com.docker.compose.project" }}|{{ index .Config.Labels "com.docker.compose.service" }}|{{.State.Running}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}missing{{end}}' \
    "$migration_postgres_id")" || return 1
  redis_record="$(docker container inspect --format \
    '{{.Id}}|{{.Name}}|{{ index .Config.Labels "com.docker.compose.project" }}|{{ index .Config.Labels "com.docker.compose.service" }}|{{.State.Running}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}missing{{end}}' \
    "$migration_redis_id")" || return 1
  [[ "$postgres_record" == \
     "$migration_postgres_id|/lecturesift-postgres-1|lecturesift|postgres|true|healthy" && \
     "$redis_record" == \
     "$migration_redis_id|/lecturesift-redis-1|lecturesift|redis|true|healthy" ]] || return 1
  redis_networks="$(docker container inspect --format \
    '{{range $name, $_ := .NetworkSettings.Networks}}{{println $name}}{{end}}' \
    "$migration_redis_id" | sed '/^$/d')" || return 1
  [[ "$redis_networks" == "lecturesift_backend" ]] || return 1
  migration_redis_ip="$(docker container inspect --format \
    '{{with index .NetworkSettings.Networks "lecturesift_backend"}}{{.IPAddress}}{{end}}' \
    "$migration_redis_id")" || return 1
  python3 - "$migration_redis_ip" <<'PY'
import ipaddress
import sys

address = ipaddress.ip_address(sys.argv[1])
if address.version != 4 or address.is_unspecified or address.is_loopback:
    raise SystemExit(1)
PY
}

validate_migration_network_metadata() {
  local metadata
  metadata="$(docker network inspect --format \
    '{{.Id}}|{{.Name}}|{{.Driver}}|{{.Internal}}|{{.Scope}}|{{ index .Labels "lecturesift.rehearsal" }}|{{ index .Labels "lecturesift.rehearsal.run" }}|{{ index .Labels "lecturesift.rehearsal.purpose" }}' \
    "$migration_network")" || return 1
  [[ "$metadata" == \
     "$migration_network_id|$migration_network|bridge|true|local|true|$rehearsal_suffix|$migration_purpose" ]]
}

validate_migration_network_topology() {
  local expected_candidate="$1" endpoints endpoint_id endpoint_name endpoint_count=0
  local postgres_seen="false" candidate_seen="false" postgres_aliases
  validate_migration_network_metadata || return 1
  endpoints="$(docker network inspect --format \
    '{{range $id, $item := .Containers}}{{printf "%s|%s\n" $id $item.Name}}{{end}}' \
    "$migration_network_id")" || return 1
  while IFS='|' read -r endpoint_id endpoint_name; do
    [[ -n "$endpoint_id" ]] || continue
    endpoint_count=$((endpoint_count + 1))
    if [[ "$endpoint_id" == "$migration_postgres_id" && \
          "$endpoint_name" == "lecturesift-postgres-1" ]]; then
      postgres_seen="true"
    elif [[ "$expected_candidate" == "true" && \
            "$endpoint_id" == "$migration_container_id" && \
            "$endpoint_name" == "$migration_container" ]]; then
      candidate_seen="true"
    else
      return 1
    fi
  done <<<"$endpoints"
  [[ "$postgres_seen" == "true" ]] || return 1
  if [[ "$expected_candidate" == "true" ]]; then
    [[ "$candidate_seen" == "true" && "$endpoint_count" == "2" ]] || return 1
  else
    [[ "$candidate_seen" == "false" && "$endpoint_count" == "1" ]] || return 1
  fi
  postgres_aliases="$(docker container inspect --format \
    "{{range (index .NetworkSettings.Networks \"$migration_network\").Aliases}}{{println .}}{{end}}" \
    "$migration_postgres_id")" || return 1
  grep -Fqx postgres <<<"$postgres_aliases"
}

validate_migration_container() {
  local metadata networks candidate_env env_key database_url_count=0
  local rehearsal_count=0 forbidden_redis_ip_count=0
  metadata="$(docker container inspect --format \
    '{{.Id}}|{{.Name}}|{{.Image}}|{{.HostConfig.ReadonlyRootfs}}|{{.HostConfig.Init}}|{{.HostConfig.NetworkMode}}|{{.HostConfig.PidsLimit}}|{{.HostConfig.Memory}}|{{.HostConfig.MemorySwap}}|{{.Config.User}}|{{ index .Config.Labels "lecturesift.rehearsal" }}|{{ index .Config.Labels "lecturesift.rehearsal.run" }}|{{ index .Config.Labels "lecturesift.rehearsal.purpose" }}' \
    "$migration_container_id")" || return 1
  [[ "$metadata" == \
     "$migration_container_id|/$migration_container|$migration_image_id|true|true|$migration_network_id|128|536870912|536870912|lecturesift|true|$rehearsal_suffix|$migration_purpose" ]] || return 1
  networks="$(docker container inspect --format \
    '{{range $name, $_ := .NetworkSettings.Networks}}{{println $name}}{{end}}' \
    "$migration_container_id" | sed '/^$/d')" || return 1
  [[ "$networks" == "$migration_network" && "$networks" != *"lecturesift_backend"* ]] || return 1
  docker container inspect --format '{{range .HostConfig.SecurityOpt}}{{println .}}{{end}}' \
    "$migration_container_id" | grep -Fqx no-new-privileges || return 1
  docker container inspect --format '{{range .HostConfig.CapDrop}}{{println .}}{{end}}' \
    "$migration_container_id" | grep -Fqx ALL || return 1
  while IFS= read -r candidate_env; do
    env_key="${candidate_env%%=*}"
    case "$candidate_env" in
      DATABASE_URL=*)
        database_url_count=$((database_url_count + 1))
        [[ "${candidate_env#DATABASE_URL=}" == "$requested_owner_database_url" ]] || return 1
        ;;
      LECTURESIFT_REHEARSAL=1) rehearsal_count=$((rehearsal_count + 1)) ;;
      LECTURESIFT_FORBIDDEN_REDIS_IP=*)
        forbidden_redis_ip_count=$((forbidden_redis_ip_count + 1))
        [[ "${candidate_env#LECTURESIFT_FORBIDDEN_REDIS_IP=}" == "$migration_redis_ip" ]] || return 1
        ;;
      *)
        case "$env_key" in
          POSTGRES_USER|POSTGRES_PASSWORD|LECTURESIFT_APP_DB_PASSWORD|LECTURESIFT_WORKER_DB_PASSWORD|\
          REDIS_URL|CELERY_BROKER_URL|CELERY_RESULT_BACKEND|OPENAI_API_KEY|\
          S3_ACCESS_KEY_ID|S3_SECRET_ACCESS_KEY|AWS_ACCESS_KEY_ID|AWS_SECRET_ACCESS_KEY|\
          BILLING_SESSION_SECRET|BILLING_LEGACY_SESSION_SECRET_HEX|\
          PAYMENT_TOKEN_BINDING_SECRET|PAYMENT_TOKEN_BINDING_LEGACY_SECRET|\
          INSTAGRAM_ACCESS_TOKEN|INSTAGRAM_APP_SECRET|INSTAGRAM_ADMIN_TOKEN|\
          IYZICO_API_KEY|IYZICO_SECRET_KEY|PAYTR_MERCHANT_KEY|PAYTR_MERCHANT_SALT|\
          RESEND_API_KEY|SMTP_PASSWORD|ADMIN_ADMIN) return 1 ;;
        esac
        ;;
    esac
  done < <(docker container inspect --format '{{range .Config.Env}}{{println .}}{{end}}' \
    "$migration_container_id")
  [[ "$database_url_count" == "1" && "$rehearsal_count" == "1" && \
     "$forbidden_redis_ip_count" == "1" ]]
}

cleanup_candidate_migration() {
  local labels endpoints endpoint_id endpoint_name candidate_present="false"
  local network_present="false" postgres_attached="false" candidate_attached="false"
  if docker container inspect "$migration_container" >/dev/null 2>&1; then
    candidate_present="true"
    labels="$(docker container inspect --format \
      '{{.Id}}|{{.Name}}|{{ index .Config.Labels "lecturesift.rehearsal" }}|{{ index .Config.Labels "lecturesift.rehearsal.run" }}|{{ index .Config.Labels "lecturesift.rehearsal.purpose" }}' \
      "$migration_container")" || return 1
    [[ "$labels" == "${migration_container_id:-$(docker container inspect --format '{{.Id}}' "$migration_container")}|/$migration_container|true|$rehearsal_suffix|$migration_purpose" ]] || return 1
    migration_container_id="$(docker container inspect --format '{{.Id}}' "$migration_container")" || return 1
    [[ "$(docker container inspect --format \
      '{{range $name, $_ := .NetworkSettings.Networks}}{{println $name}}{{end}}' \
      "$migration_container" | sed '/^$/d')" == "$migration_network" ]] || return 1
  fi
  if docker network inspect "$migration_network" >/dev/null 2>&1; then
    network_present="true"
    [[ -n "$migration_network_id" ]] || \
      migration_network_id="$(docker network inspect --format '{{.Id}}' "$migration_network")" || return 1
    validate_migration_network_metadata || return 1
    resolve_rehearsal_production_endpoints || return 1
    endpoints="$(docker network inspect --format \
      '{{range $id, $item := .Containers}}{{printf "%s|%s\n" $id $item.Name}}{{end}}' \
      "$migration_network_id")" || return 1
    while IFS='|' read -r endpoint_id endpoint_name; do
      [[ -n "$endpoint_id" ]] || continue
      if [[ "$endpoint_id" == "$migration_postgres_id" && \
            "$endpoint_name" == "lecturesift-postgres-1" ]]; then
        postgres_attached="true"
      elif [[ "$candidate_present" == "true" && \
              "$endpoint_id" == "$migration_container_id" && \
              "$endpoint_name" == "$migration_container" ]]; then
        candidate_attached="true"
      else
        return 1
      fi
    done <<<"$endpoints"
  fi
  [[ "$candidate_attached" != "true" || "$candidate_present" == "true" ]] || return 1
  if [[ "$candidate_present" == "true" ]]; then
    docker rm -f "$migration_container_id" >/dev/null || return 1
  fi
  if [[ "$network_present" == "true" ]]; then
    if [[ "$postgres_attached" == "true" ]]; then
      docker network disconnect "$migration_network_id" "$migration_postgres_id" >/dev/null || return 1
    fi
    [[ -z "$(docker network inspect --format \
      '{{range .Containers}}{{println .Name}}{{end}}' "$migration_network_id")" ]] || return 1
    docker network rm "$migration_network_id" >/dev/null || return 1
  fi
  ! docker container inspect "$migration_container" >/dev/null 2>&1 && \
    ! docker network inspect "$migration_network" >/dev/null 2>&1
}

cleanup_candidate_migration_on_exit() {
  local original_status="$?"
  trap - EXIT INT TERM HUP
  set +e
  cleanup_candidate_migration
  local cleanup_status="$?"
  set -e
  if [[ "$cleanup_status" != "0" ]]; then
    echo "Candidate migration cleanup failed; production startup must remain blocked." >&2
    original_status=1
  fi
  exit "$original_status"
}

if [[ "$rehearsal_role_mode" == "YES" ]]; then
  [[ "$target_db" =~ ^lecturesift_rehearsal_([0-9]{14})$ ]] || {
    echo "Rehearsal roles require a timestamped rehearsal database." >&2
    exit 1
  }
  rehearsal_suffix="${BASH_REMATCH[1]}"
  expected_owner_user="lecturesift_rehearsal_owner_$rehearsal_suffix"
  expected_api_user="lecturesift_rehearsal_api_$rehearsal_suffix"
  expected_worker_user="lecturesift_rehearsal_worker_$rehearsal_suffix"
  expected_owner_database_url="postgresql+psycopg://${expected_owner_user}:${requested_owner_password}@postgres:5432/${target_db}"
  [[ "$requested_owner_user" == "$expected_owner_user" &&
     "$requested_owner_database_url" == "$expected_owner_database_url" &&
     "$requested_api_user" == "$expected_api_user" &&
     "$requested_worker_user" == "$expected_worker_user" &&
     ${#requested_owner_password} -ge 32 &&
     ${#requested_api_password} -ge 32 &&
     ${#requested_worker_password} -ge 32 &&
     "$requested_owner_password" != "$requested_api_password" &&
     "$requested_owner_password" != "$requested_worker_password" &&
     "$requested_api_password" != "$requested_worker_password" ]] || {
    echo "Rehearsal owner/runtime identities are not isolated from production." >&2
    exit 1
  }
  for rehearsal_secret in \
    "$requested_owner_password" "$requested_api_password" "$requested_worker_password"; do
    [[ "$rehearsal_secret" != "${POSTGRES_PASSWORD:-}" &&
       "$rehearsal_secret" != "$LECTURESIFT_APP_DB_PASSWORD" &&
       "$rehearsal_secret" != "$LECTURESIFT_WORKER_DB_PASSWORD" ]] || {
      echo "A rehearsal database secret equals a production database secret." >&2
      exit 1
    }
  done
  for rehearsal_user in \
    "$requested_owner_user" "$requested_api_user" "$requested_worker_user"; do
    [[ "$rehearsal_user" != "$POSTGRES_USER" &&
       "$rehearsal_user" != "$LECTURESIFT_APP_DB_USER" &&
       "$rehearsal_user" != "$LECTURESIFT_WORKER_DB_USER" ]] || {
      echo "A rehearsal database role equals a production database role." >&2
      exit 1
    }
  done
  LECTURESIFT_APP_DB_USER="$requested_api_user"
  LECTURESIFT_APP_DB_PASSWORD="$requested_api_password"
  LECTURESIFT_WORKER_DB_USER="$requested_worker_user"
  LECTURESIFT_WORKER_DB_PASSWORD="$requested_worker_password"
  export LECTURESIFT_APP_DB_USER LECTURESIFT_APP_DB_PASSWORD
  export LECTURESIFT_WORKER_DB_USER LECTURESIFT_WORKER_DB_PASSWORD
  LECTURESIFT_SCHEMA_OWNER_USER="$requested_owner_user"
  LECTURESIFT_REHEARSAL_ROLE_COMMENT="lecturesift.rehearsal-role:v2:$target_db"
  export LECTURESIFT_SCHEMA_OWNER_USER LECTURESIFT_REHEARSAL_ROLE_COMMENT
  rehearsal_identity="$(
    "${compose[@]}" exec -T postgres psql --no-psqlrc --quiet \
      --tuples-only --no-align --username "$POSTGRES_USER" --dbname postgres \
      --variable=target_db="$target_db" \
      --variable=owner_user="$LECTURESIFT_SCHEMA_OWNER_USER" \
      --variable=api_user="$LECTURESIFT_APP_DB_USER" \
      --variable=worker_user="$LECTURESIFT_WORKER_DB_USER" \
      --variable=role_comment="$LECTURESIFT_REHEARSAL_ROLE_COMMENT" <<'SQL'
SELECT
  (SELECT count(*) FROM pg_roles
   WHERE rolname = :'owner_user'
     AND NOT (rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls)
     AND NOT rolinherit AND rolcanlogin
     AND coalesce(shobj_description(oid, 'pg_authid'), '') = :'role_comment')::text || '|' ||
  (SELECT count(*) FROM pg_roles WHERE rolname IN (:'api_user', :'worker_user'))::text || '|' ||
  (SELECT count(*) FROM pg_database d JOIN pg_roles r ON r.oid = d.datdba
   WHERE d.datname = :'target_db' AND r.rolname = :'owner_user')::text;
SQL
  )"
  [[ "$(printf '%s' "$rehearsal_identity" | tr -d '\r[:space:]')" == "1|0|1" ]] || {
    echo "The clone owner/database provenance is invalid or a runtime role already exists." >&2
    exit 1
  }
  owner_login="$(
    PGPASSWORD="$requested_owner_password" \
      "${compose[@]}" exec -T -e PGPASSWORD postgres \
      psql --no-psqlrc --quiet --tuples-only --no-align \
      --host 127.0.0.1 --port 5432 --username "$requested_owner_user" \
      --dbname "$target_db" --command 'SELECT current_user'
  )" || {
    echo "The isolated clone-owner password/login probe failed." >&2
    exit 1
  }
  [[ "$(printf '%s' "$owner_login" | tr -d '\r\n')" == "$requested_owner_user" ]] || {
    echo "The isolated clone-owner login resolved to an unexpected role." >&2
    exit 1
  }
elif [[ "$rehearsal_role_mode" != "NO" ]]; then
  echo "Unknown rehearsal role mode." >&2
  exit 1
fi

# Bootstrap/rotate logins first. The migration container then creates every
# table as the maintenance owner, after which a second pass installs and probes
# the runtime grants. Any failed phase prevents API/worker startup.
"${compose[@]}" exec -T \
  -e "LECTURESIFT_PROVISION_DATABASE=$target_db" \
  -e LECTURESIFT_PROVISION_PHASE=bootstrap \
  -e LECTURESIFT_APP_DB_USER -e LECTURESIFT_APP_DB_PASSWORD \
  -e LECTURESIFT_WORKER_DB_USER -e LECTURESIFT_WORKER_DB_PASSWORD \
  -e LECTURESIFT_SCHEMA_OWNER_USER -e LECTURESIFT_REHEARSAL_ROLE_COMMENT \
  postgres /bin/bash /usr/local/sbin/lecturesift-provision-app-role

if [[ "$rehearsal_role_mode" == "YES" ]]; then
  # Candidate migration gets a one-use, internal network containing only the
  # exact production PostgreSQL container under the clone-only `postgres`
  # alias. It is never attached to `lecturesift_backend`, where the
  # unauthenticated production Redis service lives.
  if docker container inspect "$migration_container" >/dev/null 2>&1 || \
     docker network inspect "$migration_network" >/dev/null 2>&1; then
    echo "A fixed-name candidate migration resource already exists; reconcile it first." >&2
    exit 1
  fi
  resolve_rehearsal_production_endpoints || {
    echo "Production PostgreSQL/Redis identity or topology is not exact." >&2
    exit 1
  }
  migration_image_id="$(docker image inspect --format '{{.Id}}' lecturesift-backend:local)" || {
    echo "The locally admitted candidate image is unavailable." >&2
    exit 1
  }
  [[ "$migration_image_id" =~ ^sha256:[0-9a-f]{64}$ ]] || exit 1
  trap cleanup_candidate_migration_on_exit EXIT
  trap 'exit 1' INT TERM HUP
  migration_network_id="$(docker network create --driver bridge --internal \
    --label lecturesift.rehearsal=true \
    --label "lecturesift.rehearsal.run=$rehearsal_suffix" \
    --label "lecturesift.rehearsal.purpose=$migration_purpose" \
    "$migration_network")"
  [[ "$migration_network_id" =~ ^[0-9a-f]{64}$ ]] || exit 1
  validate_migration_network_metadata || exit 1
  [[ -z "$(docker network inspect --format \
    '{{range .Containers}}{{println .Name}}{{end}}' "$migration_network_id")" ]] || exit 1
  docker network connect --alias postgres "$migration_network_id" \
    "$migration_postgres_id" >/dev/null
  validate_migration_network_topology false || {
    echo "Candidate migration network did not contain exactly PostgreSQL." >&2
    exit 1
  }

  migration_container_id="$(
    DATABASE_URL="$requested_owner_database_url" \
    LECTURESIFT_REHEARSAL=1 \
    LECTURESIFT_FORBIDDEN_REDIS_IP="$migration_redis_ip" \
      docker create --pull=never --name "$migration_container" \
        --label lecturesift.rehearsal=true \
        --label "lecturesift.rehearsal.run=$rehearsal_suffix" \
        --label "lecturesift.rehearsal.purpose=$migration_purpose" \
        --network "$migration_network_id" \
        --env DATABASE_URL --env LECTURESIFT_REHEARSAL=1 \
        --env LECTURESIFT_FORBIDDEN_REDIS_IP \
        --read-only --init --user lecturesift --cap-drop ALL \
        --security-opt no-new-privileges --pids-limit 128 \
        --cpus 1 --memory 512m --memory-swap 512m \
        --tmpfs /tmp:rw,noexec,nosuid,nodev,size=128m,mode=1777 \
        --tmpfs /var/lib/lecturesift:rw,noexec,nosuid,nodev,size=64m,mode=0700 \
        lecturesift-backend:local python -c '
import os
import socket

addresses = socket.getaddrinfo("postgres", 5432, type=socket.SOCK_STREAM)
if not addresses:
    raise SystemExit("candidate PostgreSQL alias did not resolve")
for host in ("redis", "lecturesift-redis-1", "lecturesift-redis-rehearsal"):
    try:
        socket.getaddrinfo(host, 6379, type=socket.SOCK_STREAM)
    except socket.gaierror:
        continue
    raise SystemExit("production Redis became resolvable during candidate migration")
try:
    connection = socket.create_connection(
        (os.environ["LECTURESIFT_FORBIDDEN_REDIS_IP"], 6379), timeout=1.0
    )
except OSError:
    pass
else:
    connection.close()
    raise SystemExit("production Redis became reachable during candidate migration")
print("MIGRATION_REDIS_ISOLATION_OK", flush=True)
from lecturesift.rollout_service import init_rollout_database
from lecturesift.costs import init_cost_database
init_rollout_database()
init_cost_database()
'
  )"
  [[ "$migration_container_id" =~ ^[0-9a-f]{64}$ ]] || exit 1
  validate_migration_container || {
    echo "Candidate migration container identity or secret boundary is invalid." >&2
    exit 1
  }
  docker start "$migration_container_id" >/dev/null
  validate_migration_network_topology true || {
    echo "Candidate migration network gained an unexpected endpoint." >&2
    exit 1
  }
  migration_exit="$(docker wait "$migration_container_id")" || exit 1
  [[ "$migration_exit" == "0" ]] || {
    echo "Candidate database migration failed inside the isolated network." >&2
    exit 1
  }
  docker logs "$migration_container_id" 2>&1 | \
    grep -Fqx MIGRATION_REDIS_ISOLATION_OK || {
      echo "Candidate migration did not prove production Redis isolation." >&2
      exit 1
    }
  cleanup_candidate_migration || {
    echo "Candidate migration resources could not be removed exactly." >&2
    exit 1
  }
  trap - EXIT INT TERM HUP
else
  "${compose[@]}" --profile maintenance run --rm --no-deps \
    -e "LECTURESIFT_PROVISION_DATABASE=$target_db" migration
fi

"${compose[@]}" exec -T \
  -e "LECTURESIFT_PROVISION_DATABASE=$target_db" \
  -e LECTURESIFT_PROVISION_PHASE=runtime \
  -e LECTURESIFT_APP_DB_USER -e LECTURESIFT_APP_DB_PASSWORD \
  -e LECTURESIFT_WORKER_DB_USER -e LECTURESIFT_WORKER_DB_PASSWORD \
  -e LECTURESIFT_SCHEMA_OWNER_USER -e LECTURESIFT_REHEARSAL_ROLE_COMMENT \
  postgres /bin/bash /usr/local/sbin/lecturesift-provision-app-role
