#!/usr/bin/env bash

set -euo pipefail

if ((BASH_VERSINFO[0] < 4)); then
  printf 'Bash 4+ is required (found %s).\n' "$BASH_VERSION" >&2
  exit 1
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
port_dir="$(cd "$script_dir/.." && pwd)"
fixture="$port_dir/../conformance/event-schema.json"
dashboard_fixture="$port_dir/../conformance/dashboard-insights.json"
release_fixture="$port_dir/../conformance/release-version.json"

# shellcheck disable=SC1091
source "$port_dir/lib/orchestrator.sh"

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

# #334: the run-scoped Rate-card capability (ADR-0026, Wrapper contract 12).
# This port reads no model listing, so it resolves no **Rate card** on any Run
# and declares the capability false with an explicit `null` card beside it.
# Pinned as a *relationship* between the fixture and this port's production
# constants rather than as a second copy of the literal, so the two cannot end
# up agreeing only with themselves.
jq -e \
  --argjson run_scoped "$GIT_LOOPY_RUN_SCOPED_INSIGHT_CAPABILITIES_JSON" \
  --argjson wire "$(git_loopy_run_insight_capabilities_json)" \
  --argjson card "$GIT_LOOPY_RATE_CARD_JSON" \
  '
    .insight_capabilities.run_scoped as $fixture
    | ($run_scoped | keys) == ($fixture.names | sort)
    and all($run_scoped[]; . == false)
    and ($fixture.declared_by | index("shell")) != null
    and ($fixture.never_resolved_by | index("shell")) != null
    # A false declaration publishes an explicit `null`, never an empty card:
    # an empty card is a record nothing can be audited against.
    and $card == null
    # The wire manifest is exactly the frozen per-distribution keys plus the
    # run-scoped ones -- a port may not smuggle a run-scoped answer into the
    # frozen manifest, nor drop one on its way to `wrapper.run.start`.
    and ($wire | keys) == ((.insight_capabilities.names + $fixture.names) | sort)
  ' "$fixture" >/dev/null ||
  fail "shell run-scoped Insight capability drifted from event-schema.json"

# #311 AC3: Parallel mode is declared, never inferred from silence. This port
# has no scheduler, so `parallel_mode` is false -- and a port that cannot fill a
# second Lane cannot honour refill, backlog, adaptation, or the contribution
# stream either, so the whole manifest must be false with it.
jq -e \
  --argjson capabilities "$GIT_LOOPY_PARALLEL_CAPABILITIES_JSON" \
  '
    .parallel_capabilities.orchestrators.shell == $capabilities
    and ($capabilities | keys_unsorted) == .parallel_capabilities.names
    and all($capabilities[]; type == "boolean")
    and (
      $capabilities.parallel_mode
      or all($capabilities[]; . == false)
    )
  ' "$fixture" >/dev/null ||
  fail "shell parallel capability manifest drifted from event-schema.json"

# The manifest is a claim about this port's own code, so read the code. A
# distribution declaring `contribution_events: false` must name no producer for
# the Lane-contribution lifecycle literals: advertising a stream no replay can
# contain is exactly the drift the fixture cannot catch by itself.
if [[ "$(jq -r '.contribution_events' <<<"$GIT_LOOPY_PARALLEL_CAPABILITIES_JSON")" == "false" ]]; then
  while IFS= read -r lifecycle_key; do
    if grep -rqE "GIT_LOOPY_EVENT_TYPES\[$lifecycle_key\]" "$port_dir/lib" "$port_dir/git-loopy.sh"; then
      fail "contribution_events is declared false but $lifecycle_key has a producer"
    fi
  done < <(
    jq -r '
      .contribution_identity.lifecycle_types[] as $literal
      | (.event_types | to_entries[] | select(.value == $literal) | .key)
    ' "$fixture"
  )
fi

