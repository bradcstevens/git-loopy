#!/usr/bin/env bash
set -euo pipefail
event=""
if [ "${1:-}" = "--event" ]; then
  event="${2:?missing hook event}"
  shift 2
fi
subcommand="${1:?usage: git-loopy-chain.sh [--event EVENT] <subcommand>}"
if [ -z "$event" ]; then
  case "$subcommand" in
    complete) event="subagentStop" ;;
    *) event="$subcommand" ;;
  esac
fi
payload="$(cat)"
logger="$(dirname "${BASH_SOURCE[0]}")/git-loopy-hook-log.sh"
log_invocation() {
  local exit_status="$1" decision="$2"
  [ -x "$logger" ] || return 0
  "$logger" --event "$event" --exit-status "$exit_status" --decision "$decision" \
    <<< "$payload" >/dev/null 2>&1 || :
}
candidates=(
  "${COPILOT_HOME:-$HOME/.copilot}/skills/next/scripts/chain.sh"
  "$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/skills/next/scripts/chain.sh"
)
for candidate in "${candidates[@]}"; do
  if [ -x "$candidate" ]; then
    set +e
    if [ "$subcommand" = "reenter" ]; then
      decision="$(python3 "$(dirname "${BASH_SOURCE[0]}")/git-loopy-agent-stop.py" <<< "$payload")"
    else
      decision="$("$candidate" "$subcommand" <<< "$payload")"
    fi
    chain_status=$?
    set -e
    log_invocation "$chain_status" "$decision"
    [ -z "$decision" ] || printf '%s\n' "$decision"
    exit "$chain_status"
  fi
done
log_invocation 0 '{"decision":"stand-down","reason":"chain-not-found"}'
echo "git-loopy chain hook: no chain.sh found; looked in ${candidates[*]}" >&2
exit 0
