#!/usr/bin/env bash

if ((BASH_VERSINFO[0] < 4)); then
  printf 'git-loopy Event-schema support requires Bash 4+ (found %s).\n' \
    "$BASH_VERSION" >&2
  if [[ "${BASH_SOURCE[0]}" != "$0" ]]; then
    return 1
  else
    exit 1
  fi
fi

# shellcheck disable=SC2034
declare -Ar GIT_LOOPY_EVENT_TYPES=(
  [WRAPPER_RUN_START]="wrapper.run.start"
  [WRAPPER_RUN_END]="wrapper.run.end"
  [WRAPPER_ITERATION_START]="wrapper.iteration.start"
  [WRAPPER_ITERATION_END]="wrapper.iteration.end"
  [WRAPPER_AFK_READY_COLLECTED]="wrapper.afk_ready.collected"
  [WRAPPER_CHECKPOINT_RECORDED]="wrapper.checkpoint.recorded"
  [WRAPPER_COMMIT_RECORDED]="wrapper.commit.recorded"
  [WRAPPER_PUSH_RECORDED]="wrapper.push.recorded"
  [WRAPPER_AUTO_CLOSE]="wrapper.auto_close"
  [WRAPPER_PR_ADVANCED]="wrapper.pr.advanced"
  [WRAPPER_STRIKE]="wrapper.strike"
  [WRAPPER_ASK_USER_ATTEMPTED]="wrapper.ask_user.attempted"
  [SESSION_CREATED]="session.created"
  [SESSION_IDLE]="session.idle"
  [SESSION_DELETED]="session.deleted"
  [ASSISTANT_MESSAGE]="assistant.message"
  [ASSISTANT_REASONING]="assistant.reasoning"
  [TOOL_CALL]="tool.call"
  [TOOL_RESULT]="tool.result"
  [TOOL_PERMISSION_REQUESTED]="tool.permission_requested"
  [TOOL_PERMISSION_DENIED]="tool.permission_denied"
  [USAGE_TOKENS]="usage.tokens"
)

GIT_LOOPY_RUN_ID=""
GIT_LOOPY_STARTED_AT=""
GIT_LOOPY_REPLAY_PATH=""

_GIT_LOOPY_CROCKFORD_ALPHABET="0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_GIT_LOOPY_RUN_ID_PATTERN='^[0-9A-HJKMNP-TV-Z]{26}$'
_GIT_LOOPY_TIMESTAMP_PATTERN='^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}Z$'

git_loopy_iso_timestamp() {
  local timestamp
  timestamp="$(date -u '+%Y-%m-%dT%H:%M:%S.%3NZ')" || return 1
  if [[ "$timestamp" =~ \.[0-9]{3}Z$ ]]; then
    printf '%s\n' "$timestamp"
    return
  fi

  # Bash 5 exposes sub-second time even when BSD date does not; Bash 4 falls
  # back to a valid whole-second timestamp without adding another dependency.
  local epoch_realtime="${EPOCHREALTIME:-}"
  local milliseconds="000"
  if [[ "$epoch_realtime" =~ ^[0-9]+\.([0-9]+)$ ]]; then
    milliseconds="${BASH_REMATCH[1]}000"
    milliseconds="${milliseconds:0:3}"
  fi
  printf '%s.%sZ\n' "$(date -u '+%Y-%m-%dT%H:%M:%S')" "$milliseconds"
}

_git_loopy_epoch_milliseconds() {
  local epoch_realtime="${EPOCHREALTIME:-}"
  if [[ "$epoch_realtime" =~ ^([0-9]+)\.([0-9]+)$ ]]; then
    local fraction="${BASH_REMATCH[2]}000"
    printf '%s%s\n' "${BASH_REMATCH[1]}" "${fraction:0:3}"
    return
  fi

  local epoch_milliseconds
  epoch_milliseconds="$(date -u '+%s%3N')" || return 1
  if [[ "$epoch_milliseconds" =~ ^[0-9]+$ ]]; then
    printf '%s\n' "$epoch_milliseconds"
  else
    printf '%s000\n' "$(date -u '+%s')"
  fi
}

