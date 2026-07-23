#!/usr/bin/env bash

# Run with: bash git-loopy/shell/prototypes/iteration_rollup_tui.sh

set -euo pipefail

prototype_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=iteration_rollup_logic.sh
source "$prototype_dir/iteration_rollup_logic.sh"

state="$(shell_rollup_initial_state)"
while true; do
  printf '\033[2J\033[H'
  printf '\033[1mPROTOTYPE: shell normalized Iteration rollup\033[0m\n'
  jq . <<<"$state"
  printf '\n\033[1mm\033[0m marker  \033[1mf\033[0m fallback  '
  printf '\033[1mt\033[0m tick  \033[1mw\033[0m wall back  '
  printf '\033[1mk\033[0m commit  \033[1ma\033[0m advance  '
  printf '\033[1mc\033[0m close  \033[1mn\033[0m finish  '
  printf '\033[1mq\033[0m quit\n> '
  IFS= read -r choice
  case "$choice" in
    m) action="marker" ;;
    f) action="fallback" ;;
    t) action="tick" ;;
    w) action="wall-back" ;;
    k) action="commit" ;;
    a) action="advance" ;;
    c) action="close" ;;
    n) action="finish" ;;
    q) exit 0 ;;
    *) continue ;;
  esac
  state="$(shell_rollup_reduce "$state" "$action")"
done
