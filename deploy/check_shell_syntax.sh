#!/usr/bin/env bash
set -Eeuo pipefail
set +x

root_dir="$(realpath -e -- "$(dirname -- "${BASH_SOURCE[0]}")/..")"
deploy_dir="$root_dir/deploy"
scripts=()

while IFS= read -r -d '' script; do
  scripts+=("$script")
done < <(find "$deploy_dir" -maxdepth 1 -type f -name '*.sh' -print0 | LC_ALL=C sort -z)

if (( ${#scripts[@]} == 0 )); then
  echo "No deployment shell scripts were found." >&2
  exit 1
fi

for script in "${scripts[@]}"; do
  /bin/bash -n "$script"
done

printf 'SHELL_SYNTAX_OK|scripts=%s\n' "${#scripts[@]}"
