#!/usr/bin/env bash
set -Eeuo pipefail
set +x
umask 077

# Verify one durable ingress topology. Source checks use the fixed scoped
# transport and never source or print Render credentials.

[[ "$(id -u)" == "0" ]] || {
  echo "Production ingress verification must run as root." >&2
  exit 1
}

MODE="${1:-}"
case "$MODE" in
  prepare|freeze|prestart|live|source|staging|activation|activated) ;;
  *)
    echo "Usage: $0 prepare|freeze|prestart|live|source|staging|activation|activated" >&2
    exit 1
    ;;
esac

ROOT_DIR="${LECTURESIFT_ROOT:-/opt/lecturesift}"
RELEASE_ENV_FILE="${LECTURESIFT_RELEASE_ENV_FILE:-/run/lecturesift/release.env}"
SOURCE_ENV_FILE="${LECTURESIFT_SOURCE_ENV_FILE:-/root/.lecturesift-render-source.env}"
EVIDENCE_TOOL="$ROOT_DIR/deploy/provider_cutover_evidence.py"
RELEASE_TOOL="$ROOT_DIR/deploy/release.sh"
SOURCE_TRANSPORT="$ROOT_DIR/deploy/source_postgres_transport.py"
SOURCE_REDIS_GUARD="$ROOT_DIR/deploy/source_redis_guard.py"
SOURCE_EXECUTOR_GATE="$ROOT_DIR/deploy/render_worker_stop_evidence.py"
HEALTH_VALIDATOR="$ROOT_DIR/deploy/validate_ingress_health.py"
INSTAGRAM_STOP_GATE="$ROOT_DIR/deploy/verify_instagram_publishers_stopped.sh"
STAGING_CONTAINER=lecturesift-caddy-staging
STAGING_UNIT=lecturesift-caddy-staging.service
PRODUCTION_UNIT=lecturesift-ingress.service
SELECTOR_UNIT=lecturesift-ingress-selector.service
PUBLIC_HOST=api.lecturesift.com
compose=(docker compose --project-directory "$ROOT_DIR" --file "$ROOT_DIR/compose.yaml")

fail() {
  echo "Production ingress verification failed: $*" >&2
  exit 1
}

for command_name in docker python3 curl systemctl sleep; do
  command -v "$command_name" >/dev/null 2>&1 || fail "$command_name is unavailable"
done
for path in "$RELEASE_ENV_FILE" "$EVIDENCE_TOOL" "$RELEASE_TOOL" \
  "$SOURCE_TRANSPORT" "$SOURCE_REDIS_GUARD" "$SOURCE_EXECUTOR_GATE" \
  "$HEALTH_VALIDATOR" "$INSTAGRAM_STOP_GATE" "$ROOT_DIR/compose.yaml"; do
  [[ -f "$path" && ! -L "$path" ]] || fail "an ingress identity input is missing or unsafe"
done

EXPECTED_REVISION="$(sed -n 's/^LECTURESIFT_EXPECTED_BUILD_REVISION=//p' "$RELEASE_ENV_FILE")"
[[ "$(wc -l <"$RELEASE_ENV_FILE")" == "1" && "$EXPECTED_REVISION" =~ ^[0-9a-f]{40}$ ]] ||
  fail "the release identity is invalid"
bash "$RELEASE_TOOL" verify >/dev/null || fail "the exact release image identity is not verified"
[[ "$(python3 "$EVIDENCE_TOOL" first-start-status --expected-revision "$EXPECTED_REVISION")" == "consumed" ]] ||
  fail "the provider first-start gate has not been consumed"
state_status="$(python3 "$EVIDENCE_TOOL" ingress-status --expected-revision "$EXPECTED_REVISION")" ||
  fail "the durable ingress state is missing, unsafe or belongs to another release"

# Only the selector owns boot policy. Concrete ingress units remain disabled.
systemctl is-enabled --quiet "$SELECTOR_UNIT" || fail "the durable ingress selector is not enabled"
systemctl is-enabled --quiet "$STAGING_UNIT" && fail "staging ingress must not own boot enablement"
systemctl is-enabled --quiet "$PRODUCTION_UNIT" && fail "production ingress must not own boot enablement"