# A refusal an operator can act on, instead of a Lane cap accepted and ignored.
# Without this the flag is a silent no-op: the Run is byte-identical to a serial
# Run, so "Parallel mode is unimplemented here" and "nothing carries
# parallel-safe" look the same from the operator's seat.
(
  GIT_LOOPY_MAX_PARALLEL=3
  refusal="$(git_loopy_assert_parallel_supported 2>&1)" && {
    printf 'FAIL: a Lane cap above 1 must be refused, not accepted\n' >&2
    exit 1
  }
  [[ "$refusal" == *"parallel_mode"* && "$refusal" == *"shell"* ]] || {
    printf 'FAIL: refusal must name the capability and the distribution\n' >&2
    printf 'actual: %s\n' "$refusal" >&2
    exit 1
  }
) || exit 1
# A cap that bash arithmetic would misread must still be refused: a
# leading-zero literal is octal to `(( ))`, and a value wider than 64 bits
# wraps. Either would turn an arithmetic error into an accepted cap.
for refused in 2 08 010 "18446744073709551617"; do
  (
    GIT_LOOPY_MAX_PARALLEL="$refused"
    ! git_loopy_assert_parallel_supported 2>/dev/null
  ) || fail "a Lane cap of '$refused' must be refused"
done
for accepted in "" 0 1 00 01; do
  (
    GIT_LOOPY_MAX_PARALLEL="$accepted"
    git_loopy_assert_parallel_supported
  ) || fail "a Lane cap of '${accepted:-unset}' is serial and must be accepted"
done
for rejected in "-1" "1.5" "two" " 2"; do
  (
    GIT_LOOPY_MAX_PARALLEL="$rejected"
    ! git_loopy_assert_parallel_supported 2>/dev/null
  ) || fail "a malformed Lane cap of '$rejected' must be rejected"
done
jq -e \
  --arg release_version "$(jq -r '.expected_release_version' "$release_fixture")" \
  '
    first(
      .serialization_cases[]
      | select(.id == "run-start-insight-capabilities")
    ).event.release_version == $release_version
  ' "$fixture" >/dev/null ||
  fail "Run-start Event drifted from the shared Release version"

while IFS= read -r case_json; do
  case_id="$(jq -r '.id' <<<"$case_json")"
  event_json="$(jq -c '.event' <<<"$case_json")"
  expected="$(jq -r '.jsonl' <<<"$case_json")"
  actual="$(git_loopy_to_jsonl_line "$event_json")"
  assert_equal "$expected" "$actual" "serialization fixture: $case_id"
done < <(jq -c '.serialization_cases[]' "$fixture")

# #311 AC2: the rolling Event stream, driven through this port's own
# serializer. This port schedules no Lane, but it is still a family member that
# has to read and write the same bytes -- a drifted literal or a re-sorted
# payload key would make its replay logs unreadable to every other member the
# day Parallel mode does arrive here.
rolling_records=0
while IFS= read -r record_json; do
  case_id="$(jq -r '.case_id' <<<"$record_json")"
  event_json="$(jq -c '.event' <<<"$record_json")"
  expected="$(jq -r '.jsonl' <<<"$record_json")"
  actual="$(git_loopy_to_jsonl_line "$event_json")"
  assert_equal "$expected" "$actual" "rolling stream: $case_id"
  rolling_records=$((rolling_records + 1))
done < <(
  jq -c '
    .rolling_stream_cases[]
    | select(.distributions | index("shell"))
    | . as $case
    | range(0; $case.events | length)
    | {case_id: $case.id, event: $case.events[.], jsonl: $case.jsonl[.]}
  ' "$fixture"
)
((rolling_records > 0)) ||
  fail "no rolling stream case names the shell distribution"

