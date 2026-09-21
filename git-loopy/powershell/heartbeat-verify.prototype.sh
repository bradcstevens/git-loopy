#!/usr/bin/env bash
# PROTOTYPE — THROWAWAY. Confirm Start-Job really is out-of-process, and that
# its child dies with a SIGKILLed parent rather than the window being too short.
set -uo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

beats="$(mktemp)"
pwsh -NoLogo -NoProfile -File "$here/heartbeat.prototype.ps1" \
  -Mode process -BeatFile "$beats" -Interval 1 >/dev/null 2>&1 &
parent=$!
sleep 5

echo "parent pid         : $parent"
echo "pwsh processes now : $(pgrep pwsh | wc -l | tr -d ' ')"
echo "children of parent : $(pgrep -P "$parent" | tr '\n' ' ')"
echo "beats before kill  : $(wc -l <"$beats" | tr -d ' ')"

kill -9 "$parent" 2>/dev/null
wait "$parent" 2>/dev/null
echo "--- parent SIGKILLed ---"

for w in 3 6 12 20; do
  sleep 3
  echo "  +${w}s beats=$(wc -l <"$beats" | tr -d ' ')  pwsh_procs=$(pgrep pwsh | wc -l | tr -d ' ')"
done
rm -f "$beats"
