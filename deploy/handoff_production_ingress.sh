#!/usr/bin/env bash
set -Eeuo pipefail
set +x
umask 077

# Reconcile public ingress from a durable release-bound state. Concrete
# ingress units never own boot enablement; the always-enabled selector calls
# this command after every reboot and operators use it for atomic transitions.

[[ "$(id -u)" == "0" ]] || {
  echo "Production ingress handoff must run as root." >&2
  exit 1
}

MODE="${1:-}"
case "$MODE" in handoff|activate|rollback|reconcile) ;; *) echo "Usage: $0 handoff|activate|rollback|reconcile" >&2; exit 1 ;; esac

ROOT_DIR="${LECTURESIFT_ROOT:-/opt/lecturesift}"
RELEASE_ENV_FILE="${LECTURESIFT_RELEASE_ENV_FILE:-/run/lecturesift/release.env}"
GATE="$ROOT_DIR/deploy/verify_production_ingress.sh"
EVIDENCE_TOOL="$ROOT_DIR/deploy/provider_cutover_evidence.py"
INSTAGRAM_STOP_GATE="$ROOT_DIR/deploy/verify_instagram_publishers_stopped.sh"
STAGING_UNIT=lecturesift-caddy-staging.service
PRODUCTION_UNIT=lecturesift-ingress.service
SELECTOR_UNIT=lecturesift-ingress-selector.service
RECONCILIATION_RUNBOOK="$ROOT_DIR/deploy/ROLLBACK_RECONCILIATION.md"
RUNTIME_ROOT=/run/lecturesift
LOCK_FILE=$RUNTIME_ROOT/ingress-handoff.lock
compose=(docker compose --project-directory "$ROOT_DIR" --file "$ROOT_DIR/compose.yaml")
finished=false

fail() { echo "Production ingress handoff failed: $*" >&2; exit 1; }
reconciliation_required() {
  fail "OVH activation may have admitted writes; traffic rollback is forbidden. Freeze both providers and follow $RECONCILIATION_RUNBOOK"
}

for command_name in systemctl python3 docker install stat flock chmod; do
  command -v "$command_name" >/dev/null 2>&1 || fail "$command_name is unavailable"
done
for path in "$GATE" "$EVIDENCE_TOOL" "$INSTAGRAM_STOP_GATE" "$RELEASE_ENV_FILE" "$ROOT_DIR/compose.yaml"; do
  [[ -f "$path" && ! -L "$path" ]] || fail "an ingress handoff input is missing or unsafe"
done

