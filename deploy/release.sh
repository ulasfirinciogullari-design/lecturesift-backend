#!/usr/bin/env bash
set -euo pipefail
set +x
umask 077
export GIT_ATTR_NOSYSTEM=1

# Build and admit exactly one clean Git commit. The marker contains no secret;
# it is root-only so an unprivileged local process cannot make an older image
# claim the source revision selected by systemd.
ROOT_DIR="${LECTURESIFT_ROOT:-/opt/lecturesift}"
RELEASE_ENV_FILE="${LECTURESIFT_RELEASE_ENV_FILE:-/run/lecturesift/release.env}"
APP_IMAGE="lecturesift-backend:local"
PROXY_IMAGE="lecturesift-egress-proxy:local"
MODE="${1:-build}"

fail() {
  echo "Release identity failed: $*" >&2
  exit 1
}

[[ "$(id -u)" == "0" ]] || fail "run this helper as root"
[[ "$MODE" == "prepare" || "$MODE" == "build" || "$MODE" == "verify" ]] || \
  fail "mode must be prepare, build or verify"
command -v git >/dev/null 2>&1 || fail "git is not installed"
command -v find >/dev/null 2>&1 || fail "find is not installed"
command -v realpath >/dev/null 2>&1 || fail "realpath is not installed"
command -v tar >/dev/null 2>&1 || fail "tar is not installed"
command -v python3 >/dev/null 2>&1 || fail "python3 is not installed"

