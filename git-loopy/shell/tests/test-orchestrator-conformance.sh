#!/usr/bin/env bash

# Config cases intentionally isolate environment mutations in subshells.
# shellcheck disable=SC2030,SC2031
set -euo pipefail

if ((BASH_VERSINFO[0] < 4)); then
  printf 'Bash 4+ is required (found %s).\n' "$BASH_VERSION" >&2
  exit 1
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
port_dir="$(cd "$script_dir/.." && pwd)"
conformance_dir="$port_dir/../conformance"

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

array_count() {
  printf '%s' "$#"
}

while IFS= read -r case_json; do
  case_id="$(jq -r '.id' <<<"$case_json")"
  body="$(jq -r '.body' <<<"$case_json")"
  expected="$(jq -r '.eligible' <<<"$case_json")"
  actual="false"
  if git_loopy_is_afk_ready "$body"; then
    actual="true"
  fi
  assert_equal "$expected" "$actual" "discriminator fixture: $case_id"
done < <(jq -c '.cases[]' "$conformance_dir/discriminator.json")

while IFS= read -r case_json; do
  case_id="$(jq -r '.id' <<<"$case_json")"
  reason="$(jq -r '.reason' <<<"$case_json")"
  expected="$(jq -r '.exit_code' <<<"$case_json")"
  actual="$(git_loopy_exit_code_for "$reason")"
  assert_equal "$expected" "$actual" "exit-code fixture: $case_id"
done < <(jq -c '.cases[]' "$conformance_dir/exit-codes.json")

assert_equal \
  "$(jq -r '.reference_regex' "$conformance_dir/close-references.json")" \
  "$GIT_LOOPY_CLOSE_KEYWORD_RE" \
  "close-keyword regex matches the shared reference"

while IFS= read -r case_json; do
  case_id="$(jq -r '.id' <<<"$case_json")"
  messages="$(jq -r '.commit_messages' <<<"$case_json")"
  pool="$(
    jq -c '(.issue_pool | map({ref: ., kind: "issue"}))
      + (.pr_pool | map({ref: ., kind: "pr"}))' <<<"$case_json"
  )"
  assert_equal \
    "$(jq -c '.extracted_refs' <<<"$case_json")" \
    "$(git_loopy_extract_close_refs "$messages")" \
    "close-references extract fixture: $case_id"
  assert_equal \
    "$(jq -c '.actionable_refs' <<<"$case_json")" \
    "$(git_loopy_actionable_close_refs "$messages" "$pool")" \
    "close-references actionable fixture: $case_id"
done < <(jq -c '.cases[]' "$conformance_dir/close-references.json")

# The auto-close backstop (§5) and the Checkpoint active-ref inference (§7) share
# one Pool-close-ref assembly (#114): descriptors from GIT_LOOPY_POOL_JSON crossed
# with the closing keywords in this Iteration's commit JSON. Isolate the global
# mutation in a subshell so no other case sees the fake Pool.
(
  git_loopy_pac_commits='[
    {"sha":"a1","subject":"feat: thing","body":"Closes #41"},
    {"sha":"b2","subject":"chore: noise","body":""}
  ]'
  GIT_LOOPY_POOL_JSON='[{"number":41},{"number":77}]'
  assert_equal \
    "[41]" \
    "$(git_loopy_pool_actionable_close_refs "$git_loopy_pac_commits")" \
    "pool-actionable-close-refs: in-Pool close-ref is actionable"
  GIT_LOOPY_POOL_JSON='[{"number":41}]'
  assert_equal \
    "[]" \
    "$(git_loopy_pool_actionable_close_refs \
      '[{"sha":"c3","subject":"fix: other","body":"Fixes #999"}]')" \
    "pool-actionable-close-refs: out-of-Pool ref excluded"
  GIT_LOOPY_POOL_JSON='[]'
  assert_equal \
    "[]" \
    "$(git_loopy_pool_actionable_close_refs "$git_loopy_pac_commits")" \
    "pool-actionable-close-refs: empty Pool yields nothing"
)

