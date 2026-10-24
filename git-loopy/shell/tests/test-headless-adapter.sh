#!/usr/bin/env bash
set -euo pipefail
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
port_dir="$(cd "$script_dir/.." && pwd)"
bash_bin="$(command -v bash)"
temp_dir="$(mktemp -d)"
cleanup() {
  local pid
  if [[ -f "$temp_dir/pids" ]]; then
    for pid in $(<"$temp_dir/pids"); do kill -KILL "$pid" 2>/dev/null || true; done
  fi
  rm -rf "$temp_dir"
}
trap cleanup EXIT
fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }

mkdir -p "$temp_dir/bin" "$temp_dir/repo/docs/agents" "$temp_dir/repo/git-loopy"
printf '# Tracker\n' >"$temp_dir/repo/docs/agents/issue-tracker.md"
printf '# Project prompt\n' >"$temp_dir/repo/git-loopy/PROMPT.md"
cp "$script_dir/scripted-copilot.sh" "$temp_dir/bin/copilot"
cat >"$temp_dir/bin/git" <<'EOF'
#!/usr/bin/env bash
[[ "${1:-}" != "-C" ]] || shift 2
case "$*" in
  "rev-parse --show-toplevel") printf '%s\n' "$FAKE_REPO_ROOT" ;;
  "rev-parse HEAD") printf '0123456789abcdef\n' ;;
  log* | "status --porcelain") ;;
  *) printf 'unexpected git: %s\n' "$*" >&2; exit 90 ;;
esac
EOF
cat >"$temp_dir/bin/gh" <<'EOF'
#!/usr/bin/env bash
case "$*" in
  --version) printf 'gh version 2.94.0\n' ;;
  "auth status") ;;
  "repo view"*) printf '{"owner":{"login":"example"},"name":"repo","defaultBranchRef":{"name":"main"}}\n' ;;
  "issue list"*) jq -c '[.]' "$FAKE_ISSUE" ;;
  "issue view"*) cat "$FAKE_ISSUE" ;;
  *) printf 'unexpected gh: %s\n' "$*" >&2; exit 91 ;;
esac
EOF
chmod +x "$temp_dir/bin/"*
export FAKE_ISSUE="$temp_dir/issue.json"
cat >"$FAKE_ISSUE" <<'EOF'
{"number":41,"title":"Adapter","body":"## What to build\nOne turn.\n## Acceptance criteria\n- Done.","state":"OPEN","labels":[{"name":"ready-for-agent"}],"comments":[],"blockedBy":{"totalCount":0,"nodes":[]},"closedByPullRequestsReferences":[]}
EOF
export FAKE_REPO_ROOT="$temp_dir/repo"
export FAKE_COPILOT_FLAGS="$temp_dir/flags"
export FAKE_COPILOT_CALLS="$temp_dir/calls"
export FAKE_COPILOT_PROMPT="$temp_dir/prompt"
export FAKE_COPILOT_CONFIG="$temp_dir/config"
export FAKE_COPILOT_REQUESTS="$temp_dir/requests"
export FAKE_COPILOT_PIDS="$temp_dir/pids"
assert_reclaimed() {
  local pid state
  [[ -s "$FAKE_COPILOT_PIDS" ]] || fail "server process was not observed"
  for pid in $(<"$FAKE_COPILOT_PIDS"); do
    state="$(ps -o stat= -p "$pid")" || continue
    [[ "$state" == *Z* ]] || fail "Copilot process $pid outlived the Run ($state)"
  done
  : >"$FAKE_COPILOT_PIDS"
}
run() {
  (
    cd "$FAKE_REPO_ROOT"
    PATH="$temp_dir/bin:$PATH" HOME="$temp_dir/home" XDG_CONFIG_HOME="$temp_dir/xdg" \
      npm_config_registry=https://packagefeedproxy.microsoft.io/npm/ \
      UV_DEFAULT_INDEX=https://packagefeedproxy.microsoft.io/pypi/simple/ \
      PIP_INDEX_URL=https://packagefeedproxy.microsoft.io/pypi/simple/ \
      "$bash_bin" "$port_dir/git-loopy.sh" --no-interactive "$@"
  ) >"$temp_dir/stdout" 2>"$temp_dir/stderr"
}