assert_healthy() {
  local service="$1" container running health
  container="$("${compose[@]}" ps -q "$service")"
  [[ -n "$container" ]] || fail "$service has no container"
  running="$(docker inspect --format '{{.State.Running}}' "$container" 2>/dev/null || true)"
  health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}missing{{end}}' "$container" 2>/dev/null || true)"
  [[ "$running" == "true" && "$health" == "healthy" ]] || fail "$service is not running and healthy"
}

assert_private_core() {
  systemctl is-active --quiet lecturesift.service || fail "the private production core unit is not active"
  local service
  for service in postgres redis egress-proxy api worker; do assert_healthy "$service"; done
}

staging_running="$(docker inspect --format '{{.State.Running}}' "$STAGING_CONTAINER" 2>/dev/null || true)"
production_caddy="$("${compose[@]}" ps -q caddy)"
production_caddy_running=""
if [[ -n "$production_caddy" ]]; then
  production_caddy_running="$(docker inspect --format '{{.State.Running}}' "$production_caddy" 2>/dev/null || true)"
fi
staging_active=false
production_active=false
systemctl is-active --quiet "$STAGING_UNIT" && staging_active=true
systemctl is-active --quiet "$PRODUCTION_UNIT" && production_active=true

validate_health_json() {
  printf '%s' "$1" | python3 "$HEALTH_VALIDATOR" \
    --provider "$2" --revision "$3" --maintenance-mode "$4"
}

target_health_revision() {
  local expected_mode="$1" public_path="${2:-false}" health_url rollout_url health_json rollout_json
  local -a curl_args
  if [[ "$public_path" == "true" ]]; then
    health_url="https://$PUBLIC_HOST/health"
    rollout_url="https://$PUBLIC_HOST/rollout/health?readiness=true"
    curl_args=(--silent --show-error --fail --max-time 20 --max-filesize 1048576 \
      --noproxy '*' --proto '=https' --tlsv1.2 --resolve "$PUBLIC_HOST:443:127.0.0.1")
    health_json="$(curl "${curl_args[@]}" "$health_url")" || return 1
    rollout_json="$(curl "${curl_args[@]}" "$rollout_url")" || return 1
  else
    health_json="$(api_container_json '/health')" || return 1
    rollout_json="$(api_container_json '/rollout/health?readiness=true')" || return 1
  fi
  validate_health_json "$health_json" ovh "$EXPECTED_REVISION" "$expected_mode" >/dev/null || return 1
  ROLLOUT_JSON="$rollout_json" python3 - <<'PY'
import json, os
r = json.loads(os.environ["ROLLOUT_JSON"])
if not (r.get("ready") is True and r.get("durable_processing_ready") is True and (r.get("storage") or {}).get("connected") is True):
    raise SystemExit(1)
PY
  printf '%s\n' "$EXPECTED_REVISION"
}

api_container_json() {
  local request_path="$1"
  "${compose[@]}" exec -T api python -I -c '
import http.client
import sys

path = sys.argv[1]
if path not in {"/health", "/rollout/health?readiness=true"}:
    raise SystemExit(1)
connection = http.client.HTTPConnection("127.0.0.1", 8000, timeout=20)
try:
    connection.request("GET", path, headers={"Host": "127.0.0.1"})
    response = connection.getresponse()
    body = response.read(1048577)
    if response.status != 200 or len(body) > 1048576:
        raise SystemExit(1)
    sys.stdout.buffer.write(body)
finally:
    connection.close()
' "$request_path"
}

source_exec() {
  local scope="$1"; shift
  python3 "$SOURCE_TRANSPORT" exec-source --source-env "$SOURCE_ENV_FILE" --scope "$scope" -- "$@"
}

bound_source_revision() {
  python3 "$EVIDENCE_TOOL" ingress-source-revision --expected-revision "$EXPECTED_REVISION"
}

