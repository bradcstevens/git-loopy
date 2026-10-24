#!/usr/bin/env bash
# Copilot CLI >= 1.0.103, JSON-RPC protocol 3 (ADR-0103).
_GIT_LOOPY_SESSION_PID=""
_GIT_LOOPY_SESSION_READ=""
_GIT_LOOPY_SESSION_WRITE=""
_GIT_LOOPY_SESSION_WRITER=""
_GIT_LOOPY_SESSION_ID=""
_GIT_LOOPY_SESSION_ITERATION=""
_GIT_LOOPY_SESSION_SEQUENCE=0
_GIT_LOOPY_SESSION_STARTED=0
_GIT_LOOPY_SESSION_IDLE=0
_GIT_LOOPY_SESSION_DEADLINE=0
_GIT_LOOPY_SESSION_FRAME=""
_GIT_LOOPY_SESSION_RESULT=""
_GIT_LOOPY_SESSION_STATE=""
declare -A _GIT_LOOPY_SESSION_PENDING=()
declare -A _GIT_LOOPY_SESSION_TOOLS=()

_git_loopy_session_error() {
  printf 'git-loopy: headless adapter: %s\n' "$*" >&2
  return 1
}

_git_loopy_session_deadline() {
  local seconds="$1" whole="${1%%.*}" fraction=""
  [[ "$seconds" != *.* ]] || fraction="${seconds#*.}"
  [[ -n "$whole" ]] || whole=0
  # SECONDS is integral: reserve its partial current tick as well, so rounding
  # never gives the work less time than the configured budget.
  _GIT_LOOPY_SESSION_DEADLINE=$((SECONDS + 10#$whole + 1))
  [[ "$fraction" != *[1-9]* ]] ||
    _GIT_LOOPY_SESSION_DEADLINE=$((_GIT_LOOPY_SESSION_DEADLINE + 1))
}

_git_loopy_session_read() {
  local LC_ALL=C mode="$1" count="$2" target="$3" chunk buffer="" status
  while :; do
    ((SECONDS < _GIT_LOOPY_SESSION_DEADLINE)) || return 124
    kill -0 "$_GIT_LOOPY_SESSION_PID" 2>/dev/null ||
      { _git_loopy_session_error "server exited"; return 1; }
    status=0
    IFS= read -r -t 1 -u "$_GIT_LOOPY_SESSION_READ" \
      "$mode" "$((count - ${#buffer}))" chunk || status=$?
    buffer+="$chunk"
    if ((status == 0)); then
      printf -v "$target" '%s' "$buffer"
      return 0
    elif ((status <= 128)); then
      _git_loopy_session_error "transport EOF"
      return 1
    fi
  done
}

_git_loopy_session_frame() {
  local LC_ALL=C line length="" count=0
  while :; do
    _git_loopy_session_read -n 8192 line || return $?
    [[ "$line" == *$'\r' ]] ||
      { _git_loopy_session_error "malformed frame header"; return 1; }
    line="${line%$'\r'}"
    [[ -n "$line" ]] || break
    count=$((count + 1))
    ((count <= 16)) || { _git_loopy_session_error "too many headers"; return 1; }
    case "$line" in
      Content-Length:*)
        [[ -z "$length" && "$line" =~ ^Content-Length:\ ([0-9]{1,8})$ ]] ||
          { _git_loopy_session_error "invalid Content-Length"; return 1; }
        length=$((10#${BASH_REMATCH[1]}))
        ;;
      Content-Type:*) ;;
      *) _git_loopy_session_error "unsupported frame header"; return 1 ;;
    esac
  done
  [[ -n "$length" ]] && ((length > 0 && length <= 16777216)) ||
    { _git_loopy_session_error "missing or oversized Content-Length"; return 1; }
  _git_loopy_session_read -N "$length" _GIT_LOOPY_SESSION_FRAME || return $?
  jq -e 'type == "object" and .jsonrpc == "2.0"' \
    <<<"$_GIT_LOOPY_SESSION_FRAME" >/dev/null ||
    { _git_loopy_session_error "invalid JSON-RPC envelope"; return 1; }
}

_git_loopy_session_write() {
  local LC_ALL=C message="$1" status=0
  # A server that stops reading must not trap the parent in a full pipe.
  (printf 'Content-Length: %s\r\n\r\n%s' "${#message}" "$message" \
    >&"$_GIT_LOOPY_SESSION_WRITE") &
  _GIT_LOOPY_SESSION_WRITER=$!
  while kill -0 "$_GIT_LOOPY_SESSION_WRITER" 2>/dev/null; do
    if ((SECONDS >= _GIT_LOOPY_SESSION_DEADLINE)); then
      kill -TERM "$_GIT_LOOPY_SESSION_WRITER" 2>/dev/null || true
      wait "$_GIT_LOOPY_SESSION_WRITER" 2>/dev/null || true
      _GIT_LOOPY_SESSION_WRITER=""
      return 124
    fi
    sleep 0.02
  done
  wait "$_GIT_LOOPY_SESSION_WRITER" || status=$?
  _GIT_LOOPY_SESSION_WRITER=""
  ((status == 0)) || { _git_loopy_session_error "transport write failed"; return 1; }
}

_git_loopy_session_request() {
  local method="$1" params="$2" message
  _GIT_LOOPY_SESSION_SEQUENCE=$((_GIT_LOOPY_SESSION_SEQUENCE + 1))
  _GIT_LOOPY_SESSION_PENDING[$_GIT_LOOPY_SESSION_SEQUENCE]="$method"
  message="$(jq -cn --argjson id "$_GIT_LOOPY_SESSION_SEQUENCE" \
    --arg method "$method" --argjson params "$params" \
    '{jsonrpc:"2.0",id:$id,method:$method,params:$params}')" || return 1
  _git_loopy_session_write "$message"
}

_git_loopy_session_permission() {
  local data="$1" request_id request tool kind call_id skill reason="" denied params
  jq -e '.resolvedByHook != true
    and (.requestId | type == "string" and length > 0)
    and (.permissionRequest | type == "object")
    and (.permissionRequest.kind | type == "string" and length > 0)' <<<"$data" >/dev/null ||
    { _git_loopy_session_error "permission not owned by the adapter or malformed"; return 1; }
  request_id="$(jq -r '.requestId' <<<"$data")"
  request="$(jq -c '.permissionRequest' <<<"$data")"
  kind="$(jq -r '.kind' <<<"$request")"
  call_id="$(jq -r '.toolCallId // empty' <<<"$request")"
  tool="$(jq -r '.toolName // empty' <<<"$request")"
  if [[ -z "$tool" && -n "$call_id" ]]; then
    tool="${_GIT_LOOPY_SESSION_TOOLS[$call_id]:-}"
  fi
  [[ -n "$tool" ]] || tool="$kind"
  skill="$(jq -r '(.args // .toolArgs // {}) |
    if type == "object" then .skill // empty else empty end' <<<"$request")" || return 1
  for denied in ${GIT_LOOPY_DENY_TOOLS_RESOLVED[@]+"${GIT_LOOPY_DENY_TOOLS_RESOLVED[@]}"}; do
    if [[ "$denied" == "$tool" || "$denied" == "$kind" ||
      ( "$tool" == skill && "$denied" == "skill($skill)" ) ]]; then
      reason=tool_in_deny_list
      break
    fi
  done
  if [[ "$tool" == skill ]]; then
    for denied in ${GIT_LOOPY_DENY_SKILLS_RESOLVED[@]+"${GIT_LOOPY_DENY_SKILLS_RESOLVED[@]}"}; do
      [[ "$denied" != "$skill" ]] || reason=skill_in_deny_list
    done
  fi
  # A managed request that needs a human is never ordinary unattended work.
  if jq -e '.managedApprovalRequired == true' <<<"$request" >/dev/null; then
    reason=managed_approval_required
  fi
  local result='{"kind":"approve-once"}'
  if [[ -n "$reason" ]]; then
    git_loopy_emit_event "${GIT_LOOPY_EVENT_TYPES[TOOL_PERMISSION_DENIED]}" \
      "$_GIT_LOOPY_SESSION_ITERATION" "$(jq -cn \
        --arg request_id "$request_id" --arg call_id "$call_id" \
        --arg tool "$tool" --arg reason "$reason" \
        '{request_id:$request_id,tool_call_id:(if $call_id == "" then null else $call_id end),
          tool_name:$tool,reason:$reason}')" || return 1
    result="$(jq -cn --arg reason "$reason" '{kind:"reject",feedback:$reason}')"
  fi
  params="$(jq -cn --arg sid "$_GIT_LOOPY_SESSION_ID" --arg id "$request_id" \
    --argjson result "$result" '{sessionId:$sid,requestId:$id,result:$result}')" || return 1
  _git_loopy_session_request session.permissions.handlePendingPermissionRequest "$params"
}

