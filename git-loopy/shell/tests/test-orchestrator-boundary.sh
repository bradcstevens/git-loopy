#!/usr/bin/env bash

set -euo pipefail

if ((BASH_VERSINFO[0] < 4)); then
  printf 'Bash 4+ is required (found %s).\n' "$BASH_VERSION" >&2
  exit 1
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
port_dir="$(cd "$script_dir/.." && pwd)"
entrypoint="$port_dir/git-loopy.sh"
release_fixture="$port_dir/../conformance/release-version.json"
real_jq="$(command -v jq)"
real_jq_dir="$(dirname "$real_jq")"
real_git="$(command -v git)"
real_git_dir="$(dirname "$real_git")"
bash_bin="$(command -v bash)"

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

assert_contains() {
  local haystack="$1"
  local needle="$2"
  local description="$3"
  [[ "$haystack" == *"$needle"* ]] ||
    fail "$description"$'\n'"missing: $needle"$'\n'"actual:  $haystack"
}

write_fake_tools() {
  local bin_dir="$1"
  mkdir -p "$bin_dir"

  cat >"$bin_dir/git" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
if [[ "$*" == "rev-parse --show-toplevel" ]]; then
  printf '%s\n' "$FAKE_REPO_ROOT"
  exit 0
fi
printf 'unexpected git invocation: %s\n' "$*" >&2
exit 90
EOF

  cat >"$bin_dir/copilot" <<'EOF'
#!/usr/bin/env bash
printf 'copilot must not run in the discovery slice\n' >&2
exit 91
EOF

  cat >"$bin_dir/gh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >>"$FAKE_GH_LOG"
case "${1-} ${2-}" in
  "auth status")
    exit "${FAKE_GH_AUTH_STATUS:-0}"
    ;;
  "repo view")
    printf '{"owner":{"login":"example"},"name":"repo","defaultBranchRef":{"name":"main"}}\n'
    ;;
  "issue list")
    count=0
    if [[ -f "$FAKE_GH_LIST_COUNT" ]]; then
      count="$(<"$FAKE_GH_LIST_COUNT")"
    fi
    count=$((count + 1))
    printf '%s\n' "$count" >"$FAKE_GH_LIST_COUNT"
    cat "$FAKE_GH_LIST_JSON"
    ;;
  "issue view")
    cat "$FAKE_GH_VIEW_DIR/${3}.json"
    ;;
  *)
    printf 'unexpected gh invocation: %s\n' "$*" >&2
    exit 92
    ;;
esac
EOF

  chmod +x "$bin_dir/git" "$bin_dir/copilot" "$bin_dir/gh"
}

make_repo() {
  local root="$1"
  mkdir -p "$root/docs/agents" "$root/git-loopy"
  printf '# Issue tracker\n' >"$root/docs/agents/issue-tracker.md"
  printf '# Project prompt\n' >"$root/git-loopy/PROMPT.md"
}

run_entrypoint() {
  local repo="$1"
  local fake_bin="$2"
  local stdout_path="$3"
  local stderr_path="$4"
  shift 4

  (
    cd "$repo"
    PATH="$fake_bin:$real_jq_dir:/usr/bin:/bin" \
      HOME="$repo/home" \
      XDG_CONFIG_HOME="$repo/xdg" \
      FAKE_REPO_ROOT="$repo" \
      "$bash_bin" "$entrypoint" "$@"
  ) >"$stdout_path" 2>"$stderr_path"
}

run_version_entrypoint() {
  local stdout_path="$1"
  local stderr_path="$2"
  shift 2

  (
    cd "$version_outside"
    PATH="$version_bin:/usr/bin:/bin" \
      HOME="$temp_dir/missing-home" \
      XDG_CONFIG_HOME="$temp_dir/version-config" \
      GIT_LOOPY_ISSUE_SOURCE="unavailable" \
      GIT_LOOPY_MAX_NMT_STRIKES="not-an-integer" \
      VERSION_TOOL_LOG="$version_tool_log" \
      "$bash_bin" "$version_runtime/git-loopy/shell/git-loopy.sh" "$@"
  ) >"$stdout_path" 2>"$stderr_path"
}