if [[ -e "$RUNTIME_ROOT" || -L "$RUNTIME_ROOT" ]]; then
  [[ -d "$RUNTIME_ROOT" && ! -L "$RUNTIME_ROOT" && "$(stat -c '%u:%g' -- "$RUNTIME_ROOT")" == "0:0" ]] ||
    fail "the ingress runtime root is unsafe"
  (( (8#$(stat -c '%a' -- "$RUNTIME_ROOT") & 8#022) == 0 )) || fail "the ingress runtime root is writable by non-root users"
else
  install -d -o root -g root -m 0700 -- "$RUNTIME_ROOT"
fi
exec 9<>"$LOCK_FILE"
chmod 0600 -- "$LOCK_FILE"
[[ -f /proc/self/fd/9 && ! -L "$LOCK_FILE" &&
   "$(stat -Lc '%u:%g:%a:%h:%d:%i' -- /proc/self/fd/9)" == "0:0:600:1:$(stat -c '%d:%i' -- "$LOCK_FILE")" ]] || fail "the ingress handoff lock is unsafe"
flock -n 9 || fail "another ingress handoff or reconciliation is active"

on_signal() { exit 130; }
on_exit() {
  local status="$?"
  trap - EXIT HUP INT TERM
  if [[ "$status" != "0" && "$finished" != "true" ]]; then
    echo "Ingress transition remains durably fenced; reload the enabled selector or reboot to reconcile its owner." >&2
  fi
  exit "$status"
}
trap on_signal HUP INT TERM
trap on_exit EXIT

EXPECTED_REVISION="$(sed -n 's/^LECTURESIFT_EXPECTED_BUILD_REVISION=//p' "$RELEASE_ENV_FILE")"
[[ "$(wc -l <"$RELEASE_ENV_FILE")" == "1" && "$EXPECTED_REVISION" =~ ^[0-9a-f]{40}$ ]] || fail "the release identity is invalid"
systemctl is-enabled --quiet "$SELECTOR_UNIT" || fail "the durable ingress selector must be enabled"
systemctl is-enabled --quiet "$STAGING_UNIT" && fail "the staging ingress must remain disabled"
systemctl is-enabled --quiet "$PRODUCTION_UNIT" && fail "the production ingress must remain disabled"

state_status() { python3 "$EVIDENCE_TOOL" ingress-status --expected-revision "$EXPECTED_REVISION"; }
transition() { python3 "$EVIDENCE_TOOL" transition-ingress-state --expected-revision "$EXPECTED_REVISION" --action "$1"; }

assert_target_writers_stopped() {
  local service container running
  systemctl is-active --quiet lecturesift.service && return 1
  for service in api worker; do
    container="$("${compose[@]}" ps -q "$service")"
    if [[ -n "$container" ]]; then
      running="$(docker inspect --format '{{.State.Running}}' "$container" 2>/dev/null || printf unknown)"
      [[ "$running" == "false" ]] || return 1
    fi
  done
  bash "$INSTAGRAM_STOP_GATE" >/dev/null
}

prove_or_stop_target_writers() {
  if bash "$GATE" freeze >/dev/null && bash "$INSTAGRAM_STOP_GATE" >/dev/null; then
    return 0
  fi
  # If private health is unavailable, remove every target request/job writer
  # before considering source restoration. A stopped target is stronger than
  # an unobservable maintenance flag.
  systemctl stop "$PRODUCTION_UNIT" >/dev/null 2>&1 || true
  systemctl stop lecturesift.service >/dev/null 2>&1 || true
  "${compose[@]}" stop --timeout 600 worker api egress-proxy >/dev/null 2>&1 || true
  assert_target_writers_stopped || fail "target writers could not be stopped and proved inactive"
}

restore_render_proxy() {
  local state
  state="$(state_status)" || fail "the durable ingress state cannot be validated"
  [[ "$state" != "activated" ]] || reconciliation_required
  case "$state" in required|render-restored|handoff-in-progress|awaiting-activation|restore-in-progress) ;; *) fail "Render restoration is not authorized from state: $state" ;; esac

  # Required ordering: prove or stop target writers first; only then prove the
  # finalized direct Render source is still frozen, suspended and queue-idle.
  prove_or_stop_target_writers
  bash "$GATE" source >/dev/null || fail "the finalized direct Render source is not safely restorable"
  if [[ "$state" == "handoff-in-progress" || "$state" == "awaiting-activation" || "$state" == "restore-in-progress" ]]; then
    [[ "$(transition begin-restore)" == "restore-in-progress" ]] || fail "the durable Render-restore fence could not be installed"
    state=restore-in-progress
  fi
  systemctl stop "$PRODUCTION_UNIT" >/dev/null || fail "production ingress could not be stopped"
  systemctl is-active --quiet "$PRODUCTION_UNIT" && fail "production ingress remains active"
  systemctl start "$STAGING_UNIT" >/dev/null || fail "Render staging ingress could not be started"
  bash "$GATE" staging >/dev/null || fail "public staging TLS did not converge to the finalized Render source"
  if [[ "$state" == "restore-in-progress" ]]; then
    [[ "$(transition complete-restore)" == "render-restored" ]] || fail "the Render-restored state could not be recorded"
  fi
}

reconcile_owner() {
  local state
  state="$(state_status)" || fail "the durable ingress state cannot be validated"
  case "$state" in
    activated)
      systemctl stop "$STAGING_UNIT" >/dev/null || reconciliation_required
      systemctl start "$PRODUCTION_UNIT" >/dev/null || reconciliation_required
      bash "$GATE" activated >/dev/null || reconciliation_required
      ;;
    awaiting-activation)
      systemctl stop "$STAGING_UNIT" >/dev/null || { restore_render_proxy; return 1; }
      if ! systemctl start "$PRODUCTION_UNIT" >/dev/null || ! bash "$GATE" live >/dev/null; then
        restore_render_proxy
        fail "frozen OVH ownership failed during reconciliation; Render was restored"
      fi
      ;;
    required|render-restored|handoff-in-progress|restore-in-progress)
      restore_render_proxy
      ;;
    *) fail "unknown durable ingress state: $state" ;;
  esac
}

