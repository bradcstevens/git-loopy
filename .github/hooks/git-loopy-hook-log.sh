#!/usr/bin/env bash
set -euo pipefail

event=""
exit_status=""
decision=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --event) event="${2:?missing event}"; shift 2 ;;
    --exit-status) exit_status="${2:?missing exit status}"; shift 2 ;;
    --decision) decision="${2?missing decision}"; shift 2 ;;
    *) exit 2 ;;
  esac
done
[ -n "$event" ] && [ -n "$exit_status" ] || exit 2
repository_root="$(
  git worktree list --porcelain |
    awk '/^worktree / { sub(/^worktree /, ""); print; exit }'
)"
[ -n "$repository_root" ] || exit 1
log_path="$repository_root/.git-loopy/hook-invocations.jsonl"
mkdir -p "$(dirname "$log_path")"
payload_file="$(mktemp "${TMPDIR:-/tmp}/git-loopy-hook-payload.XXXXXX")"
trap 'rm -f "$payload_file"' EXIT
cat > "$payload_file"
python3 - "$log_path" "$event" "$exit_status" "$decision" "$payload_file" <<'PY'
import datetime
import json
import sys

log_path, event, exit_status, output, payload_path = sys.argv[1:]
with open(payload_path, encoding="utf-8") as payload_file:
    payload_text = payload_file.read()
try:
    payload = json.loads(payload_text)
except json.JSONDecodeError:
    run = {"payload_valid": False, "payload_excerpt": payload_text[:512]}
else:
    if not isinstance(payload, dict):
        run = {"payload_valid": False, "payload_excerpt": payload_text[:512]}
    else:
        run = {
            "session_id": payload.get("sessionId"),
            "agent_id": payload.get("agentId"),
            "agent_type": payload.get("agentType"),
            "agent_name": payload.get("agentName"),
            "stop_reason": payload.get("stopReason"),
            "timestamp": payload.get("timestamp"),
        }
try:
    decision = json.loads(output)
except json.JSONDecodeError:
    if int(exit_status) == 0:
        decision = {"decision": "invalid-output", "output": output[:512]}
    else:
        decision = {"decision": "error", "exit_status": int(exit_status)}
record = {
    "logged_at": datetime.datetime.now(datetime.timezone.utc)
    .isoformat(timespec="seconds")
    .replace("+00:00", "Z"),
    "event": event,
    "run": run,
    "decision": decision,
}
with open(log_path, "a", encoding="utf-8") as log_file:
    log_file.write(json.dumps(record, separators=(",", ":")) + "\n")
PY