_git_loopy_session_event() {
  local params="$1" type data content
  [[ "$(jq -r '.sessionId' <<<"$params")" == "$_GIT_LOOPY_SESSION_ID" ]] ||
    { _git_loopy_session_error "event for an unowned session"; return 1; }
  type="$(jq -r '.event.type' <<<"$params")"
  data="$(jq -c '.event.data' <<<"$params")"
  case "$type" in
    tool.execution_start)
      local call_id tool
      call_id="$(jq -r '.toolCallId // empty' <<<"$data")"
      tool="$(jq -r '.toolName // empty' <<<"$data")"
      [[ -z "$call_id" ]] || _GIT_LOOPY_SESSION_TOOLS[$call_id]="$tool"
      ;;
    assistant.message)
      content="$(jq -r '.content // ""' <<<"$data")"
      (_git_loopy_stream_agent_output "$_GIT_LOOPY_SESSION_ITERATION" \
        "$_GIT_LOOPY_SESSION_STATE" <<<"$content")
      ;;
    session.idle) _GIT_LOOPY_SESSION_IDLE=1 ;;
    session.error)
      _git_loopy_session_error "session.error: $data"
      ;;
    permission.requested)
      _git_loopy_session_permission "$data"
      ;;
  esac
}

_git_loopy_session_pump() {
  _git_loopy_session_frame || return $?
  local message="$_GIT_LOOPY_SESSION_FRAME" method id params
  method="$(jq -r '.method // empty' <<<"$message")"
  if [[ -n "$method" ]]; then
    if jq -e 'has("id")' <<<"$message" >/dev/null; then
      _git_loopy_session_error "unsupported server request: $method"
      return 1
    fi
    params="$(jq -c '.params' <<<"$message")"
    case "$method" in
      session.event) _git_loopy_session_event "$params" || return $? ;;
      session.lifecycle) ;;
      *) _git_loopy_session_error "unsupported notification: $method"; return 1 ;;
    esac
    _GIT_LOOPY_SESSION_RESPONSE_ID=""
    return 0
  fi
  id="$(jq -r '.id // empty' <<<"$message")"
  [[ "$id" =~ ^[0-9]+$ && -n "${_GIT_LOOPY_SESSION_PENDING[$id]:-}" ]] ||
    { _git_loopy_session_error "uncorrelated response"; return 1; }
  if jq -e 'has("error") or (has("result") | not)' <<<"$message" >/dev/null; then
    _git_loopy_session_error "RPC ${_GIT_LOOPY_SESSION_PENDING[$id]} failed: $message"
    return 1
  fi
  unset '_GIT_LOOPY_SESSION_PENDING[$id]'
  _GIT_LOOPY_SESSION_RESPONSE_ID="$id"
  _GIT_LOOPY_SESSION_RESULT="$(jq -c '.result' <<<"$message")"
}

