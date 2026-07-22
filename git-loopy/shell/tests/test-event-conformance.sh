#!/usr/bin/env bash

set -euo pipefail

if ((BASH_VERSINFO[0] < 4)); then
  printf 'Bash 4+ is required (found %s).\n' "$BASH_VERSION" >&2
  exit 1
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
port_dir="$(cd "$script_dir/.." && pwd)"
fixture="$port_dir/../conformance/event-schema.json"

# shellcheck disable=SC1091
source "$port_dir/lib/events.sh"

fail() {
  printf 'FAIL: %s\n' "$*" >&2
  exit 1
}

assert_equal() {
  local expected="$1"
  local actual="$2"
  local description="$3"
  [[ "$actual" == "$expected" ]] ||
    fail "$description"$'\n'"expected: $expected"$'\n'"actual:   $actual"
}

actual_types="$(
  for name in "${!GIT_LOOPY_EVENT_TYPES[@]}"; do
    jq -cn --arg key "$name" --arg value "${GIT_LOOPY_EVENT_TYPES[$name]}" \
      '{key: $key, value: $value}'
  done | jq -cs 'from_entries'
)"
jq -e --argjson actual "$actual_types" '.event_types == $actual' "$fixture" \
  >/dev/null || fail "event type literals drifted from event-schema.json"
jq -e \
  --argjson schema_version "$GIT_LOOPY_EVENT_SCHEMA_VERSION" \
  --argjson capabilities "$GIT_LOOPY_INSIGHT_CAPABILITIES_JSON" \
  '
    .schema_version == $schema_version
    and .insight_capabilities.orchestrators.shell == $capabilities
  ' "$fixture" >/dev/null ||
  fail "shell Insight capability manifest drifted from event-schema.json"

while IFS= read -r case_json; do
  case_id="$(jq -r '.id' <<<"$case_json")"
  event_json="$(jq -c '.event' <<<"$case_json")"
  expected="$(jq -r '.jsonl' <<<"$case_json")"
  actual="$(git_loopy_to_jsonl_line "$event_json")"
  assert_equal "$expected" "$actual" "serialization fixture: $case_id"
done < <(jq -c '.serialization_cases[]' "$fixture")

set +e
invalid_output="$(git_loopy_to_jsonl_line '{}' 2>/dev/null)"
invalid_status=$?
set -e
[[ "$invalid_status" -ne 0 && -z "$invalid_output" ]] ||
  fail "invalid events must fail without emitting an empty success record"

generated_run_id="$(git_loopy_new_run_id)"
[[ "$generated_run_id" =~ ^[0-9A-HJKMNP-TV-Z]{26}$ ]] ||
  fail "generated run id is not a 26-character Crockford ULID"
[[ "$(git_loopy_new_run_id 0)" == 0000000000* ]] ||
  fail "run id does not encode its millisecond timestamp as a ULID prefix"

generated_timestamp="$(git_loopy_iso_timestamp)"
[[ "$generated_timestamp" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}Z$ ]] ||
  fail "generated timestamp is not UTC ISO-8601 with millisecond precision"

temp_dir="$(mktemp -d)"
trap 'rm -rf "$temp_dir"' EXIT

fixed_run_id="01HXR0000000000000000000AA"
fixed_started_at="2026-05-16T00:00:00.123Z"
git_loopy_events_init "$temp_dir" "$fixed_run_id" "$fixed_started_at"

expected_replay="$temp_dir/.git-loopy/logs/2026-05-16T00-00-00Z-$fixed_run_id.jsonl"
assert_equal "$expected_replay" "$GIT_LOOPY_REPLAY_PATH" \
  "replay path must use the contract stem"
[[ ! -e "$GIT_LOOPY_REPLAY_PATH" ]] ||
  fail "event context must not create the replay file before the first record"

ghp_secret="ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
gho_secret="gho_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
jwt_secret="eyJAAAAAAAAAAAAAAAAA.BBBBBBBBBBBBBBBBBBBB.CCCCCCCCCCCCCCCCCCCC"
aws_secret="AKIAABCDEFGHIJKLMNOP"
payload="$(
  jq -cn \
    --arg ghp "$ghp_secret" \
    --arg gho "$gho_secret" \
    --arg jwt "$jwt_secret" \
    --arg aws "$aws_secret" \
    '{
      content: ("token=" + $ghp),
      nested: {gho: $gho, jwt: $jwt, aws: $aws},
      zeta: 2,
      alpha: 1
    }'
)"
stream_path="$temp_dir/stream.jsonl"
git_loopy_emit_event \
  "assistant.message" \
  "1" \
  "$payload" \
  "2026-05-16T00:00:01.456Z" >"$stream_path"
git_loopy_emit_event \
  "wrapper.run.end" \
  "null" \
  '{"reason":"complete"}' \
  "2026-05-16T00:00:02.789Z" >>"$stream_path"

cmp -s "$stream_path" "$GIT_LOOPY_REPLAY_PATH" ||
  fail "streamed and replayed records must be byte-identical"
stream="$(cat "$stream_path")"
for secret in "$ghp_secret" "$gho_secret" "$jwt_secret" "$aws_secret"; do
  [[ "$stream" != *"$secret"* ]] || fail "stream leaked a known secret shape"
done
[[ "$stream" == *"<redacted-secret>"* ]] ||
  fail "stream did not contain the redaction sentinel"

jq -se '
  length == 2
  and .[0].ts == "2026-05-16T00:00:01.456Z"
  and .[0].run_id == "01HXR0000000000000000000AA"
  and .[0].iter == 1
  and .[0].type == "assistant.message"
  and .[0].content == "token=<redacted-secret>"
  and .[0].nested == {
    gho: "<redacted-secret>",
    jwt: "<redacted-secret>",
    aws: "<redacted-secret>"
  }
  and .[1].iter == null
  and .[1].type == "wrapper.run.end"
' "$stream_path" >/dev/null || fail "emitted records do not satisfy the Event schema"

if git_loopy_events_init "$temp_dir" "not-a-run-id" "$fixed_started_at" 2>/dev/null; then
  fail "malformed explicit run id was accepted"
fi

printf 'shell Event-schema conformance: ok\n'
