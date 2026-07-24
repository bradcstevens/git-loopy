#!/usr/bin/env bash

set -euo pipefail

if ((BASH_VERSINFO[0] < 4)); then
  printf 'Bash 4+ is required (found %s).\n' "$BASH_VERSION" >&2
  exit 1
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
port_dir="$(cd "$script_dir/.." && pwd)"
fixture="$port_dir/../conformance/continuation-scenarios.json"
release_fixture="$port_dir/../conformance/release-version.json"
entrypoint="$port_dir/git-loopy.sh"
scripted_github="$script_dir/scripted-github.sh"
real_jq_dir="$(dirname "$(command -v jq)")"
bash_bin="$(command -v bash)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

fail() {
  printf 'FAIL: %s\n' "$*" >&2
  exit 1
}

mkdir -p "$tmp/bin"
cp "$scripted_github" "$tmp/bin/gh"
chmod +x "$tmp/bin/gh"

run_transport_probe() {
  local probe_script="$tmp/probe-github-script.json"
  local probe_state="$tmp/probe-github-state"
  local probe_log="$tmp/probe-github-calls"
  jq -c '.github_transport_probe.github_script' "$fixture" >"$probe_script"
  : >"$probe_log"
  rm -f "$probe_state"

  local invocation
  while IFS= read -r invocation; do
    local -a probe_arguments=()
    mapfile -d '' -t probe_arguments < <(
      jq -j '.arguments[] + "\u0000"' <<<"$invocation"
    )
    local probe_stdin
    if jq -e 'has("stdin_json")' <<<"$invocation" >/dev/null; then
      probe_stdin="$(jq -c '.stdin_json' <<<"$invocation")"
    else
      probe_stdin="$(jq -r '.stdin // ""' <<<"$invocation")"
    fi
    local stdout_path="$tmp/probe.stdout"
    local stderr_path="$tmp/probe.stderr"
    local status
    set +e
    printf '%s' "$probe_stdin" |
      PATH="$tmp/bin:$real_jq_dir:/usr/bin:/bin" \
      GIT_LOOPY_SCRIPTED_GITHUB_LOG="$probe_log" \
      GIT_LOOPY_SCRIPTED_GITHUB_SCRIPT="$probe_script" \
      GIT_LOOPY_SCRIPTED_GITHUB_STATE="$probe_state" \
      "$tmp/bin/gh" "${probe_arguments[@]}" \
        >"$stdout_path" 2>"$stderr_path"
    status="${PIPESTATUS[1]}"
    set -e

    local expected_status
    expected_status="$(jq -r '.expected.exit_code' <<<"$invocation")"
    [[ "$status" == "$expected_status" ]] ||
      fail "scripted GitHub probe exit: expected $expected_status, got $status"
    if jq -e '.expected | has("stdout_json")' <<<"$invocation" >/dev/null; then
      jq -e --argjson expected "$(jq -c '.expected.stdout_json' <<<"$invocation")" \
        '. == $expected' "$stdout_path" >/dev/null ||
        fail "scripted GitHub probe JSON stdout mismatch"
    else
      local expected_stdout
      expected_stdout="$(jq -r '.expected.stdout' <<<"$invocation")"
      [[ "$(<"$stdout_path")" == "$expected_stdout" ]] ||
        fail "scripted GitHub probe stdout mismatch"
    fi
    local stderr_needle
    stderr_needle="$(jq -r '.expected.stderr_contains' <<<"$invocation")"
    grep -Fi -- "$stderr_needle" "$stderr_path" >/dev/null ||
      fail "scripted GitHub probe stderr does not contain: $stderr_needle"
  done < <(jq -c '.github_transport_probe.invocations[]' "$fixture")

  local consumed=0
  [[ ! -f "$probe_state" ]] || consumed="$(<"$probe_state")"
  local expected_steps
  expected_steps="$(jq '.github_transport_probe.github_script | length' "$fixture")"
  [[ "$consumed" == "$expected_steps" ]] ||
    fail "scripted GitHub probe did not consume every listed call"
  local actual_calls
  actual_calls="$(jq -Rsc 'split("\n") | map(select(length > 0))' <"$probe_log")"
  local expected_calls
  expected_calls="$(jq -c '.github_transport_probe.expected_github_calls' "$fixture")"
  [[ "$actual_calls" == "$expected_calls" ]] ||
    fail "scripted GitHub probe call log mismatch"
}

run_transport_probe

jq -e \
  --arg release_version "$(jq -r '.expected_release_version' "$release_fixture")" \
  '
    first(
      .scenarios[]
      | select(.id == "capabilities-shell")
    ).expected.stdout.capabilities.release_version == $release_version
  ' "$fixture" >/dev/null ||
  fail "shell Continuation capabilities drifted from the shared Release version"