# Turn scenarios drive the real Copilot turn, so they run against a real git
# repository (real head_sha / commits_between / recent_commits) with only `gh`
# and `copilot` faked. `write_turn_tools` deliberately ships no fake `git`.
write_turn_tools() {
  local bin_dir="$1"
  mkdir -p "$bin_dir"

  cat >"$bin_dir/copilot" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
: >"$FAKE_COPILOT_FLAGS"
prompt=""
capture=0
for arg in "$@"; do
  if ((capture)); then
    prompt="$arg"
    capture=0
    continue
  fi
  if [[ "$arg" == "-p" ]]; then
    capture=1
    continue
  fi
  printf '%s\n' "$arg" >>"$FAKE_COPILOT_FLAGS"
done
printf '%s' "$prompt" >"$FAKE_COPILOT_PROMPT"
# An opt-in record of the signal dispositions this process inherited. The live
# interface has to ignore SIGPIPE across its own writes; if it ever left that
# ignored for the whole Run, `exec` would carry SIG_IGN into the agent process
# and every tool it starts.
if [[ -n "${FAKE_COPILOT_SIGNALS:-}" ]]; then
  trap -p PIPE >"$FAKE_COPILOT_SIGNALS"
fi
calls=0
[[ -f "$FAKE_COPILOT_CALLS" ]] && calls="$(<"$FAKE_COPILOT_CALLS")"
printf '%s' "$((calls + 1))" >"$FAKE_COPILOT_CALLS"
# Emit on stdout to prove the agent stream is routed away from the JSONL
# Event stream (the Orchestrator sends it to stderr).
if [[ -n "${FAKE_COPILOT_OUTPUT_FILE:-}" ]]; then
  cat "$FAKE_COPILOT_OUTPUT_FILE"
else
  printf 'copilot agent stream marker\n'
fi
# A per-call commit plan (opt-in) lets a scenario vary commit messages across
# Iterations — each `<call>/<n>.msg` file is one commit's full message, read via
# `-F` so multi-line close-keyword bodies survive. Falling back to the simple
# empty-commit count keeps every existing turn scenario unchanged.
current_call="$(<"$FAKE_COPILOT_CALLS")"
if [[ -n "${FAKE_COPILOT_PLAN_DIR:-}" ]]; then
  call_dir="$FAKE_COPILOT_PLAN_DIR/$current_call"
  if [[ -d "$call_dir" ]]; then
    for msg_file in "$call_dir"/*.msg; do
      [[ -e "$msg_file" ]] || continue
      git commit -q --allow-empty -F "$msg_file"
    done
    # An optional per-call `worktree.sh` hook runs in the repo root so a scenario
    # can leave the tree dirty/untracked/ignored exactly like a real agent that
    # forgot to commit — the Checkpoint durability net (ADR-0004) is what
    # captures it.
    if [[ -f "$call_dir/worktree.sh" ]]; then
      (cd "$FAKE_REPO_ROOT" && bash "$call_dir/worktree.sh")
    fi
  fi
else
  commits="${FAKE_COPILOT_COMMITS:-0}"
  i=0
  while ((i < commits)); do
    git commit -q --allow-empty -m "agent: work $((i + 1))"
    i=$((i + 1))
  done
fi
exit "${FAKE_COPILOT_EXIT:-0}"
EOF

  cat >"$bin_dir/gh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >>"$FAKE_GH_LOG"
case "${1-} ${2-}" in
  "auth status")
    exit "${FAKE_GH_AUTH_STATUS:-0}"
    ;;
  "repo view")
    printf '{"owner":{"login":"example"},"name":"repo","defaultBranchRef":{"name":"main"}}\n'
    ;;
  "issue list")
    count=0
    if [[ -f "$FAKE_GH_LIST_COUNT" ]]; then
      count="$(<"$FAKE_GH_LIST_COUNT")"
    fi
    count=$((count + 1))
    printf '%s\n' "$count" >"$FAKE_GH_LIST_COUNT"
    if [[ -n "${FAKE_GH_EMPTY_AFTER:-}" ]] && ((count > FAKE_GH_EMPTY_AFTER)); then
      printf '[]\n'
    else
      cat "$FAKE_GH_LIST_JSON"
    fi
    ;;
  "issue view")
    cat "$FAKE_GH_VIEW_DIR/${3}.json"
    ;;
  "issue close")
    if [[ "${FAKE_GH_CLOSE_STATUS:-0}" != "0" ]]; then
      exit "$FAKE_GH_CLOSE_STATUS"
    fi
    # Record the auto-closure: the issue number (one per line) and the wrap-up
    # comment, so a scenario can assert which Pool issues the loop closed and
    # which commit SHAs the comment attributed.
    printf '%s\n' "$3" >>"$FAKE_GH_CLOSED"
    if [[ -n "${FAKE_GH_CLOSE_DIR:-}" ]]; then
      mkdir -p "$FAKE_GH_CLOSE_DIR"
      printf '%s' "${5-}" >"$FAKE_GH_CLOSE_DIR/${3}.comment"
    fi
    ;;
  *)
    printf 'unexpected gh invocation: %s\n' "$*" >&2
    exit 92
    ;;
esac
EOF

  chmod +x "$bin_dir/copilot" "$bin_dir/gh"
}

make_real_repo() {
  local root="$1"
  make_repo "$root"
  git -C "$root" init -q
  git -C "$root" config user.email tester@example.invalid
  git -C "$root" config user.name "Test Runner"
  # A realistic project ignores the runner's own `.git-loopy/` artefacts, so the
  # replay log never trips the Checkpoint dirty-check.
  printf '.git-loopy/\n' >"$root/.gitignore"
  git -C "$root" add -A
  git -C "$root" commit -q -m "initial commit"
}

# Give a real repo a bare upstream so the ADR-0004 auto-push has somewhere to go.
# `push -u origin HEAD` seeds the remote and sets the branch's upstream tracking
# ref, so a later bare `git push` from the Orchestrator fast-forwards it.
add_fake_remote() {
  local root="$1"
  local remote="$2"
  git init --bare -q "$remote"
  git -C "$root" remote add origin "$remote"
  git -C "$root" push -q -u origin HEAD
}

run_turn_entrypoint() {
  local repo="$1"
  local fake_bin="$2"
  local stdout_path="$3"
  local stderr_path="$4"
  shift 4

  (
    cd "$repo"
    PATH="$fake_bin:$real_jq_dir:$real_git_dir:/usr/bin:/bin" \
      HOME="$repo/home" \
      XDG_CONFIG_HOME="$repo/xdg" \
      FAKE_REPO_ROOT="$repo" \
      "$bash_bin" "$entrypoint" "$@"
  ) >"$stdout_path" 2>"$stderr_path"
}

setup_copilot_env() {
  local prefix="$1"
  rm -f \
    "$temp_dir/$prefix-copilot.flags" \
    "$temp_dir/$prefix-copilot.prompt" \
    "$temp_dir/$prefix-copilot.calls"
  export FAKE_COPILOT_FLAGS="$temp_dir/$prefix-copilot.flags"
  export FAKE_COPILOT_PROMPT="$temp_dir/$prefix-copilot.prompt"
  export FAKE_COPILOT_CALLS="$temp_dir/$prefix-copilot.calls"
}

if [[ -x /bin/bash ]]; then
  system_bash_major="$(/bin/bash -c 'printf "%s" "${BASH_VERSINFO[0]}"')"
  if ((system_bash_major < 4)); then
    set +e
    version_output="$(/bin/bash "$entrypoint" 2>&1)"
    version_status=$?
    set -e
    assert_equal "1" "$version_status" "Bash version-gate exit"
    assert_contains \
      "$version_output" \
      "brew install bash" \
      "stock-macOS Bash upgrade guidance"
  fi
fi

temp_dir="$(mktemp -d)"
trap 'rm -rf "$temp_dir"' EXIT

version_runtime="$temp_dir/version-runtime"
mkdir -p "$version_runtime/git-loopy"
cp -R "$port_dir" "$version_runtime/git-loopy/shell"
expected_release_version="$(jq -r '.expected_release_version' "$release_fixture")"
printf '%s\n' "$expected_release_version" >"$version_runtime/VERSION"

version_outside="$temp_dir/version-outside"
version_config="$temp_dir/version-config/git-loopy"
version_bin="$temp_dir/version-bin"
version_tool_log="$temp_dir/version-tools.log"
mkdir -p "$version_outside" "$version_config" "$version_bin"
printf 'invalid = [\n' >"$version_config/config.toml"
for tool in git gh copilot jq; do
  cat >"$version_bin/$tool" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$(basename "$0") $*" >>"$VERSION_TOOL_LOG"
exit 97
EOF
  chmod +x "$version_bin/$tool"
done

set +e
run_version_entrypoint "$temp_dir/version.stdout" "$temp_dir/version.stderr" --version
status=$?
set -e
assert_equal "0" "$status" "Release version exit"
assert_equal \
  "git-loopy $expected_release_version" \
  "$(<"$temp_dir/version.stdout")" \
  "Release version stdout"
[[ ! -s "$temp_dir/version.stderr" ]] || fail "Release version wrote to stderr"
[[ ! -e "$version_tool_log" ]] || fail "Release version invoked a Run dependency"
[[ -z "$(find "$version_outside" -mindepth 1 -print -quit)" ]] ||
  fail "Release version created Run artifacts"

while IFS= read -r case_json; do
  case_id="$(jq -r '.id' <<<"$case_json")"
  jq -jr '.value + "\n"' <<<"$case_json" >"$version_runtime/VERSION"
  if ! run_version_entrypoint \
    "$temp_dir/version-$case_id.stdout" \
    "$temp_dir/version-$case_id.stderr" \
    --version; then
    fail "valid Release version was rejected: $case_id"
  fi
  assert_equal \
    "git-loopy $(jq -r '.value' <<<"$case_json")" \
    "$(<"$temp_dir/version-$case_id.stdout")" \
    "valid Release version stdout: $case_id"
  [[ ! -s "$temp_dir/version-$case_id.stderr" ]] ||
    fail "valid Release version wrote stderr: $case_id"
done < <(jq -c '.valid_versions[]' "$release_fixture")

while IFS= read -r case_json; do
  case_id="$(jq -r '.id' <<<"$case_json")"
  jq -j '.value' <<<"$case_json" >"$version_runtime/VERSION"
  set +e
  run_version_entrypoint \
    "$temp_dir/version-$case_id.stdout" \
    "$temp_dir/version-$case_id.stderr" \
    --version
  status=$?
  set -e
  [[ "$status" -ne 0 ]] || fail "malformed Release version was accepted: $case_id"
  [[ ! -s "$temp_dir/version-$case_id.stdout" ]] ||
    fail "malformed Release version wrote stdout: $case_id"
  [[ -s "$temp_dir/version-$case_id.stderr" ]] ||
    fail "malformed Release version failed silently: $case_id"
  [[ "$(<"$temp_dir/version-$case_id.stderr")" != *"unknown"* ]] ||
    fail "malformed Release version reported unknown: $case_id"
done < <(jq -c '.invalid_versions[]' "$release_fixture")

while IFS= read -r case_json; do
  case_id="$(jq -r '.id' <<<"$case_json")"
  kind="$(jq -r '.kind' <<<"$case_json")"
  rm -rf "$version_runtime/VERSION"
  case "$kind" in
    missing) ;;
    directory) mkdir "$version_runtime/VERSION" ;;
    invalid_utf8) printf '\377\n' >"$version_runtime/VERSION" ;;
    *) fail "unsupported invalid Release authority fixture kind: $kind" ;;
  esac
  set +e
  run_version_entrypoint \
    "$temp_dir/version-$case_id.stdout" \
    "$temp_dir/version-$case_id.stderr" \
    --version
  status=$?
  set -e
  [[ "$status" -ne 0 ]] || fail "invalid Release metadata was accepted: $case_id"
  [[ ! -s "$temp_dir/version-$case_id.stdout" ]] ||
    fail "invalid Release metadata wrote stdout: $case_id"
  [[ -s "$temp_dir/version-$case_id.stderr" ]] ||
    fail "invalid Release metadata failed silently: $case_id"
  [[ "$(<"$temp_dir/version-$case_id.stderr")" != *"unknown"* ]] ||
    fail "invalid Release metadata reported unknown: $case_id"
done < <(jq -c '.invalid_authority_inputs[]' "$release_fixture")

rm -rf "$version_runtime/VERSION"
set +e
run_version_entrypoint \
  "$temp_dir/version-capabilities-missing.stdout" \
  "$temp_dir/version-capabilities-missing.stderr" \
  continuation capabilities
status=$?
set -e
[[ "$status" -ne 0 ]] ||
  fail "Continuation capabilities accepted missing Release metadata"
[[ ! -s "$temp_dir/version-capabilities-missing.stdout" ]] ||
  fail "Continuation capabilities wrote success output without Release metadata"
assert_contains \
  "$(<"$temp_dir/version-capabilities-missing.stderr")" \
  "cannot read Release metadata" \
  "Continuation capabilities missing Release metadata diagnostic"
[[ "$(<"$temp_dir/version-capabilities-missing.stderr")" != *"unknown"* ]] ||
  fail "Continuation capabilities reported an unknown Release version"

printf '%s\n' "$expected_release_version" >"$version_runtime/VERSION"

set +e
"$bash_bin" "$entrypoint" --help \
  >"$temp_dir/help.stdout" 2>"$temp_dir/help.stderr"
status=$?
set -e
assert_equal "0" "$status" "help exit"
assert_contains "$(<"$temp_dir/help.stdout")" "Usage:" "help stdout"
[[ ! -s "$temp_dir/help.stderr" ]] || fail "help wrote to stderr"

repo="$temp_dir/empty"
fake_bin="$temp_dir/empty-bin"
make_repo "$repo"
write_fake_tools "$fake_bin"
printf '[]\n' >"$temp_dir/empty-list.json"
mkdir -p "$temp_dir/empty-views"
export FAKE_GH_LOG="$temp_dir/empty-gh.log"
export FAKE_GH_LIST_COUNT="$temp_dir/empty-list.count"
export FAKE_GH_LIST_JSON="$temp_dir/empty-list.json"
export FAKE_GH_VIEW_DIR="$temp_dir/empty-views"

if ! run_entrypoint \
  "$repo" "$fake_bin" "$temp_dir/empty.stdout" "$temp_dir/empty.stderr"; then
  fail "empty GitHub Pool did not exit 0: $(<"$temp_dir/empty.stderr")"
fi

expected_types="$(
  jq -cn '[
    "wrapper.run.start",
    "wrapper.iteration.start",
    "wrapper.afk_ready.collected",
    "wrapper.iteration.end",
    "wrapper.run.end"
  ]'
)"
actual_types="$(jq -sc '[.[].type]' "$temp_dir/empty.stdout")"
assert_equal "$expected_types" "$actual_types" "empty-Pool event sequence"
jq -se --arg release_version "$expected_release_version" '
  .[0].issue_source == "github"
  and .[0].release_version == $release_version
  and .[0].schema_version == 1
  and .[0].insight_capabilities == {
    agent_output: true,
    structured_agent_events: false,
    token_usage: false,
    context_window: false,
    skill_consultation: false,
    cost: false
  }
  and .[2].issues == []
  and .[3].outcome == "no_progress"
  and (.[3].duration_seconds | type == "number" and . >= 0)
  and .[3].summary == {
    model: null,
    tokens_in: null,
    tokens_out: null,
    observed_tokens: null,
    cost_usd: null,
    tool_count: null,
    skill_call_count: null,
    skills_consulted: null,
    commits: 0,
    auto_closures: 0,
    pr_advances: 0,
    strikes: 0,
    peak_context_window: null
  }
  and .[3].issues == []
  and .[4].outcome == "empty_pool"
  and .[4].iterations_run == 1
' "$temp_dir/empty.stdout" >/dev/null ||
  fail "empty-Pool event payloads drifted"

mapfile -t replay_files < <(find "$repo/.git-loopy/logs" -type f -name '*.jsonl')
assert_equal "1" "${#replay_files[@]}" "empty Run replay file count"
cmp -s "$temp_dir/empty.stdout" "${replay_files[0]}" ||
  fail "empty Run stream and replay differ"
assert_contains "$(<"$FAKE_GH_LOG")" "auth status" "GitHub auth preflight"
assert_contains "$(<"$FAKE_GH_LOG")" "repo view" "GitHub repo preflight"
assert_contains "$(<"$FAKE_GH_LOG")" "issue list" "GitHub Pool collection"

export GIT_LOOPY_MODEL="env-model"
export GIT_LOOPY_REASONING_EFFORT="high"
export GIT_LOOPY_ISSUE_SOURCE="prds"
export GIT_LOOPY_MAX_NMT_STRIKES="7"
export GIT_LOOPY_DENY_TOOLS="env-tool"
export GIT_LOOPY_DENY_SKILLS="env-skill"
export GIT_LOOPY_SEND_TIMEOUT_SECONDS="90"
if ! run_entrypoint \
  "$repo" "$fake_bin" "$temp_dir/env.stdout" "$temp_dir/env.stderr"; then
  fail "environment-only Run did not exit 0: $(<"$temp_dir/env.stderr")"
fi
unset GIT_LOOPY_MODEL
unset GIT_LOOPY_REASONING_EFFORT
unset GIT_LOOPY_ISSUE_SOURCE
unset GIT_LOOPY_MAX_NMT_STRIKES
unset GIT_LOOPY_DENY_TOOLS
unset GIT_LOOPY_DENY_SKILLS
unset GIT_LOOPY_SEND_TIMEOUT_SECONDS
jq -se '
  .[0].model == "env-model"
  and .[0].reasoning_effort == "high"
  and .[0].issue_source == "prds"
  and .[0].max_nmt_strikes == 7
  and .[0].deny_tools == ["env-tool"]
  and .[0].deny_skills == ["env-skill"]
  and .[0].send_timeout_seconds == 90
' "$temp_dir/env.stdout" >/dev/null ||
  fail "entrypoint discarded environment-only configuration"

repo="$temp_dir/github-cap"
fake_bin="$temp_dir/github-cap-bin"
make_real_repo "$repo"
write_turn_tools "$fake_bin"
cat >"$temp_dir/github-list.json" <<'EOF'
[
  {
    "number": 41,
    "title": "Eligible",
    "body": "## What to build\nShip it.\n\n## Acceptance criteria\n- Done.",
    "labels": [{"name": "ready-for-agent"}],
    "state": "OPEN",
    "url": "https://example.invalid/issues/41"
  },
  {
    "number": 42,
    "title": "Bare planning issue",
    "body": "No required headings.",
    "labels": [{"name": "ready-for-agent"}],
    "state": "OPEN",
    "url": "https://example.invalid/issues/42"
  }
]
EOF
mkdir -p "$temp_dir/github-views"
cat >"$temp_dir/github-views/41.json" <<'EOF'
{
  "number": 41,
  "title": "Eligible",
  "body": "## What to build\nShip it.\n\n## Acceptance criteria\n- Done.",
  "labels": [{"name": "ready-for-agent"}],
  "state": "OPEN",
  "url": "https://example.invalid/issues/41",
  "comments": [
    {
      "author": "maintainer",
      "body": "please prioritise",
      "createdAt": "2026-03-01T00:00:00Z"
    }
  ]
}
EOF
export FAKE_GH_LOG="$temp_dir/github-cap-gh.log"
export FAKE_GH_LIST_COUNT="$temp_dir/github-cap-list.count"
export FAKE_GH_LIST_JSON="$temp_dir/github-list.json"
export FAKE_GH_VIEW_DIR="$temp_dir/github-views"
setup_copilot_env "github-cap"
export FAKE_COPILOT_COMMITS=0
cat >"$temp_dir/github-cap-agent-output" <<'EOF'
pre-marker output
<working issue=41>
<working issue=42>
post-marker output
EOF
export FAKE_COPILOT_OUTPUT_FILE="$temp_dir/github-cap-agent-output"
export GIT_LOOPY_MODEL="env-model"
export GIT_LOOPY_REASONING_EFFORT="medium"
export GIT_LOOPY_DENY_TOOLS="env-tool"
export GIT_LOOPY_DENY_SKILLS="env-skill"

if ! run_turn_entrypoint \
  "$repo" "$fake_bin" "$temp_dir/github-cap.stdout" \
  "$temp_dir/github-cap.stderr" 2 --model cli-model --deny-tool cli-tool \
  --deny-skill cli-skill; then
  fail "bounded turn Run did not exit 0: $(<"$temp_dir/github-cap.stderr")"
fi
unset GIT_LOOPY_MODEL GIT_LOOPY_REASONING_EFFORT GIT_LOOPY_DENY_TOOLS \
  GIT_LOOPY_DENY_SKILLS FAKE_COPILOT_COMMITS FAKE_COPILOT_OUTPUT_FILE
assert_equal "2" "$(<"$FAKE_GH_LIST_COUNT")" "Pool is rebuilt each Iteration"
assert_equal "2" "$(<"$FAKE_COPILOT_CALLS")" \
  "exactly one Copilot turn per non-empty Iteration"
assert_equal \
  "2" \
  "$(jq -sc '[.[] | select(.type == "wrapper.afk_ready.collected")] | length' \
    "$temp_dir/github-cap.stdout")" \
  "collection event count"
jq -se '
  ([.[] | select(.type == "wrapper.afk_ready.collected") | .issues] | all(. == [41]))
  and (.[-1].type == "wrapper.run.end")
  and (.[-1].outcome == "iteration_cap")
  and (.[-1].iterations_run == 2)
  and ([.[] | select(.type == "wrapper.commit.recorded")] | length == 0)
' "$temp_dir/github-cap.stdout" >/dev/null ||
  fail "bounded turn events did not carry the filtered Pool"
if grep -q '^issue view 42 ' "$FAKE_GH_LOG"; then
  fail "ineligible issue was enriched after the cheap discriminator pass"
fi

# Wrapper contract §3.1 — the bare planning issue a human triaged to
# ready-for-agent leaves the Pool *named*, with the reason it failed, once per
# Iteration and before the collection it explains. It is still never enriched.
jq -se '
  ([.[] | select(.type == "wrapper.pool.excluded")] | length == 2)
  and ([.[] | select(.type == "wrapper.pool.excluded")]
    | all(.issue == 42
      and .title == "Bare planning issue"
      and .reason == "missing_both_sections"))
  and ([.[] | select(.type == "wrapper.afk_ready.collected") | .excluded]
    | all(. == 1))
  and ([.[] | .type]
    | (index("wrapper.pool.excluded")) < (index("wrapper.afk_ready.collected")))
' "$temp_dir/github-cap.stdout" >/dev/null ||
  fail "excluded ready-for-agent candidate was not reported with its reason"
grep -q 'excluded 42 — missing both sections' "$temp_dir/github-cap.stderr" ||
  fail "Pool exclusion did not reach the operator's own output"

# The Iteration assembled the Python-reference minimum context: last-5 commits,
# the filtered Pool block (with recent comments), and the resolved prompt body.
cap_prompt="$(<"$FAKE_COPILOT_PROMPT")"
assert_contains "$cap_prompt" "Previous commits: " "prompt carries the commits prefix"
assert_contains "$cap_prompt" "initial commit" "prompt carries recent commit subjects"
assert_contains "$cap_prompt" \
  "=== Issue #41: Eligible [labels: ready-for-agent] ===" \
  "prompt carries the filtered issue block"
assert_contains "$cap_prompt" \
  "--- Recent comments (newest first, up to 5) ---" \
  "prompt carries recent comments"
assert_contains "$cap_prompt" "please prioritise" "prompt carries comment bodies"
assert_contains "$cap_prompt" "# Project prompt" \
  "prompt carries the resolved shared prompt"

# Resolved settings honor CLI-over-environment-over-default precedence.
cap_flags="$(<"$FAKE_COPILOT_FLAGS")"
assert_contains "$cap_flags" "--yolo" "turn passes --yolo"
assert_contains "$cap_flags" "--no-color" "turn streams without color"
grep -Fxq 'cli-model' "$FAKE_COPILOT_FLAGS" ||
  fail "CLI --model did not override the environment model"
if grep -Fxq 'env-model' "$FAKE_COPILOT_FLAGS"; then
  fail "environment model leaked past the CLI override"
fi
grep -Fxq 'medium' "$FAKE_COPILOT_FLAGS" ||
  fail "environment reasoning effort was not forwarded"
grep -Fxq 'cli-tool' "$FAKE_COPILOT_FLAGS" ||
  fail "CLI deny-tool not forwarded"
grep -Fxq 'env-tool' "$FAKE_COPILOT_FLAGS" ||
  fail "environment deny-tool not forwarded"
grep -Fxq 'skill(cli-skill)' "$FAKE_COPILOT_FLAGS" ||
  fail "CLI deny-skill not mapped onto --deny-tool skill(...)"
grep -Fxq 'skill(env-skill)' "$FAKE_COPILOT_FLAGS" ||
  fail "environment deny-skill not mapped onto --deny-tool skill(...)"

# The agent's own output remains human-readable on stderr and is also represented
# once as truthful unclassified Events on stdout/replay.
assert_contains "$(<"$temp_dir/github-cap.stderr")" \
  "pre-marker output" \
  "agent output streams to stderr"
jq -se '
  ([.[] | select(.type == "agent.output") | .text] == [
    "pre-marker output",
    "<working issue=41>",
    "<working issue=42>",
    "post-marker output",
    "pre-marker output",
    "<working issue=41>",
    "<working issue=42>",
    "post-marker output"
  ])
  and ([.[] | select(.type == "agent.output")] | all(.kind == "unclassified"))
  and ([.[] | select(.type == "wrapper.issue.activated")] | length == 2)
  and ([.[] | select(.type == "wrapper.issue.activated")]
    | all(
      .issue == 41
      and .binding_source == "working_marker"
      and .activated_at == .ts
    ))
' "$temp_dir/github-cap.stdout" >/dev/null ||
  fail "shell output Events or immutable Working-marker binding drifted"
assert_contains "$(<"$temp_dir/github-cap.stderr")" \
  "conflicting Active-issue marker for #42 ignored; Iteration is already bound to #41" \
  "conflicting marker diagnostic"
mapfile -t cap_replay < <(find "$repo/.git-loopy/logs" -type f -name '*.jsonl')
assert_equal "1" "${#cap_replay[@]}" "turn Run replay file count"
cmp -s "$temp_dir/github-cap.stdout" "${cap_replay[0]}" ||
  fail "turn Run stream and replay differ"

rm -f "$FAKE_GH_LIST_COUNT"
setup_copilot_env "github-default"
export FAKE_COPILOT_COMMITS=0
export FAKE_GH_EMPTY_AFTER=1
if ! run_turn_entrypoint \
  "$repo" "$fake_bin" "$temp_dir/github-default.stdout" \
  "$temp_dir/github-default.stderr"; then
  fail "unlimited turn Run did not exit 0: $(<"$temp_dir/github-default.stderr")"
fi
unset FAKE_COPILOT_COMMITS FAKE_GH_EMPTY_AFTER
assert_equal "2" "$(<"$FAKE_GH_LIST_COUNT")" \
  "unlimited Run rebuilds the Pool until it empties"
assert_equal "1" "$(<"$FAKE_COPILOT_CALLS")" \
  "unlimited Run runs one turn before its Pool empties"
jq -se '
  ([.[] | select(.type == "wrapper.afk_ready.collected") | .issues] == [[41], []])
  and .[-1].type == "wrapper.run.end"
  and .[-1].outcome == "empty_pool"
  and .[-1].iterations_run == 2
' "$temp_dir/github-default.stdout" >/dev/null ||
  fail "unlimited turn Run did not terminate on an empty Pool"

# A turn that produces new commits records one commit event per commit, in
# git's newest-first order, and only closes the Iteration afterwards.
repo="$temp_dir/agent-commits"
fake_bin="$temp_dir/agent-commits-bin"
make_real_repo "$repo"
write_turn_tools "$fake_bin"
cp "$temp_dir/github-list.json" "$temp_dir/agent-commits-list.json"
export FAKE_GH_LOG="$temp_dir/agent-commits-gh.log"
export FAKE_GH_LIST_COUNT="$temp_dir/agent-commits-list.count"
export FAKE_GH_LIST_JSON="$temp_dir/agent-commits-list.json"
export FAKE_GH_VIEW_DIR="$temp_dir/github-views"
setup_copilot_env "agent-commits"
export FAKE_COPILOT_COMMITS=2
if ! run_turn_entrypoint \
  "$repo" "$fake_bin" "$temp_dir/agent-commits.stdout" \
  "$temp_dir/agent-commits.stderr" 1; then
  fail "agent-commit turn Run did not exit 0: $(<"$temp_dir/agent-commits.stderr")"
fi
unset FAKE_COPILOT_COMMITS
expected_commit_seq="$(
  jq -cn '[
    "wrapper.run.start",
    "wrapper.iteration.start",
    "wrapper.pool.excluded",
    "wrapper.afk_ready.collected",
    "agent.output",
    "wrapper.commit.recorded",
    "wrapper.commit.recorded",
    "wrapper.issue.activated",
    "wrapper.iteration.end",
    "wrapper.run.end"
  ]'
)"
actual_commit_seq="$(jq -sc '[.[].type]' "$temp_dir/agent-commits.stdout")"
assert_equal "$expected_commit_seq" "$actual_commit_seq" \
  "commit events precede the Iteration end that closes their Iteration"
jq -se '
  ([.[] | select(.type == "wrapper.commit.recorded") | .subject]
    == ["agent: work 2", "agent: work 1"])
  and ([.[] | select(.type == "wrapper.commit.recorded")]
    | all(has("sha") and has("subject") and has("date")))
  and ([.[] | select(.type == "wrapper.commit.recorded")]
    | all(.date | test("^[0-9]{4}-[0-9]{2}-[0-9]{2}$")))
  and ([.[] | select(.type == "wrapper.commit.recorded")]
    | all(.sha | test("^[0-9a-f]{40}$")))
  and ([.[] | select(.type == "wrapper.issue.activated")]
    | length == 1
      and .[0].issue == 41
      and .[0].binding_source == "single_member_pool")
  and (([.[] | select(.type == "wrapper.issue.activated")][0]) as $activation
    | ([.[] | select(.type == "wrapper.iteration.end")][0]
      | .outcome == "advanced"
      and (.duration_seconds | type == "number" and . >= 0)
      and .summary == {
        model: null,
        tokens_in: null,
        tokens_out: null,
        observed_tokens: null,
        cost_usd: null,
        tool_count: null,
        skill_call_count: null,
        skills_consulted: null,
        commits: 2,
        auto_closures: 0,
        pr_advances: 0,
        strikes: 0,
        peak_context_window: null
      }
      and (.issues | length == 1)
      and .issues[0].issue == 41
      and .issues[0].status == "advanced"
      and .issues[0].first_started_at == $activation.activated_at
      and .issues[0].closed_at == null
      and .issues[0].issue_elapsed_seconds == null
      and (.issues[0].active_seconds | type == "number" and . >= 0)
      and .issues[0].cumulative_active_seconds == .issues[0].active_seconds
      and .issues[0].consumption
        == {model: null, tokens_in: null, tokens_out: null}
      and .issues[0].cost_usd == null
      and .issues[0].peak_context_window == null))
  and .[-1].outcome == "iteration_cap"
' "$temp_dir/agent-commits.stdout" >/dev/null ||
  fail "new agent commits were not recorded as contract commit events"

# A non-zero agent process warns and the Run still finishes cleanly
# (warn-and-continue); the real exit status is preserved, not a pipeline's.
repo="$temp_dir/agent-nonzero"
fake_bin="$temp_dir/agent-nonzero-bin"
make_real_repo "$repo"
write_turn_tools "$fake_bin"
cp "$temp_dir/github-list.json" "$temp_dir/agent-nonzero-list.json"
export FAKE_GH_LOG="$temp_dir/agent-nonzero-gh.log"
export FAKE_GH_LIST_COUNT="$temp_dir/agent-nonzero-list.count"
export FAKE_GH_LIST_JSON="$temp_dir/agent-nonzero-list.json"
export FAKE_GH_VIEW_DIR="$temp_dir/github-views"
setup_copilot_env "agent-nonzero"
export FAKE_COPILOT_COMMITS=0
export FAKE_COPILOT_EXIT=7
if ! run_turn_entrypoint \
  "$repo" "$fake_bin" "$temp_dir/agent-nonzero.stdout" \
  "$temp_dir/agent-nonzero.stderr" 1; then
  fail "non-zero agent turn must not fail the Run: \
$(<"$temp_dir/agent-nonzero.stderr")"
fi
unset FAKE_COPILOT_COMMITS FAKE_COPILOT_EXIT
assert_equal "1" "$(<"$FAKE_COPILOT_CALLS")" \
  "the turn ran despite its non-zero exit"
assert_contains "$(<"$temp_dir/agent-nonzero.stderr")" \
  "copilot turn exited with status 7" \
  "non-zero agent exit warns to stderr"
jq -se '
  ([.[] | select(.type == "wrapper.commit.recorded")] | length == 0)
  and .[-1].outcome == "iteration_cap"
' "$temp_dir/agent-nonzero.stdout" >/dev/null ||
  fail "non-zero agent turn drifted from warn-and-continue"

# The turn feeds EXACTLY the last five commits (contract §4), newest-first, and
# truncates older history. Every other turn scenario runs against a <=3-commit
# repo, so this is the only guard on the shared `-n5` recent-commits bound the
# Python reference and both native ports must agree on.
repo="$temp_dir/recent-five"
fake_bin="$temp_dir/recent-five-bin"
make_real_repo "$repo"
for n in 1 2 3 4 5 6 7; do
  git -C "$repo" commit -q --allow-empty -m "history commit $n"
done
write_turn_tools "$fake_bin"
cp "$temp_dir/github-list.json" "$temp_dir/recent-five-list.json"
export FAKE_GH_LOG="$temp_dir/recent-five-gh.log"
export FAKE_GH_LIST_COUNT="$temp_dir/recent-five-list.count"
export FAKE_GH_LIST_JSON="$temp_dir/recent-five-list.json"
export FAKE_GH_VIEW_DIR="$temp_dir/github-views"
setup_copilot_env "recent-five"
export FAKE_COPILOT_COMMITS=0
if ! run_turn_entrypoint \
  "$repo" "$fake_bin" "$temp_dir/recent-five.stdout" \
  "$temp_dir/recent-five.stderr" 1; then
  fail "recent-five turn Run did not exit 0: $(<"$temp_dir/recent-five.stderr")"
fi
unset FAKE_COPILOT_COMMITS
recent_prompt="$(<"$FAKE_COPILOT_PROMPT")"
for n in 3 4 5 6 7; do
  assert_contains "$recent_prompt" "history commit $n" \
    "prompt carries the last-five commit $n"
done
for n in 1 2; do
  [[ "$recent_prompt" != *"history commit $n"* ]] ||
    fail "prompt carried commit $n from beyond the last five"
done
[[ "$recent_prompt" != *"initial commit"* ]] ||
  fail "prompt carried the initial commit from beyond the last five"
# Newest-first: commit 7 is rendered before commit 3 in the recent-commits block.
newest_first_prefix="${recent_prompt%%history commit 3*}"
assert_contains "$newest_first_prefix" "history commit 7" \
  "recent commits are rendered newest-first"

repo="$temp_dir/large-github"
fake_bin="$temp_dir/large-github-bin"
make_real_repo "$repo"
write_turn_tools "$fake_bin"
cat >"$temp_dir/large-github-list.json" <<'EOF'
[
  {
    "number": 51,
    "title": "Large eligible issue",
    "body": "## What to build\nShip it.\n\n## Acceptance criteria\n- Done.",
    "labels": [{"name": "ready-for-agent"}],
    "state": "OPEN",
    "url": "https://example.invalid/issues/51"
  }
]
EOF
mkdir -p "$temp_dir/large-github-views"
printf '## What to build\n' >"$temp_dir/large-body.md"
arg_max="$(getconf ARG_MAX 2>/dev/null || printf '2097152')"
head -c "$((arg_max + 65536))" </dev/zero |
  tr '\0' x >>"$temp_dir/large-body.md"
printf '\n\n## Acceptance criteria\n- Done.\n' >>"$temp_dir/large-body.md"
jq -n --rawfile body "$temp_dir/large-body.md" '{
  number: 51,
  title: "Large eligible issue",
  body: $body,
  labels: [{name: "ready-for-agent"}],
  state: "OPEN",
  url: "https://example.invalid/issues/51",
  comments: []
}' >"$temp_dir/large-github-views/51.json"
export FAKE_GH_LOG="$temp_dir/large-github-gh.log"
export FAKE_GH_LIST_COUNT="$temp_dir/large-github-list.count"
export FAKE_GH_LIST_JSON="$temp_dir/large-github-list.json"
export FAKE_GH_VIEW_DIR="$temp_dir/large-github-views"
setup_copilot_env "large-github"

if ! run_turn_entrypoint \
  "$repo" "$fake_bin" "$temp_dir/large-github.stdout" \
  "$temp_dir/large-github.stderr" 1; then
  fail "large GitHub issue Run failed: $(<"$temp_dir/large-github.stderr")"
fi
# The oversized body is collected through jq and files, never through argv, so
# collection succeeds. The assembled prompt then exceeds the OS argv limit, so
# the CLI turn cannot exec: it degrades to a warning and the Run still finishes.
jq -se '
  ([.[] | select(.type == "wrapper.afk_ready.collected")][0].issues == [51])
  and .[-1].outcome == "iteration_cap"
  and ([.[] | select(.type == "wrapper.commit.recorded")] | length == 0)
' "$temp_dir/large-github.stdout" >/dev/null ||
  fail "large GitHub issue was not collected"
assert_contains "$(<"$temp_dir/large-github.stderr")" \
  "copilot turn exited with status" \
  "an oversized prompt degrades the turn without failing the Run"
[[ ! -f "$temp_dir/large-github-copilot.calls" ]] ||
  fail "copilot ran despite an oversized argv"

repo="$temp_dir/prds"
fake_bin="$temp_dir/prds-bin"
make_real_repo "$repo"
write_turn_tools "$fake_bin"
mkdir -p \
  "$repo/prds/alpha/done" \
  "$repo/prds/alpha-beta/done" \
  "$repo/prds/feature/done" \
  "$repo/prds/large/done"
mkdir -p "$temp_dir/outside-prds"
cp "$temp_dir/large-body.md" "$repo/prds/large/001-ready.md"
cat >"$repo/prds/alpha/001-ready.md" <<'EOF'
## What to build
Ship alpha.

## Acceptance criteria
- Done.
EOF
cat >"$repo/prds/alpha-beta/001-ready.md" <<'EOF'
## What to build
Ship alpha-beta.

## Acceptance criteria
- Done.
EOF
cat >"$repo/prds/feature/001-ready.md" <<'EOF'
## What to build
Ship it.

## Acceptance criteria
- Done.
EOF
cat >"$temp_dir/outside-prds/004-escaped.md" <<'EOF'
## What to build
Read outside the worktree.

## Acceptance criteria
- Escaped.
EOF
ln -s "$temp_dir/outside-prds" "$repo/prds/escaped"
printf 'No required headings.\n' >"$repo/prds/feature/002-bare.md"
cat >"$repo/prds/feature/done/003-archived.md" <<'EOF'
## What to build
Old work.

## Acceptance criteria
- Archived.
EOF
export FAKE_GH_LOG="$temp_dir/prds-gh.log"
export FAKE_GH_LIST_COUNT="$temp_dir/prds-list.count"
export FAKE_GH_LIST_JSON="$temp_dir/empty-list.json"
export FAKE_GH_VIEW_DIR="$temp_dir/empty-views"
export GIT_LOOPY_ISSUE_SOURCE="github"
setup_copilot_env "prds"

if ! run_turn_entrypoint \
  "$repo" "$fake_bin" "$temp_dir/prds.stdout" "$temp_dir/prds.stderr" \
  1 --issue-source prds; then
  fail "local-PRD discovery did not exit 0: $(<"$temp_dir/prds.stderr")"
fi
unset GIT_LOOPY_ISSUE_SOURCE
jq -se '
  .[0].issue_source == "prds"
  and (
    [.[] | select(.type == "wrapper.afk_ready.collected")][0].issues
    == [
      "prds/alpha-beta/001-ready.md",
      "prds/alpha/001-ready.md",
      "prds/feature/001-ready.md",
      "prds/large/001-ready.md"
    ]
  )
  and .[-1].outcome == "iteration_cap"
' "$temp_dir/prds.stdout" >/dev/null ||
  fail "local-PRD collection or CLI precedence drifted"
[[ ! -e "$FAKE_GH_LOG" ]] || fail "PRDs mode invoked gh"
# The oversized `prds/large` body is collected through `$(<path)` / --rawfile,
# never argv, so the Pool builds; the assembled prompt then exceeds the argv
# limit and the turn degrades to a warning without failing the Run.
assert_contains "$(<"$temp_dir/prds.stderr")" \
  "copilot turn exited with status" \
  "PRDs turn degrades gracefully on an oversized prompt"

repo="$temp_dir/prds-root-link"
fake_bin="$temp_dir/prds-root-link-bin"
make_repo "$repo"
write_fake_tools "$fake_bin"
mkdir -p "$temp_dir/outside-prds-root/feature"
cat >"$temp_dir/outside-prds-root/feature/001-escaped.md" <<'EOF'
## What to build
Read outside the worktree.

## Acceptance criteria
- Escaped.
EOF
ln -s "$temp_dir/outside-prds-root" "$repo/prds"
export FAKE_GH_LOG="$temp_dir/prds-root-link-gh.log"
export FAKE_GH_LIST_COUNT="$temp_dir/prds-root-link-list.count"
export FAKE_GH_LIST_JSON="$temp_dir/empty-list.json"
export FAKE_GH_VIEW_DIR="$temp_dir/empty-views"

if ! run_entrypoint \
  "$repo" "$fake_bin" "$temp_dir/prds-root-link.stdout" \
  "$temp_dir/prds-root-link.stderr" 1 --issue-source prds; then
  fail "linked-PRD-root Run did not exit 0: $(<"$temp_dir/prds-root-link.stderr")"
fi
jq -se '
  ([.[] | select(.type == "wrapper.afk_ready.collected")][0].issues == [])
  and .[-1].outcome == "empty_pool"
' "$temp_dir/prds-root-link.stdout" >/dev/null ||
  fail "local-PRD collection followed a linked root outside the worktree"
assert_contains \
  "$(<"$temp_dir/prds-root-link.stderr")" \
  "linked prds root is not allowed" \
  "linked local-PRD root warning"

# Closed-world Skill policy (contract §17.6): the shell port has no native
# `enabled_skills` support, so every policy surface the family fixture names must
# abort *before* the Pool is collected and before Copilot is invoked. Silently
# running a wider capability set than the operator configured is the one outcome
# this fails closed to prevent. The surfaces come from the shared fixture, so a
# surface added to the contract fails here rather than leaking through.
write_fail_closed_tools() {
  local bin_dir="$1"
  write_fake_tools "$bin_dir"
  cat >"$bin_dir/copilot" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"$FAKE_COPILOT_LOG"
exit 0
EOF
  chmod +x "$bin_dir/copilot"
}

skill_policy_fixture="$port_dir/../conformance/skill-policy.json"

assert_fail_closed() {
  local label="$1"
  local surface="$2"
  local repo="$3"
  local fake_bin="$4"
  shift 4

  set +e
  run_entrypoint \
    "$repo" "$fake_bin" \
    "$temp_dir/$label.stdout" "$temp_dir/$label.stderr" "$@"
  local status=$?
  set -e

  assert_equal "1" "$status" "fail-closed exit for $label"
  assert_contains \
    "$(<"$temp_dir/$label.stderr")" "$surface" \
    "fail-closed diagnostic names the surface for $label"
  assert_contains \
    "$(<"$temp_dir/$label.stderr")" "Python Orchestrator" \
    "fail-closed diagnostic directs the operator for $label"
  [[ ! -s "$temp_dir/$label.stdout" ]] ||
    fail "fail-closed run emitted Run events for $label"
  [[ ! -e "$FAKE_COPILOT_LOG" ]] ||
    fail "fail-closed run invoked Copilot for $label"
  if [[ -e "$FAKE_GH_LOG" ]] && grep -q '^issue list ' "$FAKE_GH_LOG"; then
    fail "fail-closed run reached Pool collection for $label"
  fi
}

while IFS= read -r surface; do
  label="fail-closed-${surface//[^a-zA-Z_]/-}"
  repo="$temp_dir/$label"
  fake_bin="$temp_dir/$label-bin"
  make_repo "$repo"
  write_fail_closed_tools "$fake_bin"
  export FAKE_COPILOT_LOG="$temp_dir/$label-copilot.log"
  export FAKE_GH_LOG="$temp_dir/$label-gh.log"
  export FAKE_GH_LIST_COUNT="$temp_dir/$label-list.count"
  export FAKE_GH_LIST_JSON="$temp_dir/github-list.json"
  export FAKE_GH_VIEW_DIR="$temp_dir/github-views"

  case "$surface" in
    GIT_LOOPY_ENABLED_SKILLS)
      # Explicitly empty: an exact empty replacement is a real policy, and the
      # port must not read "empty" as "nothing configured".
      export GIT_LOOPY_ENABLED_SKILLS=""
      assert_fail_closed "$label" "$surface" "$repo" "$fake_bin"
      unset GIT_LOOPY_ENABLED_SKILLS
      ;;
    --enable-skill | --disable-skill)
      assert_fail_closed "$label" "$surface" "$repo" "$fake_bin" "$surface" tdd
      ;;
    enabled_skills)
      printf 'enabled_skills = ["tdd"]\n' >"$repo/git-loopy/config.toml"
      assert_fail_closed "$label" "$surface" "$repo" "$fake_bin"
      ;;
    *)
      fail "unhandled Skill-policy surface in the shared fixture: $surface"
      ;;
  esac
  unset FAKE_COPILOT_LOG
done < <(jq -r '.native_transition.policy_surfaces[]' "$skill_policy_fixture")

# The global scope is a standard Config location too, so a global `enabled_skills`
# fails closed from a repository that carries no Config of its own.
repo="$temp_dir/fail-closed-global"
fake_bin="$temp_dir/fail-closed-global-bin"
make_repo "$repo"
write_fail_closed_tools "$fake_bin"
mkdir -p "$repo/xdg/git-loopy"
printf 'enabled_skills = []\n' >"$repo/xdg/git-loopy/config.toml"
export FAKE_COPILOT_LOG="$temp_dir/fail-closed-global-copilot.log"
export FAKE_GH_LOG="$temp_dir/fail-closed-global-gh.log"
export FAKE_GH_LIST_COUNT="$temp_dir/fail-closed-global-list.count"
export FAKE_GH_LIST_JSON="$temp_dir/github-list.json"
export FAKE_GH_VIEW_DIR="$temp_dir/github-views"
assert_fail_closed \
  "fail-closed-global" "enabled_skills" "$repo" "$fake_bin"
unset FAKE_COPILOT_LOG

# Positive control (contract §17.2): legacy deny-only inputs are deprecated final
# guards, not a closed-world surface, so they still resolve and reach the Pool.
repo="$temp_dir/legacy-deny-runs"
fake_bin="$temp_dir/legacy-deny-runs-bin"
make_repo "$repo"
write_fake_tools "$fake_bin"
export FAKE_GH_LOG="$temp_dir/legacy-deny-runs-gh.log"
export FAKE_GH_LIST_COUNT="$temp_dir/legacy-deny-runs-list.count"
export FAKE_GH_LIST_JSON="$temp_dir/empty-list.json"
export FAKE_GH_VIEW_DIR="$temp_dir/empty-views"

set +e
export GIT_LOOPY_DENY_SKILLS="legacy-skill"
run_entrypoint \
  "$repo" "$fake_bin" \
  "$temp_dir/legacy-deny-runs.stdout" "$temp_dir/legacy-deny-runs.stderr" \
  --deny-skill flag-skill
status=$?
unset GIT_LOOPY_DENY_SKILLS
set -e
assert_equal "0" "$status" "legacy deny-only invocation still runs"
assert_contains \
  "$(<"$FAKE_GH_LOG")" "issue list" \
  "legacy deny-only invocation reached Pool collection"

# The strongest form of "Copilot is never invoked on failure": drive the real
# turn machinery — a real git repository and a Pool that would otherwise start an
# Iteration — and prove the abort still lands before the agent process exists.
repo="$temp_dir/fail-closed-turn"
fake_bin="$temp_dir/fail-closed-turn-bin"
make_real_repo "$repo"
write_turn_tools "$fake_bin"
printf 'enabled_skills = ["tdd"]\n' >"$repo/git-loopy/config.toml"
export FAKE_GH_LOG="$temp_dir/fail-closed-turn-gh.log"
export FAKE_GH_LIST_COUNT="$temp_dir/fail-closed-turn-list.count"
export FAKE_GH_LIST_JSON="$temp_dir/github-list.json"
export FAKE_GH_VIEW_DIR="$temp_dir/github-views"
setup_copilot_env "fail-closed-turn"
export FAKE_COPILOT_COMMITS=0

set +e
run_turn_entrypoint \
  "$repo" "$fake_bin" \
  "$temp_dir/fail-closed-turn.stdout" "$temp_dir/fail-closed-turn.stderr" 1
status=$?
set -e
assert_equal "1" "$status" "a configured policy aborts a Run that had real work"
assert_contains \
  "$(<"$temp_dir/fail-closed-turn.stderr")" \
  "enabled_skills" \
  "the turn-level abort names the surface"
[[ ! -e "$FAKE_COPILOT_CALLS" ]] ||
  fail "fail-closed Run invoked the fake Copilot process"
[[ ! -e "$FAKE_GH_LOG" ]] ||
  fail "fail-closed Run reached the GitHub dependency checks"
[[ ! -s "$temp_dir/fail-closed-turn.stdout" ]] ||
  fail "fail-closed Run emitted Run events"

# Control: the same repository, tools, and Pool without the Config key does start
# the agent — so the assertions above can actually fail.
rm -f "$repo/git-loopy/config.toml"
setup_copilot_env "fail-closed-turn-control"
export FAKE_COPILOT_COMMITS=0
if ! run_turn_entrypoint \
  "$repo" "$fake_bin" \
  "$temp_dir/fail-closed-control.stdout" "$temp_dir/fail-closed-control.stderr" 1; then
  fail "control Run did not exit 0: $(<"$temp_dir/fail-closed-control.stderr")"
fi
[[ -e "$FAKE_COPILOT_CALLS" ]] ||
  fail "control Run never invoked the fake Copilot process"
unset FAKE_COPILOT_COMMITS

repo="$temp_dir/missing-tracker"
fake_bin="$temp_dir/missing-tracker-bin"
mkdir -p "$repo/git-loopy"
printf '# Prompt\n' >"$repo/git-loopy/PROMPT.md"
write_fake_tools "$fake_bin"
export FAKE_GH_LOG="$temp_dir/missing-tracker-gh.log"
export FAKE_GH_LIST_COUNT="$temp_dir/missing-tracker-list.count"
export FAKE_GH_LIST_JSON="$temp_dir/empty-list.json"
export FAKE_GH_VIEW_DIR="$temp_dir/empty-views"

set +e
run_entrypoint \
  "$repo" "$fake_bin" "$temp_dir/missing-tracker.stdout" \
  "$temp_dir/missing-tracker.stderr"
status=$?
set -e
assert_equal "1" "$status" "missing issue-tracker configuration exit"
assert_contains \
  "$(<"$temp_dir/missing-tracker.stderr")" \
  "/setup-agent-skills" \
  "missing setup guidance"
[[ ! -s "$temp_dir/missing-tracker.stdout" ]] ||
  fail "preflight failure emitted Iteration work"
[[ ! -e "$FAKE_GH_LOG" ]] || fail "preflight continued after missing tracker"

repo="$temp_dir/auth-failure"
fake_bin="$temp_dir/auth-failure-bin"
make_repo "$repo"
write_fake_tools "$fake_bin"
export FAKE_GH_LOG="$temp_dir/auth-failure-gh.log"
export FAKE_GH_LIST_COUNT="$temp_dir/auth-failure-list.count"
export FAKE_GH_LIST_JSON="$temp_dir/empty-list.json"
export FAKE_GH_VIEW_DIR="$temp_dir/empty-views"
export FAKE_GH_AUTH_STATUS=1

set +e
run_entrypoint \
  "$repo" "$fake_bin" "$temp_dir/auth-failure.stdout" \
  "$temp_dir/auth-failure.stderr"
status=$?
set -e
unset FAKE_GH_AUTH_STATUS
assert_equal "1" "$status" "GitHub authentication preflight exit"
assert_contains \
  "$(<"$temp_dir/auth-failure.stderr")" \
  "gh auth login" \
  "GitHub authentication guidance"
[[ ! -s "$temp_dir/auth-failure.stdout" ]] ||
  fail "authentication failure emitted Run events"
if grep -q '^issue list ' "$FAKE_GH_LOG"; then
  fail "authentication failure reached Pool collection"
fi

repo="$temp_dir/usage"
fake_bin="$temp_dir/usage-bin"
make_repo "$repo"
write_fake_tools "$fake_bin"
export FAKE_GH_LOG="$temp_dir/usage-gh.log"
export FAKE_GH_LIST_COUNT="$temp_dir/usage-list.count"
export FAKE_GH_LIST_JSON="$temp_dir/empty-list.json"
export FAKE_GH_VIEW_DIR="$temp_dir/empty-views"

set +e
run_entrypoint \
  "$repo" "$fake_bin" "$temp_dir/usage.stdout" "$temp_dir/usage.stderr" nope
status=$?
set -e
assert_equal "2" "$status" "malformed invocation exit"
[[ ! -s "$temp_dir/usage.stdout" ]] || fail "usage error emitted Run events"
[[ ! -e "$FAKE_GH_LOG" ]] || fail "usage error reached preflight"

# A turn whose commits carry closing keywords auto-closes the referenced *Pool
# issue* exactly once — repeated references to the same issue collapse to one
# closure attributing every referencing SHA, and an out-of-Pool reference (a PR
# or a stranger issue) is never touched.
repo="$temp_dir/auto-close"
fake_bin="$temp_dir/auto-close-bin"
make_real_repo "$repo"
write_turn_tools "$fake_bin"
cp "$temp_dir/github-list.json" "$temp_dir/auto-close-list.json"
export FAKE_GH_LOG="$temp_dir/auto-close-gh.log"
export FAKE_GH_LIST_COUNT="$temp_dir/auto-close-list.count"
export FAKE_GH_LIST_JSON="$temp_dir/auto-close-list.json"
export FAKE_GH_VIEW_DIR="$temp_dir/github-views"
export FAKE_GH_CLOSED="$temp_dir/auto-close-closed.log"
export FAKE_GH_CLOSE_DIR="$temp_dir/auto-close-comments"
setup_copilot_env "auto-close"
export FAKE_COPILOT_PLAN_DIR="$temp_dir/auto-close-plan"
mkdir -p "$FAKE_COPILOT_PLAN_DIR/1"
cat >"$FAKE_COPILOT_PLAN_DIR/1/1.msg" <<'EOF'
feat: land the eligible work

Closes #41 Fixes #77
EOF
cat >"$FAKE_COPILOT_PLAN_DIR/1/2.msg" <<'EOF'
chore: follow-up tidy

Resolves #41
EOF
if ! run_turn_entrypoint \
  "$repo" "$fake_bin" "$temp_dir/auto-close.stdout" \
  "$temp_dir/auto-close.stderr" 1; then
  fail "auto-close turn Run did not exit 0: $(<"$temp_dir/auto-close.stderr")"
fi
unset FAKE_COPILOT_PLAN_DIR FAKE_GH_CLOSED FAKE_GH_CLOSE_DIR
assert_equal "1" "$(grep -c '^41$' "$temp_dir/auto-close-closed.log")" \
  "the referenced Pool issue is closed exactly once"
if grep -q '^77$' "$temp_dir/auto-close-closed.log"; then
  fail "an out-of-Pool reference was closed"
fi
jq -se '
  ([.[] | select(.type == "wrapper.auto_close")] | length == 1)
  and ([.[] | select(.type == "wrapper.auto_close")][0]
    | .issue == 41 and (.shas | length == 2) and .sha == .shas[0])
  and ([.[] | select(.type == "wrapper.commit.recorded") | .subject]
    == ["chore: follow-up tidy", "feat: land the eligible work"])
  and ([.[] | select(.type == "wrapper.issue.activated")]
    | length == 1
      and .[0].issue == 41
      and .[0].binding_source == "closure")
  and (
    ([.[] | .type] | index("wrapper.issue.activated"))
    < ([.[] | .type] | index("wrapper.auto_close"))
  )
  and (([.[] | select(.type == "wrapper.issue.activated")][0]) as $activation
    | ([.[] | select(.type == "wrapper.auto_close")][0]) as $closure
    | ([.[] | select(.type == "wrapper.iteration.end")][0]
      | .outcome == "closed"
      and .summary.commits == 2
      and .summary.auto_closures == 1
      and .summary.strikes == 0
      and (.issues | length == 1)
      and .issues[0].issue == 41
      and .issues[0].status == "closed"
      and .issues[0].first_started_at == $activation.activated_at
      and .issues[0].closed_at == $closure.ts
      and (.issues[0].issue_elapsed_seconds | type == "number" and . >= 0)
      and (.issues[0].active_seconds | type == "number" and . >= 0)
      and .issues[0].cumulative_active_seconds == .issues[0].active_seconds))
  and ([.[] | select(.type == "wrapper.strike")] | length == 0)
  and .[-1].outcome == "iteration_cap"
' "$temp_dir/auto-close.stdout" >/dev/null ||
  fail "auto-close did not close the Pool issue once with both SHAs"
close_shas="$(jq -sr '[.[] | select(.type == "wrapper.auto_close")][0]
  | .shas | join(" ")' "$temp_dir/auto-close.stdout")"
close_comment="$(<"$temp_dir/auto-close-comments/41.comment")"
for sha in $close_shas; do
  assert_contains "$close_comment" "$sha" "closure comment cites commit $sha"
done
assert_contains "$close_comment" "gh issue reopen 41" \
  "closure comment documents how to reopen"

# Progress resets the Strike counter: a no-progress Iteration records a Strike,
# the next Iteration's agent commit clears it, and a following no-progress
# Iteration is Strike 1 again — never 2.
repo="$temp_dir/strike-reset"
fake_bin="$temp_dir/strike-reset-bin"
make_real_repo "$repo"
write_turn_tools "$fake_bin"
cp "$temp_dir/github-list.json" "$temp_dir/strike-reset-list.json"
export FAKE_GH_LOG="$temp_dir/strike-reset-gh.log"
export FAKE_GH_LIST_COUNT="$temp_dir/strike-reset-list.count"
export FAKE_GH_LIST_JSON="$temp_dir/strike-reset-list.json"
export FAKE_GH_VIEW_DIR="$temp_dir/github-views"
export FAKE_GH_CLOSED="$temp_dir/strike-reset-closed.log"
setup_copilot_env "strike-reset"
export FAKE_COPILOT_PLAN_DIR="$temp_dir/strike-reset-plan"
mkdir -p "$FAKE_COPILOT_PLAN_DIR/2"
printf 'agent: real work\n' >"$FAKE_COPILOT_PLAN_DIR/2/1.msg"
if ! run_turn_entrypoint \
  "$repo" "$fake_bin" "$temp_dir/strike-reset.stdout" \
  "$temp_dir/strike-reset.stderr" 3; then
  fail "strike-reset Run did not exit 0: $(<"$temp_dir/strike-reset.stderr")"
fi
unset FAKE_COPILOT_PLAN_DIR FAKE_GH_CLOSED
jq -se '
  ([.[] | select(.type == "wrapper.strike")]
    | length == 2
    and all(.outcome == "warn" and .strikes == 1))
  and ([.[] | select(.type == "wrapper.commit.recorded") | .subject]
    == ["agent: real work"])
  and ([.[] | select(.type == "wrapper.auto_close")] | length == 0)
  and ([.[] | select(.type == "wrapper.iteration.end") | .outcome]
    == ["no_progress", "advanced", "no_progress"])
  and ([.[] | select(.type == "wrapper.iteration.end") | .summary.strikes]
    == [1, 0, 1])
  and ([.[] | select(.type == "wrapper.iteration.end") | .summary.commits]
    == [0, 1, 0])
  and ([.[] | select(.type == "wrapper.iteration.end") | .issues[0].status]
    == ["no-progress", "advanced", "no-progress"])
  and ([.[] | select(.type == "wrapper.iteration.end") | .issues[0]]
    | all(.closed_at == null and .issue_elapsed_seconds == null))
  and ([.[] | select(.type == "wrapper.iteration.end")
      | .issues[0].first_started_at] | unique | length == 1)
  and ([.[] | select(.type == "wrapper.iteration.end")
      | .issues[0].cumulative_active_seconds] as $active
    | $active == ($active | sort))
  and .[-1].outcome == "iteration_cap"
  and .[-1].iterations_run == 3
' "$temp_dir/strike-reset.stdout" >/dev/null ||
  fail "an intervening agent commit did not reset the Strike counter"
[[ ! -e "$temp_dir/strike-reset-closed.log" ]] ||
  fail "strike-reset closed an issue with no closing keyword"

# Consecutive no-progress Iterations accumulate Strikes and the threshold ends
# the Run as stuck (exit 1), even with the iteration cap unlimited.
repo="$temp_dir/stuck"
fake_bin="$temp_dir/stuck-bin"
make_real_repo "$repo"
write_turn_tools "$fake_bin"
cp "$temp_dir/github-list.json" "$temp_dir/stuck-list.json"
export FAKE_GH_LOG="$temp_dir/stuck-gh.log"
export FAKE_GH_LIST_COUNT="$temp_dir/stuck-list.count"
export FAKE_GH_LIST_JSON="$temp_dir/stuck-list.json"
export FAKE_GH_VIEW_DIR="$temp_dir/github-views"
export FAKE_GH_CLOSED="$temp_dir/stuck-closed.log"
setup_copilot_env "stuck"
export FAKE_COPILOT_PLAN_DIR="$temp_dir/stuck-plan"
mkdir -p "$FAKE_COPILOT_PLAN_DIR"
set +e
run_turn_entrypoint \
  "$repo" "$fake_bin" "$temp_dir/stuck.stdout" "$temp_dir/stuck.stderr" 0
status=$?
set -e
unset FAKE_COPILOT_PLAN_DIR FAKE_GH_CLOSED
assert_equal "1" "$status" "a stuck Run exits 1"
jq -se '
  ([.[] | select(.type == "wrapper.strike") | {strikes, outcome}]
    == [
      {strikes: 1, outcome: "warn"},
      {strikes: 2, outcome: "warn"},
      {strikes: 3, outcome: "abort"}
    ])
  and ([.[] | select(.type == "wrapper.commit.recorded")] | length == 0)
  and ([.[] | select(.type == "wrapper.auto_close")] | length == 0)
  and ([.[] | select(.type == "wrapper.iteration.end") | .outcome]
    == ["no_progress", "no_progress", "aborted"])
  and ([.[] | select(.type == "wrapper.iteration.end") | .issues[0]]
    | .[-1].status == "aborted"
      and .[-1].closed_at == null
      and .[-1].issue_elapsed_seconds == null)
  and .[-1].type == "wrapper.run.end"
  and .[-1].outcome == "stuck"
  and .[-1].iterations_run == 3
' "$temp_dir/stuck.stdout" >/dev/null ||
  fail "consecutive Strikes did not terminate the Run as stuck"
[[ ! -e "$temp_dir/stuck-closed.log" ]] || fail "stuck Run closed an issue"

# A recognized runner Checkpoint is excluded from the agent-commit tally: it is
# not recorded as a contract commit and does not count as progress, so its
# Iteration still records a Strike (Checkpoint exclusion holds even before this
# port authors Checkpoints).
repo="$temp_dir/checkpoint-skip"
fake_bin="$temp_dir/checkpoint-skip-bin"
make_real_repo "$repo"
write_turn_tools "$fake_bin"
cp "$temp_dir/github-list.json" "$temp_dir/checkpoint-skip-list.json"
export FAKE_GH_LOG="$temp_dir/checkpoint-skip-gh.log"
export FAKE_GH_LIST_COUNT="$temp_dir/checkpoint-skip-list.count"
export FAKE_GH_LIST_JSON="$temp_dir/checkpoint-skip-list.json"
export FAKE_GH_VIEW_DIR="$temp_dir/github-views"
export FAKE_GH_CLOSED="$temp_dir/checkpoint-skip-closed.log"
setup_copilot_env "checkpoint-skip"
export FAKE_COPILOT_PLAN_DIR="$temp_dir/checkpoint-skip-plan"
mkdir -p "$FAKE_COPILOT_PLAN_DIR/1"
cat >"$FAKE_COPILOT_PLAN_DIR/1/1.msg" <<'EOF'
Checkpoint: capture uncommitted work-in-progress

GitLoopy-Checkpoint: 41
EOF
if ! run_turn_entrypoint \
  "$repo" "$fake_bin" "$temp_dir/checkpoint-skip.stdout" \
  "$temp_dir/checkpoint-skip.stderr" 1; then
  fail "checkpoint-skip Run did not exit 0: \
$(<"$temp_dir/checkpoint-skip.stderr")"
fi
unset FAKE_COPILOT_PLAN_DIR FAKE_GH_CLOSED
jq -se '
  ([.[] | select(.type == "wrapper.commit.recorded")] | length == 0)
  and ([.[] | select(.type == "wrapper.strike")]
    | length == 1 and all(.strikes == 1 and .outcome == "warn"))
  and ([.[] | select(.type == "wrapper.auto_close")] | length == 0)
  and .[-1].outcome == "iteration_cap"
' "$temp_dir/checkpoint-skip.stdout" >/dev/null ||
  fail "a runner Checkpoint was counted toward agent progress"
[[ ! -e "$temp_dir/checkpoint-skip-closed.log" ]] ||
  fail "checkpoint-skip closed an issue"

# A dirty worktree the agent left uncommitted is captured in exactly one runner
# Checkpoint (ADR-0004): staged with `git add -A`, attributed to the Active
# issue, close-keyword-free, surfaced as wrapper.checkpoint.recorded (never a
# commit.recorded), and excluded from Strike progress (the Iteration still
# strikes). The Checkpoint is a new local commit, so the branch is auto-pushed
# to its upstream and the remote receives it.
repo="$temp_dir/checkpoint-dirty"
fake_bin="$temp_dir/checkpoint-dirty-bin"
remote="$temp_dir/checkpoint-dirty-remote.git"
make_real_repo "$repo"
add_fake_remote "$repo" "$remote"
write_turn_tools "$fake_bin"
cp "$temp_dir/github-list.json" "$temp_dir/checkpoint-dirty-list.json"
export FAKE_GH_LOG="$temp_dir/checkpoint-dirty-gh.log"
export FAKE_GH_LIST_COUNT="$temp_dir/checkpoint-dirty-list.count"
export FAKE_GH_LIST_JSON="$temp_dir/checkpoint-dirty-list.json"
export FAKE_GH_VIEW_DIR="$temp_dir/github-views"
export FAKE_GH_CLOSED="$temp_dir/checkpoint-dirty-closed.log"
setup_copilot_env "checkpoint-dirty"
export FAKE_COPILOT_PLAN_DIR="$temp_dir/checkpoint-dirty-plan"
mkdir -p "$FAKE_COPILOT_PLAN_DIR/1"
cat >"$FAKE_COPILOT_PLAN_DIR/1/worktree.sh" <<'EOF'
printf 'work in progress the agent forgot to commit\n' >wip.txt
EOF
if ! run_turn_entrypoint \
  "$repo" "$fake_bin" "$temp_dir/checkpoint-dirty.stdout" \
  "$temp_dir/checkpoint-dirty.stderr" 1; then
  fail "checkpoint-dirty Run did not exit 0: $(<"$temp_dir/checkpoint-dirty.stderr")"
fi
unset FAKE_COPILOT_PLAN_DIR FAKE_GH_CLOSED
jq -se '
  ([.[] | select(.type == "wrapper.checkpoint.recorded")] | length == 1)
  and ([.[] | select(.type == "wrapper.checkpoint.recorded")][0]
    | .issue == 41 and (.sha | type == "string") and (.sha | length > 0))
  and ([.[] | select(.type == "wrapper.commit.recorded")] | length == 0)
  and ([.[] | select(.type == "wrapper.push.recorded")] | length == 1)
  and ([.[] | select(.type == "wrapper.strike")]
    | length == 1 and all(.strikes == 1 and .outcome == "warn"))
  and ([.[] | select(.type == "wrapper.auto_close")] | length == 0)
  and .[-1].outcome == "iteration_cap"
' "$temp_dir/checkpoint-dirty.stdout" >/dev/null ||
  fail "a dirty worktree was not captured in exactly one pushed Checkpoint"
checkpoint_sha="$(jq -sr '[.[] | select(.type == "wrapper.checkpoint.recorded")][0]
  | .sha' "$temp_dir/checkpoint-dirty.stdout")"
checkpoint_msg="$(git -C "$repo" show -s --format=%B "$checkpoint_sha")"
assert_contains "$checkpoint_msg" \
  "Checkpoint: capture work-in-progress for issue 41" \
  "the Checkpoint subject is attributed to the Active issue"
assert_contains "$checkpoint_msg" "GitLoopy-Checkpoint: 41" \
  "the Checkpoint carries the runner trailer"
if printf '%s' "$checkpoint_msg" |
  grep -iEq '(close[sd]?|fix(es|ed)?|resolve[sd]?)[[:space:]]+#[0-9]+'; then
  fail "the Checkpoint message matched a closing keyword"
fi
[[ ! -e "$temp_dir/checkpoint-dirty-closed.log" ]] ||
  fail "the Checkpoint closed an issue"
assert_equal "" "$(git -C "$repo" status --porcelain)" \
  "the worktree is clean after the Checkpoint"
assert_contains \
  "$(git -C "$repo" show "$checkpoint_sha":wip.txt)" \
  "work in progress" "the Checkpoint captured the uncommitted file"
branch="$(git -C "$repo" rev-parse --abbrev-ref HEAD)"
assert_equal \
  "$(git -C "$repo" rev-parse HEAD)" \
  "$(git --git-dir="$remote" rev-parse "refs/heads/$branch")" \
  "the push landed the Checkpoint on the remote"

# A clean tree with one agent commit makes no Checkpoint but still auto-pushes:
# the commit is recorded, no checkpoint event fires, wrapper.push.recorded lands,
# and the remote receives the agent commit.
repo="$temp_dir/agent-commit-push"
fake_bin="$temp_dir/agent-commit-push-bin"
remote="$temp_dir/agent-commit-push-remote.git"
make_real_repo "$repo"
add_fake_remote "$repo" "$remote"
write_turn_tools "$fake_bin"
cp "$temp_dir/github-list.json" "$temp_dir/agent-commit-push-list.json"
export FAKE_GH_LOG="$temp_dir/agent-commit-push-gh.log"
export FAKE_GH_LIST_COUNT="$temp_dir/agent-commit-push-list.count"
export FAKE_GH_LIST_JSON="$temp_dir/agent-commit-push-list.json"
export FAKE_GH_VIEW_DIR="$temp_dir/github-views"
export FAKE_GH_CLOSED="$temp_dir/agent-commit-push-closed.log"
setup_copilot_env "agent-commit-push"
export FAKE_COPILOT_PLAN_DIR="$temp_dir/agent-commit-push-plan"
mkdir -p "$FAKE_COPILOT_PLAN_DIR/1"
cat >"$FAKE_COPILOT_PLAN_DIR/1/1.msg" <<'EOF'
feat: real work

Closes #41
EOF
export FAKE_GH_CLOSE_STATUS=1
if ! run_turn_entrypoint \
  "$repo" "$fake_bin" "$temp_dir/agent-commit-push.stdout" \
  "$temp_dir/agent-commit-push.stderr" 1; then
  fail "agent-commit-push Run did not exit 0: \
$(<"$temp_dir/agent-commit-push.stderr")"
fi
unset FAKE_COPILOT_PLAN_DIR FAKE_GH_CLOSED FAKE_GH_CLOSE_STATUS
jq -se '
  ([.[] | select(.type == "wrapper.commit.recorded") | .subject]
    == ["feat: real work"])
  and ([.[] | select(.type == "wrapper.checkpoint.recorded")] | length == 0)
  and ([.[] | select(.type == "wrapper.push.recorded")] | length == 1)
  and ([.[] | select(.type == "wrapper.strike")] | length == 0)
  and ([.[] | select(.type == "wrapper.auto_close")] | length == 0)
  and ([.[] | select(.type == "wrapper.issue.activated")]
    | length == 1
      and .[0].issue == 41
      and .[0].binding_source == "commit")
  and .[-1].outcome == "iteration_cap"
' "$temp_dir/agent-commit-push.stdout" >/dev/null ||
  fail "a clean agent commit did not push without a Checkpoint"
[[ ! -e "$temp_dir/agent-commit-push-closed.log" ]] ||
  fail "a failed closure was recorded as closed"
branch="$(git -C "$repo" rev-parse --abbrev-ref HEAD)"
assert_equal \
  "$(git -C "$repo" rev-parse HEAD)" \
  "$(git --git-dir="$remote" rev-parse "refs/heads/$branch")" \
  "the push landed the agent commit on the remote"

# Ignored files are never captured: the agent leaves only a .gitignore-matched
# artefact, so the tree is clean under normal ignore rules — no Checkpoint, no
# push, and (no progress) a Strike. The ignored file stays on disk, uncommitted.
repo="$temp_dir/ignored-clean"
fake_bin="$temp_dir/ignored-clean-bin"
make_real_repo "$repo"
printf '*.ignored\n' >>"$repo/.gitignore"
git -C "$repo" commit -q -am "ignore scratch artefacts"
write_turn_tools "$fake_bin"
cp "$temp_dir/github-list.json" "$temp_dir/ignored-clean-list.json"
export FAKE_GH_LOG="$temp_dir/ignored-clean-gh.log"
export FAKE_GH_LIST_COUNT="$temp_dir/ignored-clean-list.count"
export FAKE_GH_LIST_JSON="$temp_dir/ignored-clean-list.json"
export FAKE_GH_VIEW_DIR="$temp_dir/github-views"
export FAKE_GH_CLOSED="$temp_dir/ignored-clean-closed.log"
setup_copilot_env "ignored-clean"
export FAKE_COPILOT_PLAN_DIR="$temp_dir/ignored-clean-plan"
mkdir -p "$FAKE_COPILOT_PLAN_DIR/1"
cat >"$FAKE_COPILOT_PLAN_DIR/1/worktree.sh" <<'EOF'
printf 'ignored noise\n' >scratch.ignored
EOF
pre_head="$(git -C "$repo" rev-parse HEAD)"
if ! run_turn_entrypoint \
  "$repo" "$fake_bin" "$temp_dir/ignored-clean.stdout" \
  "$temp_dir/ignored-clean.stderr" 1; then
  fail "ignored-clean Run did not exit 0: $(<"$temp_dir/ignored-clean.stderr")"
fi
unset FAKE_COPILOT_PLAN_DIR FAKE_GH_CLOSED
jq -se '
  ([.[] | select(.type == "wrapper.checkpoint.recorded")] | length == 0)
  and ([.[] | select(.type == "wrapper.push.recorded")] | length == 0)
  and ([.[] | select(.type == "wrapper.commit.recorded")] | length == 0)
  and ([.[] | select(.type == "wrapper.strike")]
    | length == 1 and all(.strikes == 1 and .outcome == "warn"))
  and .[-1].outcome == "iteration_cap"
' "$temp_dir/ignored-clean.stdout" >/dev/null ||
  fail "an ignored-only worktree was not treated as clean"
assert_equal "$pre_head" "$(git -C "$repo" rev-parse HEAD)" \
  "no commit was authored for an ignored-only change"
[[ -f "$repo/scratch.ignored" ]] ||
  fail "the ignored artefact was removed"
assert_equal "" "$(git -C "$repo" ls-files scratch.ignored)" \
  "the ignored artefact was never committed"

# A local-only repo (no upstream) keeps working: the agent commit is recorded,
# the auto-push fails and warns without aborting, no wrapper.push.recorded lands,
# and the Run still exits 0.
repo="$temp_dir/local-only"
fake_bin="$temp_dir/local-only-bin"
make_real_repo "$repo"
write_turn_tools "$fake_bin"
cp "$temp_dir/github-list.json" "$temp_dir/local-only-list.json"
export FAKE_GH_LOG="$temp_dir/local-only-gh.log"
export FAKE_GH_LIST_COUNT="$temp_dir/local-only-list.count"
export FAKE_GH_LIST_JSON="$temp_dir/local-only-list.json"
export FAKE_GH_VIEW_DIR="$temp_dir/github-views"
export FAKE_GH_CLOSED="$temp_dir/local-only-closed.log"
setup_copilot_env "local-only"
export FAKE_COPILOT_PLAN_DIR="$temp_dir/local-only-plan"
mkdir -p "$FAKE_COPILOT_PLAN_DIR/1"
cat >"$FAKE_COPILOT_PLAN_DIR/1/1.msg" <<'EOF'
feat: local work

Refs #41
EOF
if ! run_turn_entrypoint \
  "$repo" "$fake_bin" "$temp_dir/local-only.stdout" \
  "$temp_dir/local-only.stderr" 1; then
  fail "local-only Run did not exit 0: $(<"$temp_dir/local-only.stderr")"
fi
unset FAKE_COPILOT_PLAN_DIR FAKE_GH_CLOSED
jq -se '
  ([.[] | select(.type == "wrapper.commit.recorded") | .subject]
    == ["feat: local work"])
  and ([.[] | select(.type == "wrapper.push.recorded")] | length == 0)
  and ([.[] | select(.type == "wrapper.checkpoint.recorded")] | length == 0)
  and .[-1].outcome == "iteration_cap"
' "$temp_dir/local-only.stdout" >/dev/null ||
  fail "a local-only push failure did not degrade gracefully"
assert_contains "$(<"$temp_dir/local-only.stderr")" \
  "auto-push failed" "the local-only push failure warned"

# A pathologically slow agent turn is bounded by the resolved send timeout: the
# Orchestrator's built-in watchdog terminates it at ~the bound rather than
# letting a hung agent hang the Iteration forever (issue #112). The terminated
# turn lands no agent commit, so the Iteration is a failed, no-progress turn
# (contract §4/§6) that still completes cleanly at the cap.
repo="$temp_dir/send-timeout"
fake_bin="$temp_dir/send-timeout-bin"
make_real_repo "$repo"
write_turn_tools "$fake_bin"
# Overwrite the shared fake `copilot` with one that sleeps far past the bound and
# dies promptly on SIGTERM (taking its sleep child with it) so nothing lingers.
cat >"$fake_bin/copilot" <<'EOF'
#!/usr/bin/env bash
sleep_child=""
trap 'if [[ -n "$sleep_child" ]]; then kill "$sleep_child" 2>/dev/null || true; fi; exit 143' TERM
printf 'slow copilot: turn started\n' >&2
sleep "${FAKE_COPILOT_SLEEP:-60}" &
sleep_child=$!
wait "$sleep_child"
# Only reached if the turn was never bounded (the pre-fix bug): make it loud.
printf 'slow copilot: turn finished unbounded\n' >&2
EOF
chmod +x "$fake_bin/copilot"
cp "$temp_dir/github-list.json" "$temp_dir/send-timeout-list.json"
export FAKE_GH_LOG="$temp_dir/send-timeout-gh.log"
export FAKE_GH_LIST_COUNT="$temp_dir/send-timeout-list.count"
export FAKE_GH_LIST_JSON="$temp_dir/send-timeout-list.json"
export FAKE_GH_VIEW_DIR="$temp_dir/github-views"
setup_copilot_env "send-timeout"
export GIT_LOOPY_SEND_TIMEOUT_SECONDS=1
export FAKE_COPILOT_SLEEP=60
send_timeout_start=$SECONDS
if ! run_turn_entrypoint \
  "$repo" "$fake_bin" "$temp_dir/send-timeout.stdout" \
  "$temp_dir/send-timeout.stderr" 1; then
  fail "a bounded slow turn must not fail the Run: $(<"$temp_dir/send-timeout.stderr")"
fi
send_timeout_elapsed=$((SECONDS - send_timeout_start))
unset GIT_LOOPY_SEND_TIMEOUT_SECONDS FAKE_COPILOT_SLEEP
((send_timeout_elapsed < 30)) ||
  fail "the slow turn was not bounded (Run took ${send_timeout_elapsed}s, bound 1s)"
send_timeout_stderr="$(<"$temp_dir/send-timeout.stderr")"
assert_contains "$send_timeout_stderr" \
  "copilot turn exceeded the 1s send timeout" \
  "the bounded turn warns that the send timeout fired"
[[ "$send_timeout_stderr" != *"turn finished unbounded"* ]] ||
  fail "the slow turn ran to completion instead of being terminated at the bound"
jq -se '
  ([.[] | select(.type == "wrapper.commit.recorded")] | length == 0)
  and ([.[] | select(.type == "wrapper.strike")] | length == 1)
  and ([.[] | select(.type == "wrapper.strike") | .outcome] == ["warn"])
  and (.[-1].type == "wrapper.run.end")
  and (.[-1].outcome == "iteration_cap")
  and (.[-1].iterations_run == 1)
' "$temp_dir/send-timeout.stdout" >/dev/null ||
  fail "a bounded slow turn was not accounted as a failed, no-progress Iteration"

# The native rollup seam measures lifecycle duration from Bash's monotonic
# SECONDS clock, never by subtracting wall timestamps. Simulate a wall-clock
# adjustment that places authoritative closure before activation in UTC while
# monotonic time continues forward.
(
  # shellcheck source=../lib/orchestrator.sh
  source "$port_dir/lib/orchestrator.sh"
  git_loopy_monotonic_clock_start
  trap git_loopy_monotonic_clock_stop EXIT
  _GIT_LOOPY_ITERATION_STARTED_MONOTONIC="$(
    git_loopy_monotonic_seconds
  )"
  _GIT_LOOPY_ACTIVE_REF=41
  _GIT_LOOPY_ACTIVE_STARTED_AT="2026-05-16T00:00:10.000Z"
  _GIT_LOOPY_ACTIVE_STARTED_MONOTONIC="$_GIT_LOOPY_ITERATION_STARTED_MONOTONIC"
  _GIT_LOOPY_ACTIVE_CLOSED_AT="2026-05-16T00:00:01.000Z"
  while :; do
    _GIT_LOOPY_ACTIVE_CLOSED_MONOTONIC="$(git_loopy_monotonic_seconds)"
    ((_GIT_LOOPY_ACTIVE_CLOSED_MONOTONIC >
      _GIT_LOOPY_ACTIVE_STARTED_MONOTONIC)) && break
    sleep 0.1
  done
  _GIT_LOOPY_ISSUE_FIRST_STARTED_AT[41]="2026-05-16T00:00:10.000Z"
  _GIT_LOOPY_ISSUE_FIRST_STARTED_MONOTONIC[41]="$_GIT_LOOPY_ACTIVE_STARTED_MONOTONIC"
  while [[ "$(git_loopy_monotonic_seconds)" == "$_GIT_LOOPY_ACTIVE_CLOSED_MONOTONIC" ]]; do
    sleep 0.1
  done
  git_loopy_build_iteration_rollup 0 1 0 0
  jq -e '
    .outcome == "closed"
    and .duration_seconds >= 2
    and .issues[0].active_seconds >= 1
    and .issues[0].issue_elapsed_seconds == .issues[0].active_seconds
    and .issues[0].first_started_at == "2026-05-16T00:00:10.000Z"
    and .issues[0].closed_at == "2026-05-16T00:00:01.000Z"
  ' <<<"$GIT_LOOPY_ITERATION_ROLLUP_JSON" >/dev/null
) || fail "wall-clock adjustment changed monotonic shell lifecycle durations"

# The shared TUI helper (PRD #173). Every case below drives the real entrypoint
# against a fake `git-loopy-tui` that records what it was asked to do, so
# discovery, the compatibility probe, transport, mid-Run failure, and teardown
# are observed at the process boundary rather than asserted about in-process.
#
# `FAKE_TUI_STDIN` is what the child actually received; the replay log is what
# the Run recorded. The two must agree, because the contract's "serialize once"
# rule means the live destination and the replay log are the same bytes.
write_fake_tui() {
  local path="$1"
  local label="$2"
  mkdir -p "$(dirname "$path")"
  cat >"$path" <<EOF
#!/usr/bin/env bash
set -uo pipefail
label="$label"
EOF
  cat >>"$path" <<'EOF'
if [[ "${1:-}" == "--schema-version" ]]; then
  version="${FAKE_TUI_VERSION:-0.0.0}"
  minimum="${FAKE_TUI_MIN_SCHEMA:-1}"
  maximum="${FAKE_TUI_MAX_SCHEMA:-1}"
  if [[ -n "${FAKE_TUI_PROBE:-}" ]]; then
    printf '%s\n' "$FAKE_TUI_PROBE"
  else
    printf '{"name": "git-loopy-tui", "version": "%s", ' "$version"
    printf '"min_event_schema_version": %s, ' "$minimum"
    printf '"max_event_schema_version": %s, ' "$maximum"
    printf '"wrapper_contract_version": "1.0"}\n'
  fi
  exit "${FAKE_TUI_PROBE_STATUS:-0}"
fi
printf '%s\n' "$label" >>"$FAKE_TUI_STARTED"
delivered=0
while IFS= read -r line; do
  delivered=$((delivered + 1))
  printf '%s\n' "$line" >>"$FAKE_TUI_STDIN"
  if [[ -n "${FAKE_TUI_EXIT_AFTER:-}" ]] && ((delivered >= FAKE_TUI_EXIT_AFTER)); then
    exit "${FAKE_TUI_EXIT_CODE:-0}"
  fi
done
if [[ -n "${FAKE_TUI_LINGER_SECONDS:-}" ]]; then
  sleep "$FAKE_TUI_LINGER_SECONDS"
fi
exit 0
EOF
  chmod +x "$path"
}

setup_tui_env() {
  local prefix="$1"
  rm -f "$temp_dir/$prefix-tui.stdin" "$temp_dir/$prefix-tui.started"
  export FAKE_TUI_STDIN="$temp_dir/$prefix-tui.stdin"
  export FAKE_TUI_STARTED="$temp_dir/$prefix-tui.started"
  # A clone-local helper is an artifact of this distribution, so contract §16
  # requires exact Release-version equality; the default fake is a well-installed
  # one and a case that wants drift says so explicitly.
  export FAKE_TUI_VERSION="$expected_release_version"
  unset FAKE_TUI_PROBE FAKE_TUI_PROBE_STATUS FAKE_TUI_EXIT_AFTER \
    FAKE_TUI_EXIT_CODE FAKE_TUI_LINGER_SECONDS
  export FAKE_TUI_MIN_SCHEMA=1
  export FAKE_TUI_MAX_SCHEMA=1
}

replay_log_for() {
  local repo="$1"
  local log
  log="$(find "$repo/.git-loopy/logs" -name '*.jsonl' -print -quit 2>/dev/null)"
  [[ -n "$log" ]] || fail "no replay log under $repo"
  printf '%s\n' "$log"
}

# The whole interactive path in one case: the clone-local helper is discovered,
# probed, started, and fed. Because the live destination *is* the child, stdout
# carries no Event stream at all — and the replay log still holds every byte.
tui_repo="$temp_dir/tui-delivery"
tui_bin="$temp_dir/tui-delivery-bin"
make_repo "$tui_repo"
write_fake_tools "$tui_bin"
write_fake_tui "$tui_repo/.git-loopy/bin/git-loopy-tui" "clone-local"
setup_tui_env "delivery"
export FAKE_GH_LOG="$temp_dir/tui-delivery-gh.log"
export FAKE_GH_LIST_COUNT="$temp_dir/tui-delivery-list.count"
export FAKE_GH_LIST_JSON="$temp_dir/empty-list.json"
export FAKE_GH_VIEW_DIR="$temp_dir/empty-views"

set +e
run_entrypoint \
  "$tui_repo" "$tui_bin" \
  "$temp_dir/tui-delivery.stdout" "$temp_dir/tui-delivery.stderr" \
  --interactive
status=$?
set -e
assert_equal "0" "$status" "interactive empty-pool Run exit"
[[ -s "$FAKE_TUI_STARTED" ]] || fail "interactive Run never started the helper"
assert_equal "clone-local" "$(<"$FAKE_TUI_STARTED")" "helper discovery precedence"
[[ ! -s "$temp_dir/tui-delivery.stdout" ]] ||
  fail "interactive Run also wrote the Event stream to stdout"
assert_equal \
  "$(<"$(replay_log_for "$tui_repo")")" \
  "$(<"$FAKE_TUI_STDIN")" \
  "helper stdin and replay log parity"
grep -q '"type": "wrapper.run.end"' "$FAKE_TUI_STDIN" ||
  fail "helper never received the final Run event"

# Discovery falls through to PATH only when the clone has no pinned helper. The
# two fakes label themselves, so "which one ran" is observed rather than assumed.
tui_repo="$temp_dir/tui-path"
tui_bin="$temp_dir/tui-path-bin"
make_repo "$tui_repo"
write_fake_tools "$tui_bin"
write_fake_tui "$tui_bin/git-loopy-tui" "path"
setup_tui_env "path"
export FAKE_GH_LOG="$temp_dir/tui-path-gh.log"
export FAKE_GH_LIST_COUNT="$temp_dir/tui-path-list.count"

set +e
run_entrypoint \
  "$tui_repo" "$tui_bin" \
  "$temp_dir/tui-path.stdout" "$temp_dir/tui-path.stderr" \
  --interactive
status=$?
set -e
assert_equal "0" "$status" "PATH-helper Run exit"
assert_equal "path" "$(<"$FAKE_TUI_STARTED")" "PATH helper discovery"

# Both present: the repository's pinned helper wins, because a clone that pins a
# version means to coordinate exactly with it.
tui_repo="$temp_dir/tui-precedence"
tui_bin="$temp_dir/tui-precedence-bin"
make_repo "$tui_repo"
write_fake_tools "$tui_bin"
write_fake_tui "$tui_bin/git-loopy-tui" "path"
write_fake_tui "$tui_repo/.git-loopy/bin/git-loopy-tui" "clone-local"
setup_tui_env "precedence"
export FAKE_GH_LOG="$temp_dir/tui-precedence-gh.log"
export FAKE_GH_LIST_COUNT="$temp_dir/tui-precedence-list.count"

set +e
run_entrypoint \
  "$tui_repo" "$tui_bin" \
  "$temp_dir/tui-precedence.stdout" "$temp_dir/tui-precedence.stderr" \
  --interactive
status=$?
set -e
assert_equal "0" "$status" "discovery-precedence Run exit"
assert_equal "clone-local" "$(<"$FAKE_TUI_STARTED")" \
  "clone-local helper outranks a helper on PATH"

# The probe is a *gate*, not a formality: a helper that cannot decode this Run's
# Event schema is refused before it is ever handed a byte, so it never gets to
# blank the terminal and then fail.
tui_repo="$temp_dir/tui-incompatible"
tui_bin="$temp_dir/tui-incompatible-bin"
make_repo "$tui_repo"
write_fake_tools "$tui_bin"
write_fake_tui "$tui_repo/.git-loopy/bin/git-loopy-tui" "clone-local"
setup_tui_env "incompatible"
export FAKE_TUI_MIN_SCHEMA=2
export FAKE_TUI_MAX_SCHEMA=3
export FAKE_GH_LOG="$temp_dir/tui-incompatible-gh.log"
export FAKE_GH_LIST_COUNT="$temp_dir/tui-incompatible-list.count"

set +e
run_entrypoint \
  "$tui_repo" "$tui_bin" \
  "$temp_dir/tui-incompatible.stdout" "$temp_dir/tui-incompatible.stderr" \
  --interactive
status=$?
set -e
assert_equal "0" "$status" "incompatible-helper Run exit"
[[ ! -e "$FAKE_TUI_STARTED" ]] ||
  fail "an incompatible helper was started anyway"
assert_contains \
  "$(<"$temp_dir/tui-incompatible.stderr")" \
  "does not support Event schema 1" \
  "incompatible-helper diagnostic"
jq -se '.[-1].type == "wrapper.run.end"' \
  "$temp_dir/tui-incompatible.stdout" >/dev/null ||
  fail "incompatible helper did not fall back to raw JSONL on stdout"

# An explicit request that cannot be met explains itself; the Run continues.
tui_repo="$temp_dir/tui-missing"
tui_bin="$temp_dir/tui-missing-bin"
make_repo "$tui_repo"
write_fake_tools "$tui_bin"
setup_tui_env "missing"
export FAKE_GH_LOG="$temp_dir/tui-missing-gh.log"
export FAKE_GH_LIST_COUNT="$temp_dir/tui-missing-list.count"

set +e
run_entrypoint \
  "$tui_repo" "$tui_bin" \
  "$temp_dir/tui-missing.stdout" "$temp_dir/tui-missing.stderr" \
  --interactive
status=$?
set -e
assert_equal "0" "$status" "missing-helper Run exit"
assert_contains \
  "$(<"$temp_dir/tui-missing.stderr")" \
  "interactive mode was requested" \
  "explicit missing-helper diagnostic"
jq -se '.[-1].type == "wrapper.run.end"' \
  "$temp_dir/tui-missing.stdout" >/dev/null ||
  fail "missing helper did not fall back to raw JSONL on stdout"

# Auto-detection never waits for, or speaks about, a terminal it does not have.
# The suite redirects stdout to a file, so this is a genuine non-TTY Run with a
# perfectly good helper sitting right there.
tui_repo="$temp_dir/tui-auto"
tui_bin="$temp_dir/tui-auto-bin"
make_repo "$tui_repo"
write_fake_tools "$tui_bin"
write_fake_tui "$tui_repo/.git-loopy/bin/git-loopy-tui" "clone-local"
setup_tui_env "auto"
export FAKE_GH_LOG="$temp_dir/tui-auto-gh.log"
export FAKE_GH_LIST_COUNT="$temp_dir/tui-auto-list.count"

set +e
run_entrypoint \
  "$tui_repo" "$tui_bin" \
  "$temp_dir/tui-auto.stdout" "$temp_dir/tui-auto.stderr"
status=$?
set -e
assert_equal "0" "$status" "non-TTY auto-detected Run exit"
[[ ! -e "$FAKE_TUI_STARTED" ]] ||
  fail "a non-TTY Run started the helper"
[[ ! -s "$temp_dir/tui-auto.stderr" ]] ||
  fail "a non-TTY Run warned about an interface nobody asked for"
jq -se '.[-1].type == "wrapper.run.end"' "$temp_dir/tui-auto.stdout" >/dev/null ||
  fail "non-TTY Run did not emit raw JSONL on stdout"

# `GIT_LOOPY_INTERACTIVE` is the middle tier: it outranks TTY auto-detection and
# is outranked by an explicit flag.
tui_repo="$temp_dir/tui-env"
tui_bin="$temp_dir/tui-env-bin"
make_repo "$tui_repo"
write_fake_tools "$tui_bin"
write_fake_tui "$tui_repo/.git-loopy/bin/git-loopy-tui" "clone-local"
setup_tui_env "env"
export FAKE_GH_LOG="$temp_dir/tui-env-gh.log"
export FAKE_GH_LIST_COUNT="$temp_dir/tui-env-list.count"
export GIT_LOOPY_INTERACTIVE=1

set +e
run_entrypoint \
  "$tui_repo" "$tui_bin" \
  "$temp_dir/tui-env.stdout" "$temp_dir/tui-env.stderr"
status=$?
set -e
assert_equal "0" "$status" "environment-selected interactive Run exit"
assert_equal "clone-local" "$(<"$FAKE_TUI_STARTED")" \
  "GIT_LOOPY_INTERACTIVE=1 selects the live interface without a TTY"

setup_tui_env "env-flag-wins"
set +e
run_entrypoint \
  "$tui_repo" "$tui_bin" \
  "$temp_dir/tui-env-flag.stdout" "$temp_dir/tui-env-flag.stderr" \
  --no-interactive
status=$?
set -e
assert_equal "0" "$status" "--no-interactive over GIT_LOOPY_INTERACTIVE=1 exit"
[[ ! -e "$FAKE_TUI_STARTED" ]] ||
  fail "--no-interactive did not outrank GIT_LOOPY_INTERACTIVE=1"
jq -se '.[-1].type == "wrapper.run.end"' \
  "$temp_dir/tui-env-flag.stdout" >/dev/null ||
  fail "--no-interactive did not keep raw JSONL on stdout"

export GIT_LOOPY_INTERACTIVE=0
setup_tui_env "env-off"
set +e
run_entrypoint \
  "$tui_repo" "$tui_bin" \
  "$temp_dir/tui-env-off.stdout" "$temp_dir/tui-env-off.stderr"
status=$?
set -e
unset GIT_LOOPY_INTERACTIVE
assert_equal "0" "$status" "GIT_LOOPY_INTERACTIVE=0 Run exit"
[[ ! -e "$FAKE_TUI_STARTED" ]] ||
  fail "GIT_LOOPY_INTERACTIVE=0 still started the helper"

# Contract §16: a pinned helper is an artifact of this distribution, so a
# Release mismatch there is drift and fails closed. The same mismatch in a helper
# discovered on PATH is a package-manager fact, not drift: the schema probe is
# the compatibility authority, so that Run continues with a warning.
tui_repo="$temp_dir/tui-pinned-drift"
tui_bin="$temp_dir/tui-pinned-drift-bin"
make_repo "$tui_repo"
write_fake_tools "$tui_bin"
write_fake_tui "$tui_repo/.git-loopy/bin/git-loopy-tui" "clone-local"
setup_tui_env "pinned-drift"
export FAKE_TUI_VERSION="0.0.1-not-this-release"
export FAKE_GH_LOG="$temp_dir/tui-pinned-drift-gh.log"
export FAKE_GH_LIST_COUNT="$temp_dir/tui-pinned-drift-list.count"

set +e
run_entrypoint \
  "$tui_repo" "$tui_bin" \
  "$temp_dir/tui-pinned-drift.stdout" "$temp_dir/tui-pinned-drift.stderr" \
  --interactive
status=$?
set -e
assert_equal "0" "$status" "pinned Release-drift Run exit"
[[ ! -e "$FAKE_TUI_STARTED" ]] ||
  fail "a pinned helper from another Release was started"
assert_contains \
  "$(<"$temp_dir/tui-pinned-drift.stderr")" \
  "reinstall it to match this clone" \
  "pinned Release-drift diagnostic"

tui_repo="$temp_dir/tui-external-drift"
tui_bin="$temp_dir/tui-external-drift-bin"
make_repo "$tui_repo"
write_fake_tools "$tui_bin"
write_fake_tui "$tui_bin/git-loopy-tui" "path"
setup_tui_env "external-drift"
export FAKE_TUI_VERSION="0.0.1-another-release"
export FAKE_GH_LOG="$temp_dir/tui-external-drift-gh.log"
export FAKE_GH_LIST_COUNT="$temp_dir/tui-external-drift-list.count"

set +e
run_entrypoint \
  "$tui_repo" "$tui_bin" \
  "$temp_dir/tui-external-drift.stdout" "$temp_dir/tui-external-drift.stderr" \
  --interactive
status=$?
set -e
assert_equal "0" "$status" "external Release-drift Run exit"
assert_equal "path" "$(<"$FAKE_TUI_STARTED")" \
  "an externally discovered helper from another Release still runs"
assert_contains \
  "$(<"$temp_dir/tui-external-drift.stderr")" \
  "0.0.1-another-release" \
  "external Release-drift warning"

# Mid-Run death. The child accepts two Events and quits; from there the Run must
# say so exactly once, put every later Event on stdout as raw JSONL, keep the
# replay log whole, and never start a second child.
tui_repo="$temp_dir/tui-crash"
tui_bin="$temp_dir/tui-crash-bin"
make_repo "$tui_repo"
write_fake_tools "$tui_bin"
write_fake_tui "$tui_repo/.git-loopy/bin/git-loopy-tui" "clone-local"
setup_tui_env "crash"
export FAKE_TUI_EXIT_AFTER=2
export FAKE_TUI_EXIT_CODE=7
export FAKE_GH_LOG="$temp_dir/tui-crash-gh.log"
export FAKE_GH_LIST_COUNT="$temp_dir/tui-crash-list.count"

set +e
run_entrypoint \
  "$tui_repo" "$tui_bin" \
  "$temp_dir/tui-crash.stdout" "$temp_dir/tui-crash.stderr" \
  --interactive
status=$?
set -e
unset FAKE_TUI_EXIT_AFTER FAKE_TUI_EXIT_CODE
assert_equal "0" "$status" "a helper that died mid-Run did not change the Run exit"
assert_equal "1" "$(wc -l <"$FAKE_TUI_STARTED" | tr -d ' ')" \
  "the helper was respawned after it died"
assert_equal \
  "1" \
  "$(grep -c 'continuing with raw JSONL output' "$temp_dir/tui-crash.stderr")" \
  "mid-Run helper failure diagnostic count"
jq -se '.[-1].type == "wrapper.run.end"' "$temp_dir/tui-crash.stdout" >/dev/null ||
  fail "the Run did not fall back to raw JSONL on stdout after the helper died"
crash_replay="$(replay_log_for "$tui_repo")"
jq -se '
  ([.[] | .type] | index("wrapper.run.start")) == 0
  and .[-1].type == "wrapper.run.end"
' "$crash_replay" >/dev/null ||
  fail "the replay log lost Events when the helper died"
assert_equal "2" "$(wc -l <"$FAKE_TUI_STDIN" | tr -d ' ')" \
  "Events delivered before the helper died"
# Delivery happened once: an Event the child accepted is not replayed onto
# stdout by the fallback, and an Event the child never saw is.
crash_first_delivered="$(head -n 1 "$FAKE_TUI_STDIN")"
if grep -qF "$crash_first_delivered" "$temp_dir/tui-crash.stdout"; then
  fail "an Event delivered to the helper was repeated on stdout"
fi
if grep -qF "$crash_first_delivered" "$crash_replay"; then
  :
else
  fail "an Event delivered to the helper is missing from the replay log"
fi

# Teardown is bounded. A child that ignores EOF is reaped rather than waited on
# forever, and the Run's own exit code survives.
tui_repo="$temp_dir/tui-linger"
tui_bin="$temp_dir/tui-linger-bin"
make_repo "$tui_repo"
write_fake_tools "$tui_bin"
write_fake_tui "$tui_repo/.git-loopy/bin/git-loopy-tui" "clone-local"
setup_tui_env "linger"
export FAKE_TUI_LINGER_SECONDS=30
export GIT_LOOPY_TUI_GRACE_SECONDS=1
export FAKE_GH_LOG="$temp_dir/tui-linger-gh.log"
export FAKE_GH_LIST_COUNT="$temp_dir/tui-linger-list.count"

linger_started="$(date +%s)"
set +e
run_entrypoint \
  "$tui_repo" "$tui_bin" \
  "$temp_dir/tui-linger.stdout" "$temp_dir/tui-linger.stderr" \
  --interactive
status=$?
set -e
linger_elapsed=$(($(date +%s) - linger_started))
unset FAKE_TUI_LINGER_SECONDS GIT_LOOPY_TUI_GRACE_SECONDS
assert_equal "0" "$status" "lingering-helper Run exit"
((linger_elapsed < 20)) ||
  fail "the Run waited on an overstaying helper for ${linger_elapsed}s"

# The strongest exit-code guard: a Run that ends *stuck* under a live interface
# still exits 1. A teardown that swallowed the Run's status would pass every
# case above and fail only here.
repo="$temp_dir/tui-stuck"
fake_bin="$temp_dir/tui-stuck-bin"
make_real_repo "$repo"
write_turn_tools "$fake_bin"
write_fake_tui "$repo/.git-loopy/bin/git-loopy-tui" "clone-local"
setup_tui_env "stuck"
cp "$temp_dir/github-list.json" "$temp_dir/tui-stuck-list.json"
export FAKE_GH_LOG="$temp_dir/tui-stuck-gh.log"
export FAKE_GH_LIST_COUNT="$temp_dir/tui-stuck-list.count"
export FAKE_GH_LIST_JSON="$temp_dir/tui-stuck-list.json"
export FAKE_GH_VIEW_DIR="$temp_dir/github-views"
setup_copilot_env "tui-stuck"
export FAKE_COPILOT_COMMITS=0
export FAKE_COPILOT_SIGNALS="$temp_dir/tui-stuck-copilot.signals"
rm -f "$FAKE_COPILOT_SIGNALS"

set +e
run_turn_entrypoint \
  "$repo" "$fake_bin" \
  "$temp_dir/tui-stuck.stdout" "$temp_dir/tui-stuck.stderr" \
  --interactive 0
status=$?
set -e
unset FAKE_COPILOT_COMMITS
assert_equal "1" "$status" "a stuck interactive Run still exits 1"
assert_equal "clone-local" "$(<"$FAKE_TUI_STARTED")" \
  "the stuck Run ran under the live interface"
[[ -e "$FAKE_COPILOT_SIGNALS" ]] ||
  fail "the interactive stuck Run never reached the agent process"
[[ ! -s "$FAKE_COPILOT_SIGNALS" ]] ||
  fail "the live interface left SIGPIPE ignored for the agent process: $(<"$FAKE_COPILOT_SIGNALS")"
unset FAKE_COPILOT_SIGNALS
jq -se '
  .[-1].type == "wrapper.run.end" and .[-1].outcome == "stuck"
' "$FAKE_TUI_STDIN" >/dev/null ||
  fail "the helper did not receive the stuck Run's final Event"

# An execute-frontier Run is a separate, noninteractive lifecycle: it replaces
# Pool collection with the native Continuation Reconciliation rather than
# treating Dispatches as ordinary Iterations. This uses the public entrypoint
# with only the GitHub boundary scripted, so a regression cannot hide behind an
# in-process Continuation helper.
write_frontier_tools() {
  local bin_dir="$1"
  write_fake_tools "$bin_dir"
  cat >"$bin_dir/copilot" <<'EOF'
#!/usr/bin/env bash
printf 'copilot must not run during an execute-frontier lifecycle\n' >&2
exit 91
EOF
  cat >"$bin_dir/gh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >>"$FAKE_GH_LOG"
case "${1-} ${2-}" in
  "auth status")
    exit 0
    ;;
  "repo view")
    printf '{"owner":{"login":"octo"},"name":"example","defaultBranchRef":{"name":"main"}}\n'
    ;;
  "issue list")
    [[ "$*" == *"--label git-loopy-continuation"* ]] || {
      printf 'ordinary Pool collection is forbidden in execute-frontier mode\n' >&2
      exit 92
    }
    printf '[]\n'
    ;;
  *)
    printf 'unexpected gh invocation: %s\n' "$*" >&2
    exit 92
    ;;
esac
EOF
  chmod +x "$bin_dir/copilot" "$bin_dir/gh"
}

repo="$temp_dir/execute-frontier"
fake_bin="$temp_dir/execute-frontier-bin"
make_repo "$repo"
write_frontier_tools "$fake_bin"
export FAKE_GH_LOG="$temp_dir/execute-frontier-gh.log"
export FAKE_GH_LIST_COUNT="$temp_dir/execute-frontier-list.count"
export FAKE_GH_LIST_JSON="$temp_dir/empty-list.json"
export FAKE_GH_VIEW_DIR="$temp_dir/empty-views"
export GIT_LOOPY_CONTINUATION_MODE="execute-frontier"
export GIT_LOOPY_CONTINUATION_TRUSTED_PRODUCERS="planner"
export GIT_LOOPY_CONTINUATION_ACTOR="runner"
export GIT_LOOPY_CONTINUATION_REPOSITORIES="octo/example"
export GIT_LOOPY_CONTINUATION_EFFECT_SCOPES="tracker-write"

if ! run_entrypoint \
  "$repo" "$fake_bin" \
  "$temp_dir/execute-frontier.stdout" "$temp_dir/execute-frontier.stderr"; then
  fail "execute-frontier lifecycle did not exit cleanly: $(<"$temp_dir/execute-frontier.stderr")"
fi
unset GIT_LOOPY_CONTINUATION_MODE GIT_LOOPY_CONTINUATION_TRUSTED_PRODUCERS \
  GIT_LOOPY_CONTINUATION_ACTOR GIT_LOOPY_CONTINUATION_REPOSITORIES \
  GIT_LOOPY_CONTINUATION_EFFECT_SCOPES

jq -se '
  [.[].type] == [
    "wrapper.run.start",
    "wrapper.continuation.stopped",
    "wrapper.run.end"
  ]
  and .[1].iter == null
  and .[1].mode == "execute-frontier"
  and .[1].repository == "octo/example"
  and .[1].performer == "runner"
  and .[1].reason == "frontier-drained"
  and .[1].successor_executed == false
  and .[2].outcome == "empty_pool"
  and .[2].iterations_run == 0
' "$temp_dir/execute-frontier.stdout" >/dev/null ||
  fail "execute-frontier did not use the isolated Continuation lifecycle"
[[ "$(<"$FAKE_GH_LOG")" == *"issue list --repo octo/example --state all --label git-loopy-continuation"* ]] ||
  fail "execute-frontier did not reconcile its configured repository"

# Global and project config remain distinct inputs to native authority
# resolution. Their intersections select the one shared repository; the
# project does not silently replace the global ceiling.
repo="$temp_dir/execute-frontier-config"
fake_bin="$temp_dir/execute-frontier-config-bin"
make_repo "$repo"
write_frontier_tools "$fake_bin"
mkdir -p "$repo/xdg/git-loopy"
cat >"$repo/xdg/git-loopy/config.toml" <<'EOF'
continuation_mode = "execute-frontier"
continuation_trusted_producers = ["planner", "other"]
continuation_actor = "runner"
continuation_maintainers = ["ada", "grace"]
continuation_repositories = ["octo/example", "octo/other"]
continuation_targets = []
continuation_action_kinds = []
continuation_instruction_modes = ["skill"]
continuation_effect_scopes = ["tracker-write"]
EOF
cat >"$repo/git-loopy/config.toml" <<'EOF'
continuation_mode = "execute-frontier"
continuation_trusted_producers = ["planner"]
continuation_maintainers = ["ada"]
continuation_repositories = ["octo/example"]
continuation_targets = []
continuation_action_kinds = []
continuation_instruction_modes = ["skill"]
continuation_effect_scopes = ["tracker-write"]
EOF
export FAKE_GH_LOG="$temp_dir/execute-frontier-config-gh.log"
export FAKE_GH_LIST_COUNT="$temp_dir/execute-frontier-config-list.count"
export FAKE_GH_LIST_JSON="$temp_dir/empty-list.json"
export FAKE_GH_VIEW_DIR="$temp_dir/empty-views"

if ! run_entrypoint \
  "$repo" "$fake_bin" \
  "$temp_dir/execute-frontier-config.stdout" \
  "$temp_dir/execute-frontier-config.stderr"; then
  fail "configured execute-frontier lifecycle failed: $(<"$temp_dir/execute-frontier-config.stderr")"
fi
jq -se '
  [.[].type] == [
    "wrapper.run.start",
    "wrapper.continuation.stopped",
    "wrapper.run.end"
  ]
  and .[1].repository == "octo/example"
  and .[1].performer == "runner"
' "$temp_dir/execute-frontier-config.stdout" >/dev/null ||
  fail "global and project Continuation sources were not narrowed natively"

# A ready frozen Action is bound to exactly one noninteractive Copilot process.
# The shared Automation fixture drives the real shell Reconciliation three
# times: freeze, authorize, then observe the already-dispatched member drain.
write_frontier_dispatch_tools() {
  local bin_dir="$1"
  write_frontier_tools "$bin_dir"
  cp "$script_dir/scripted-github.sh" "$bin_dir/scripted-github"
  cat >"$bin_dir/gh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
case "${1-} ${2-}" in
  "auth status")
    printf '%s\n' "$*" >>"$FAKE_GH_LOG"
    exit 0
    ;;
  "repo view")
    printf '%s\n' "$*" >>"$FAKE_GH_LOG"
    printf '{"owner":{"login":"octo"},"name":"example","defaultBranchRef":{"name":"main"}}\n'
    exit 0
    ;;
  *)
    exec "$(dirname "$0")/scripted-github" "$@"
    ;;
esac
EOF
  cat >"$bin_dir/copilot" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
calls=0
[[ -f "$FAKE_COPILOT_CALLS" ]] && calls="$(<"$FAKE_COPILOT_CALLS")"
printf '%s' "$((calls + 1))" >"$FAKE_COPILOT_CALLS"
printf '%s\n' "$*" >"$FAKE_COPILOT_FRONTIER_ARGS"
EOF
  chmod +x "$bin_dir/gh" "$bin_dir/copilot" "$bin_dir/scripted-github"
}

repo="$temp_dir/execute-frontier-dispatch"
fake_bin="$temp_dir/execute-frontier-dispatch-bin"
make_repo "$repo"
write_frontier_dispatch_tools "$fake_bin"
export FAKE_GH_LOG="$temp_dir/execute-frontier-dispatch-gh.log"
export FAKE_GH_LIST_COUNT="$temp_dir/execute-frontier-dispatch-list.count"
export FAKE_GH_LIST_JSON="$temp_dir/empty-list.json"
export FAKE_GH_VIEW_DIR="$temp_dir/empty-views"
export FAKE_COPILOT_CALLS="$temp_dir/execute-frontier-dispatch-copilot.calls"
export FAKE_COPILOT_FRONTIER_ARGS="$temp_dir/execute-frontier-dispatch-copilot.args"
scripted_github_log="$temp_dir/execute-frontier-dispatch-scripted.log"
scripted_github_script="$temp_dir/execute-frontier-dispatch-script.json"
scripted_github_state="$temp_dir/execute-frontier-dispatch-script.state"
export GIT_LOOPY_SCRIPTED_GITHUB_LOG="$scripted_github_log"
export GIT_LOOPY_SCRIPTED_GITHUB_SCRIPT="$scripted_github_script"
export GIT_LOOPY_SCRIPTED_GITHUB_STATE="$scripted_github_state"
jq '
  [.scenarios[]
   | select(.id == "automation-binds-one-dispatch")
   | .github_script[]] as $steps
  | $steps + $steps + $steps
' "$port_dir/../conformance/continuation-scenarios.json" \
  >"$GIT_LOOPY_SCRIPTED_GITHUB_SCRIPT"
export GIT_LOOPY_CONTINUATION_MODE="execute-frontier"
export GIT_LOOPY_CONTINUATION_TRUSTED_PRODUCERS="planner"
export GIT_LOOPY_CONTINUATION_ACTOR="runner"
export GIT_LOOPY_CONTINUATION_REPOSITORIES="octo/example"
export GIT_LOOPY_CONTINUATION_EFFECT_SCOPES="tracker-write"

if ! run_entrypoint \
  "$repo" "$fake_bin" \
  "$temp_dir/execute-frontier-dispatch.stdout" \
  "$temp_dir/execute-frontier-dispatch.stderr"; then
  fail "execute-frontier dispatch failed: $(<"$temp_dir/execute-frontier-dispatch.stderr")"
fi
unset GIT_LOOPY_CONTINUATION_MODE GIT_LOOPY_CONTINUATION_TRUSTED_PRODUCERS \
  GIT_LOOPY_CONTINUATION_ACTOR GIT_LOOPY_CONTINUATION_REPOSITORIES \
  GIT_LOOPY_CONTINUATION_EFFECT_SCOPES GIT_LOOPY_SCRIPTED_GITHUB_LOG \
  GIT_LOOPY_SCRIPTED_GITHUB_SCRIPT GIT_LOOPY_SCRIPTED_GITHUB_STATE

assert_equal "1" "$(<"$FAKE_COPILOT_CALLS")" \
  "one Dispatch creates one Copilot process"
jq -se '
  [.[].type] == [
    "wrapper.run.start",
    "wrapper.continuation_dispatch.started",
    "wrapper.continuation_dispatch.ended",
    "wrapper.continuation.stopped",
    "wrapper.run.end"
  ]
  and .[1].iter == null
  and .[1].mode == "execute-frontier"
  and .[1].instruction_mode == "skill"
  and .[1].noninteractive == true
  and .[2].outcome == "complete"
  and .[2].boundary == false
  and .[2].evidence_recorded == false
  and .[3].reason == "frontier-drained"
  and .[3].dispatched == [.[1].action_identity]
  and .[3].successor_executed == false
  and ([.[1], .[2], .[3]] | tostring
       | contains("/to-spec") | not)
  and ([.[1], .[2], .[3]] | tostring
       | contains("planner") | not)
  and .[4].outcome == "empty_pool"
  and .[4].iterations_run == 0
' "$temp_dir/execute-frontier-dispatch.stdout" >/dev/null ||
  fail "execute-frontier Dispatch Events drifted or leaked an Instruction"
assert_equal "6" "$(<"$scripted_github_state")" \
  "execute-frontier re-reconciles after its Dispatch"

# A denied Skill is not honestly available to the Performer. The native
# posture therefore stops at performer-ineligible before a session can be
# created with a contradictory `--deny-tool skill(...)` option.
repo="$temp_dir/execute-frontier-denied-skill"
fake_bin="$temp_dir/execute-frontier-denied-skill-bin"
make_repo "$repo"
write_frontier_dispatch_tools "$fake_bin"
export FAKE_GH_LOG="$temp_dir/execute-frontier-denied-skill-gh.log"
export FAKE_GH_LIST_COUNT="$temp_dir/execute-frontier-denied-skill-list.count"
export FAKE_GH_LIST_JSON="$temp_dir/empty-list.json"
export FAKE_GH_VIEW_DIR="$temp_dir/empty-views"
export FAKE_COPILOT_CALLS="$temp_dir/execute-frontier-denied-skill-copilot.calls"
export FAKE_COPILOT_FRONTIER_ARGS="$temp_dir/execute-frontier-denied-skill-copilot.args"
scripted_github_log="$temp_dir/execute-frontier-denied-skill-scripted.log"
scripted_github_script="$temp_dir/execute-frontier-denied-skill-script.json"
scripted_github_state="$temp_dir/execute-frontier-denied-skill-script.state"
export GIT_LOOPY_SCRIPTED_GITHUB_LOG="$scripted_github_log"
export GIT_LOOPY_SCRIPTED_GITHUB_SCRIPT="$scripted_github_script"
export GIT_LOOPY_SCRIPTED_GITHUB_STATE="$scripted_github_state"
jq '
  [.scenarios[]
   | select(.id == "automation-binds-one-dispatch")
   | .github_script[]] as $steps
  | $steps + $steps
' "$port_dir/../conformance/continuation-scenarios.json" \
  >"$GIT_LOOPY_SCRIPTED_GITHUB_SCRIPT"
export GIT_LOOPY_CONTINUATION_MODE="execute-frontier"
export GIT_LOOPY_CONTINUATION_TRUSTED_PRODUCERS="planner"
export GIT_LOOPY_CONTINUATION_ACTOR="runner"
export GIT_LOOPY_CONTINUATION_REPOSITORIES="octo/example"
export GIT_LOOPY_CONTINUATION_EFFECT_SCOPES="tracker-write"
export GIT_LOOPY_DENY_SKILLS="to-spec"

if ! run_entrypoint \
  "$repo" "$fake_bin" \
  "$temp_dir/execute-frontier-denied-skill.stdout" \
  "$temp_dir/execute-frontier-denied-skill.stderr"; then
  fail "denied-skill execute-frontier lifecycle failed: $(<"$temp_dir/execute-frontier-denied-skill.stderr")"
fi
unset GIT_LOOPY_CONTINUATION_MODE GIT_LOOPY_CONTINUATION_TRUSTED_PRODUCERS \
  GIT_LOOPY_CONTINUATION_ACTOR GIT_LOOPY_CONTINUATION_REPOSITORIES \
  GIT_LOOPY_CONTINUATION_EFFECT_SCOPES GIT_LOOPY_DENY_SKILLS \
  GIT_LOOPY_SCRIPTED_GITHUB_LOG GIT_LOOPY_SCRIPTED_GITHUB_SCRIPT \
  GIT_LOOPY_SCRIPTED_GITHUB_STATE

[[ ! -e "$FAKE_COPILOT_CALLS" ]] ||
  fail "a denied Skill still created a Continuation Dispatch session"
jq -se '
  [.[].type] == [
    "wrapper.run.start",
    "wrapper.continuation.stopped",
    "wrapper.run.end"
  ]
  and .[1].reason == "performer-ineligible"
  and .[1].successor_executed == false
  and .[2].outcome == "empty_pool"
' "$temp_dir/execute-frontier-denied-skill.stdout" >/dev/null ||
  fail "a denied Skill was not excluded from the frozen Performer posture"
assert_equal "4" "$(<"$scripted_github_state")" \
  "denied Skill runs no dispatch before its stop"

printf 'shell Orchestrator boundary: ok\n'
