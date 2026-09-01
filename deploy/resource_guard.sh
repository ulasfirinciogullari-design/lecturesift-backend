#!/usr/bin/env bash
set -euo pipefail

# Production is intentionally sized for the selected OVH 4-vCPU / 8-GB VPS.
# Container limits in compose.yaml preserve memory for the kernel, Docker and
# SSH; these host and disk gates prevent starting the stack on a smaller host
# or when one valid maximum-size job could consume the emergency reserve.
MIN_HOST_CPUS=4
MIN_HOST_MEMORY_BYTES=7516192768
DEFAULT_HOST_DISK_RESERVE_BYTES=10737418240
DEFAULT_MAX_JOB_WORK_BYTES=8589934592
DEFAULT_API_WORKSPACE_BYTES=3221225472
DEFAULT_WORKER_WORKSPACE_BYTES=12884901888

fail() {
  echo "Resource guard failed: $*" >&2
  exit 1
}

decimal_bytes() {
  local name="$1" value="${!1:-}"
  [[ "$value" =~ ^[1-9][0-9]*$ ]] || fail "$name must be a positive decimal byte count"
  printf '%s' "$value"
}

command -v docker >/dev/null 2>&1 || fail "Docker is not installed"
command -v python3 >/dev/null 2>&1 || fail "python3 is not installed"

host_cpus="$(getconf _NPROCESSORS_ONLN 2>/dev/null || true)"
[[ "$host_cpus" =~ ^[0-9]+$ ]] || fail "online CPU count could not be determined"
(( host_cpus >= MIN_HOST_CPUS )) || fail "at least 4 online CPUs are required"

host_memory_bytes="$(awk '/^MemTotal:/ {printf "%.0f\n", $2 * 1024; exit}' /proc/meminfo)"
[[ "$host_memory_bytes" =~ ^[0-9]+$ ]] || fail "host memory could not be determined"
(( host_memory_bytes >= MIN_HOST_MEMORY_BYTES )) || \
  fail "at least 7 GiB of visible memory is required for the 8-GB VPS profile"

LECTURESIFT_HOST_DISK_RESERVE_BYTES="${LECTURESIFT_HOST_DISK_RESERVE_BYTES:-$DEFAULT_HOST_DISK_RESERVE_BYTES}"
LECTURESIFT_MAX_JOB_WORK_BYTES="${LECTURESIFT_MAX_JOB_WORK_BYTES:-$DEFAULT_MAX_JOB_WORK_BYTES}"
LECTURESIFT_MAX_API_WORKSPACE_BYTES="${LECTURESIFT_MAX_API_WORKSPACE_BYTES:-$DEFAULT_API_WORKSPACE_BYTES}"
LECTURESIFT_MAX_WORKER_WORKSPACE_BYTES="${LECTURESIFT_MAX_WORKER_WORKSPACE_BYTES:-$DEFAULT_WORKER_WORKSPACE_BYTES}"
LECTURESIFT_MAX_VIDEO_BYTES="${LECTURESIFT_MAX_VIDEO_BYTES:-}"
LECTURESIFT_MAX_DOCUMENT_BYTES="${LECTURESIFT_MAX_DOCUMENT_BYTES:-}"

disk_reserve="$(decimal_bytes LECTURESIFT_HOST_DISK_RESERVE_BYTES)"
job_budget="$(decimal_bytes LECTURESIFT_MAX_JOB_WORK_BYTES)"
api_workspace_budget="$(decimal_bytes LECTURESIFT_MAX_API_WORKSPACE_BYTES)"
worker_workspace_budget="$(decimal_bytes LECTURESIFT_MAX_WORKER_WORKSPACE_BYTES)"
max_video_bytes="$(decimal_bytes LECTURESIFT_MAX_VIDEO_BYTES)"
max_document_bytes="$(decimal_bytes LECTURESIFT_MAX_DOCUMENT_BYTES)"

(( max_video_bytes <= 1073741824 )) || fail "media upload limit may not exceed the 1 GiB ingress contract"
(( max_document_bytes <= 104857600 )) || fail "document upload limit may not exceed the 100 MiB plan contract"
(( job_budget >= max_video_bytes * 4 )) || \
  fail "per-job workspace must be at least four times the maximum media upload"
(( api_workspace_budget >= max_video_bytes * 2 )) || \
  fail "API workspace must hold at least two simultaneous maximum-size streamed uploads"
(( worker_workspace_budget >= job_budget )) || \
  fail "worker workspace limit must be at least the per-job workspace budget"

docker_root="$(docker info --format '{{.DockerRootDir}}' 2>/dev/null)"
[[ "$docker_root" == /* && -d "$docker_root" && ! -L "$docker_root" ]] || \
  fail "Docker root must be an existing non-symlink absolute directory"
docker_root="$(realpath -e -- "$docker_root")"

available_bytes="$(df --output=avail -B1 -- "$docker_root" | awk 'NR == 2 {print $1}')"
[[ "$available_bytes" =~ ^[0-9]+$ ]] || fail "Docker filesystem free space could not be determined"
required_free_bytes=$((disk_reserve + job_budget + max_video_bytes))
(( available_bytes >= required_free_bytes )) || \
  fail "Docker filesystem cannot preserve the host reserve plus one maximum-size in-flight job"

volume_size_bytes() {
  local volume="$1" budget="$2" mountpoint canonical_mountpoint
  if ! docker volume inspect "$volume" >/dev/null 2>&1; then
    return 0
  fi
  mountpoint="$(docker volume inspect --format '{{.Mountpoint}}' "$volume")"
  [[ "$mountpoint" == /* && -d "$mountpoint" && ! -L "$mountpoint" ]] || \
    fail "$volume has an unsafe mountpoint"
  canonical_mountpoint="$(realpath -e -- "$mountpoint")"
  case "$canonical_mountpoint" in
    "$docker_root"/volumes/*/_data) ;;
    *) fail "$volume mountpoint escaped the Docker volume root" ;;
  esac
  size="$(du -s -B1 -- "$canonical_mountpoint" | awk '{print $1}')"
  [[ "$size" =~ ^[0-9]+$ ]] || fail "$volume size could not be determined"
  (( size <= budget )) || fail "$volume exceeds its bounded workspace budget: $volume"
}

volume_size_bytes lecturesift-api-work "$api_workspace_budget"
volume_size_bytes lecturesift-worker-work "$worker_workspace_budget"

echo "LectureSift 4-vCPU / 8-GB resource guard passed."
