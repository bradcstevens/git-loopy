#!/usr/bin/env bash

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=prototype_reconciliation_model.sh
source "$script_dir/prototype_reconciliation_model.sh"

state='{
  "facts": {
    "completion-a": "unsatisfied",
    "completion-b": "unsatisfied",
    "completion-c": "unsatisfied",
    "prerequisite-b": "unsatisfied",
    "prerequisite-c": "unverified"
  },
  "actions": [
    {"key": "a", "completion_fact": "completion-a", "prerequisite_facts": []},
    {"key": "b", "completion_fact": "completion-b", "prerequisite_facts": ["prerequisite-b"]},
    {"key": "c", "completion_fact": "completion-c", "prerequisite_facts": ["prerequisite-c"]}
  ]
}'

render() {
  printf '\033[2J\033[H'
  printf '\033[1mCurrent projection\033[0m\n'
  prototype_reconciliation_project "$state" | jq .
  printf '\n\033[1mCommands\033[0m\n'
  printf '  \033[1ms FACT STATUS\033[0m  set satisfied|unsatisfied|unverified\n'
  printf '  \033[1mq\033[0m              quit\n'
}

render
while read -r command fact status; do
  case "$command" in
    s)
      state="$(prototype_reconciliation_apply "$state" "$fact" "$status")"
      ;;
    q)
      break
      ;;
  esac
  render
done