direct_render_revision() {
  local expected_source_revision expected_fingerprint observed_fingerprint health_json
  [[ "$SOURCE_ENV_FILE" == "/root/.lecturesift-render-source.env" ]] || fail "the Render source environment path is fixed"
  python3 "$SOURCE_TRANSPORT" validate --source-env "$SOURCE_ENV_FILE" >/dev/null || fail "the Render source environment is invalid"
  expected_source_revision="$(bound_source_revision)" || return 1
  expected_fingerprint="$(python3 "$EVIDENCE_TOOL" ingress-source-fingerprint --expected-revision "$EXPECTED_REVISION")" || return 1
  observed_fingerprint="$(source_exec fingerprint python3 "$EVIDENCE_TOOL" source-fingerprint)" || return 1
  [[ "$observed_fingerprint" == "$expected_fingerprint" ]] || fail "the direct Render source identity changed after finalization"
  health_json="$(source_exec health python3 -I -c '
import os, ssl, urllib.request
request = urllib.request.Request(os.environ["SOURCE_HEALTH_URL"], headers={"User-Agent": "LectureSift-Ingress-Gate/2"})
with urllib.request.urlopen(request, timeout=20, context=ssl.create_default_context()) as response:
    if response.geturl() != os.environ["SOURCE_HEALTH_URL"]: raise SystemExit(1)
    body = response.read(1048577)
    if response.status != 200 or len(body) > 1048576: raise SystemExit(1)
    print(body.decode("utf-8", errors="strict"))
')" || return 1
  validate_health_json "$health_json" render "$expected_source_revision" freeze
}

verify_source_frozen_idle() {
  local expected_stop_digest observed_stop_digest
  direct_render_revision >/dev/null || fail "the direct Render source identity, revision, provider or freeze is invalid"
  expected_stop_digest="$(python3 "$EVIDENCE_TOOL" ingress-source-executor-stop-digest --expected-revision "$EXPECTED_REVISION")" ||
    fail "the finalized Render executor-stop digest is unavailable"
  observed_stop_digest="$(python3 "$SOURCE_EXECUTOR_GATE")" ||
    fail "the Render worker or Instagram scheduler is not provably suspended"
  [[ "$observed_stop_digest" == "$expected_stop_digest" ]] ||
    fail "the Render worker/scheduler stop proof changed after finalization"
  source_exec redis python3 "$SOURCE_REDIS_GUARD" assert-idle >/dev/null 2>&1 ||
    fail "the Render source queue is not empty and idle"
}

public_render_revision() {
  local expected_source_revision health_json attempt
  expected_source_revision="$(bound_source_revision)" || return 1
  for attempt in {1..20}; do
    health_json="$(curl --silent --show-error --fail --max-time 3 --max-filesize 1048576 \
      --noproxy '*' --proto '=https' --tlsv1.2 --resolve "$PUBLIC_HOST:443:127.0.0.1" \
      "https://$PUBLIC_HOST/health" 2>/dev/null)" || true
    if [[ -n "$health_json" ]] && validate_health_json "$health_json" render "$expected_source_revision" freeze >/dev/null 2>&1; then
      printf '%s\n' "$expected_source_revision"
      return 0
    fi
    sleep 2
  done
  return 1
}

verify_public_render() {
  local direct_revision public_revision
  verify_source_frozen_idle
  direct_revision="$(direct_render_revision)" || return 1
  public_revision="$(public_render_revision)" || fail "the public staging TLS path did not become ready as the frozen Render provider"
  [[ "$public_revision" == "$direct_revision" ]] || fail "the public staging TLS path does not match the finalized direct Render source"
  printf '%s\n' "$direct_revision"
}