state="$(state_status)" || fail "the durable ingress state cannot be validated"
if [[ "$MODE" == "reconcile" ]]; then
  reconcile_owner
  finished=true
  echo "PRODUCTION_INGRESS_RECONCILED|state=$(state_status)"
  exit 0
fi
if [[ "$MODE" == "rollback" ]]; then
  [[ "$state" != "activated" ]] || reconciliation_required
  restore_render_proxy
  finished=true
  echo "PRODUCTION_INGRESS_ROLLBACK_OK|public_origin=render|target_mode=freeze-or-stopped"
  exit 0
fi
if [[ "$MODE" == "activate" ]]; then
  [[ "$state" == "awaiting-activation" ]] || { [[ "$state" == "activated" ]] && reconciliation_required; fail "production ingress is not awaiting activation"; }
  # This volatile gate is immediately adjacent to the sole atomic write-
  # enabling transition. Any failure leaves awaiting-activation untouched.
  bash "$GATE" activation >/dev/null || fail "activation revalidation failed; awaiting state was preserved"
  [[ "$(transition activate)" == "activated" ]] || fail "the durable activation transition failed"
  bash "$GATE" activated >/dev/null || reconciliation_required
  finished=true
  echo "PRODUCTION_INGRESS_ACTIVATION_OK|public_origin=ovh|target_mode=off"
  exit 0
fi

case "$state" in
  activated) reconciliation_required ;;
  awaiting-activation)
    bash "$GATE" live >/dev/null || { restore_render_proxy; fail "frozen OVH ingress failed; Render was restored"; }
    finished=true
    echo "PRODUCTION_INGRESS_HANDOFF_OK|public_origin=ovh|target_mode=freeze|activation_required=true"
    exit 0
    ;;
  handoff-in-progress|restore-in-progress)
    restore_render_proxy
    fail "an interrupted transition was reconciled to Render; inspect and rerun handoff"
    ;;
  required|render-restored) ;;
  *) fail "ingress handoff cannot continue from state: $state" ;;
esac

bash "$GATE" prepare >/dev/null || fail "private OVH freeze or finalized public Render acceptance failed"
[[ "$(python3 "$EVIDENCE_TOOL" begin-ingress-handoff --expected-revision "$EXPECTED_REVISION")" == "handoff-in-progress" ]] ||
  fail "the durable handoff fence could not be installed"
if ! systemctl stop "$STAGING_UNIT" >/dev/null; then restore_render_proxy; fail "staging ingress could not be stopped; Render restore attempted"; fi
if ! systemctl start "$PRODUCTION_UNIT" >/dev/null; then restore_render_proxy; fail "production ingress was rejected; Render restored"; fi
if ! bash "$GATE" live >/dev/null; then restore_render_proxy; fail "production ingress failed frozen public gate; Render restored"; fi
[[ "$(transition await-activation)" == "awaiting-activation" ]] || { restore_render_proxy; fail "activation fence could not be recorded; Render restored"; }

finished=true
echo "PRODUCTION_INGRESS_HANDOFF_OK|public_origin=ovh|target_mode=freeze|activation_required=true"