while IFS= read -r case_json; do
  case_id="$(jq -r '.id' <<<"$case_json")"
  max_strikes="$(jq -r '.max_strikes' <<<"$case_json")"
  strikes=0
  outcome="running"
  step_index=0
  while IFS= read -r step_json; do
    step_index=$((step_index + 1))
    read -r commits closures checkpoints pr_advances saw_nmt < <(
      jq -r '.signals
        | "\(.commits_in_iter) \(.auto_closures_in_iter) \(.checkpoints_in_iter) \(.pr_advances_in_iter) \(.saw_nmt_sentinel)"' \
        <<<"$step_json"
    )
    progress="false"
    if git_loopy_did_iteration_make_progress \
      "$commits" "$closures" "$checkpoints" "$pr_advances" "$saw_nmt"; then
      progress="true"
    fi
    tick="$(
      git_loopy_strike_tick "$max_strikes" "$strikes" "$outcome" \
        "$commits" "$closures" "$checkpoints" "$pr_advances" "$saw_nmt"
    )"
    strikes="${tick%% *}"
    outcome="${tick##* }"
    assert_equal \
      "$(jq -r '.expected.progress' <<<"$step_json")" \
      "$progress" \
      "progress-strikes fixture: $case_id step $step_index (progress)"
    assert_equal \
      "$(jq -r '.expected.strikes' <<<"$step_json")" \
      "$strikes" \
      "progress-strikes fixture: $case_id step $step_index (strikes)"
    assert_equal \
      "$(jq -r '.expected.outcome' <<<"$step_json")" \
      "$outcome" \
      "progress-strikes fixture: $case_id step $step_index (outcome)"
  done < <(jq -c '.steps[]' <<<"$case_json")
done < <(jq -c '.cases[]' "$conformance_dir/progress-strikes.json")

while IFS= read -r case_json; do
  case_id="$(jq -r '.id' <<<"$case_json")"
  active_ref="$(jq -r 'if .active_ref == null then "" else (.active_ref | tostring) end' <<<"$case_json")"
  message="$(git_loopy_checkpoint_message "$active_ref")"
  assert_equal \
    "$(jq -r '.expected_message' <<<"$case_json")" \
    "$message" \
    "checkpoint-messages author fixture: $case_id"
  assert_equal \
    "[]" \
    "$(git_loopy_extract_close_refs "$message")" \
    "checkpoint-messages author fixture: $case_id (no close refs)"
  is_checkpoint="false"
  if git_loopy_is_checkpoint_message "$message"; then
    is_checkpoint="true"
  fi
  assert_equal "true" "$is_checkpoint" \
    "checkpoint-messages author fixture: $case_id (is checkpoint)"
  [[ "$message" != *"#"* ]] ||
    fail "checkpoint-messages author fixture: $case_id contains '#'"
done < <(jq -c '.author_cases[]' "$conformance_dir/checkpoint-messages.json")

while IFS= read -r case_json; do
  case_id="$(jq -r '.id' <<<"$case_json")"
  message="$(jq -r '.message' <<<"$case_json")"
  expected="$(jq -r '.is_checkpoint' <<<"$case_json")"
  actual="false"
  if git_loopy_is_checkpoint_message "$message"; then
    actual="true"
  fi
  assert_equal "$expected" "$actual" \
    "checkpoint-messages detection fixture: $case_id"
done < <(jq -c '.detection_cases[]' "$conformance_dir/checkpoint-messages.json")

(
  unset GIT_LOOPY_MODEL
  unset GIT_LOOPY_REASONING_EFFORT
  unset GIT_LOOPY_ISSUE_SOURCE
  unset GIT_LOOPY_MAX_NMT_STRIKES
  unset GIT_LOOPY_DENY_TOOLS
  unset GIT_LOOPY_DENY_SKILLS
  unset GIT_LOOPY_SEND_TIMEOUT_SECONDS

  git_loopy_resolve_config
  assert_equal "0" "$GIT_LOOPY_MAX_ITERATIONS" "default iteration cap"
  assert_equal "claude-opus-4.8" "$GIT_LOOPY_MODEL" "default model"
  assert_equal "max" "$GIT_LOOPY_REASONING_EFFORT" "default reasoning effort"
  assert_equal "github" "$GIT_LOOPY_ISSUE_SOURCE" "default issue source"
  assert_equal "3" "$GIT_LOOPY_MAX_NMT_STRIKES" "default Strike threshold"
  assert_equal "7200" "$GIT_LOOPY_SEND_TIMEOUT_SECONDS" "default send timeout"
  assert_equal \
    "0" \
    "$(array_count \
      ${GIT_LOOPY_DENY_TOOLS_RESOLVED[@]+"${GIT_LOOPY_DENY_TOOLS_RESOLVED[@]}"})" \
    "default tool denylist"
  assert_equal \
    "0" \
    "$(array_count \
      ${GIT_LOOPY_DENY_SKILLS_RESOLVED[@]+"${GIT_LOOPY_DENY_SKILLS_RESOLVED[@]}"})" \
    "default skill denylist"
)