source_revision=""
case "$MODE" in
  prepare)
    [[ "$state_status" == "required" || "$state_status" == "render-restored" ]] || fail "a new handoff cannot be prepared from the current durable state"
    [[ "$staging_running" == "true" && "$staging_active" == "true" ]] || fail "the selector does not own Render staging ingress"
    [[ "$production_caddy_running" != "true" && "$production_active" == "false" ]] || fail "production ingress must be inactive before handoff"
    assert_private_core
    target_health_revision freeze false >/dev/null || fail "the private OVH target is not healthy, OVH-identified and frozen"
    source_revision="$(verify_public_render)"
    ;;
  freeze)
    case "$state_status" in required|render-restored|handoff-in-progress|awaiting-activation|restore-in-progress) ;; *) fail "durable state does not permit restore" ;; esac
    assert_private_core
    target_health_revision freeze false >/dev/null || fail "the OVH target write freeze cannot be proved"
    ;;
  source)
    [[ "$state_status" != "activated" ]] || fail "Render restoration is forbidden after activation"
    source_revision="$(direct_render_revision)" || fail "the direct frozen Render source contract failed"
    verify_source_frozen_idle
    ;;
  prestart)
    case "$state_status" in handoff-in-progress|awaiting-activation|activated) ;; *) fail "production Caddy lacks a durable start fence" ;; esac
    [[ "$staging_running" != "true" && "$staging_active" == "false" ]] || fail "Render staging ingress is still active"
    [[ "$production_caddy_running" != "true" ]] || fail "production Caddy is already running outside its unit"
    assert_private_core
    expected_mode=freeze; [[ "$state_status" == "activated" ]] && expected_mode=off
    target_health_revision "$expected_mode" false >/dev/null || fail "private OVH does not match durable state"
    ;;
  live)
    case "$state_status" in handoff-in-progress|awaiting-activation|activated) ;; *) fail "production ingress has no durable runtime state" ;; esac
    [[ "$staging_running" != "true" && "$staging_active" == "false" ]] || fail "both ingress owners are active"
    [[ "$production_caddy_running" == "true" ]] || fail "production Caddy is not running"
    assert_private_core
    expected_mode=freeze; [[ "$state_status" == "activated" ]] && expected_mode=off
    target_health_revision "$expected_mode" true >/dev/null || fail "public OVH does not match durable state"
    ;;
  staging)
    case "$state_status" in required|restore-in-progress|render-restored) ;; *) fail "durable state does not authorize Render ownership" ;; esac
    [[ "$production_active" == "false" && "$production_caddy_running" != "true" ]] || fail "production ingress must be inactive and stopped before Render restoration"
    [[ "$staging_running" == "true" && "$staging_active" == "true" ]] || fail "Render staging ingress is not running and active"
    source_revision="$(verify_public_render)"
    ;;
  activation)
    [[ "$state_status" == "awaiting-activation" ]] || fail "target is not awaiting activation"
    [[ "$staging_running" != "true" && "$staging_active" == "false" ]] || fail "Render staging ingress is still active"
    [[ "$production_caddy_running" == "true" && "$production_active" == "true" ]] || fail "OVH ingress is not the sole runtime owner"
    assert_private_core
    target_health_revision freeze true >/dev/null || fail "public OVH is not healthy and frozen"
    verify_source_frozen_idle
    bash "$INSTAGRAM_STOP_GATE" >/dev/null || fail "an OVH Instagram publisher is not fully stopped"
    ;;
  activated)
    [[ "$state_status" == "activated" ]] || fail "durable ingress state is not activated"
    [[ "$staging_running" != "true" && "$staging_active" == "false" ]] || fail "Render staging ingress still owns runtime"
    [[ "$production_caddy_running" == "true" && "$production_active" == "true" ]] || fail "OVH ingress is not active"
    assert_private_core
    target_health_revision off true >/dev/null || fail "public OVH target did not activate cleanly"
    ;;
esac

if [[ -n "$source_revision" ]]; then
  echo "PRODUCTION_INGRESS_GATE_OK|mode=$MODE|revision=$EXPECTED_REVISION|source_revision=$source_revision"
else
  echo "PRODUCTION_INGRESS_GATE_OK|mode=$MODE|revision=$EXPECTED_REVISION"
fi
