#!/usr/bin/env bash

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
port_dir="$(cd "$script_dir/.." && pwd)"
fixture="$port_dir/../conformance/issue-lease.json"

# shellcheck disable=SC1091
source "$port_dir/lib/issue-lease.sh"

cases=0
while IFS= read -r case_json; do
  request="$(jq -cn --argjson fixture "$(jq -c . "$fixture")" --argjson case "$case_json" '
    {
      raw: (if $case | has("raw") then $case.raw else
        ($fixture.record + ($case.set // {}) | delpaths(
          ($case.remove // []) | map([.])
        ) | tojson) end),
      now: $case.now, repository: $fixture.repository, issue: $fixture.issue
    } + (if $case | has("skew_tolerance_seconds") then
      {skew_tolerance_seconds: $case.skew_tolerance_seconds} else {} end)
  ')"
  actual="$(git_loopy_inspect_lease "$request")"
  expected="$(jq -cS '.expected' <<<"$case_json")"
  actual="$(jq -cS '{state, owner: .record.run_id, diagnostics}' <<<"$actual")"
  if [[ "$actual" != "$expected" ]]; then
    printf 'FAIL: %s\nexpected: %s\nactual:   %s\n' \
      "$(jq -r .id <<<"$case_json")" "$expected" "$actual" >&2
    exit 1
  fi
  cases=$((cases + 1))
done < <(jq -c '.cases[]' "$fixture")
((cases > 0))

cases=0
while IFS= read -r case_json; do
  request="$(jq -cn --argjson fixture "$(jq -c . "$fixture")" --argjson case "$case_json" '
    {raw: ($fixture.record | tojson), now: 1000,
     repository: $fixture.repository, issue: $fixture.issue}
    + {($case.field): $case.value}
  ')"
  if diagnostic="$(git_loopy_inspect_lease "$request" 2>&1)"; then
    printf 'FAIL: invalid Lease context accepted: %s\n' "$(jq -r .id <<<"$case_json")" >&2
    exit 1
  fi
  if [[ "$diagnostic" != *"invalid $(jq -r .field <<<"$case_json")"* ]]; then
    printf 'FAIL: unexpected Lease context diagnostic: %s\n' "$diagnostic" >&2
    exit 1
  fi
  cases=$((cases + 1))
done < <(jq -c '.invalid_context_cases[]' "$fixture")
((cases > 0))

request="$(jq -c '{raw: (.record | tojson), now: 1000, repository, issue}' "$fixture")"
actual="$(git_loopy_inspect_lease "$request" | jq -cS '.record')"
[[ "$actual" == "$(jq -cS '.record' "$fixture")" ]] || {
  printf 'FAIL: Lease inspection changed the stored record\n' >&2
  exit 1
}
cases=0
while IFS= read -r case_json; do
  request="$(jq -c '{action, state, owner, run_id}' <<<"$case_json")"
  actual="$(git_loopy_decide_lease_action "$request")"
  expected="$(jq -r '.expected' <<<"$case_json")"
  if [[ "$actual" != "$expected" ]]; then
    printf 'FAIL: %s\nexpected: %s\nactual:   %s\n' \
      "$(jq -r .id <<<"$case_json")" "$expected" "$actual" >&2
    exit 1
  fi
  cases=$((cases + 1))
done < <(jq -c '.action_cases[]' "$fixture")
((cases > 0))

for invalid in '{"action":"steal","state":"expired","owner":null,"run_id":"r"}' \
               '{"action":"claim","state":"gone","owner":null,"run_id":"r"}'; do
  if diagnostic="$(git_loopy_decide_lease_action "$invalid" 2>&1)"; then
    printf 'FAIL: invalid Lease action accepted: %s\n' "$invalid" >&2
    exit 1
  fi
  [[ "$diagnostic" == *"Lease action: invalid"* ]] || {
    printf 'FAIL: unexpected Lease action diagnostic: %s\n' "$diagnostic" >&2
    exit 1
  }
done

printf 'Lease record Conformance passed.\n'
