#!/usr/bin/env bash
set -euo pipefail

chain_script="${GIT_LOOPY_NEXT_CHAIN:-}"
if [[ -z "$chain_script" ]]; then
  chain_script="$HOME/.copilot/skills/next/chain.sh"
fi

if [[ ! -f "$chain_script" ]]; then
  printf 'git-loopy chain hook: /next chain.sh was not found at %s\n' "$chain_script" >&2
  printf 'Install the /next skill or set GIT_LOOPY_NEXT_CHAIN to its chain.sh path.\n' >&2
  exit 1
fi

exec bash "$chain_script" "$@"