run 2 --model test-model --reasoning-effort high ||
  fail "Run refused: $(<"$temp_dir/stderr")"
[[ "$(<"$FAKE_COPILOT_CALLS")" == 2 ]] || fail "two work sessions expected"
jq -se 'length == 2 and all(.[]; .memory.enabled == false
  and .model == "test-model" and .reasoningEffort == "high"
  and .contextTier == "default" and .enableFileHooks == false
  and .requestPermission == true and (has("customAgents") | not))
  and ([.[].sessionId] | unique | length) == 2' \
  "$FAKE_COPILOT_CONFIG" >/dev/null || fail "isolated Run-wide session settings"
jq -se '[.[] | select(.method == "connect")] | length == 1' \
  "$FAKE_COPILOT_REQUESTS" >/dev/null || fail "one server handshake per Run"
jq -se '[.[] | select(.type == "wrapper.iteration.end")] | length == 2' \
  "$temp_dir/stdout" >/dev/null || fail "Iterations did not complete"
assert_reclaimed

for selector in 'shell(git push)' 'MyMCP(tool)' 'shell(*)'; do
  : >"$FAKE_COPILOT_REQUESTS"
  if run 1 --deny-tool "$selector"; then
    fail "unsupported deny selector admitted work: $selector"
  fi
  [[ ! -s "$FAKE_COPILOT_REQUESTS" ]] || fail "selector refusal started a server"
  grep -q 'unsupported deny-tool selector' "$temp_dir/stderr" ||
    fail "unsupported selector refusal did not explain the restriction"
done

export FAKE_COPILOT_PERMISSIONS="$temp_dir/permissions"
export FAKE_COPILOT_PERMISSION_AGENT=child-policy
cat >"$FAKE_COPILOT_PERMISSIONS" <<'EOF'
[
  {"requestId":"deny-1","permissionRequest":{"kind":"custom-tool","toolName":"forbidden","toolCallId":"call-1","args":{}}},
  {"requestId":"allow-2","permissionRequest":{"kind":"custom-tool","toolName":"ordinary","toolCallId":"call-2","args":{}}},
  {"requestId":"deny-3","permissionRequest":{"kind":"custom-tool","toolName":"skill","toolCallId":"call-3","args":{"skill":"blocked-skill"}}},
  {"requestId":"deny-4","permissionRequest":{"kind":"shell","toolCallId":"call-4","fullCommandText":"git status"}}
]
EOF
: >"$FAKE_COPILOT_REQUESTS"
run 1 --deny-tool forbidden --deny-tool shell --deny-skill blocked-skill ||
  fail "permission Run refused: $(<"$temp_dir/stderr")"
jq -se '[.[] | select(.method == "session.permissions.handlePendingPermissionRequest")
  | [.params.requestId,.params.result.kind]] ==
  [["deny-1","reject"],["allow-2","approve-once"],["deny-3","reject"],["deny-4","reject"]]' \
  "$FAKE_COPILOT_REQUESTS" >/dev/null || fail "sole per-request permission policy"
jq -se '[.[] | select(.type == "tool.permission_denied")
  | [.request_id,.tool_call_id,.reason]] ==
  [["deny-1","call-1","tool_in_deny_list"],["deny-3","call-3","skill_in_deny_list"],
   ["deny-4","call-4","tool_in_deny_list"]]' \
  "$temp_dir/stdout" >/dev/null || fail "attributable denial evidence"
jq -se --slurpfile fixture "$port_dir/../conformance/event-schema.json" \
  '($fixture[0].serialization_cases[] | select(.id == "headless-permission-denied")
    | .event | del(.ts,.run_id)) as $expected
  | any(.[]; (del(.ts,.run_id)) == $expected)' "$temp_dir/stdout" >/dev/null ||
  fail "native denial differs from the shared encoding"
