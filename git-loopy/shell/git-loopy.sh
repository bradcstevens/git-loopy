#!/usr/bin/env bash

if [[ -z "${BASH_VERSION:-}" ]] || ((BASH_VERSINFO[0] < 4)); then
  printf '%s\n' \
    "git-loopy's shell Orchestrator requires Bash 4+ (found ${BASH_VERSION:-unknown})." \
    "macOS ships Bash 3.2; install a current Bash with \`brew install bash\` and rerun this script with it." \
    >&2
  exit 1
fi

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
packaged_prompt="$(cd "$script_dir/.." && pwd)/PROMPT.md"

# shellcheck disable=SC1091
source "$script_dir/lib/orchestrator.sh"

set +e
git_loopy_main "$packaged_prompt" "$@"
status=$?
set -e
exit "$status"