apply_rollup_input() {
  # Drive the production rollup builder from one fixture `input` object.
  local case_json="$1"
  local prefix="$2"
  TEST_MONOTONIC_NOW="$(jq -r "${prefix}.finished_monotonic" <<<"$case_json")"
  git_loopy_monotonic_seconds() {
    printf '%s\n' "$TEST_MONOTONIC_NOW"
  }
  _GIT_LOOPY_ITERATION_STARTED_MONOTONIC="$(
    jq -r "${prefix}.iteration_started_monotonic" <<<"$case_json"
  )"
  _GIT_LOOPY_ACTIVE_REF="$(jq -r "${prefix}.active_issue" <<<"$case_json")"
  _GIT_LOOPY_ACTIVE_STARTED_AT="$(
    jq -r "${prefix}.active_started_at" <<<"$case_json"
  )"
  _GIT_LOOPY_ACTIVE_STARTED_MONOTONIC="$(
    jq -r "${prefix}.active_started_monotonic" <<<"$case_json"
  )"
  _GIT_LOOPY_ACTIVE_CLOSED_AT="$(
    jq -r "${prefix}.active_closed_at // \"\"" <<<"$case_json"
  )"
  _GIT_LOOPY_ACTIVE_CLOSED_MONOTONIC="$(
    jq -r "${prefix}.active_closed_monotonic // 0" <<<"$case_json"
  )"
  _GIT_LOOPY_ISSUE_FIRST_STARTED_AT=()
  _GIT_LOOPY_ISSUE_FIRST_STARTED_MONOTONIC=()
  _GIT_LOOPY_ISSUE_CUMULATIVE_ACTIVE=()
  _GIT_LOOPY_ISSUE_FIRST_STARTED_AT["$_GIT_LOOPY_ACTIVE_REF"]="$(
    jq -r "${prefix}.first_started_at // ${prefix}.active_started_at" \
      <<<"$case_json"
  )"
  _GIT_LOOPY_ISSUE_FIRST_STARTED_MONOTONIC["$_GIT_LOOPY_ACTIVE_REF"]="$(
    jq -r \
      "${prefix}.first_started_monotonic // ${prefix}.active_started_monotonic" \
      <<<"$case_json"
  )"
  _GIT_LOOPY_ISSUE_CUMULATIVE_ACTIVE["$_GIT_LOOPY_ACTIVE_REF"]="$(
    jq -r "${prefix}.previous_cumulative_active_seconds" <<<"$case_json"
  )"
  git_loopy_build_iteration_rollup \
    "$(jq -r "${prefix}.commits // 0" <<<"$case_json")" \
    "$(jq -r "${prefix}.auto_closures // 0" <<<"$case_json")" \
    "$(jq -r "${prefix}.pr_advances // 0" <<<"$case_json")" \
    "$(jq -r "${prefix}.strikes // 0" <<<"$case_json")" \
    "$(jq -r "${prefix}.terminal_outcome // \"\"" <<<"$case_json")"
}

while IFS= read -r case_json; do
  case_id="$(jq -r '.id' <<<"$case_json")"
  apply_rollup_input "$case_json" ".input"
  jq -e --argjson actual "$GIT_LOOPY_ITERATION_ROLLUP_JSON" \
    '.expected == $actual' <<<"$case_json" >/dev/null ||
    fail "normalized rollup fixture: $case_id"

  # An unavailable measurement must be unavailable *because* this port declares
  # it so. The fixture above pins the nulls as literals, which cannot tell a
  # deliberately unknown measurement apart from one that was simply forgotten --
  # so derive the demand from the production capability manifest instead. A
  # capability this port declares false MUST send every measurement it governs
  # as null; contract 12's value semantics forbid reporting an unavailable
  # counter as 0 or an unavailable collection as []. Flip a capability and this
  # asks for the opposite.
  jq -e \
    --argjson capabilities "$GIT_LOOPY_INSIGHT_CAPABILITIES_JSON" \
    --argjson rollup "$GIT_LOOPY_ITERATION_ROLLUP_JSON" \
    '
      # Every normalized measurement each Insight capability governs. Keys are
      # asserted below to equal the manifest keys exactly, so a new capability
      # cannot arrive uncovered and a typo cannot quietly govern nothing. Two
      # of them govern no rollup field at all -- `agent_output` rides the
      # `agent.output` Event and `routing` rides `wrapper.pickup.bound` -- and
      # their empty lists are what makes that key equality meaningful.
      def governed:
        {
          agent_output: [],
          routing: [],
          structured_agent_events: [$rollup.summary.tool_count],
          token_usage: (
            [
              $rollup.summary.model,
              $rollup.summary.tokens_in,
              $rollup.summary.tokens_out,
              $rollup.summary.observed_tokens
            ]
            + [$rollup.issues[]?.consumption.model]
            + [$rollup.issues[]?.consumption.tokens_in]
            + [$rollup.issues[]?.consumption.tokens_out]
          ),
          context_window: (
            [$rollup.summary.peak_context_window]
            + [$rollup.issues[]?.peak_context_window]
          ),
          skill_consultation: [
            $rollup.summary.skill_call_count,
            $rollup.summary.skills_consulted
          ],
          cost: (
            [$rollup.summary.cost_usd]
            + [$rollup.issues[]?.cost_usd]
          )
        };
      (governed | keys) == ($capabilities | keys)
      and (
        [$capabilities | to_entries[] | select(.value == false) | .key]
        | length > 0
      )
      and (
        all(
          $capabilities | to_entries[] | select(.value == false) | .key;
          governed[.] | all(. == null)
        )
      )
    ' >/dev/null <<<'null' ||
    fail "unavailable telemetry must stay null for a declared-false capability: $case_id"

  # The complement: a fact this port really can observe is never nulled away in
  # the name of honesty. Without this, nulling the whole rollup would pass.
  jq -e --argjson rollup "$GIT_LOOPY_ITERATION_ROLLUP_JSON" \
    '
      ($rollup.outcome | type) == "string"
      and ($rollup.duration_seconds | type) == "number"
      and all(
        $rollup.summary
        | .commits, .auto_closures, .pr_advances, .strikes;
        type == "number"
      )
      and all(
        $rollup.issues[]?;
        (.issue | type) != "null"
        and (.status | type) == "string"
        and (.first_started_at | type) == "string"
        and (.active_seconds | type) == "number"
        and (.cumulative_active_seconds | type) == "number"
      )
    ' >/dev/null <<<'null' ||
    fail "observable lifecycle and accounting facts must stay observed: $case_id"

  # #334 AC3: billing telemetry arrives on the SDK event stream, which this port
  # does not subscribe to, so it emits no Credits, premium-request or cache-split
  # figure at all. Those keys are *omitted* rather than nulled -- that omission is
  # what makes them additive for the reference Orchestrator -- so their absence at
  # any depth is the assertion. A fabricated 0 here would say the Iteration was
  # free rather than that this port cannot see what it cost.
  jq -e --argjson rollup "$GIT_LOOPY_ITERATION_ROLLUP_JSON" \
    '
      [$rollup | paths | .[-1] | select(type == "string")]
      | any(IN("credits", "premium_requests", "cache_read", "cache_write"))
      | not
    ' >/dev/null <<<'null' ||
    fail "this port must emit no billing figure it cannot observe: $case_id"