(
  export GIT_LOOPY_MODEL="env-model"
  export GIT_LOOPY_REASONING_EFFORT="low"
  export GIT_LOOPY_ISSUE_SOURCE="github"
  export GIT_LOOPY_MAX_NMT_STRIKES="7"
  export GIT_LOOPY_DENY_TOOLS="env-tool,shared-tool"
  export GIT_LOOPY_DENY_SKILLS="env-skill"
  export GIT_LOOPY_SEND_TIMEOUT_SECONDS="90"

  git_loopy_resolve_config \
    2 \
    --model cli-model \
    --reasoning-effort xhigh \
    --issue-source prds \
    --max-nmt-strikes 5 \
    --deny-tool cli-tool \
    --deny-tool shared-tool \
    --deny-skill cli-skill \
    --send-timeout-seconds 45

  assert_equal "2" "$GIT_LOOPY_MAX_ITERATIONS" "CLI iteration cap"
  assert_equal "cli-model" "$GIT_LOOPY_MODEL" "CLI model precedence"
  assert_equal "xhigh" "$GIT_LOOPY_REASONING_EFFORT" "CLI effort precedence"
  assert_equal "prds" "$GIT_LOOPY_ISSUE_SOURCE" "CLI source precedence"
  assert_equal "5" "$GIT_LOOPY_MAX_NMT_STRIKES" "CLI Strike precedence"
  assert_equal "45" "$GIT_LOOPY_SEND_TIMEOUT_SECONDS" "CLI timeout precedence"
  assert_equal \
    "cli-tool,shared-tool,env-tool" \
    "$(IFS=,; printf '%s' "${GIT_LOOPY_DENY_TOOLS_RESOLVED[*]}")" \
    "tool denylists are unioned and stable"
  assert_equal \
    "cli-skill,env-skill" \
    "$(IFS=,; printf '%s' "${GIT_LOOPY_DENY_SKILLS_RESOLVED[*]}")" \
    "skill denylists are unioned and stable"
)

(
  export GIT_LOOPY_MODEL="claude-opus-4.7-xhigh"
  unset GIT_LOOPY_REASONING_EFFORT

  git_loopy_resolve_config
  assert_equal "claude-opus-4.7" "$GIT_LOOPY_MODEL" "suffixed model base id"
  assert_equal "xhigh" "$GIT_LOOPY_REASONING_EFFORT" "model suffix effort"
)

(
  export GIT_LOOPY_MODEL="claude-opus-4.7-xhigh"
  export GIT_LOOPY_REASONING_EFFORT="medium"

  git_loopy_resolve_config
  assert_equal "claude-opus-4.7" "$GIT_LOOPY_MODEL" "overridden suffix base id"
  assert_equal \
    "medium" \
    "$GIT_LOOPY_REASONING_EFFORT" \
    "explicit effort overrides model suffix"
)

(
  export GIT_LOOPY_MODEL="claude-sonnet-4.6"
  unset GIT_LOOPY_REASONING_EFFORT

  git_loopy_resolve_config
  assert_equal \
    "" \
    "$GIT_LOOPY_REASONING_EFFORT" \
    "non-default model leaves effort omitted"
)

for invalid_args in \
  "not-a-number" \
  "-1" \
  "--issue-source nowhere" \
  "--max-nmt-strikes 0" \
  "--reasoning-effort impossible" \
  "--reasoning-effort=" \
  "--send-timeout-seconds 0" \
  "--model --help" \
  "--unknown"; do
  read -r -a args <<<"$invalid_args"
  if (git_loopy_resolve_config "${args[@]}" 2>/dev/null); then
    fail "malformed invocation was accepted: $invalid_args"
  fi
