#!/usr/bin/env bash
set -Eeuo pipefail
set +x
umask 077

# Prove that neither the source-era host scheduler nor an ad-hoc Compose
# jobs-profile container can publish while provider ownership is in flight.

[[ "$(id -u)" == "0" ]] || {
  echo "Instagram publisher verification must run as root." >&2
  exit 1
}

ROOT_DIR="${LECTURESIFT_ROOT:-/opt/lecturesift}"
[[ "$ROOT_DIR" == "/opt/lecturesift" ]] || {
  echo "Instagram publisher verification failed: the production root is fixed" >&2
  exit 1
}
exec /usr/bin/python3 "$ROOT_DIR/deploy/verify_instagram_publishers_stopped.py"
