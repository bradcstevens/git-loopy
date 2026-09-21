#!/usr/bin/env bash

# Pure remote URL -> JSON owner/repo or null, staged ahead of native transport.
git_loopy_repository_from_remote_url() {
  jq -cn --arg url "$1" '
    def slug:
      gsub("\\A/+|/+\\z"; "") | split("/")
      | if length != 2 then null
        else .[1] |= sub("\\.[gG][iI][tT]\\z"; "")
        | if all(.[]; test("\\A[A-Za-z0-9_.-]+\\z"))
          then join("/") else null end
        end;
    $url | gsub("\\A\\s+|\\s+\\z"; "")
    | if contains("://") then
        gsub("[\t\r\n]"; "")
        | [capture("\\A(?<scheme>[A-Za-z][A-Za-z0-9+.-]*)://(?<authority>[^/?#]*)(?<path>[^?#]*)")][0]
        | if . == null then null
          elif (.scheme | ascii_downcase) as $scheme
            | (["ssh", "git", "http", "https", "git+ssh"] | index($scheme)) == null
            then null
          elif (.authority | sub(".*@"; "") | test("\\A[^:]") | not)
            then null
          else .path | slug end
      else
        [capture("\\A(?:[^/@]+@)?[^/:]{2,}:(?<path>[^/].*)\\z")][0]
        | if . == null then null else .path | slug end
      end
  '
}

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

# ADR-0033: the pure Lease action decision, identical in every member.
# Input is {action, state, owner: nullable run_id, run_id}; prints the verdict.
# Only identity confers ownership, and an unreadable record is owned by nobody.
git_loopy_decide_lease_action() {
  jq -rn --argjson input "$1" '
    $input
    | .action as $action
    | .state as $state
    | (.owner != null and .owner == .run_id) as $ours
    | if (["claim", "release", "fence"] | index($action)) == null
        then error("Lease action: invalid action \($action)")
      elif (["absent", "live", "expired"] | index($state)) == null
        then error("Lease action: invalid state \($state)")
      elif $action == "claim" then
        if $state == "live" then "refuse"
        elif $state == "absent" then "claim"
        else "steal" end
      elif $action == "fence" then (if $ours then "hold" else "lost" end)
      elif $state == "absent" then "absent"
      elif $ours then "release"
      else "not_owned" end
  '
}
