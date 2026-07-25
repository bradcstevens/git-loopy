#!/usr/bin/env bash

# The shared TUI helper's supervision seam (PRD #173, ADR-0013).
#
# The shell Orchestrator does not draw anything. It decides whether this Run
# wants a live interface, finds a `git-loopy-tui` it is allowed to trust, starts
# it as a child, and routes the already-serialized Event stream into that child's
# stdin instead of stdout. Everything the child does with those bytes — raw mode,
# the alternate screen, key input, restoration — belongs to the child.
#
# The whole module exists to keep one promise: a live interface is *presentation*
# and can never make a Run fail. Every failure path here ends in raw JSONL on
# stdout with the replay log untouched, and never in a non-zero Run.

if ((BASH_VERSINFO[0] < 4)); then
  printf 'git-loopy TUI supervision requires Bash 4+ (found %s).\n' \
    "$BASH_VERSION" >&2
  if [[ "${BASH_SOURCE[0]}" != "$0" ]]; then
    return 1
  fi
  exit 1
fi

# The clone-local helper the repository pins, relative to the repository root.
declare -r GIT_LOOPY_TUI_CLONE_RELATIVE_PATH=".git-loopy/bin/git-loopy-tui"
declare -r GIT_LOOPY_TUI_COMMAND_NAME="git-loopy-tui"
# A fixed descriptor rather than `exec {var}>`: dynamic descriptor allocation
# arrived in Bash 4.1 and this port supports 4.0. Nothing else in the port opens
# a descriptor above 2, so a constant is unambiguous and greppable.
declare -r _GIT_LOOPY_TUI_FD=8

GIT_LOOPY_TUI_HELPER_PATH=""
GIT_LOOPY_TUI_HELPER_SOURCE=""
GIT_LOOPY_TUI_ACTIVE=0
_GIT_LOOPY_TUI_PID=""
_GIT_LOOPY_TUI_FIFO_DIR=""
# Once a Run has fallen back it stays fallen back: a helper that died once is not
# a helper that will survive being started again, and a respawn mid-Run would
# split the live stream across two children.
_GIT_LOOPY_TUI_RETIRED=0

_git_loopy_tui_warn() {
  printf 'git-loopy: %s\n' "$1" >&2
}

# Mirrors `git_loopy.interactive.detect.resolve_interactive`: the explicit flag
# wins, then a non-blank `GIT_LOOPY_INTERACTIVE`, then whether stdout is a
# terminal. Echoes `<on|off> <explicit|auto>` — the second field is what decides
# how loudly an unfulfillable request is reported, so it has to survive the
# resolution rather than be re-derived later.
git_loopy_tui_resolve_intent() {
  local flag="${1:-}"
  local env_value="${2:-}"
  local isatty="${3:-0}"

  local trimmed="${env_value#"${env_value%%[![:space:]]*}"}"
  trimmed="${trimmed%"${trimmed##*[![:space:]]}"}"

  local intent
  local source
  if [[ -n "$flag" ]]; then
    intent="$flag"
    source="explicit"
  elif [[ -n "$trimmed" ]]; then
    source="explicit"
    case "${trimmed,,}" in
      1 | true | yes | on) intent="on" ;;
      *) intent="off" ;;
    esac
  else
    source="auto"
    if [[ "$isatty" == "1" ]]; then
      intent="on"
    else
      intent="off"
    fi
  fi

  printf '%s %s\n' "$intent" "$source"
}

# Clone-local first, then PATH. The clone-local helper is version-pinned by the
# repository, so it wins over whatever a package manager happens to have
# installed globally; both still have to pass the probe below.
git_loopy_tui_discover() {
  local repo_root="${1:?repository root is required}"

  local pinned="$repo_root/$GIT_LOOPY_TUI_CLONE_RELATIVE_PATH"
  if [[ -x "$pinned" ]]; then
    printf 'clone-local %s\n' "$pinned"
    return 0
  fi

  local discovered
  discovered="$(command -v "$GIT_LOOPY_TUI_COMMAND_NAME" 2>/dev/null)" || return 1
  [[ -n "$discovered" ]] || return 1
  printf 'path %s\n' "$discovered"
}

# The pre-fullscreen gate. `--schema-version` is the only helper invocation that
# is safe before a decision: it reads no stdin and touches no terminal, so a
# helper that turns out to be incompatible never gets to blank the screen first.
#
# The answer is a *range* because a later helper may decode more than one
# Event-schema version at once, so the test is containment, not equality.
git_loopy_tui_probe() {
  local helper="${1:?helper path is required}"

  local probe
  probe="$("$helper" --schema-version 2>/dev/null)" || return 1
  jq -e \
    --argjson wanted "$GIT_LOOPY_EVENT_SCHEMA_VERSION" \
    '
      type == "object"
      and (.min_event_schema_version | type) == "number"
      and (.max_event_schema_version | type) == "number"
      and .min_event_schema_version <= $wanted
      and $wanted <= .max_event_schema_version
    ' <<<"$probe" >/dev/null 2>&1 || return 1

  jq -r 'if (.version | type) == "string" then .version else "" end' \
    <<<"$probe" 2>/dev/null
}

