#!/usr/bin/env bash

# PROTOTYPE - run with:
#   bash git-loopy/shell/prototypes/immutable-revisions-prototype.sh
#
# Question: can a jq-backed shell reducer select live Producer revision heads
# solely from ancestry, keep the repairable index non-authoritative, and require
# explicit re-attestation before a tainted head can stop quarantining guidance?

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=immutable-revisions-prototype-lib.sh
source "$script_dir/immutable-revisions-prototype-lib.sh"

state="$(git_loopy_revision_prototype_initial_state)"

render() {
  printf '\033[2J\033[H'
  printf '\033[1mImmutable Producer revision prototype\033[0m\n\n'
  jq . <<<"$state"
  printf '\n\033[1m[r]\033[0m root  \033[1m[f]\033[0m fork  '
  printf '\033[1m[s]\033[0m resolve all heads  \033[1m[m]\033[0m mutate head\n'
  printf '\033[1m[a]\033[0m re-attest tainted heads  '
  printf '\033[1m[i]\033[0m toggle repairable index  \033[1m[q]\033[0m quit\n'
}

render
while IFS= read -r -n 1 key; do
  case "$key" in
    r) state="$(git_loopy_revision_prototype_apply "$state" root)" ;;
    f) state="$(git_loopy_revision_prototype_apply "$state" fork)" ;;
    s) state="$(git_loopy_revision_prototype_apply "$state" resolve)" ;;
    m) state="$(git_loopy_revision_prototype_apply "$state" mutate)" ;;
    a) state="$(git_loopy_revision_prototype_apply "$state" reattest)" ;;
    i) state="$(git_loopy_revision_prototype_apply "$state" toggle-index)" ;;
    q) break ;;
    *) continue ;;
  esac
  render
done
