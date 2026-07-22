#!/usr/bin/env bash

set -euo pipefail

if ((BASH_VERSINFO[0] < 4)); then
  printf 'Bash 4+ is required (found %s).\n' "$BASH_VERSION" >&2
  exit 1
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
port_dir="$(cd "$script_dir/.." && pwd)"
fixture="$port_dir/../conformance/continuation-scenarios.json"
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

while IFS= read -r scenario; do
  id="$(jq -r '.id' <<<"$scenario")"
  mapfile -d '' -t arguments < <(jq -j '.arguments[] + "\u0000"' <<<"$scenario")
  request_source="$(jq -r '.request.source // "none"' <<<"$scenario")"
  request_content=""
  if [[ "$request_source" != "none" ]]; then
    if jq -e '.request | has("base64")' <<<"$scenario" >/dev/null; then
      request_content=""
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

  if jq -e '.expected.stdout == null' <<<"$scenario" >/dev/null; then
    [[ ! -s "$stdout_path" ]] || fail "$id unexpectedly wrote stdout"
  else
    expected_json="$(jq -c '.expected.stdout' <<<"$scenario")"
    actual_json="$(jq -c . "$stdout_path")" ||
      fail "$id stdout is not one JSON object"
    [[ "$actual_json" == "$expected_json" ]] ||
      fail "$id stdout"$'\n'"expected: $expected_json"$'\n'"actual:   $actual_json"
    [[ "$(wc -l <"$stdout_path" | tr -d ' ')" == "1" ]] ||
      fail "$id stdout is not exactly one line"
  fi

  stderr_needle="$(jq -r '.expected.stderr_contains // ""' <<<"$scenario")"
  if [[ -z "$stderr_needle" ]]; then
    [[ ! -s "$stderr_path" ]] || fail "$id unexpectedly wrote stderr"
  else
    grep -Fi -- "$stderr_needle" "$stderr_path" >/dev/null ||
      fail "$id stderr does not contain: $stderr_needle"
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

printf 'shell Continuation conformance: ok\n'
