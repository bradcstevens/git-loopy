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
real_jq_dir="$(dirname "$(command -v jq)")"
bash_bin="$(command -v bash)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

fail() {
  printf 'FAIL: %s\n' "$*" >&2
  exit 1
}

mkdir -p "$tmp/bin"
cat >"$tmp/bin/gh" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"$GIT_LOOPY_SCRIPTED_GITHUB_LOG"
exit 97
EOF
chmod +x "$tmp/bin/gh"

manifest="$(jq -c '.capability_manifest' "$fixture")"
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
  : >"$github_log"
  set +e
  if [[ "$request_source" == "stdin" ]]; then
    printf '%s' "$request_content" |
      PATH="$tmp/bin:$real_jq_dir:/usr/bin:/bin" \
      GIT_LOOPY_SCRIPTED_GITHUB_LOG="$github_log" \
      "$bash_bin" "$entrypoint" "${arguments[@]}" \
        >"$stdout_path" 2>"$stderr_path"
    status="${PIPESTATUS[1]}"
  else
    PATH="$tmp/bin:$real_jq_dir:/usr/bin:/bin" \
      GIT_LOOPY_SCRIPTED_GITHUB_LOG="$github_log" \
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
    if [[ "$expected_json" == *'"$fixture":"capability_manifest"'* ]]; then
      expected_json="$(jq -cn --argjson manifest "$manifest" \
        '{ok:true,capabilities:$manifest}')"
    fi
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
done < <(jq -c '.scenarios[]' "$fixture")

printf 'shell Continuation conformance: ok\n'