_git_loopy_session_rpc() {
  _git_loopy_session_request "$@" || return $?
  local id="$_GIT_LOOPY_SESSION_SEQUENCE"
  while :; do
    _git_loopy_session_pump || return $?
    [[ "$_GIT_LOOPY_SESSION_RESPONSE_ID" != "$id" ]] || return 0
  done
}

git_loopy_session_start() {
  ((_GIT_LOOPY_SESSION_STARTED == 0)) || {
    [[ -n "$_GIT_LOOPY_SESSION_PID" ]] &&
      kill -0 "$_GIT_LOOPY_SESSION_PID" 2>/dev/null ||
      { _git_loopy_session_error "server is no longer running"; return 1; }
    return 0
  }
  _GIT_LOOPY_SESSION_STARTED=1
  local -a launch=()
  if _git_loopy_sigpipe_shim; then launch=("${_GIT_LOOPY_SIGPIPE_SHIM[@]}"); fi
  launch+=(copilot --headless --no-auto-update)
  local monitor=0
  [[ "$-" != *m* ]] || monitor=1
  # Job control gives this child its own process group, including descendants
  # whose immediate parent exits before teardown. No external session helper.
  set -m
  coproc GIT_LOOPY_HEADLESS { exec "${launch[@]}"; }
  _GIT_LOOPY_SESSION_PID="$GIT_LOOPY_HEADLESS_PID"
  exec {_GIT_LOOPY_SESSION_READ}<&"${GIT_LOOPY_HEADLESS[0]}"
  exec {_GIT_LOOPY_SESSION_WRITE}>&"${GIT_LOOPY_HEADLESS[1]}"
  ((monitor == 1)) || set +m
  _git_loopy_session_deadline 30
  _git_loopy_session_rpc connect '{}' || {
    _git_loopy_session_error "handshake failed before work"
    return 1
  }
  jq -e '.protocolVersion == 3' <<<"$_GIT_LOOPY_SESSION_RESULT" >/dev/null ||
    { _git_loopy_session_error "protocol-version mismatch (requires 3): $_GIT_LOOPY_SESSION_RESULT"; return 1; }
}

