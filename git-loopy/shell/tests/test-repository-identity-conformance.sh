#!/usr/bin/env bash

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
port_dir="$(cd "$script_dir/.." && pwd)"
fixture="$port_dir/../conformance/repository-identity.json"

# shellcheck disable=SC1091
source "$port_dir/lib/issue-lease.sh"

cases=0
while IFS= read -r case_json; do
  # Command substitution would strip trailing newlines from the fixture URL.
  IFS= read -r -d '' url < <(jq -j '.url + "\u0000"' <<<"$case_json")
  actual="$(git_loopy_repository_from_remote_url "$url")"
  expected="$(jq -c .expected <<<"$case_json")"
  if [[ "$actual" != "$expected" ]]; then
    printf 'FAIL: %s\nexpected: %s\nactual:   %s\n' \
      "$(jq -r .id <<<"$case_json")" "$expected" "$actual" >&2
    exit 1
  fi
  cases=$((cases + 1))
done < <(jq -c '.cases[]' "$fixture")
((cases > 0))

printf 'Repository identity Conformance passed (%s cases).\n' "$cases"
