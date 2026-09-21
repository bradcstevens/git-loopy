#!/usr/bin/env bash
# PROTOTYPE — THROWAWAY. Not production, not gated, not sourced by anything.
#
# Question (ADR-0033 slice 5): can Bash run a Lease renewer that is independent
# of agent progress AND does not outlive a SIGKILLed parent?
#
# The second half is the one that matters. orchestrator.sh:2555's monotonic
# ticker is stopped by an explicit `kill -TERM` from the parent, so a SIGKILLed
# parent orphans it. For a tick counter that is harmless. For a Lease renewer it
# is fatal: an orphan keeps pushing heartbeats, the Lease never expires, and
# crash recovery -- the entire point of the TTL -- silently stops working.
#
# Renewal is simulated by appending a line to a file. This spike is about
# process lifetime, not about git transport.

set -uo pipefail

beat() { printf 'beat %s\n' "$(date +%s)" >>"$1"; }

# A: the orchestrator.sh:2555 ticker pattern, applied verbatim to a Lease.
renew_naive() {
  local beat_file=$1 interval=$2
  (
    trap 'exit 0' TERM INT
    while sleep "$interval"; do beat "$beat_file"; done
  ) &
  printf '%s\n' "$!"
}

# B: poll the parent's liveness each tick and exit when it is gone.
renew_pid_poll() {
  local beat_file=$1 interval=$2 parent_pid=$$
  (
    trap 'exit 0' TERM INT
    while sleep "$interval"; do
      kill -0 "$parent_pid" 2>/dev/null || exit 0
      beat "$beat_file"
    done
  ) &
  printf '%s\n' "$!"
}

# C: block on a pipe the parent holds open. Parent death closes the last write
# end, the read sees EOF, and the renewer exits -- no polling, no PID reuse.
renew_death_pipe() {
  local beat_file=$1 interval=$2 fifo
  fifo="$(mktemp -u)"
  mkfifo "$fifo" || return 1
  # <> so opening does not block waiting for a reader.
  exec {parent_w}<>"$fifo"
  (
    exec {parent_w}>&-        # the renewer must NOT hold a write end open
    exec {r}<"$fifo"
    trap 'exit 0' TERM INT
    local rc
    while :; do
      read -t "$interval" -u "$r" _
      rc=$?
      if ((rc > 128)); then
        beat "$beat_file"     # interval elapsed
      else
        exit 0                # EOF (parent gone) or read error
      fi
    done
  ) &
  printf '%s\n' "$!"
  rm -f "$fifo"
}

# Simulate a long, silent agent session: start a renewer, then block forever.
# The driver SIGKILLs this process and watches whether beats keep arriving.
if [[ "${1:-}" == "parent" ]]; then
  mode=$2 beat_file=$3 interval=$4
  case "$mode" in
    naive) renew_naive "$beat_file" "$interval" >/dev/null ;;
    pid-poll) renew_pid_poll "$beat_file" "$interval" >/dev/null ;;
    death-pipe) renew_death_pipe "$beat_file" "$interval" >/dev/null ;;
    *) printf 'unknown mode %s\n' "$mode" >&2; exit 2 ;;
  esac
  while :; do sleep 3600; done   # the agent session that produces no output
fi