run_github_failure_probes() {
  local workflow
  workflow="$(
    jq -c '
      .workflows[]
      | select(
          .id == "trusted-planning-action"
          and ((.distributions // []) | index("shell"))
        )
    ' "$fixture"
  )"
  local request
  request="$(jq -c '.commands[0].request.json' <<<"$workflow")"
  local github_script="$tmp/publish-failure-github-script.json"
  local github_state="$tmp/publish-failure-github-state"
  local github_log="$tmp/publish-failure-github-calls"
  jq -cn \
    --arg command "api repos/octo/example/issues/comments/7001" \
    '[{
      command: $command,
      exit_code: 1,
      stdout: "",
      stderr: "evidence unavailable"
    }]' >"$github_script"
  : >"$github_log"

  local stdout_path="$tmp/publish-failure.stdout"
  local stderr_path="$tmp/publish-failure.stderr"
  local status
  set +e
  printf '%s' "$request" |
    PATH="$tmp/bin:$real_jq_dir:/usr/bin:/bin" \
    GIT_LOOPY_SCRIPTED_GITHUB_LOG="$github_log" \
    GIT_LOOPY_SCRIPTED_GITHUB_SCRIPT="$github_script" \
    GIT_LOOPY_SCRIPTED_GITHUB_STATE="$github_state" \
    "$bash_bin" "$entrypoint" continuation publish \
      >"$stdout_path" 2>"$stderr_path"
  status="${PIPESTATUS[1]}"
  set -e

  [[ "$status" == 1 ]] ||
    fail "publish GitHub failure exit: expected 1, got $status"
  jq -e '
    .ok == false
    and .operation == "publish"
    and .error.code == "github_error"
  ' "$stdout_path" >/dev/null ||
    fail "publish GitHub failure did not return a typed error"
  [[ "$(wc -l <"$github_log" | tr -d ' ')" == 1 ]] ||
    fail "publish continued mutating GitHub after evidence failure"

  request="$(jq -c '.commands[1].request.json' <<<"$workflow")"
  github_script="$tmp/reconcile-failure-github-script.json"
  github_state="$tmp/reconcile-failure-github-state"
  github_log="$tmp/reconcile-failure-github-calls"
  jq -cn \
    --arg command \
      "issue list --repo octo/example --state all --label git-loopy-continuation --limit 100 --json number,state,url,comments" \
    '[{
      command: $command,
      exit_code: 1,
      stdout: "",
      stderr: "carrier discovery unavailable"
    }]' >"$github_script"
  : >"$github_log"

  stdout_path="$tmp/reconcile-failure.stdout"
  stderr_path="$tmp/reconcile-failure.stderr"
  set +e
  printf '%s' "$request" |
    PATH="$tmp/bin:$real_jq_dir:/usr/bin:/bin" \
    GIT_LOOPY_SCRIPTED_GITHUB_LOG="$github_log" \
    GIT_LOOPY_SCRIPTED_GITHUB_SCRIPT="$github_script" \
    GIT_LOOPY_SCRIPTED_GITHUB_STATE="$github_state" \
    "$bash_bin" "$entrypoint" continuation reconcile \
      >"$stdout_path" 2>"$stderr_path"
  status="${PIPESTATUS[1]}"
  set -e

  [[ "$status" == 1 ]] ||
    fail "reconcile GitHub failure exit: expected 1, got $status"
  jq -e '
    .ok == false
    and .operation == "reconcile"
    and .error.code == "github_error"
  ' "$stdout_path" >/dev/null ||
    fail "reconcile GitHub failure did not return a typed error"
}

run_github_failure_probes

materialize_publish_case() {
  local case="$1"
  jq -c --argjson case "$case" '
    def pointer:
      ltrimstr("/")
      | split("/")
      | map(gsub("~1"; "/") | gsub("~0"; "~"))
      | map(if test("^(0|[1-9][0-9]*)$") then tonumber else . end);
    def apply_patch($operations):
      reduce $operations[] as $operation (.;
        if $operation.op == "remove" then
          delpaths([$operation.path | pointer])
        else
          setpath($operation.path | pointer; $operation.value)
        end
      );
    .completion_records as $records
    | if ($case | has("base_case")) then
        (
          $records.valid_publish_cases[]
          | select(.id == $case.base_case)
        ) as $base
        | $records.publish_request_templates[$base.template]
        | apply_patch($base.patch)
      else
        $records.publish_request_templates[$case.template]
      end
    | apply_patch($case.patch)
  ' "$fixture"
}

run_portable_json_profile_probes() {
  local case
  while IFS= read -r case; do
    local id request github_script github_state github_log
    id="$(jq -r '.id' <<<"$case")"
    request="$(materialize_publish_case "$case")"
    github_script="$tmp/$id-profile-github-script.json"
    github_state="$tmp/$id-profile-github-state"
    github_log="$tmp/$id-profile-github-calls"
    printf '[]\n' >"$github_script"
    : >"$github_log"

    local stdout_path="$tmp/$id-profile.stdout"
    local stderr_path="$tmp/$id-profile.stderr"
    local status
    set +e
    printf '%s' "$request" |
      PATH="$tmp/bin:$real_jq_dir:/usr/bin:/bin" \
      GIT_LOOPY_SCRIPTED_GITHUB_LOG="$github_log" \
      GIT_LOOPY_SCRIPTED_GITHUB_SCRIPT="$github_script" \
      GIT_LOOPY_SCRIPTED_GITHUB_STATE="$github_state" \
      "$bash_bin" "$entrypoint" continuation publish \
        >"$stdout_path" 2>"$stderr_path"
    status="${PIPESTATUS[1]}"
    set -e

    [[ "$status" == "$(jq -r '.expected.exit_code' <<<"$case")" ]] ||
      fail "$id portable JSON exit mismatch"
    [[ "$(<"$stdout_path")" == "$(jq -r '.expected.stdout_exact' <<<"$case")" ]] ||
      fail "$id portable JSON stdout mismatch"
    [[ "$(<"$stderr_path")" == "$(jq -r '.expected.stderr_exact' <<<"$case")" ]] ||
      fail "$id portable JSON stderr mismatch"
    [[ ! -s "$github_log" ]] ||
      fail "$id reached GitHub before portable JSON rejection"
  done < <(jq -c '.completion_records.canonical_json_rejections[]' "$fixture")
}

