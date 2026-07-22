#!/usr/bin/env bash

# PROTOTYPE: prove native shell canonicalization matches the shared tracer identities.
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fixture="$script_dir/../conformance/continuation-scenarios.json"

sha256() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum | awk '{print $1}'
  else
    shasum -a 256 | awk '{print $1}'
  fi
}

workflow="$(
  jq -c '.workflows[] | select(.id == "python-trusted-planning-action")' "$fixture"
)"
completion="$(
  jq -cS '.commands[0].request.json.completion' <<<"$workflow"
)"
action="$(jq -c '.commands[0].request.json.completion.actions[0]' <<<"$workflow")"
semantics="$(
  jq -cS '
    def without_advisory:
      if type == "object" then
        with_entries(select(.key != "advisory_extensions") | .value |= without_advisory)
      elif type == "array" then map(without_advisory)
      else .
      end;
    {
      instruction,
      prerequisites,
      interaction,
      completion_condition,
      effects: (.effects // []),
      requirements: (.requirements // []),
      triggers: (.triggers // [])
    }
    | without_advisory
  ' <<<"$action"
)"
identity_source="$(
  jq -cS '
    {
      anchor: .commands[0].request.json.completion.workstream.anchor,
      kind: .commands[0].request.json.completion.actions[0].kind,
      target: .commands[0].request.json.completion.actions[0].target,
      occurrence: .commands[0].request.json.completion.actions[0].occurrence
    }
  ' <<<"$workflow"
)"

revision_id="$(printf '%s' "$completion" | sha256)"
semantic_fingerprint="$(printf '%s' "$semantics" | sha256)"
action_identity="$(printf '%s' "$identity_source" | sha256)"
expected_revision="$(
  jq -r '.commands[0].expected.stdout.receipt.revision_id' <<<"$workflow"
)"
expected_fingerprint="$(
  jq -r '
    .commands[0].expected.stdout.receipt.semantic_fingerprints["publish-spec"]
  ' <<<"$workflow"
)"
expected_identity="$(
  jq -r '.commands[1].expected.stdout.result.actions[0].identity' <<<"$workflow"
)"

jq -n \
  --arg revision_id "$revision_id" \
  --arg expected_revision "$expected_revision" \
  --arg semantic_fingerprint "$semantic_fingerprint" \
  --arg expected_fingerprint "$expected_fingerprint" \
  --arg action_identity "$action_identity" \
  --arg expected_identity "$expected_identity" \
  '{
    revision: {
      observed: $revision_id,
      expected: $expected_revision,
      matches: ($revision_id == $expected_revision)
    },
    semantic_fingerprint: {
      observed: $semantic_fingerprint,
      expected: $expected_fingerprint,
      matches: ($semantic_fingerprint == $expected_fingerprint)
    },
    action_identity: {
      observed: $action_identity,
      expected: $expected_identity,
      matches: ($action_identity == $expected_identity)
    }
  }'

[[ "$revision_id" == "$expected_revision" ]]
[[ "$semantic_fingerprint" == "$expected_fingerprint" ]]
[[ "$action_identity" == "$expected_identity" ]]