unset FAKE_COPILOT_PERMISSIONS FAKE_COPILOT_PERMISSION_AGENT
assert_reclaimed

: >"$FAKE_COPILOT_REQUESTS"
export FAKE_COPILOT_PROTOCOL=99
if run 1; then fail "protocol mismatch admitted work"; fi
grep -q 'protocol-version mismatch' "$temp_dir/stderr" || fail "mismatch diagnostic"
jq -se 'map(.method) == ["connect"]' "$FAKE_COPILOT_REQUESTS" >/dev/null ||
  fail "work began before a compatible handshake"
unset FAKE_COPILOT_PROTOCOL
assert_reclaimed

: >"$FAKE_COPILOT_REQUESTS"
export FAKE_COPILOT_MODE=hang
started=$SECONDS
run 1 --send-timeout-seconds 2 || fail "timed out Iteration did not reconcile"
((SECONDS - started < 20)) || fail "adapter deadline failed to bound work"
jq -se 'map(.method) == ["connect","session.create","session.send","session.abort","session.detach"]' \
  "$FAKE_COPILOT_REQUESTS" >/dev/null || fail "deadline did not abort and detach"
grep -q 'exceeded the 2s send timeout' "$temp_dir/stderr" || fail "timeout diagnostic"
unset FAKE_COPILOT_MODE
assert_reclaimed

for failure in hang crash; do
  : >"$FAKE_COPILOT_REQUESTS"
  if [[ "$failure" == hang ]]; then
    export FAKE_COPILOT_MODE=hang
  else
    export FAKE_COPILOT_EXIT=7
  fi
  expected_exit="$(jq '.cases[] | select(.id == "preflight-failure-is-aborted") | .exit_code' \
    "$port_dir/../conformance/exit-codes.json")"
  expected_reason="$(jq -r '.cases[] | select(.id == "preflight-failure-is-aborted") | .reason' \
    "$port_dir/../conformance/exit-codes.json")"
  status=0
  run 2 --send-timeout-seconds 1 || status=$?
  [[ "$status" == "$expected_exit" ]] || fail "server loss violated the exit-code fixture"
  jq -se 'map(.method) | map(select(. == "connect")) | length == 1' \
    "$FAKE_COPILOT_REQUESTS" >/dev/null || fail "a Run restarted its server"
  jq -se --arg reason "$expected_reason" '
    ([.[] | select(.type == "wrapper.iteration.start") | .iter] == [1])
    and ([.[] | select(.type == "wrapper.iteration.end") | .iter] == [1])
    and ([.[] | select(.type == "wrapper.strike") | .strikes] == [1])
    and .[-1].type == "wrapper.run.end"
    and .[-1].outcome == $reason
    and .[-1].iterations_run == 1
  ' "$temp_dir/stdout" >/dev/null ||
    fail "$failure left an unfinished Iteration or Run"
  unset FAKE_COPILOT_MODE FAKE_COPILOT_EXIT
  assert_reclaimed
done

export FAKE_COPILOT_MODE=wedged
started=$SECONDS
run 1 --send-timeout-seconds 1 || fail "wedged session did not reconcile"
((SECONDS - started < 20)) || fail "abort acknowledgement blocked teardown"
grep -q 'abort failed; reclaiming server' "$temp_dir/stderr" ||
  fail "unacknowledged abort was not reported"
unset FAKE_COPILOT_MODE
assert_reclaimed

for mode in malformed truncated; do
  : >"$FAKE_COPILOT_REQUESTS"
  export FAKE_COPILOT_MODE="$mode"
  if run 1; then fail "$mode handshake admitted work"; fi
  jq -se 'map(.method) == ["connect"]' "$FAKE_COPILOT_REQUESTS" >/dev/null ||
    fail "$mode handshake started work"
  assert_reclaimed