run_portable_json_profile_probes

run_portable_json_acceptance_probes() {
  local case
  while IFS= read -r case; do
    local id request expected_bytes actual_bytes
    id="$(jq -r '.id' <<<"$case")"
    request="$(materialize_publish_case "$case")"
    expected_bytes="$(jq -r '.canonical_completion_bytes // empty' <<<"$case")"
    if [[ -n "$expected_bytes" ]]; then
      actual_bytes="$(
        jq -cS '.completion' <<<"$request" |
          tr -d '\n' |
          LC_ALL=C wc -c |
          tr -d ' '
      )"
      [[ "$actual_bytes" == "$expected_bytes" ]] ||
        fail "$id canonical completion byte length mismatch"
    fi
    run_ephemeral_acceptance \
      "$id" \
      "$request" \
      '["action"]' \
      "$(jq -c '.expected.stdout_exact' <<<"$case")"
  done < <(
    jq -c '.completion_records.canonical_json_acceptances[]' "$fixture"
  )
}

run_producer_revision_bound_probe() {
  local request completion_length padding
  request="$(
    jq -c '
      .completion_records.publish_request_templates["shared-continue"]
      | .completion.advisory_extensions = reduce range(0; 5) as $index (
          {};
          .["note_\($index)"] = ("x" * 8000)
        )
      | .completion.advisory_extensions.note_5 = ""
    ' "$fixture"
  )"
  completion_length="$(
    jq -cS '.completion' <<<"$request" |
      LC_ALL=C wc -c |
      tr -d ' '
  )"
  padding=$((49000 - completion_length + 1))
  ((padding > 0 && padding <= 8192)) ||
    fail "producer revision bound fixture padding is invalid"
  request="$(
    jq -c --argjson padding "$padding" \
      '.completion.advisory_extensions.note_5 = ("x" * $padding)' \
      <<<"$request"
  )"

  local github_script="$tmp/revision-bound-github-script.json"
  local github_state="$tmp/revision-bound-github-state"
  local github_log="$tmp/revision-bound-github-calls"
  printf '[]\n' >"$github_script"
  : >"$github_log"
  local stdout_path="$tmp/revision-bound.stdout"
  local stderr_path="$tmp/revision-bound.stderr"
  local status
  set +e
  printf '%s' "$request" |
    PATH="$tmp/bin:$real_jq_dir:/usr/bin:/bin" \
    GIT_LOOPY_SCRIPTED_GITHUB_LOG="$github_log" \
    GIT_LOOPY_SCRIPTED_GITHUB_SCRIPT="$github_script" \
    GIT_LOOPY_SCRIPTED_GITHUB_STATE="$github_state" \
    "$bash_bin" "$entrypoint" continuation publish \
      >"$stdout_path" 2>"$stderr_path"
  status="${PIPESTATUS[1]}"
  set -e

  [[ "$status" == 1 ]] ||
    fail "producer revision bound exit: expected 1, got $status"
  jq -e '
    .error.code == "invalid_request"
    and .error.message
      == "Producer revision exceeds maximum record length 49152"
  ' "$stdout_path" >/dev/null ||
    fail "producer revision bound diagnostic mismatch"
  [[ ! -s "$github_log" ]] ||
    fail "oversized Producer revision reached GitHub"
}

run_producer_revision_bound_probe

run_semantic_before_size_probe() {
  local request
  request="$(
    jq -c '
      .completion_records.publish_request_templates["shared-continue"]
      | del(.completion.workstream)
      | .completion.advisory_extensions = reduce range(0; 7) as $index (
          {};
          .["note_\($index)"] = ("x" * 8192)
        )
    ' "$fixture"
  )"
  local github_script="$tmp/semantic-before-size-github-script.json"
  local github_state="$tmp/semantic-before-size-github-state"
  local github_log="$tmp/semantic-before-size-github-calls"
  printf '[]\n' >"$github_script"
  : >"$github_log"
  local stdout_path="$tmp/semantic-before-size.stdout"
  local stderr_path="$tmp/semantic-before-size.stderr"
  local status
  set +e
  printf '%s' "$request" |
    PATH="$tmp/bin:$real_jq_dir:/usr/bin:/bin" \
    GIT_LOOPY_SCRIPTED_GITHUB_LOG="$github_log" \
    GIT_LOOPY_SCRIPTED_GITHUB_SCRIPT="$github_script" \
    GIT_LOOPY_SCRIPTED_GITHUB_STATE="$github_state" \
    "$bash_bin" "$entrypoint" continuation publish \
      >"$stdout_path" 2>"$stderr_path"
  status="${PIPESTATUS[1]}"
  set -e

  [[ "$status" == 1 ]] ||
    fail "semantic-before-size exit: expected 1, got $status"
  jq -e '
    .error.message == "completion is missing required field: workstream"
  ' "$stdout_path" >/dev/null ||
    fail "completion size rejection preceded semantic validation"
  [[ ! -s "$github_log" ]] ||
    fail "malformed oversized completion reached GitHub"
}