# Contract §16: Release equality is product identity, never a compatibility
# authority. A helper staged as part of *this* distribution must match exactly
# and fails closed on drift; an externally discovered one may still run on the
# strength of the schema probe, but the operator is told the Releases differ.
git_loopy_tui_check_release_identity() {
  local source="${1:?helper source is required}"
  local helper_version="${2-}"
  local release_version="${3-}"

  [[ -n "$helper_version" && -n "$release_version" ]] || return 0
  [[ "$helper_version" != "$release_version" ]] || return 0

  if [[ "$source" == "clone-local" ]]; then
    return 1
  fi
  _git_loopy_tui_warn \
    "the $GIT_LOOPY_TUI_COMMAND_NAME helper on PATH reports Release version $helper_version, not $release_version; its Event-schema support is compatible, so the live interface continues"
  return 0
}

# Transport is a FIFO rather than a process substitution so the child has a real
# PID to wait on and reap — `$!` after `>(...)` is not dependable on every Bash
# this port supports. The parent opens the FIFO read-write (`<>`) because a
# write-only open blocks until a reader arrives, which would hang a Run whose
# helper died during startup. Closing that one descriptor still yields EOF at the
# child, because the parent is then the only writer that existed.
git_loopy_tui_start() {
  local helper="${1:?helper path is required}"

  ((_GIT_LOOPY_TUI_RETIRED == 0)) || return 1

  local fifo_dir
  fifo_dir="$(mktemp -d "${TMPDIR:-/tmp}/git-loopy-tui.XXXXXX")" || return 1
  local fifo="$fifo_dir/stdin"
  mkfifo "$fifo" || {
    rm -rf "$fifo_dir"
    return 1
  }

  "$helper" <"$fifo" &
  local pid=$!
  if ! eval "exec $_GIT_LOOPY_TUI_FD<>\"\$fifo\""; then
    kill "$pid" 2>/dev/null
    wait "$pid" 2>/dev/null
    rm -rf "$fifo_dir"
    return 1
  fi

  _GIT_LOOPY_TUI_PID="$pid"
  _GIT_LOOPY_TUI_FIFO_DIR="$fifo_dir"
  # shellcheck disable=SC2034  # read by the boundary suite and by diagnostics
  GIT_LOOPY_TUI_HELPER_PATH="$helper"
  GIT_LOOPY_TUI_ACTIVE=1
  GIT_LOOPY_LIVE_SINK="git_loopy_tui_live_sink"
}

_git_loopy_tui_close_fd() {
  [[ -n "$_GIT_LOOPY_TUI_FIFO_DIR" ]] || return 0
  eval "exec $_GIT_LOOPY_TUI_FD>&-" 2>/dev/null || true
}

# One diagnostic, one direction. The line that could not be delivered is not
# dropped: it goes to stdout with everything after it, so the raw stream a
# consumer sees is still complete from the operator's point of view.
git_loopy_tui_fallback() {
  local reason="${1:-the live interface stopped}"

  if ((GIT_LOOPY_TUI_ACTIVE == 1)); then
    GIT_LOOPY_TUI_ACTIVE=0
    _GIT_LOOPY_TUI_RETIRED=1
    GIT_LOOPY_LIVE_SINK="git_loopy_live_sink_stdout"
    _git_loopy_tui_close_fd
    _git_loopy_tui_warn "$reason; continuing with raw JSONL output on stdout"
  fi
}

git_loopy_tui_live_sink() {
  local line="$1"

  if ((GIT_LOOPY_TUI_ACTIVE == 0)); then
    git_loopy_live_sink_stdout "$line"
    return 0
  fi

  # SIGPIPE is ignored *only* across the write itself. Ignoring it for the whole
  # Run would be inherited through `exec` by the agent process and every tool it
  # starts, quietly changing how they behave on a closed pipe; a Run's live
  # interface has no business reaching that far.
  local delivered=0
  trap '' PIPE
  if eval "printf '%s\n' \"\$line\" >&$_GIT_LOOPY_TUI_FD" 2>/dev/null; then
    delivered=1
  fi
  trap - PIPE

  if ((delivered == 0)); then
    git_loopy_tui_fallback \
      "the $GIT_LOOPY_TUI_COMMAND_NAME helper stopped reading the Event stream"
    git_loopy_live_sink_stdout "$line"
    return 0
  fi

  # A child that exited after accepting the write would otherwise only be noticed
  # on the *next* Event — and a Run whose last Event is the one that vanished
  # would never report it at all.
  if [[ -n "$_GIT_LOOPY_TUI_PID" ]] &&
    ! kill -0 "$_GIT_LOOPY_TUI_PID" 2>/dev/null; then
    git_loopy_tui_fallback "the $GIT_LOOPY_TUI_COMMAND_NAME helper exited"
  fi
}