done
unset FAKE_COPILOT_MODE

export FAKE_COPILOT_MODE=fragmented
export FAKE_COPILOT_OUTPUT_FILE="$temp_dir/unicode"
printf 'caf\303\251 \360\237\232\200\n' >"$FAKE_COPILOT_OUTPUT_FILE"
cp "$FAKE_COPILOT_OUTPUT_FILE" "$temp_dir/repo/git-loopy/PROMPT.md"
run 1 || fail "fragmented multibyte Run refused"
grep -Fq "$(<"$FAKE_COPILOT_OUTPUT_FILE")" "$FAKE_COPILOT_PROMPT" ||
  fail "outbound multibyte payload changed"
jq -se --rawfile text "$FAKE_COPILOT_OUTPUT_FILE" \
  'any(.[]; .type == "agent.output" and .text == ($text | rtrimstr("\n")))' \
  "$temp_dir/stdout" >/dev/null || fail "inbound multibyte payload changed"
unset FAKE_COPILOT_MODE FAKE_COPILOT_OUTPUT_FILE
assert_reclaimed

: >"$FAKE_COPILOT_REQUESTS"
export FAKE_COPILOT_MODE=initial-idle
run 1 --send-timeout-seconds 1 || fail "initial-idle session did not reconcile"
jq -se 'any(.[]; .method == "session.abort")' "$FAKE_COPILOT_REQUESTS" >/dev/null ||
  fail "session creation idle was mistaken for work completion"
unset FAKE_COPILOT_MODE
assert_reclaimed

: >"$FAKE_COPILOT_REQUESTS"
export FAKE_COPILOT_MODE=subagent-idle
run 1 --send-timeout-seconds 1 || fail "root turn did not reconcile"
jq -se 'any(.[]; .method == "session.abort")' "$FAKE_COPILOT_REQUESTS" >/dev/null ||
  fail "a Subagent idle completed the root turn"
grep -q 'exceeded the 1s send timeout' "$temp_dir/stderr" || fail "root deadline was lost"
jq -se 'all(.[]; .type != "agent.output")' "$temp_dir/stdout" >/dev/null ||
  fail "Subagent text was attributed to the root turn"
unset FAKE_COPILOT_MODE
assert_reclaimed

for signal in TERM INT HUP; do
  : >"$FAKE_COPILOT_REQUESTS"
  export FAKE_COPILOT_MODE=hang
  set -m
  (
    cd "$FAKE_REPO_ROOT"
    exec env PATH="$temp_dir/bin:$PATH" HOME="$temp_dir/home" \
      XDG_CONFIG_HOME="$temp_dir/xdg" \
      npm_config_registry=https://packagefeedproxy.microsoft.io/npm/ \
      UV_DEFAULT_INDEX=https://packagefeedproxy.microsoft.io/pypi/simple/ \
      PIP_INDEX_URL=https://packagefeedproxy.microsoft.io/pypi/simple/ \
      "$bash_bin" "$port_dir/git-loopy.sh" 1 --no-interactive
  ) >"$temp_dir/stdout" 2>"$temp_dir/stderr" &
  run_pid=$!
  set +m
  deadline=$((SECONDS + 20))
  until [[ -f "$FAKE_COPILOT_REQUESTS" ]] &&
    grep -q '"method":"session.send"' "$FAKE_COPILOT_REQUESTS"; do
    ((SECONDS < deadline)) || { kill -TERM "$run_pid"; fail "signal Run never started work"; }
    sleep 0.1
  done
  kill -"$signal" "$run_pid"
  status=0
  wait "$run_pid" || status=$?
  expected=143
  [[ "$signal" != INT ]] || expected=130
  [[ "$signal" != HUP ]] || expected=129
  [[ "$status" == "$expected" ]] || fail "$signal exit was $status"
  unset FAKE_COPILOT_MODE
  assert_reclaimed
done
printf 'shell headless adapter: ok\n'