[[ "$ROOT_DIR" == /* && -d "$ROOT_DIR" && ! -L "$ROOT_DIR" ]] || \
  fail "deployment root must be an existing non-symlink absolute directory"
ROOT_DIR="$(realpath -e -- "$ROOT_DIR")"
unsafe_checkout_entry="$(find "$ROOT_DIR" -xdev \
  \( ! -user root -o -perm /022 \) -print -quit)"
[[ -z "$unsafe_checkout_entry" ]] || \
  fail "deployment checkout must be root-owned and not group/other writable"
git_root="$(git -C "$ROOT_DIR" rev-parse --show-toplevel 2>/dev/null)" || \
  fail "deployment root is not a Git worktree"
[[ "$(realpath -e -- "$git_root")" == "$ROOT_DIR" ]] || \
  fail "deployment root must be the Git worktree top level"

source_revision() {
  local revision status tree_entries
  revision="$(git -C "$ROOT_DIR" rev-parse --verify 'HEAD^{commit}' 2>/dev/null)" || \
    fail "HEAD does not resolve to one commit"
  [[ "$revision" =~ ^[0-9a-fA-F]{40}$ ]] || fail "HEAD is not a full 40-hex commit id"
  status="$(git -C "$ROOT_DIR" status --porcelain=v1 --untracked-files=all)" || \
    fail "Git cleanliness could not be determined"
  [[ -z "$status" ]] || \
    fail "deployment checkout is dirty or contains untracked files"
  tree_entries="$(git -C "$ROOT_DIR" ls-tree -r "$revision")" || \
    fail "Git tree modes could not be inspected"
  if grep -E '^(120000|160000) ' <<<"$tree_entries" >/dev/null; then
    fail "release commits may not contain symlinks or unmaterialized submodules"
  fi
  printf '%s' "${revision,,}"
}

[[ "$RELEASE_ENV_FILE" == /* ]] || fail "release environment path must be absolute"
release_dir="$(dirname -- "$RELEASE_ENV_FILE")"
if [[ -e "$release_dir" || -L "$release_dir" ]]; then
  [[ -d "$release_dir" && ! -L "$release_dir" ]] || \
    fail "release environment directory is unsafe"
else
  install -d -o root -g root -m 0700 -- "$release_dir"
fi
release_dir="$(realpath -e -- "$release_dir")"
[[ "$(stat -c '%u' -- "$release_dir")" == "0" ]] || \
  fail "release environment directory must be root-owned"
release_dir_mode="$(stat -c '%a' -- "$release_dir")"
(( (8#$release_dir_mode & 8#077) == 0 )) || \
  fail "release environment directory must not grant group/other access"
[[ "$(dirname -- "$RELEASE_ENV_FILE")" == "$release_dir" ]] || \
  fail "release environment path resolves through an unexpected directory"

command -v flock >/dev/null 2>&1 || fail "flock is not installed"
exec 9>"$release_dir/.release.lock"
chmod 0600 "$release_dir/.release.lock"
flock -n 9 || fail "another release operation is active"

check_release_file() {
  local mode
  [[ -f "$RELEASE_ENV_FILE" && ! -L "$RELEASE_ENV_FILE" ]] || \
    fail "release environment is missing or unsafe"
  [[ "$(stat -c '%u' -- "$RELEASE_ENV_FILE")" == "0" ]] || \
    fail "release environment must be root-owned"
  mode="$(stat -c '%a' -- "$RELEASE_ENV_FILE")"
  [[ "$mode" == "400" || "$mode" == "600" ]] || \
    fail "release environment must have mode 0400 or 0600"
}

read_expected_revision() {
  local line
  check_release_file
  [[ "$(wc -l <"$RELEASE_ENV_FILE")" == "1" ]] || \
    fail "release environment must contain exactly one line"
  IFS= read -r line <"$RELEASE_ENV_FILE" || \
    fail "release environment could not be read"
  [[ "$line" =~ ^LECTURESIFT_EXPECTED_BUILD_REVISION=([0-9a-f]{40})$ ]] || \
    fail "release environment does not contain one exact revision"
  printf '%s' "${BASH_REMATCH[1]}"
}

write_release_file() {
  local revision="$1" expected temporary
  expected="LECTURESIFT_EXPECTED_BUILD_REVISION=$revision"
  if [[ -e "$RELEASE_ENV_FILE" || -L "$RELEASE_ENV_FILE" ]]; then
    check_release_file
    if [[ "$(cat -- "$RELEASE_ENV_FILE")" == "$expected" && \
          "$(wc -l <"$RELEASE_ENV_FILE")" == "1" ]]; then
      return 0
    fi
  fi
  temporary="$(mktemp -- "$release_dir/.release.env.XXXXXXXX")"
  chmod 0600 "$temporary"
  chown root:root "$temporary"
  printf '%s\n' "$expected" >"$temporary"
  mv -fT -- "$temporary" "$RELEASE_ENV_FILE"
  check_release_file
}

image_matches() {
  local image="$1" expected="$2" expected_supply="$3" label supply_label environment
  label="$(docker image inspect --format \
    '{{ index .Config.Labels "org.opencontainers.image.revision" }}' \
    "$image" 2>/dev/null || true)"
  [[ "$label" == "$expected" ]] || return 1
  supply_label="$(docker image inspect --format \
    '{{ index .Config.Labels "io.lecturesift.supply-chain-lock-sha256" }}' \
    "$image" 2>/dev/null || true)"
  [[ "$supply_label" == "$expected_supply" ]] || return 1
  environment="$(docker image inspect --format \
    '{{range .Config.Env}}{{println .}}{{end}}' "$image" 2>/dev/null || true)"
  grep -Fqx "LECTURESIFT_BUILD_REVISION=$expected" <<<"$environment" && \
    grep -Fqx "LECTURESIFT_SUPPLY_CHAIN_LOCK_SHA256=$expected_supply" <<<"$environment"
}

revision="$(source_revision)"
if [[ "$MODE" == "prepare" ]]; then
  write_release_file "$revision"
  echo "Release identity prepared for $revision."
  exit 0
fi

expected_revision="$(read_expected_revision)"
[[ "$revision" == "$expected_revision" ]] || \
  fail "release marker no longer matches clean source HEAD"
command -v docker >/dev/null 2>&1 || fail "Docker is not installed"
supply_chain_digest="$(python3 "$ROOT_DIR/deploy/supply_chain_lock.py" \
  --root "$ROOT_DIR" --print-digest)" || fail "supply-chain lock is invalid"
[[ "$supply_chain_digest" =~ ^[0-9a-f]{64}$ ]] || \
  fail "supply-chain lock digest is invalid"

if [[ "$MODE" == "build" ]]; then
  if ! image_matches "$APP_IMAGE" "$expected_revision" "$supply_chain_digest" || \
     ! image_matches "$PROXY_IMAGE" "$expected_revision" "$supply_chain_digest"; then
    release_context="$(mktemp -d -- /var/tmp/lecturesift-release.XXXXXXXX)"
    candidate_app="lecturesift-backend:release-$expected_revision"
    candidate_proxy="lecturesift-egress-proxy:release-$expected_revision"
    cleanup_release_build() {
      rm -rf -- "$release_context"
      docker image rm "$candidate_app" "$candidate_proxy" >/dev/null 2>&1 || true
    }
    trap cleanup_release_build EXIT

    # The build context comes from the immutable commit object rather than the
    # mutable checkout. The clean-tree checks remain mandatory, while a
    # transient worktree edit cannot be smuggled into a correctly labelled
    # image and restored before the post-build check.
    git -c core.attributesFile=/dev/null -C "$ROOT_DIR" \
      archive --format=tar "$expected_revision" \
      | tar -xf - -C "$release_context"
    docker build --pull \
      --build-arg "LECTURESIFT_BUILD_REVISION=$expected_revision" \
      --build-arg "LECTURESIFT_SUPPLY_CHAIN_LOCK_SHA256=$supply_chain_digest" \
      --tag "$candidate_app" "$release_context"
    docker build --pull \
      --build-arg "LECTURESIFT_BUILD_REVISION=$expected_revision" \
      --build-arg "LECTURESIFT_SUPPLY_CHAIN_LOCK_SHA256=$supply_chain_digest" \
      --tag "$candidate_proxy" "$release_context/deploy/egress-proxy"

    [[ "$(source_revision)" == "$expected_revision" ]] || \
      fail "source HEAD or cleanliness changed during release build"
    image_matches "$candidate_app" "$expected_revision" "$supply_chain_digest" || \
      fail "candidate application image identity is invalid"
    image_matches "$candidate_proxy" "$expected_revision" "$supply_chain_digest" || \
      fail "candidate proxy image identity is invalid"
    docker image tag "$candidate_app" "$APP_IMAGE"
    docker image tag "$candidate_proxy" "$PROXY_IMAGE"
    cleanup_release_build
    trap - EXIT
  fi
fi

# Re-read the repository after a potentially long build. A checkout mutation
# during context transfer can never be admitted even if Docker produced an
# otherwise healthy image with the requested label.
[[ "$(source_revision)" == "$expected_revision" ]] || \
  fail "source HEAD or cleanliness changed during release build"
image_matches "$APP_IMAGE" "$expected_revision" "$supply_chain_digest" || \
  fail "application image label/environment does not match source HEAD"
image_matches "$PROXY_IMAGE" "$expected_revision" "$supply_chain_digest" || \
  fail "egress proxy image label/environment does not match source HEAD"

echo "Release image verified for $expected_revision."
