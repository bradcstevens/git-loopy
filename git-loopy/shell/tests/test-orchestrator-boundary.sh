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

repo="$temp_dir/github-cap"
fake_bin="$temp_dir/github-cap-bin"
make_repo "$repo"
write_fake_tools "$fake_bin"
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
  "comments": []
}
EOF
export FAKE_GH_LOG="$temp_dir/github-cap-gh.log"
export FAKE_GH_LIST_COUNT="$temp_dir/github-cap-list.count"
export FAKE_GH_LIST_JSON="$temp_dir/github-list.json"
export FAKE_GH_VIEW_DIR="$temp_dir/github-views"

if ! run_entrypoint \
  "$repo" "$fake_bin" "$temp_dir/github-cap.stdout" \
  "$temp_dir/github-cap.stderr" 2; then
  fail "bounded discovery Run did not exit 0: $(<"$temp_dir/github-cap.stderr")"
fi
assert_equal "2" "$(<"$FAKE_GH_LIST_COUNT")" "Pool is rebuilt each Iteration"
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
' "$temp_dir/github-cap.stdout" >/dev/null ||
  fail "bounded discovery events did not carry the filtered Pool"
if grep -q '^issue view 42 ' "$FAKE_GH_LOG"; then
  fail "ineligible issue was enriched after the cheap discriminator pass"
fi

rm -f "$FAKE_GH_LIST_COUNT"
if ! run_entrypoint \
  "$repo" "$fake_bin" "$temp_dir/github-default.stdout" \
  "$temp_dir/github-default.stderr"; then
  fail "default discovery Run did not exit 0: $(<"$temp_dir/github-default.stderr")"
fi
assert_equal "1" "$(<"$FAKE_GH_LIST_COUNT")" "default discovery count"
jq -se '
  .[-1].type == "wrapper.run.end"
  and .[-1].outcome == "pool_discovered"
  and .[-1].iterations_run == 1
' "$temp_dir/github-default.stdout" >/dev/null ||
  fail "default non-empty discovery outcome drifted"

repo="$temp_dir/prds"
fake_bin="$temp_dir/prds-bin"
make_repo "$repo"
write_fake_tools "$fake_bin"
mkdir -p "$repo/prds/feature/done"
mkdir -p "$temp_dir/outside-prds"
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

if ! run_entrypoint \
  "$repo" "$fake_bin" "$temp_dir/prds.stdout" "$temp_dir/prds.stderr" \
  1 --issue-source prds; then
  fail "local-PRD discovery did not exit 0: $(<"$temp_dir/prds.stderr")"
fi
unset GIT_LOOPY_ISSUE_SOURCE
jq -se '
  .[0].issue_source == "prds"
  and (
    [.[] | select(.type == "wrapper.afk_ready.collected")][0].issues
    == ["prds/feature/001-ready.md"]
  )
  and .[-1].outcome == "iteration_cap"
' "$temp_dir/prds.stdout" >/dev/null ||
  fail "local-PRD collection or CLI precedence drifted"
[[ ! -e "$FAKE_GH_LOG" ]] || fail "PRDs mode invoked gh"

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