run_semantic_before_size_probe

run_completion_semantic_rejection_probes() {
  local case
  while IFS= read -r case; do
    local id request github_script github_state github_log
    id="$(jq -r '.id' <<<"$case")"
    request="$(materialize_publish_case "$case")"
    github_script="$tmp/$id-rejection-github-script.json"
    github_state="$tmp/$id-rejection-github-state"
    github_log="$tmp/$id-rejection-github-calls"
    printf '[]\n' >"$github_script"
    : >"$github_log"

    local stdout_path="$tmp/$id-rejection.stdout"
    local stderr_path="$tmp/$id-rejection.stderr"
    local status
    set +e
    printf '%s' "$request" |
      PATH="$tmp/bin:$real_jq_dir:/usr/bin:/bin" \
      GIT_LOOPY_SCRIPTED_GITHUB_LOG="$github_log" \
      GIT_LOOPY_SCRIPTED_GITHUB_SCRIPT="$github_script" \
      GIT_LOOPY_SCRIPTED_GITHUB_STATE="$github_state" \
      "$bash_bin" "$entrypoint" continuation publish \
        >"$stdout_path" 2>"$stderr_path"
    status="${PIPESTATUS[1]}"
    set -e

    [[ "$status" == 1 ]] ||
      fail "$id completion rejection exit: expected 1, got $status"
    [[ "$(<"$stdout_path")" == \
      "$(jq -r '.expected.stdout_exact' <<<"$case")" ]] ||
      fail "$id completion rejection stdout mismatch"
    [[ "$(<"$stderr_path")" == \
      "$(jq -r '.expected.stderr_exact' <<<"$case")" ]] ||
      fail "$id completion rejection stderr mismatch"
    [[ ! -s "$github_log" ]] ||
      fail "$id mutated GitHub before completion rejection"
  done < <(
    jq -c '.completion_records.semantic_rejections[]' "$fixture"
  )
}

run_completion_semantic_rejection_probes

run_ephemeral_publication_probe() {
  local request
  request="$(
    jq -c '
      .completion_records.publish_request_templates["shared-continue"]
      | .completion.publication = "ephemeral"
      | del(.completion.carrier, .completion.workstream.anchor)
      | .completion.transition.evidence = []
      | .trusted_producers = []
    ' "$fixture"
  )"
  local github_script="$tmp/ephemeral-github-script.json"
  local github_state="$tmp/ephemeral-github-state"
  local github_log="$tmp/ephemeral-github-calls"
  printf '[]\n' >"$github_script"
  : >"$github_log"

  local stdout_path="$tmp/ephemeral.stdout"
  local stderr_path="$tmp/ephemeral.stderr"
  local status
  set +e
  printf '%s' "$request" |
    PATH="$tmp/bin:$real_jq_dir:/usr/bin:/bin" \
    GIT_LOOPY_SCRIPTED_GITHUB_LOG="$github_log" \
    GIT_LOOPY_SCRIPTED_GITHUB_SCRIPT="$github_script" \
    GIT_LOOPY_SCRIPTED_GITHUB_STATE="$github_state" \
    "$bash_bin" "$entrypoint" continuation publish \
      >"$stdout_path" 2>"$stderr_path"
  status="${PIPESTATUS[1]}"
  set -e

  [[ "$status" == 0 ]] ||
    fail "ephemeral publication exit: expected 0, got $status"
  jq -e '
    .ok == true
    and .operation == "publish"
    and .receipt.status == "unpublished"
    and .receipt.publication == "ephemeral"
    and .receipt.disposition == "continue"
    and (.receipt.semantic_fingerprints.action | test("^[0-9a-f]{64}$"))
  ' "$stdout_path" >/dev/null ||
    fail "ephemeral publication did not return its typed unpublished receipt"
  [[ ! -s "$stderr_path" ]] ||
    fail "ephemeral publication unexpectedly wrote stderr"
  [[ ! -s "$github_log" ]] ||
    fail "ephemeral publication reached GitHub"
}

run_ephemeral_publication_probe

