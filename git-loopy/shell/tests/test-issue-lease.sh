#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$script_dir/../lib/orchestrator.sh"
fixture="$script_dir/../../conformance/issue-lease.json"

while IFS= read -r row; do
  input="$(jq -c '.input' <<<"$row")"
  expected="$(jq -cS '.expected' <<<"$row")"
  actual="$(git_loopy_inspect_lease "$input" | jq -cS '{
    state, owner: .record.run_id, diagnostics
  }')"
  if [[ "$actual" != "$expected" ]]; then
    printf 'FAIL: %s\nexpected: %s\nactual: %s\n' \
      "$(jq -r '.id' <<<"$row")" "$expected" "$actual" >&2
    exit 1
  fi
done < <(jq -c '
  . as $fixture | .cases[] | . as $case
  | ($fixture.record + (.set // {})
     | delpaths(($case.remove // []) | map([.]))) as $record
  | {
      id, expected,
      input: {
        raw: (if has("raw") then .raw else ($record | tojson) end),
        now, repository: $fixture.repository, issue: $fixture.issue
      } + (if has("skew_tolerance_seconds") then
        {skew_tolerance_seconds} else {} end)
    }
' "$fixture")

while IFS= read -r row; do
  if actual="$(git_loopy_inspect_lease "$(jq -c '.input' <<<"$row")" 2>&1)"; then
    printf 'FAIL: accepted invalid context %s\n' "$(jq -r '.id' <<<"$row")" >&2
    exit 1
  fi
  if [[ "$actual" != *"invalid $(jq -r '.field' <<<"$row")"* ]]; then
    printf 'FAIL: wrong context diagnostic: %s\n' "$actual" >&2
    exit 1
  fi
done < <(jq -c '
  . as $fixture | .invalid_context_cases[]
  | {id, field, input: ({
      raw: ($fixture.record | tojson), now: 1000,
      repository: $fixture.repository, issue: $fixture.issue
    } + {(.field): .value})}
' "$fixture")

printf 'PASS: Lease record Conformance\n'
