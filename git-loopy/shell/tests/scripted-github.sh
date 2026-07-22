#!/usr/bin/env bash

set -euo pipefail

: "${GIT_LOOPY_SCRIPTED_GITHUB_LOG:?}"
: "${GIT_LOOPY_SCRIPTED_GITHUB_SCRIPT:?}"
: "${GIT_LOOPY_SCRIPTED_GITHUB_STATE:?}"

command="$*"
printf '%s\n' "$command" >>"$GIT_LOOPY_SCRIPTED_GITHUB_LOG"

index=0
if [[ -f "$GIT_LOOPY_SCRIPTED_GITHUB_STATE" ]]; then
  index="$(<"$GIT_LOOPY_SCRIPTED_GITHUB_STATE")"
fi
script_length="$(jq 'length' "$GIT_LOOPY_SCRIPTED_GITHUB_SCRIPT")"
if ((index >= script_length)); then
  printf 'unlisted GitHub call: %s\n' "$command" >&2
  exit 98
fi

step="$(jq -c --argjson index "$index" '.[$index]' \
  "$GIT_LOOPY_SCRIPTED_GITHUB_SCRIPT")"
expected_command="$(jq -r '.command' <<<"$step")"
if [[ "$command" != "$expected_command" ]]; then
  printf 'expected GitHub call %q, got %q\n' \
    "$expected_command" "$command" >&2
  exit 98
fi

if jq -e 'has("expected_stdin_json")' <<<"$step" >/dev/null; then
  stdin_file="$GIT_LOOPY_SCRIPTED_GITHUB_STATE.stdin.$$"
  trap 'rm -f "$stdin_file"' EXIT
  cat >"$stdin_file"
  actual_stdin="$(jq -c . "$stdin_file" 2>/dev/null)" || {
    printf 'GitHub call stdin was not valid JSON\n' >&2
    exit 98
  }
  expected_stdin="$(jq -c '.expected_stdin_json' <<<"$step")"
  if [[ "$actual_stdin" != "$expected_stdin" ]]; then
    printf 'GitHub call stdin did not match fixture\n' >&2
    exit 98
  fi
elif jq -e 'has("expected_stdin")' <<<"$step" >/dev/null; then
  stdin_file="$GIT_LOOPY_SCRIPTED_GITHUB_STATE.stdin.$$"
  trap 'rm -f "$stdin_file"' EXIT
  cat >"$stdin_file"
  expected_stdin="$(jq -r '.expected_stdin' <<<"$step")"
  if [[ "$(<"$stdin_file")" != "$expected_stdin" ]]; then
    printf 'GitHub call stdin did not match fixture\n' >&2
    exit 98
  fi
fi

printf '%s' "$((index + 1))" >"$GIT_LOOPY_SCRIPTED_GITHUB_STATE"
if jq -e 'has("stdout_json")' <<<"$step" >/dev/null; then
  jq -c '.stdout_json' <<<"$step"
else
  jq -j '.stdout // ""' <<<"$step"
fi
jq -j '.stderr // ""' <<<"$step" >&2
exit "$(jq -r '.exit_code' <<<"$step")"
