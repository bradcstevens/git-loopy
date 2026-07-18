#!/usr/bin/env bash

set -euo pipefail

if ((BASH_VERSINFO[0] < 4)); then
  printf 'Bash 4+ is required (found %s).\n' "$BASH_VERSION" >&2
  exit 1
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
port_dir="$(cd "$script_dir/.." && pwd)"
entrypoint="$port_dir/git-loopy.sh"
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
calls=0
[[ -f "$FAKE_COPILOT_CALLS" ]] && calls="$(<"$FAKE_COPILOT_CALLS")"
printf '%s' "$((calls + 1))" >"$FAKE_COPILOT_CALLS"
# Emit on stdout to prove the agent stream is routed away from the JSONL
# Event stream (the Orchestrator sends it to stderr).
printf 'copilot agent stream marker\n'
commits="${FAKE_COPILOT_COMMITS:-0}"
i=0
while ((i < commits)); do
  git commit -q --allow-empty -m "agent: work $((i + 1))"
  i=$((i + 1))
done
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
  git -C "$root" commit -q --allow-empty -m "initial commit"
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
jq -se '
  .[0].issue_source == "github"
  and .[2].issues == []
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
  GIT_LOOPY_DENY_SKILLS FAKE_COPILOT_COMMITS
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

# The agent's own output streams to stderr, never onto the JSONL Event stream.
assert_contains "$(<"$temp_dir/github-cap.stderr")" \
  "copilot agent stream marker" \
  "agent output streams to stderr"
[[ "$(<"$temp_dir/github-cap.stdout")" != *"copilot agent stream marker"* ]] ||
  fail "agent output polluted the JSONL Event stream"
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
    "wrapper.afk_ready.collected",
    "wrapper.commit.recorded",
    "wrapper.commit.recorded",
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

printf 'shell Orchestrator boundary: ok\n'
