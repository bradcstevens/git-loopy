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

printf 'shell Orchestrator conformance: ok\n'