run_ephemeral_acceptance() {
  local id="$1"
  local request="$2"
  local expected_keys="$3"
  local expected_stdout_json="${4:-null}"
  local github_script="$tmp/$id-acceptance-github-script.json"
  local github_state="$tmp/$id-acceptance-github-state"
  local github_log="$tmp/$id-acceptance-github-calls"
  printf '[]\n' >"$github_script"
  : >"$github_log"

  local stdout_path="$tmp/$id-acceptance.stdout"
  local stderr_path="$tmp/$id-acceptance.stderr"
  local status
  set +e
  printf '%s' "$request" |
    PATH="$tmp/bin:$real_jq_dir:/usr/bin:/bin" \
    GIT_LOOPY_SCRIPTED_GITHUB_LOG="$github_log" \
    GIT_LOOPY_SCRIPTED_GITHUB_SCRIPT="$github_script" \
    GIT_LOOPY_SCRIPTED_GITHUB_STATE="$github_state" \
    "$bash_bin" "$entrypoint" continuation publish \
      >"$stdout_path" 2>"$stderr_path"
  status="${PIPESTATUS[1]}"
  set -e

  [[ "$status" == 0 ]] ||
    fail "$id acceptance exit: expected 0, got $status"
  jq -e --argjson expected_keys "$expected_keys" '
    .receipt.status == "unpublished"
    and (.receipt.semantic_fingerprints | keys) == ($expected_keys | sort)
    and all(
      .receipt.semantic_fingerprints[];
      test("^[0-9a-f]{64}$")
    )
  ' "$stdout_path" >/dev/null ||
    fail "$id acceptance receipt mismatch"
  if [[ "$expected_stdout_json" != "null" ]]; then
    local expected_stdout_path="$tmp/$id-acceptance-expected.stdout"
    jq -jr '.' <<<"$expected_stdout_json" >"$expected_stdout_path"
    cmp -s "$stdout_path" "$expected_stdout_path" ||
      fail "$id exact stdout mismatch"
  fi
  [[ ! -s "$stderr_path" ]] ||
    fail "$id acceptance unexpectedly wrote stderr"
  [[ ! -s "$github_log" ]] ||
    fail "$id ephemeral acceptance reached GitHub"
}

run_portable_json_acceptance_probes

while IFS= read -r entry; do
  action_kind="$(jq -r '.key' <<<"$entry")"
  action_schema="$(jq -c '.value' <<<"$entry")"
  action_request="$(
    jq -c \
      --arg kind "$action_kind" \
      --argjson schema "$action_schema" '
      .completion_records as $records
      | $records.publish_request_templates["shared-continue"]
      | .completion.publication = "ephemeral"
      | del(.completion.carrier, .completion.workstream.anchor)
      | .completion.transition.evidence = []
      | .trusted_producers = []
      | .completion.actions[0].kind = $kind
      | .completion.actions[0].interaction =
          $records.interaction_examples[
            $schema.example_interaction
          ]
    ' "$fixture"
  )"
  run_ephemeral_acceptance \
    "action-kind-$(jq -rn --arg value "$action_kind" '$value | @uri')" \
    "$action_request" \
    '["action"]' \
    "$(jq -c '.expected_stdout_exact' <<<"$action_schema")"
done < <(jq -c '.completion_records.action_kind_schemas | to_entries[]' "$fixture")

while IFS= read -r entry; do
  condition_kind="$(jq -r '.key' <<<"$entry")"
  condition_schema="$(jq -c '.value' <<<"$entry")"
  condition_request="$(
    jq -c \
      --argjson schema "$condition_schema" '
      .completion_records.publish_request_templates["shared-continue"] as $template
      | $template
      | .completion.publication = "ephemeral"
      | del(.completion.carrier, .completion.workstream.anchor)
      | .completion.transition.evidence = []
      | .trusted_producers = []
      | .completion.actions[0] as $action
      | .completion.actions = (
          [
            $schema.supporting_action_keys[] as $key
            | $action + {key: $key}
          ]
          + [$action + {prerequisites: [$schema.example]}]
        )
    ' "$fixture"
  )"
  expected_condition_keys="$(
    jq -cn --argjson schema "$condition_schema" \
      '$schema.supporting_action_keys + ["action"] | sort'
  )"
  run_ephemeral_acceptance \
    "condition-kind-$condition_kind" \
    "$condition_request" \
    "$expected_condition_keys" \
    "$(jq -c '.expected_stdout_exact' <<<"$condition_schema")"
done < <(jq -c '.completion_records.condition_schemas | to_entries[]' "$fixture")