git_loopy_session_stop() {
  if [[ -n "$_GIT_LOOPY_SESSION_WRITER" ]]; then
    kill -TERM "$_GIT_LOOPY_SESSION_WRITER" 2>/dev/null || true
    wait "$_GIT_LOOPY_SESSION_WRITER" 2>/dev/null || true
    _GIT_LOOPY_SESSION_WRITER=""
  fi
  if [[ -n "$_GIT_LOOPY_SESSION_STATE" ]]; then
    rm -rf "$_GIT_LOOPY_SESSION_STATE"
    _GIT_LOOPY_SESSION_STATE=""
  fi
  [[ -n "$_GIT_LOOPY_SESSION_PID" ]] || return 0
  # Terminate descendants before the server so it can reap its own children.
  local pid
  local -a children=()
  while read -r pid; do
    children+=("$pid")
    kill -TERM "$pid" 2>/dev/null || true
  done < <(ps -axo pid=,ppid=,pgid= | awk -v root="$_GIT_LOOPY_SESSION_PID" '
    { parent[$1]=$2; group[$1]=$3 }
    END {
      for (pid in parent) {
        if (pid == root) continue
        ancestor=pid
        while (ancestor != root && ancestor in parent) ancestor=parent[ancestor]
        if (ancestor == root || group[pid] == root) print pid
      }
    }')
  if ((${#children[@]})); then
    sleep 0.1
    for pid in "${children[@]}"; do kill -KILL "$pid" 2>/dev/null || true; done
  fi
  kill -TERM -- "-$_GIT_LOOPY_SESSION_PID" 2>/dev/null || true
  sleep 0.1
  kill -KILL -- "-$_GIT_LOOPY_SESSION_PID" 2>/dev/null || true
  wait "$_GIT_LOOPY_SESSION_PID" 2>/dev/null || true
  [[ -z "$_GIT_LOOPY_SESSION_READ" ]] || exec {_GIT_LOOPY_SESSION_READ}<&-
  [[ -z "$_GIT_LOOPY_SESSION_WRITE" ]] || exec {_GIT_LOOPY_SESSION_WRITE}>&-
  _GIT_LOOPY_SESSION_PID=""
}

git_loopy_session_turn() {
  _GIT_LOOPY_SESSION_ITERATION="$1"
  local prompt="$2" config status=0
  _GIT_LOOPY_SESSION_IDLE=0
  _GIT_LOOPY_SESSION_TOOLS=()
  _GIT_LOOPY_SESSION_STATE="$(mktemp -d)" || return 1
  if [[ -n "$_GIT_LOOPY_PICKUP_REF" ]]; then
    printf '%s' "$_GIT_LOOPY_PICKUP_REF" >"$_GIT_LOOPY_SESSION_STATE/active-ref"
    printf '%s' "$_GIT_LOOPY_PICKUP_AT" >"$_GIT_LOOPY_SESSION_STATE/active-at"
    printf '%s' "$_GIT_LOOPY_ACTIVE_STARTED_MONOTONIC" >"$_GIT_LOOPY_SESSION_STATE/active-monotonic"
  fi
  _GIT_LOOPY_SESSION_ID="$GIT_LOOPY_RUN_ID-$1"
  config="$(jq -cn --arg sid "$_GIT_LOOPY_SESSION_ID" --arg model "$GIT_LOOPY_MODEL" \
    --arg effort "$GIT_LOOPY_REASONING_EFFORT" --arg cwd "$GIT_LOOPY_REPO_ROOT" \
    '{sessionId:$sid,model:$model,workingDirectory:$cwd,memory:{enabled:false},contextTier:"default",
      requestPermission:true,enableFileHooks:false,streaming:false}
      + (if $effort == "" then {} else {reasoningEffort:$effort} end)')" || return 1
  _git_loopy_session_deadline "$GIT_LOOPY_SEND_TIMEOUT_SECONDS"
  _git_loopy_session_rpc session.create "$config" || status=$?
  if ((status == 0)); then
    if ! jq -e --arg sid "$_GIT_LOOPY_SESSION_ID" '.sessionId == $sid' \
      <<<"$_GIT_LOOPY_SESSION_RESULT" >/dev/null; then
      _git_loopy_session_error "server returned a different session identity"
      status=1
    fi
  fi
  if ((status == 0)); then
    _GIT_LOOPY_SESSION_IDLE=0
    _git_loopy_session_rpc session.send "$(jq -cn \
      --arg sid "$_GIT_LOOPY_SESSION_ID" --arg prompt "$prompt" \
      '{sessionId:$sid,prompt:$prompt}')" || status=$?
  fi
  while ((status == 0 && _GIT_LOOPY_SESSION_IDLE == 0)); do
    _git_loopy_session_pump || status=$?
  done
  if ((status == 124)); then
    printf 'git-loopy: copilot turn exceeded the %ss send timeout; terminated.\n' \
      "$GIT_LOOPY_SEND_TIMEOUT_SECONDS" >&2
  fi
  if [[ -n "$_GIT_LOOPY_SESSION_ID" ]]; then
    _git_loopy_session_deadline 2
    if ((status != 0)); then
      _git_loopy_session_rpc session.abort \
        "$(jq -cn --arg sid "$_GIT_LOOPY_SESSION_ID" '{sessionId:$sid}')" ||
        printf 'git-loopy: headless session abort failed; reclaiming server.\n' >&2
    fi
    _git_loopy_session_rpc session.detach \
      "$(jq -cn --arg sid "$_GIT_LOOPY_SESSION_ID" '{sessionId:$sid}')" || {
      printf 'git-loopy: headless session detach failed; reclaiming server.\n' >&2
      ((status != 0)) || status=1
    }
  fi
  _GIT_LOOPY_SESSION_ID=""
  if [[ -f "$_GIT_LOOPY_SESSION_STATE/active-ref" ]]; then
    _GIT_LOOPY_ACTIVE_REF="$(<"$_GIT_LOOPY_SESSION_STATE/active-ref")"
    _GIT_LOOPY_ACTIVE_STARTED_AT="$(<"$_GIT_LOOPY_SESSION_STATE/active-at")"
    _GIT_LOOPY_ACTIVE_STARTED_MONOTONIC="$(<"$_GIT_LOOPY_SESSION_STATE/active-monotonic")"
    _git_loopy_remember_issue_start
  fi
  if ((status != 0)); then
    git_loopy_session_stop
  else
    rm -rf "$_GIT_LOOPY_SESSION_STATE"
    _GIT_LOOPY_SESSION_STATE=""
  fi
  return "$status"
}
