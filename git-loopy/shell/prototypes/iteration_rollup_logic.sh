#!/usr/bin/env bash

# PROTOTYPE: Can shell-observable facts produce the normalized Iteration payload
# while monotonic time survives wall-clock changes, fallback binding covers
# pre-marker work, and only source closure populates closure-only fields?

shell_rollup_initial_state() {
  jq -cn '{
    iter: 1,
    wall: 1784833200,
    mono: 100,
    iter_started_wall: 1784833200,
    iter_started_mono: 100,
    active: null,
    commits: 0,
    auto_closures: 0,
    pr_advances: 0,
    strikes: 0,
    cumulative: {},
    first_started: {},
    first_started_mono: {},
    finalized: []
  }'
}

shell_rollup_reduce() {
  local state="${1:?state is required}"
  local action="${2:?action is required}"

  jq -c --arg action "$action" '
    def iso:
      strftime("%Y-%m-%dT%H:%M:%S") + ".000Z";
    def bind($source):
      .active = {
        issue: 42,
        source: $source,
        activated_wall: (
          if $source == "working_marker" then .wall else .iter_started_wall end
        ),
        activated_mono: (
          if $source == "working_marker" then .mono else .iter_started_mono end
        ),
        closed_wall: null,
        closed_mono: null,
        advanced: false
      }
      | .first_started["42"] //= .active.activated_wall
      | .first_started_mono["42"] //= .active.activated_mono;
    if $action == "tick" then
      .wall += 1 | .mono += 1
    elif $action == "wall-back" then
      .wall -= 30
    elif $action == "marker" and .active == null then
      bind("working_marker")
    elif $action == "fallback" and .active == null then
      bind("single_member_pool")
    elif $action == "commit" then
      .commits += 1
    elif $action == "advance" and .active != null then
      .pr_advances += 1 | .active.advanced = true
    elif $action == "close" and .active != null then
      .auto_closures += 1
      | .active.closed_wall = .wall
      | .active.closed_mono = .mono
    elif $action == "finish" then
      . as $state
      | (
          if .active == null then
            []
          else
            (.active.closed_mono // .mono) as $ended
            | ($ended - .active.activated_mono | if . < 0 then 0 else . end) as $active
            | ((.cumulative["42"] // 0) + $active) as $cumulative
            | .cumulative["42"] = $cumulative
            | [{
                issue: .active.issue,
                status: (
                  if .active.closed_wall != null then "closed"
                  elif .active.advanced or .commits > 0 then "advanced"
                  else "no-progress"
                  end
                ),
                first_started_at: (.first_started["42"] | iso),
                closed_at: (
                  if .active.closed_wall == null then null
                  else (.active.closed_wall | iso)
                  end
                ),
                issue_elapsed_seconds: (
                  if .active.closed_mono == null then null
                  else (.active.closed_mono - .first_started_mono["42"])
                  end
                ),
                active_seconds: $active,
                cumulative_active_seconds: $cumulative,
                consumption: {model: null, tokens_in: null, tokens_out: null},
                cost_usd: null,
                peak_context_window: null
              }]
          end
        ) as $issues
      | {
          outcome: (
            if ($issues | length) == 0 then "no_progress"
            else $issues[0].status
            end
          ),
          duration_seconds: (
            .mono - .iter_started_mono | if . < 0 then 0 else . end
          ),
          summary: {
            model: null,
            tokens_in: null,
            tokens_out: null,
            observed_tokens: null,
            cost_usd: null,
            tool_count: null,
            skill_call_count: null,
            skills_consulted: null,
            commits: .commits,
            auto_closures: .auto_closures,
            pr_advances: .pr_advances,
            strikes: .strikes,
            peak_context_window: null
          },
          issues: $issues
        } as $payload
      | $state
      | .finalized += [$payload]
      | .iter += 1
      | .iter_started_wall = .wall
      | .iter_started_mono = .mono
      | .active = null
      | .commits = 0
      | .auto_closures = 0
      | .pr_advances = 0
      | if ($issues | length) == 1 then
          .cumulative["42"] = $issues[0].cumulative_active_seconds
        else
          .
        end
    else
      .
    end
  ' <<<"$state"
}
