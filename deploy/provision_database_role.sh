#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="${LECTURESIFT_ROOT:-/opt/lecturesift}"
DB_ENV_FILE="${LECTURESIFT_DB_ENV_FILE:-/etc/lecturesift/postgres.env}"

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
: "${LECTURESIFT_APP_DB_USER:?Missing LECTURESIFT_APP_DB_USER}"
: "${LECTURESIFT_APP_DB_PASSWORD:?Missing LECTURESIFT_APP_DB_PASSWORD}"
: "${LECTURESIFT_WORKER_DB_USER:?Missing LECTURESIFT_WORKER_DB_USER}"
: "${LECTURESIFT_WORKER_DB_PASSWORD:?Missing LECTURESIFT_WORKER_DB_PASSWORD}"

compose=(docker compose --project-directory "$ROOT_DIR" --file "$ROOT_DIR/compose.yaml")
target_db="${LECTURESIFT_PROVISION_DATABASE:-$POSTGRES_DB}"

# Bootstrap/rotate logins first. The migration container then creates every
# table as the maintenance owner, after which a second pass installs and probes
# the runtime grants. Any failed phase prevents API/worker startup.
"${compose[@]}" exec -T \
  -e "LECTURESIFT_PROVISION_DATABASE=$target_db" \
  -e LECTURESIFT_PROVISION_PHASE=bootstrap \
  postgres /bin/bash /usr/local/sbin/lecturesift-provision-app-role

"${compose[@]}" --profile maintenance run --rm --no-deps \
  -e "LECTURESIFT_PROVISION_DATABASE=$target_db" migration

"${compose[@]}" exec -T \
  -e "LECTURESIFT_PROVISION_DATABASE=$target_db" \
  -e LECTURESIFT_PROVISION_PHASE=runtime \
  postgres /bin/bash /usr/local/sbin/lecturesift-provision-app-role