git_loopy_new_run_id() {
  local time_ms="${1:-}"
  if [[ -z "$time_ms" ]]; then
    time_ms="$(_git_loopy_epoch_milliseconds)" || return 1
  fi
  if [[ ! "$time_ms" =~ ^[0-9]+$ ]] ||
    ((10#$time_ms >= 281474976710656)); then
    printf 'run-id timestamp must be an unsigned 48-bit millisecond value\n' >&2
    return 1
  fi

  local value=$((10#$time_ms))
  local timestamp_part=""
  local index
  local i
  for ((i = 0; i < 10; i++)); do
    index=$((value & 31))
    timestamp_part="${_GIT_LOOPY_CROCKFORD_ALPHABET:index:1}${timestamp_part}"
    value=$((value >> 5))
  done

  local random_bytes
  random_bytes="$(od -An -N10 -tu1 /dev/urandom)" || return 1
  local -a bytes
  read -r -a bytes <<<"$random_bytes"
  [[ "${#bytes[@]}" -eq 10 ]] || {
    printf 'could not read 80 bits of randomness for run id\n' >&2
    return 1
  }

  local random_part=""
  local buffer=0
  local bits=0
  local byte
  for byte in "${bytes[@]}"; do
    buffer=$(((buffer << 8) | byte))
    bits=$((bits + 8))
    while ((bits >= 5)); do
      bits=$((bits - 5))
      index=$(((buffer >> bits) & 31))
      random_part+="${_GIT_LOOPY_CROCKFORD_ALPHABET:index:1}"
      if ((bits == 0)); then
        buffer=0
      else
        buffer=$((buffer & ((1 << bits) - 1)))
      fi
    done
  done

  printf '%s%s\n' "$timestamp_part" "$random_part"
}

git_loopy_events_init() {
  local repo_root="${1:?repo root is required}"
  local run_id="${2:-}"
  local started_at="${3:-}"

  if [[ -z "$run_id" ]]; then
    run_id="$(git_loopy_new_run_id)" || return 1
  fi
  if [[ ! "$run_id" =~ $_GIT_LOOPY_RUN_ID_PATTERN ]]; then
    printf 'run id must be a 26-character Crockford-base32 ULID\n' >&2
    return 1
  fi

  if [[ -z "$started_at" ]]; then
    started_at="$(git_loopy_iso_timestamp)" || return 1
  fi
  if [[ ! "$started_at" =~ $_GIT_LOOPY_TIMESTAMP_PATTERN ]]; then
    printf 'started-at must be UTC ISO-8601 with millisecond precision\n' >&2
    return 1
  fi

  local filename_timestamp="${started_at%%.*}"
  filename_timestamp="${filename_timestamp//:/-}Z"

  GIT_LOOPY_RUN_ID="$run_id"
  # shellcheck disable=SC2034
  GIT_LOOPY_STARTED_AT="$started_at"
  GIT_LOOPY_REPLAY_PATH="$repo_root/.git-loopy/logs/$filename_timestamp-$run_id.jsonl"
}

git_loopy_make_event() {
  local type="${1:?event type is required}"
  local iteration="${2:?iteration is required; use null for run-scope events}"
  local payload="${3-}"
  local timestamp="${4:-}"
  [[ -n "$payload" ]] || payload='{}'

  [[ -n "$GIT_LOOPY_RUN_ID" ]] || {
    printf 'event context is not initialized\n' >&2
    return 1
  }
  if [[ "$iteration" != "null" ]] &&
    { [[ ! "$iteration" =~ ^[1-9][0-9]*$ ]]; }; then
    printf 'iteration must be null or a positive integer\n' >&2
    return 1
  fi
  if [[ -z "$timestamp" ]]; then
    timestamp="$(git_loopy_iso_timestamp)" || return 1
  fi
  if [[ ! "$timestamp" =~ $_GIT_LOOPY_TIMESTAMP_PATTERN ]]; then
    printf 'event timestamp must be UTC ISO-8601 with millisecond precision\n' >&2
    return 1
  fi

  jq -ce '
    type == "object"
    and ([keys[] | select(. == "ts" or . == "run_id" or . == "iter" or . == "type")] | length == 0)
  ' <<<"$payload" >/dev/null || {
    printf 'event payload must be an object without envelope keys\n' >&2
    return 1
  }

  jq -cn \
    --arg ts "$timestamp" \
    --arg run_id "$GIT_LOOPY_RUN_ID" \
    --argjson iter "$iteration" \
    --arg type "$type" \
    --argjson payload "$payload" \
    '{ts: $ts, run_id: $run_id, iter: $iter, type: $type} + $payload'
}

git_loopy_scrub_event() {
  local event="${1:?event JSON is required}"
  jq -c '
    walk(
      if type == "string" then
        gsub("ghp_[A-Za-z0-9]{36,}"; "<redacted-secret>")
        | gsub("gho_[A-Za-z0-9]{36,}"; "<redacted-secret>")
        | gsub("eyJ[A-Za-z0-9_-]{17,}\\.[A-Za-z0-9_-]{20,}\\.[A-Za-z0-9_-]{20,}"; "<redacted-secret>")
        | gsub("AKIA[0-9A-Z]{16}"; "<redacted-secret>")
      else
        .
      end
    )
  ' <<<"$event"
}

git_loopy_to_jsonl_line() {
  local event="${1:?event JSON is required}"
  local scrubbed
  scrubbed="$(git_loopy_scrub_event "$event")" || return 1

  jq -ejr '
    def spaced_json:
      if type == "object" then
        "{" + (
          [keys_unsorted[] as $key
            | ($key | tojson) + ": " + (.[$key] | spaced_json)]
          | join(", ")
        ) + "}"
      elif type == "array" then
        "[" + (map(spaced_json) | join(", ")) + "]"
      else
        tojson
      end;

    if (
      type == "object"
      and has("ts")
      and has("run_id")
      and has("iter")
      and has("type")
    ) then
      . as $event
      | ["ts", "run_id", "iter", "type"] as $envelope
      | ($envelope + [
          keys[]
          | select(. != "ts" and . != "run_id" and . != "iter" and . != "type")
        ]) as $ordered_keys
      | "{" + (
          [$ordered_keys[] as $key
            | ($key | tojson) + ": " + ($event[$key] | spaced_json)]
          | join(", ")
        ) + "}"
    else
      error("event must contain ts, run_id, iter, and type")
    end
  ' <<<"$scrubbed" || return 1
  printf '\n'
}

git_loopy_emit_event() {
  local type="${1:?event type is required}"
  local iteration="${2:?iteration is required; use null for run-scope events}"
  local payload="${3-}"
  local timestamp="${4:-}"
  [[ -n "$payload" ]] || payload='{}'

  [[ -n "$GIT_LOOPY_REPLAY_PATH" ]] || {
    printf 'event context is not initialized\n' >&2
    return 1
  }

  local event
  event="$(git_loopy_make_event "$type" "$iteration" "$payload" "$timestamp")" ||
    return 1
  local line
  line="$(git_loopy_to_jsonl_line "$event")" || return 1

  mkdir -p "$(dirname "$GIT_LOOPY_REPLAY_PATH")" || return 1
  printf '%s\n' "$line" >>"$GIT_LOOPY_REPLAY_PATH" || return 1
  printf '%s\n' "$line"
}