run_shared_disposition_probe() {
  local disposition="$1"
  local request="$2"
  local expected_stdout_json="${3:-null}"
  local canonical_completion revision_id expected_fingerprints record body
  canonical_completion="$(jq -cS '.completion' <<<"$request")"
  revision_id="$(
    printf '%s' "$canonical_completion" |
      if command -v sha256sum >/dev/null 2>&1; then
        sha256sum | awk '{print $1}'
      else
        shasum -a 256 | awk '{print $1}'
      fi
  )"
  expected_fingerprints="{}"
  if [[ "$expected_stdout_json" != "null" ]]; then
    expected_fingerprints="$(
      jq -c 'fromjson | .receipt.semantic_fingerprints' \
        <<<"$expected_stdout_json"
    )"
  fi
  record="$(
    jq -cS \
      --arg revision_id "$revision_id" \
      --argjson fingerprints "$expected_fingerprints" '
      .completion + {
        revision_id: $revision_id,
        semantic_fingerprints: $fingerprints
      }
    ' <<<"$request"
  )"
  body="<!-- git-loopy-continuation:1 -->"$'\n```json\n'"$record"$'\n```'

  local github_script="$tmp/$disposition-github-script.json"
  local github_state="$tmp/$disposition-github-state"
  local github_log="$tmp/$disposition-github-calls"
  jq -cn \
    --arg body "$body" '
    [
      {
        command: "api repos/octo/example/issues/comments/7001",
        exit_code: 0,
        stdout_json: {id: 7001, user: {login: "planner"}}
      },
      {
        command: (
          "label create git-loopy-continuation --repo octo/example "
          + "--color 5319E7 --description Repairable discovery index for "
          + "git-loopy Continuation records --force"
        ),
        exit_code: 0,
        stdout: ""
      },
      {
        command: (
          "issue edit 237 --repo octo/example "
          + "--add-label git-loopy-continuation"
        ),
        exit_code: 0,
        stdout: ""
      },
      {
        command: (
          "api --method POST repos/octo/example/issues/237/comments --input -"
        ),
        exit_code: 0,
        expected_stdin_json: {body: $body},
        stdout_json: {
          id: 9001,
          html_url: (
            "https://github.com/octo/example/issues/237#issuecomment-9001"
          ),
          user: {login: "planner"}
        }
      },
      {
        command: "api repos/octo/example/issues/comments/9001",
        exit_code: 0,
        stdout_json: {
          id: 9001,
          html_url: (
            "https://github.com/octo/example/issues/237#issuecomment-9001"
          ),
          body: $body,
          user: {login: "planner"}
        }
      }
    ]
  ' >"$github_script"
  : >"$github_log"

  local stdout_path="$tmp/$disposition.stdout"
  local stderr_path="$tmp/$disposition.stderr"
  local status
  set +e
  printf '%s' "$request" |
    PATH="$tmp/bin:$real_jq_dir:/usr/bin:/bin" \
    GIT_LOOPY_SCRIPTED_GITHUB_LOG="$github_log" \
    GIT_LOOPY_SCRIPTED_GITHUB_SCRIPT="$github_script" \
    GIT_LOOPY_SCRIPTED_GITHUB_STATE="$github_state" \
    "$bash_bin" "$entrypoint" continuation publish \
      >"$stdout_path" 2>"$stderr_path"
  status="${PIPESTATUS[1]}"
  set -e

  [[ "$status" == 0 ]] ||
    fail "$disposition shared publication exit: expected 0, got $status"
  jq -e \
    --arg revision_id "$revision_id" \
    --argjson fingerprints "$expected_fingerprints" '
    .receipt.status == "committed"
    and .receipt.revision_id == $revision_id
    and .receipt.semantic_fingerprints == $fingerprints
  ' "$stdout_path" >/dev/null ||
    fail "$disposition shared publication receipt mismatch"
  if [[ "$expected_stdout_json" != "null" ]]; then
    local expected_stdout_path="$tmp/$disposition-expected.stdout"
    jq -jr '.' <<<"$expected_stdout_json" >"$expected_stdout_path"
    cmp -s "$stdout_path" "$expected_stdout_path" ||
      fail "$disposition exact stdout mismatch"
  fi
  [[ ! -s "$stderr_path" ]] ||
    fail "$disposition shared publication unexpectedly wrote stderr"
  [[ "$(wc -l <"$github_log" | tr -d ' ')" == 5 ]] ||
    fail "$disposition shared publication GitHub boundary mismatch"
}

run_literal_publish_case() {
  local group="$1"
  local case="$2"
  local id request expected_stdout_json expected_keys
  id="$(jq -r '.id' <<<"$case")"
  request="$(materialize_publish_case "$case")"
  jq -e '.expected.stdout_exact | type == "string"' <<<"$case" >/dev/null ||
    fail "$group-$id is missing literal expected stdout"
  expected_stdout_json="$(jq -c '.expected.stdout_exact' <<<"$case")"

  case "$(jq -r '.completion.publication' <<<"$request")" in
    ephemeral)
      expected_keys="$(
        jq -c 'fromjson | .receipt.semantic_fingerprints | keys' \
          <<<"$expected_stdout_json"
      )"
      run_ephemeral_acceptance \
        "$group-$id" \
        "$request" \
        "$expected_keys" \
        "$expected_stdout_json"
      ;;
    shared)
      run_shared_disposition_probe \
        "$group-$id" \
        "$request" \
        "$expected_stdout_json"
      ;;
    *)
      fail "$group-$id has unsupported fixture publication"
      ;;
  esac
}

while IFS= read -r publish_case; do
  run_literal_publish_case "valid-publish" "$publish_case"
done < <(jq -c '.completion_records.valid_publish_cases[]' "$fixture")

while IFS= read -r fingerprint_case; do
  run_literal_publish_case "fingerprint" "$fingerprint_case"
done < <(jq -c '.completion_records.fingerprint_cases[]' "$fixture")

while IFS= read -r terminal_case; do
  outcome_kind="$(jq -r '.id' <<<"$terminal_case")"
  terminal_request="$(materialize_publish_case "$terminal_case")"
  run_shared_disposition_probe \
    "terminal-$outcome_kind" \
    "$terminal_request" \
    "$(jq -c '.expected.stdout_exact' <<<"$terminal_case")"
done < <(jq -c '.completion_records.terminal_outcome_cases[]' "$fixture")

no_guidance_request="$(
  jq -c '
    .completion_records.publish_request_templates["shared-continue"]
    | del(.completion.actions)
    | .completion.disposition = "no-guidance"
    | .completion.no_guidance = {
        reason: "no-successor-created",
        summary: "No trusted successor exists.",
        references: [
          {kind: "issue", repository: "octo/example", number: 237}
        ]
      }
  ' "$fixture"
)"
run_shared_disposition_probe "no-guidance" "$no_guidance_request"

