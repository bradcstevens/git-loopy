#!/usr/bin/env bash
# PROTOTYPE — THROWAWAY driver. Prints full state after every action.
#
# For each renewer strategy: prove it beats while the "agent session" is silent,
# then SIGKILL the parent and see whether beats keep arriving. Beats arriving
# after the parent is dead == the Lease would never expire.

set -uo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
spike="$here/heartbeat.prototype.sh"
interval=1
silent_window=3
orphan_window=4

printf '%-12s %-9s %-9s %-9s %-8s %s\n' \
  MODE BEATS/3s AT-KILL AFTER-4s ORPHAN VERDICT
printf '%s\n' "----------------------------------------------------------------------"

for mode in naive pid-poll death-pipe; do
  beats="$(mktemp)"
  bash "$spike" parent "$mode" "$beats" "$interval" &
  parent=$!

  sleep "$silent_window"
  during=$(wc -l <"$beats" | tr -d ' ')

  kill -9 "$parent" 2>/dev/null
  wait "$parent" 2>/dev/null
  at_kill=$(wc -l <"$beats" | tr -d ' ')

  sleep "$orphan_window"
  after=$(wc -l <"$beats" | tr -d ' ')

  # Any renewer still alive is an orphan holding the Lease open.
  orphans=$(pgrep -P 1 -f "heartbeat.prototype" 2>/dev/null | wc -l | tr -d ' ')

  if ((during == 0)); then
    verdict="BROKEN: never renewed during a silent session"
  elif ((after > at_kill)); then
    verdict="LEAK: orphan kept renewing -> Lease never expires"
  else
    verdict="OK: renewed, then died with its parent"
  fi

  printf '%-12s %-9s %-9s %-9s %-8s %s\n' \
    "$mode" "$during" "$at_kill" "$after" "$orphans" "$verdict"

  # Reap anything the strategy failed to clean up, so modes stay independent.
  for stray in $(pgrep -f "heartbeat.prototype" 2>/dev/null); do
    [[ "$stray" == "$$" ]] || kill -9 "$stray" 2>/dev/null
  done
  rm -f "$beats"
done