done

temp_dir="$(mktemp -d)"
trap 'rm -rf "$temp_dir"' EXIT
repo="$temp_dir/repo"
global_home="$temp_dir/global"
packaged_prompt="$temp_dir/packaged/PROMPT.md"
mkdir -p "$repo/git-loopy" "$global_home/git-loopy" "$(dirname "$packaged_prompt")"
printf 'packaged\n' >"$packaged_prompt"
printf 'global\n' >"$global_home/git-loopy/PROMPT.md"

XDG_CONFIG_HOME="$global_home"
export XDG_CONFIG_HOME
assert_equal \
  "$global_home/git-loopy/PROMPT.md" \
  "$(git_loopy_resolve_prompt "$repo" "$packaged_prompt")" \
  "global prompt overrides packaged prompt"

printf 'project\n' >"$repo/git-loopy/PROMPT.md"
resolved_project_prompt="$(
  git_loopy_resolve_prompt "$repo" "$packaged_prompt"
)"
assert_equal \
  "project" \
  "$(<"$resolved_project_prompt")" \
  "project prompt overrides global prompt"
[[ "$resolved_project_prompt" == "$repo/git-loopy/"* ]] ||
  fail "project prompt did not resolve from project scope"

rm "$repo/git-loopy/PROMPT.md" "$global_home/git-loopy/PROMPT.md"
assert_equal \
  "$packaged_prompt" \
  "$(git_loopy_resolve_prompt "$repo" "$packaged_prompt")" \
  "packaged prompt is the final fallback"
rm "$packaged_prompt"
if git_loopy_resolve_prompt "$repo" "$packaged_prompt" >/dev/null; then
  fail "prompt resolution succeeded with every scope absent"
fi

# --- Send-timeout watchdog (Wrapper contract §4 real-exit-status + §6) ----------
# `git_loopy_run_bounded_turn` bounds one agent turn by the resolved send timeout
# with a built-in watchdog (no `timeout(1)` dependency). A turn that overruns the
# bound is terminated at ~the bound and reported as a failed turn (exit 124) —
# landing no agent commit, so §6 counts the Iteration no-progress; a turn that
# finishes within the bound returns its own real exit status, and its stdout is
# always folded to stderr so the JSONL Event stream stays clean.

bounded_start=$SECONDS
set +e
git_loopy_run_bounded_turn 1 sleep 30 2>"$temp_dir/bounded-overrun.stderr"
bounded_status=$?
set -e
bounded_elapsed=$((SECONDS - bounded_start))
assert_equal "124" "$bounded_status" \
  "an overrunning turn is reported with the timeout exit code"
((bounded_elapsed < 15)) ||
  fail "an overrunning turn was not bounded (took ${bounded_elapsed}s, bound 1s)"
[[ "$(<"$temp_dir/bounded-overrun.stderr")" == *"exceeded the 1s send timeout"* ]] ||
  fail "an overrunning turn did not warn that the bound fired"

# A turn that ignores SIGTERM is still force-terminated (SIGTERM -> SIGKILL
# escalation), so a wedged agent can never hang the Iteration.
bounded_start=$SECONDS
set +e
git_loopy_run_bounded_turn 1 bash -c 'trap "" TERM; sleep 10' 2>/dev/null
bounded_stubborn_status=$?
set -e
bounded_stubborn_elapsed=$((SECONDS - bounded_start))
assert_equal "124" "$bounded_stubborn_status" \
  "a SIGTERM-ignoring turn is still reported as a timed-out turn"
((bounded_stubborn_elapsed < 20)) ||
  fail "a SIGTERM-ignoring turn was not force-terminated (took ${bounded_stubborn_elapsed}s)"

set +e
git_loopy_run_bounded_turn 30 bash -c 'exit 7' 2>/dev/null
bounded_within_status=$?
set -e
assert_equal "7" "$bounded_within_status" \
  "a within-bound turn preserves its real exit status (contract §4)"

bounded_stdout="$(git_loopy_run_bounded_turn 30 bash -c 'printf agent-marker' 2>/dev/null)"
assert_equal "" "$bounded_stdout" \
  "the turn's own stdout is folded to stderr, never onto the Event stream"

printf 'shell Orchestrator conformance: ok\n'
