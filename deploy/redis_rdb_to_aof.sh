#!/bin/sh
set -eu

SOURCE_RDB="/restore/redis-dump.rdb"
DATA_DIR="/data"
SOCKET="/tmp/lecturesift-redis-restore.sock"
CONFIG="/probe/redis.conf"

fail() {
  echo "Redis RDB-to-AOF restore failed: $*" >&2
  exit 1
}

[ "$(id -u)" = "0" ] || fail "the converter must start as root"
[ -f "$SOURCE_RDB" ] && [ ! -L "$SOURCE_RDB" ] || fail "the source RDB is missing or unsafe"
[ -d "$DATA_DIR" ] && [ ! -L "$DATA_DIR" ] || fail "the target data mount is missing or unsafe"
[ -f "$CONFIG" ] && [ ! -L "$CONFIG" ] || fail "the production Redis config is missing or unsafe"

# Caller mounts only the disposable/production Redis volume at this exact
# location. Remove every previous persistence artifact before preloading RDB.
find "$DATA_DIR" -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
cp "$SOURCE_RDB" "$DATA_DIR/dump.rdb"
chown -R redis:redis "$DATA_DIR"
chmod 0600 "$DATA_DIR/dump.rdb"

shutdown_redis() {
  redis-cli -s "$SOCKET" SHUTDOWN NOSAVE >/dev/null 2>&1 || true
  rm -f "$SOCKET"
}
trap shutdown_redis EXIT INT TERM

start_and_wait() {
  su-exec redis redis-server "$@" \
    --port 0 --unixsocket "$SOCKET" --unixsocketperm 0700 --daemonize yes
  ready=false
  for _ in $(seq 1 120); do
    if [ "$(redis-cli -s "$SOCKET" --raw PING 2>/dev/null || true)" = "PONG" ]; then
      ready=true
      break
    fi
    sleep 0.25
  done
  [ "$ready" = "true" ] || fail "Redis did not load the restored persistence set"
}

# First boot must explicitly disable AOF so Redis loads dump.rdb. Only after
# the complete dataset is in memory do we enable AOF and wait for a successful,
# fsynced multipart rewrite.
start_and_wait --dir "$DATA_DIR" --dbfilename dump.rdb --appendonly no --save ''
source_dbsize="$(redis-cli -s "$SOCKET" --raw DBSIZE)"
case "$source_dbsize" in *[!0-9]*|'') fail "the preloaded RDB returned an invalid key count" ;; esac
redis-cli -s "$SOCKET" --raw CONFIG SET appendfsync always | grep -qx OK || \
  fail "appendfsync could not be hardened for conversion"
redis-cli -s "$SOCKET" --raw CONFIG SET aof-use-rdb-preamble yes | grep -qx OK || \
  fail "the RDB AOF preamble could not be enabled"
redis-cli -s "$SOCKET" --raw CONFIG SET appendonly yes | grep -qx OK || \
  fail "AOF could not be enabled after RDB preload"

rewrite_ready=false
for _ in $(seq 1 600); do
  persistence="$(redis-cli -s "$SOCKET" --raw INFO persistence)"
  aof_enabled="$(printf '%s\n' "$persistence" | awk -F: '$1 == "aof_enabled" {gsub("\\r", "", $2); print $2}')"
  rewrite_active="$(printf '%s\n' "$persistence" | awk -F: '$1 == "aof_rewrite_in_progress" {gsub("\\r", "", $2); print $2}')"
  rewrite_status="$(printf '%s\n' "$persistence" | awk -F: '$1 == "aof_last_bgrewrite_status" {gsub("\\r", "", $2); print $2}')"
  aof_size="$(printf '%s\n' "$persistence" | awk -F: '$1 == "aof_current_size" {gsub("\\r", "", $2); print $2}')"
  if [ "$aof_enabled" = "1" ] && [ "$rewrite_active" = "0" ] && \
     [ "$rewrite_status" = "ok" ] && [ -n "$aof_size" ] && [ "$aof_size" -gt 0 ]; then
    rewrite_ready=true
    break
  fi
  sleep 0.25
done
[ "$rewrite_ready" = "true" ] || fail "the initial AOF rewrite did not complete successfully"
waitaof_local="$(redis-cli -s "$SOCKET" --raw WAITAOF 1 0 30000 | sed -n '1p')"
[ "$waitaof_local" = "1" ] || fail "the converted AOF was not durably fsynced"
shutdown_redis
trap shutdown_redis EXIT INT TERM

[ -s "$DATA_DIR/appendonlydir/appendonly.aof.manifest" ] || \
  fail "Redis did not create a multipart AOF manifest"

# A second boot uses the exact production appendonly configuration. Matching
# key count proves normal startup selected the converted AOF rather than
# creating an empty persistence set.
start_and_wait "$CONFIG" --appendfsync always
target_dbsize="$(redis-cli -s "$SOCKET" --raw DBSIZE)"
[ "$target_dbsize" = "$source_dbsize" ] || \
  fail "production-style AOF startup did not reproduce the RDB key count"
waitaof_local="$(redis-cli -s "$SOCKET" --raw WAITAOF 1 0 30000 | sed -n '1p')"
[ "$waitaof_local" = "1" ] || fail "the production-style AOF was not durably fsynced"
shutdown_redis
trap - EXIT INT TERM

printf 'RESTORED_DBSIZE=%s\n' "$source_dbsize"
