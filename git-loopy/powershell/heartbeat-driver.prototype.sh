#!/usr/bin/env bash
# PROTOTYPE — THROWAWAY driver for the PowerShell renewer shapes.
# Start-Job runs the renewer in a separate pwsh process; Start-ThreadJob runs it
# in-process. Under a SIGKILLed parent those differ exactly where it matters.

set -uo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

printf '%-8s %-11s %-9s %-10s %s\n' MODE BEATS/4s AT-KILL AFTER-4s VERDICT
printf '%s\n' "------------------------------------------------------------------"

for mode in process thread; do
  beats="$(mktemp)"
  pwsh -NoLogo -NoProfile -File "$here/heartbeat.prototype.ps1" \
    -Mode "$mode" -BeatFile "$beats" -Interval 1 >/dev/null 2>&1 &
  parent=$!

  sleep 4
  during=$(wc -l <"$beats" | tr -d ' ')

  kill -9 "$parent" 2>/dev/null
  wait "$parent" 2>/dev/null
  at_kill=$(wc -l <"$beats" | tr -d ' ')

  sleep 4
  after=$(wc -l <"$beats" | tr -d ' ')

  if ((during == 0)); then
    verdict="BROKEN: never renewed during a silent session"
  elif ((after > at_kill)); then
    verdict="LEAK: orphan kept renewing -> Lease never expires"
  else
    verdict="OK: renewed, then died with its parent"
  fi
  printf '%-8s %-11s %-9s %-10s %s\n' "$mode" "$during" "$at_kill" "$after" "$verdict"
  rm -f "$beats"
done

printf '\nsurviving renewer processes: '
pgrep -fl heartbeat.prototype 2>/dev/null || printf 'none\n'