ephemeral_no_guidance_request="$(
  jq -c '
    .completion_records.publish_request_templates["shared-continue"]
    | .completion.publication = "ephemeral"
    | del(
        .completion.actions,
        .completion.carrier,
        .completion.workstream.anchor
      )
    | .completion.transition.evidence = []
    | .trusted_producers = []
    | .completion.disposition = "no-guidance"
    | .completion.no_guidance = {
        reason: "ephemeral-only",
        summary: "Advice remains outside shared Reconciliation.",
        references: [
          {kind: "issue", repository: "octo/example", number: 237}
        ]
      }
  ' "$fixture"
)"
run_ephemeral_acceptance \
  "ephemeral-no-guidance" \
  "$ephemeral_no_guidance_request" \
  '[]'

while IFS= read -r scenario; do
  id="$(jq -r '.id' <<<"$scenario")"
  mapfile -d '' -t arguments < <(jq -j '.arguments[] + "\u0000"' <<<"$scenario")
  request_source="$(jq -r '.request.source // "none"' <<<"$scenario")"
  request_content=""
  if [[ "$request_source" != "none" ]]; then
    if jq -e '.request | has("base64")' <<<"$scenario" >/dev/null; then
      request_content=""
    elif jq -e '.request | has("raw_segments")' <<<"$scenario" >/dev/null; then
      request_content="$(
        while IFS= read -r segment; do
          text="$(jq -r '.text' <<<"$segment")"
          repeat="$(jq -r '.repeat // 1' <<<"$segment")"
          for ((repeat_index = 0; repeat_index < repeat; repeat_index++)); do
            printf '%s' "$text"
          done
        done < <(jq -c '.request.raw_segments[]' <<<"$scenario")
      )"
    elif jq -e '.request | has("raw")' <<<"$scenario" >/dev/null; then
      request_content="$(jq -r '.request.raw' <<<"$scenario")"
    else
      request_content="$(jq -c '.request.json' <<<"$scenario")"
    fi
  fi
  if [[ "$request_source" == "file" ]]; then
    input_file="$tmp/$id-request.json"
    if jq -e '.request | has("base64")' <<<"$scenario" >/dev/null; then
      encoded="$(jq -r '.request.base64' <<<"$scenario")"
      if ! printf '%s' "$encoded" | base64 --decode >"$input_file" 2>/dev/null; then
        printf '%s' "$encoded" | base64 -D >"$input_file"
      fi
    else
      printf '%s' "$request_content" >"$input_file"
    fi
    for index in "${!arguments[@]}"; do
      [[ "${arguments[$index]}" != '$INPUT_FILE' ]] ||
        arguments[$index]="$input_file"
    done
  fi

  stdout_path="$tmp/$id.stdout"
  stderr_path="$tmp/$id.stderr"
  github_log="$tmp/$id.github"
  github_script="$tmp/$id-github-script.json"
  github_state="$tmp/$id-github-state"
  jq -c '.github_script' <<<"$scenario" >"$github_script"
  : >"$github_log"
  rm -f "$github_state"
  set +e
  if [[ "$request_source" == "stdin" ]]; then
    printf '%s' "$request_content" |
      PATH="$tmp/bin:$real_jq_dir:/usr/bin:/bin" \
      GIT_LOOPY_SCRIPTED_GITHUB_LOG="$github_log" \
      GIT_LOOPY_SCRIPTED_GITHUB_SCRIPT="$github_script" \
      GIT_LOOPY_SCRIPTED_GITHUB_STATE="$github_state" \
      "$bash_bin" "$entrypoint" "${arguments[@]}" \
        >"$stdout_path" 2>"$stderr_path"
    status="${PIPESTATUS[1]}"
  else
    PATH="$tmp/bin:$real_jq_dir:/usr/bin:/bin" \
      GIT_LOOPY_SCRIPTED_GITHUB_LOG="$github_log" \
      GIT_LOOPY_SCRIPTED_GITHUB_SCRIPT="$github_script" \
      GIT_LOOPY_SCRIPTED_GITHUB_STATE="$github_state" \
      "$bash_bin" "$entrypoint" "${arguments[@]}" \
        >"$stdout_path" 2>"$stderr_path"
    status=$?
  fi
  set -e

  expected_status="$(jq -r '.expected.exit_code' <<<"$scenario")"
  [[ "$status" == "$expected_status" ]] ||
    fail "$id exit: expected $expected_status, got $status"

  if jq -e '
    .expected.stdout == null and (.expected | has("stdout_exact") | not)
  ' <<<"$scenario" >/dev/null; then
    [[ ! -s "$stdout_path" ]] || fail "$id unexpectedly wrote stdout"
  else
    actual_json="$(jq -c . "$stdout_path")" ||
      fail "$id stdout is not one JSON object"
    if jq -e '.expected | has("stdout_exact")' <<<"$scenario" >/dev/null; then
      expected_stdout="$(jq -r '.expected.stdout_exact' <<<"$scenario")"
      [[ "$(<"$stdout_path")" == "$expected_stdout" ]] ||
        fail "$id exact stdout mismatch"
    else
      expected_json="$(jq -c '.expected.stdout' <<<"$scenario")"
      jq -e --argjson expected "$expected_json" \
        '. == $expected' "$stdout_path" >/dev/null ||
        fail "$id stdout"$'\n'"expected: $expected_json"$'\n'"actual:   $actual_json"
    fi
    [[ "$(wc -l <"$stdout_path" | tr -d ' ')" == "1" ]] ||
      fail "$id stdout is not exactly one line"
  fi

  if jq -e '.expected | has("stderr_exact")' <<<"$scenario" >/dev/null; then
    expected_stderr="$(jq -r '.expected.stderr_exact' <<<"$scenario")"
    [[ "$(<"$stderr_path")" == "$expected_stderr" ]] ||
      fail "$id exact stderr mismatch"
  else
    stderr_needle="$(jq -r '.expected.stderr_contains // ""' <<<"$scenario")"
    if [[ -z "$stderr_needle" ]]; then
    [[ ! -s "$stderr_path" ]] || fail "$id unexpectedly wrote stderr"
    else
      grep -Fi -- "$stderr_needle" "$stderr_path" >/dev/null ||
        fail "$id stderr does not contain: $stderr_needle"
    fi
  fi
  actual_github_calls="$(
    jq -Rsc 'split("\n") | map(select(length > 0))' <"$github_log"
  )"
  expected_github_calls="$(jq -c '.expected.github_calls' <<<"$scenario")"
  [[ "$actual_github_calls" == "$expected_github_calls" ]] ||
    fail "$id scripted GitHub calls"$'\n'"expected: $expected_github_calls"$'\n'"actual:   $actual_github_calls"
  consumed=0
  [[ ! -f "$github_state" ]] || consumed="$(<"$github_state")"
  expected_steps="$(jq '.github_script | length' <<<"$scenario")"
  [[ "$consumed" == "$expected_steps" ]] ||
    fail "$id did not consume every scripted GitHub call"