# Run-end teardown. Closing stdin is the child's cue to draw its final frame,
# restore the terminal, and exit; the grace period bounds how long a Run waits
# for a child that does not take the cue. The Run's own exit code is never
# touched here, which is why this returns success unconditionally.
git_loopy_tui_finish() {
  local grace="${GIT_LOOPY_TUI_GRACE_SECONDS:-5}"
  [[ "$grace" =~ ^[0-9]+([.][0-9]+)?$ ]] || grace=5

  if [[ -n "$_GIT_LOOPY_TUI_PID" ]]; then
    GIT_LOOPY_TUI_ACTIVE=0
    # shellcheck disable=SC2034  # consumed by git_loopy_emit_event in events.sh
    GIT_LOOPY_LIVE_SINK="git_loopy_live_sink_stdout"
    _git_loopy_tui_close_fd

    local waited=0
    local step=10
    local limit
    limit="$(
      awk -v grace="$grace" 'BEGIN { printf "%d", grace * 100 }' 2>/dev/null
    )" || limit=500
    [[ "$limit" =~ ^[0-9]+$ ]] || limit=500
    while kill -0 "$_GIT_LOOPY_TUI_PID" 2>/dev/null; do
      if ((waited >= limit)); then
        kill -TERM "$_GIT_LOOPY_TUI_PID" 2>/dev/null || true
        break
      fi
      sleep 0.1
      waited=$((waited + step))
    done
    wait "$_GIT_LOOPY_TUI_PID" 2>/dev/null || true
    _GIT_LOOPY_TUI_PID=""
  fi

  if [[ -n "$_GIT_LOOPY_TUI_FIFO_DIR" ]]; then
    rm -rf "$_GIT_LOOPY_TUI_FIFO_DIR"
    _GIT_LOOPY_TUI_FIFO_DIR=""
  fi
  return 0
}

# The single entry point the Run uses: resolve the intent, and when it is `on`,
# earn the live interface by discovering and probing a helper. Every unfulfilled
# outcome leaves the stdout sink exactly as it was, so the caller has nothing to
# undo. An explicit request that cannot be met says why; an auto-detected miss
# stays one line, because the operator never asked for anything.
git_loopy_tui_begin() {
  local repo_root="${1:?repository root is required}"
  local flag="${2-}"
  local env_value="${3-}"
  local release_version="${4-}"
  local isatty=0
  [[ -t 1 ]] && isatty=1

  local resolved
  resolved="$(git_loopy_tui_resolve_intent "$flag" "$env_value" "$isatty")" ||
    return 0
  local intent="${resolved%% *}"
  local source="${resolved##* }"
  [[ "$intent" == "on" ]] || return 0

  local discovered
  if ! discovered="$(git_loopy_tui_discover "$repo_root")"; then
    if [[ "$source" == "explicit" ]]; then
      _git_loopy_tui_warn \
        "interactive mode was requested but no $GIT_LOOPY_TUI_COMMAND_NAME helper was found in $GIT_LOOPY_TUI_CLONE_RELATIVE_PATH or on PATH; continuing with raw JSONL output on stdout"
    else
      _git_loopy_tui_warn \
        "no $GIT_LOOPY_TUI_COMMAND_NAME helper found; using plain output"
    fi
    return 0
  fi

  local helper_source="${discovered%% *}"
  local helper="${discovered#* }"

  local helper_version
  if ! helper_version="$(git_loopy_tui_probe "$helper")"; then
    if [[ "$source" == "explicit" ]]; then
      _git_loopy_tui_warn \
        "interactive mode was requested but $helper does not support Event schema $GIT_LOOPY_EVENT_SCHEMA_VERSION; continuing with raw JSONL output on stdout"
    else
      _git_loopy_tui_warn \
        "$GIT_LOOPY_TUI_COMMAND_NAME is not schema-compatible; using plain output"
    fi
    return 0
  fi

  if ! git_loopy_tui_check_release_identity \
    "$helper_source" "$helper_version" "$release_version"; then
    _git_loopy_tui_warn \
      "the pinned $helper reports Release version $helper_version, not $release_version; reinstall it to match this clone. Continuing with raw JSONL output on stdout"
    return 0
  fi

  # shellcheck disable=SC2034  # read by the boundary suite and by diagnostics
  GIT_LOOPY_TUI_HELPER_SOURCE="$helper_source"
  if ! git_loopy_tui_start "$helper"; then
    _git_loopy_tui_warn \
      "the $GIT_LOOPY_TUI_COMMAND_NAME helper could not be started; continuing with raw JSONL output on stdout"
    return 0
  fi
}
