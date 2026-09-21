#!/usr/bin/env bash

# ADR-0033: staged pure inspection, not a Lease transport or a side-effect fence.
# Input is {raw: nullable JSON message string, now, repository, issue,
#           skew_tolerance_seconds?: 60}. A failed fetch must never become null.
git_loopy_inspect_lease() {
  jq -cn --argjson input "$1" '
    def whole($minimum):
      if type != "number" then false
      else . >= $minimum and . <= 9007199254740991 and . == floor end;
    def matches($pattern):
      if type == "string" then test($pattern) else false end;
    def malformed:
      {state: "expired", record: null, diagnostics: ["malformed_record"]};
    $input
    | (if has("skew_tolerance_seconds") then .skew_tolerance_seconds else 60 end) as $skew
    | if (.now | whole(0) | not) then error("Lease inspection: invalid now")
      elif (.issue | whole(1) | not) then error("Lease inspection: invalid issue")
      elif ($skew | whole(0) | not) then error("Lease inspection: invalid skew_tolerance_seconds")
      elif (.repository | matches("\\A[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\\z") | not)
        then error("Lease inspection: invalid repository")
      elif (has("raw") | not) then error("Lease inspection: invalid raw")
      elif .raw == null then {state: "absent", record: null, diagnostics: []}
      else
        (try (.raw | fromjson) catch null) as $record
        | if ($record | type) != "object" then malformed
          elif ($record.run_id | matches("\\A[0-7][0-9A-HJKMNP-TV-Z]{25}\\z") | not)
            then malformed
          elif ($record.repository | matches("\\A[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\\z") | not)
            then malformed
          elif ($record.repository | ascii_downcase) != (.repository | ascii_downcase)
            then malformed
          elif ($record.host | type) != "string" or $record.host == "" then malformed
          elif ($record.issue | whole(1) | not)
            or ($record.claimed_at | whole(0) | not)
            or ($record.heartbeat_at | whole(0) | not)
            or ($record.ttl_seconds | whole(1) | not)
            or ($record.pid | whole(1) | not) then malformed
          elif $record.issue != .issue or $record.heartbeat_at < $record.claimed_at
            then malformed
          else {
            state: (if .now - $record.heartbeat_at > $record.ttl_seconds
              then "expired" else "live" end),
            record: ($record | {
              run_id, issue, repository, claimed_at, heartbeat_at, ttl_seconds, host, pid
            }),
            diagnostics: (if $record.claimed_at - .now > $skew
              then ["clock_skew"] else [] end)
          } end
      end
  '
}
