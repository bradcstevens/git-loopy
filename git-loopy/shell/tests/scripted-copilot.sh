#!/usr/bin/env bash
# Offline protocol-3 server. Its framing is independent of the production adapter.
set -euo pipefail
export LC_ALL=C

[[ "$*" == "--headless --no-auto-update" ]] || {
  printf 'expected headless stdio launch, got: %s\n' "$*" >&2
  exit 91
}
[[ -z "${FAKE_COPILOT_FLAGS:-}" ]] || printf '%s\n' "$@" >"$FAKE_COPILOT_FLAGS"
[[ -z "${FAKE_COPILOT_SIGNALS:-}" ]] || trap -p PIPE >"$FAKE_COPILOT_SIGNALS"
if [[ -n "${FAKE_COPILOT_PIDS:-}" ]]; then
  printf '%s\n' "$$" >>"$FAKE_COPILOT_PIDS"
  # A stubborn grandchild must be reclaimed by the Run, not by this double.
  bash -c 'trap "" TERM; sleep 300 & echo "$BASHPID $!" >>"$FAKE_COPILOT_PIDS"; wait' &
fi

frame() {
  if [[ "${FAKE_COPILOT_MODE:-}" == fragmented ]]; then
    printf 'Content-Len'
    sleep 0.02
    printf 'gth: %s\r\n\r\n%s' "${#1}" "${1:0:7}"
    sleep 0.02
    printf '%s' "${1:7}"
  else
    printf 'Content-Length: %s\r\n\r\n%s' "${#1}" "$1"
  fi
}
reply() {
  frame "$(jq -cn --argjson id "$id" --argjson result "$1" \
    '{jsonrpc:"2.0", id:$id, result:$result}')"
}
event() {
  frame "$(jq -cn --arg sid "$session" --arg type "$1" --argjson data "$2" \
    --arg agent "${3:-}" \
    '{jsonrpc:"2.0", method:"session.event",
      params:{sessionId:$sid,event:({type:$type,data:$data}
        + if $agent == "" then {} else {agentId:$agent} end)}}')"
}
work() {
  local calls=0
  [[ ! -f "$FAKE_COPILOT_CALLS" ]] || calls="$(<"$FAKE_COPILOT_CALLS")"
  calls=$((calls + 1))
  printf '%s' "$calls" >"$FAKE_COPILOT_CALLS"
  jq -rj '.params.prompt' <<<"$work_message" >"$FAKE_COPILOT_PROMPT"
  if [[ "${FAKE_COPILOT_MODE:-}" == subagent-idle ]]; then
    event assistant.message '{"content":"child output is not root output"}' child-1
    event session.idle '{}' child-1
    return
  fi
  if [[ "${FAKE_COPILOT_MODE:-}" == "hang" || "${FAKE_COPILOT_MODE:-}" == "wedged" ||
    "${FAKE_COPILOT_MODE:-}" == "initial-idle" ]]; then
    return
  fi
  local output="copilot agent stream marker"
  [[ -z "${FAKE_COPILOT_OUTPUT_FILE:-}" ]] ||
    output="$(<"$FAKE_COPILOT_OUTPUT_FILE")"
  event assistant.message "$(jq -cn --arg content "$output" '{content:$content}')"
  if [[ -n "${FAKE_COPILOT_PLAN_DIR:-}" ]]; then
    local call_dir="$FAKE_COPILOT_PLAN_DIR/$calls" msg_file
    for msg_file in "$call_dir"/*.msg; do
      [[ -e "$msg_file" ]] || continue
      git commit -q --allow-empty -F "$msg_file"
    done
    [[ ! -f "$call_dir/worktree.sh" ]] ||
      (cd "$FAKE_REPO_ROOT" && bash "$call_dir/worktree.sh")
  else
    local i
    for ((i = 0; i < ${FAKE_COPILOT_COMMITS:-0}; i++)); do
      git commit -q --allow-empty -m "agent: work $((i + 1))"
    done
  fi
  if [[ "${FAKE_COPILOT_EXIT:-0}" != 0 ]]; then
    exit "$FAKE_COPILOT_EXIT"
  fi
  event session.idle '{}'
}

sessions=0
session=""
permissions_left=0
while IFS= read -r header; do
  [[ "$header" =~ ^Content-Length:\ ([0-9]+)$'\r'$ ]] || exit 92
  length="${BASH_REMATCH[1]}"
  IFS= read -r blank
  [[ "$blank" == $'\r' ]] || exit 93
  IFS= read -r -N "$length" message || exit 94
  [[ -z "${FAKE_COPILOT_REQUESTS:-}" ]] ||
    printf '%s\n' "$message" >>"$FAKE_COPILOT_REQUESTS"
  id="$(jq -c '.id' <<<"$message")"
  case "$(jq -r '.method' <<<"$message")" in
    connect)
      case "${FAKE_COPILOT_MODE:-}" in
        malformed) printf 'Content-Length: nope\r\n\r\n'; continue ;;
        truncated) printf 'Content-Length: 100\r\n\r\n{}'; exit 0 ;;
      esac
      reply "{\"protocolVersion\":${FAKE_COPILOT_PROTOCOL:-3}}"
      ;;
    session.create)
      sessions=$((sessions + 1))
      session="$(jq -r '.params.sessionId // empty' <<<"$message")"
      [[ -n "$session" ]] || session="work-$sessions"
      [[ -z "${FAKE_COPILOT_CONFIG:-}" ]] ||
        jq -c '.params' <<<"$message" >>"$FAKE_COPILOT_CONFIG"
      [[ "${FAKE_COPILOT_MODE:-}" != initial-idle ]] || event session.idle '{}'
      reply "$(jq -cn --arg sid "$session" '{sessionId:$sid}')"
      ;;
    session.send)
      work_message="$message"
      send_id="$id"
      if [[ -n "${FAKE_COPILOT_PERMISSIONS:-}" ]]; then
        permissions_left="$(jq 'length' "$FAKE_COPILOT_PERMISSIONS")"
        while IFS= read -r permission; do
          if jq -e '.permissionRequest.kind == "shell"' <<<"$permission" >/dev/null; then
            event tool.execution_start "$(jq -c \
              '{toolCallId:.permissionRequest.toolCallId,toolName:"bash"}' <<<"$permission")"
          fi
          event permission.requested "$permission" "${FAKE_COPILOT_PERMISSION_AGENT:-}"
        done < <(jq -c '.[]' "$FAKE_COPILOT_PERMISSIONS")
      fi
      if ((permissions_left == 0)); then
        reply '{"messageId":"message-1"}'
        work
      fi
      ;;
    session.permissions.handlePendingPermissionRequest)
      reply '{"kind":"approved"}'
      permissions_left=$((permissions_left - 1))
      if ((permissions_left == 0)); then
        id="$send_id"
        reply '{"messageId":"message-1"}'
        work
      fi
      ;;
    session.abort | session.detach)
      [[ "${FAKE_COPILOT_MODE:-}" != wedged ]] || continue
      reply '{}'
      ;;
    *)
      printf 'unexpected RPC: %s\n' "$message" >&2
      exit 95
      ;;
  esac
done