done < <(
  jq -c '.normalized_rollup_cases[] | select(.orchestrator == "shell")' "$fixture"
)

# The renderer-neutral Dashboard seam is only anti-drift if the native trace it
# pins is one this port can actually emit. Every native Dashboard case therefore
# declares the producer input behind each `wrapper.iteration.end`, and the real
# rollup builder must reproduce that Event's rollup payload exactly.
dashboard_rollups=0
while IFS= read -r case_json; do
  case_id="$(jq -r '.case_id' <<<"$case_json")"
  event_index="$(jq -r '.event_index' <<<"$case_json")"
  apply_rollup_input "$case_json" ".input"
  jq -e --argjson actual "$GIT_LOOPY_ITERATION_ROLLUP_JSON" \
    '.expected == $actual' <<<"$case_json" >/dev/null ||
    fail "dashboard producer rollup: $case_id event $event_index"
  dashboard_rollups=$((dashboard_rollups + 1))
done < <(
  jq -c '
    .cases[]
    | . as $case
    | ($case.producer_rollups // [])[]
    | select(.distributions | index("shell"))
    | {
        case_id: $case.id,
        event_index: .event_index,
        input: .input,
        expected: (
          $case.events[.event_index]
          | {outcome, duration_seconds, summary, issues}
        )
      }
  ' "$dashboard_fixture"
)
((dashboard_rollups > 0)) ||
  fail "no native Dashboard case declares a shell producer rollup"

# The same demand one Event earlier: a native case's `wrapper.run.start` must
# declare what this port really declares. Without this the Dashboard fixture
# could pin a native trace whose capability manifest no port emits, and every
# consumer would agree with a producer that does not exist.
native_run_starts=0
while IFS= read -r declared; do
  jq -e \
    --argjson wire "$(git_loopy_run_insight_capabilities_json)" \
    --argjson card "$GIT_LOOPY_RATE_CARD_JSON" \
    '
      .insight_capabilities == $wire
      and (. | has("rate_card"))
      and .rate_card == $card
    ' <<<"$declared" >/dev/null ||
    fail "native Dashboard Run start declares a manifest this port cannot emit"
  native_run_starts=$((native_run_starts + 1))
done < <(
  jq -c '
    .cases[]
    | select((.producer_rollups // []) | any(.distributions | index("shell")))
    | .events[0]
    | select(.type == "wrapper.run.start")
  ' "$dashboard_fixture"
)
((native_run_starts > 0)) ||
  fail "no native Dashboard case pins a shell Run start"

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