done < <(
  jq -c '
    .scenarios[]
    | select((.distributions // ["shell"]) | index("shell"))
  ' "$fixture"
)

while IFS= read -r workflow; do
  id="$(jq -r '.id' <<<"$workflow")"
  github_log="$tmp/$id.github"
  github_script="$tmp/$id-github-script.json"
  github_state="$tmp/$id-github-state"
  jq -c '.github_script' <<<"$workflow" >"$github_script"
  : >"$github_log"
  rm -f "$github_state"

  while IFS= read -r command; do
    mapfile -d '' -t arguments < <(
      jq -j '.arguments[] + "\u0000"' <<<"$command"
    )
    request_content="$(jq -c '.request.json' <<<"$command")"
    stdout_path="$tmp/$id.stdout"
    stderr_path="$tmp/$id.stderr"
    set +e
    printf '%s' "$request_content" |
      PATH="$tmp/bin:$real_jq_dir:/usr/bin:/bin" \
      GIT_LOOPY_SCRIPTED_GITHUB_LOG="$github_log" \
      GIT_LOOPY_SCRIPTED_GITHUB_SCRIPT="$github_script" \
      GIT_LOOPY_SCRIPTED_GITHUB_STATE="$github_state" \
      "$bash_bin" "$entrypoint" "${arguments[@]}" \
        >"$stdout_path" 2>"$stderr_path"
    status="${PIPESTATUS[1]}"
    set -e

    expected_status="$(jq -r '.expected.exit_code' <<<"$command")"
    [[ "$status" == "$expected_status" ]] ||
      fail "$id exit: expected $expected_status, got $status"
    expected_json="$(jq -c '.expected.stdout' <<<"$command")"
    actual_json="$(jq -c . "$stdout_path")" ||
      fail "$id stdout is not one JSON object"
    if jq -e '.expected | has("stdout_exact")' <<<"$command" >/dev/null; then
      expected_stdout="$(jq -r '.expected.stdout_exact' <<<"$command")"
      [[ "$(<"$stdout_path")" == "$expected_stdout" ]] ||
        fail "$id exact stdout mismatch"
      [[ "$(wc -l <"$stdout_path" | tr -d ' ')" == "1" ]] ||
        fail "$id exact stdout is not one newline-terminated line"
    else
      jq -e --argjson expected "$expected_json" \
        '. == $expected' "$stdout_path" >/dev/null ||
        fail "$id stdout"$'\n'"expected: $expected_json"$'\n'"actual:   $actual_json"
    fi
    stderr_needle="$(jq -r '.expected.stderr_contains // ""' <<<"$command")"
    if [[ -z "$stderr_needle" ]]; then
      [[ ! -s "$stderr_path" ]] || fail "$id unexpectedly wrote stderr"
    else
      grep -Fi -- "$stderr_needle" "$stderr_path" >/dev/null ||
        fail "$id stderr does not contain: $stderr_needle"
    fi
  done < <(jq -c '.commands[]' <<<"$workflow")

  actual_github_calls="$(
    jq -Rsc 'split("\n") | map(select(length > 0))' <"$github_log"
  )"
  expected_github_calls="$(jq -c '.expected_github_calls' <<<"$workflow")"
  [[ "$actual_github_calls" == "$expected_github_calls" ]] ||
    fail "$id scripted GitHub calls"$'\n'"expected: $expected_github_calls"$'\n'"actual:   $actual_github_calls"
  consumed=0
  [[ ! -f "$github_state" ]] || consumed="$(<"$github_state")"
  expected_steps="$(jq '.github_script | length' <<<"$workflow")"
  [[ "$consumed" == "$expected_steps" ]] ||
    fail "$id did not consume every scripted GitHub call"
done < <(
  jq -c '
    .workflows[]
    | select((.distributions // []) | index("shell"))
  ' "$fixture"
)

printf 'shell Continuation conformance: ok\n'
