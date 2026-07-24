#!/usr/bin/env bash

# PROTOTYPE: Can native shell Reconciliation represent stable durable facts as
# satisfied/unsatisfied/unverified and quarantine only the affected Action
# while retaining independent Ready/Blocked guidance?

prototype_reconciliation_apply() {
  local state="$1"
  local fact="$2"
  local status="$3"
  jq -c --arg fact "$fact" --arg status "$status" \
    '.facts[$fact] = $status' <<<"$state"
}

prototype_reconciliation_project() {
  local state="$1"
  jq -c '
    . as $state
    | def project:
      . as $action
      | $state.facts[$action.completion_fact] as $completion
      | if $completion == "satisfied" then
          {retired: $action.key}
        elif $completion == "unverified" then
          {diagnostic: {code: "unverified_completion", action_key: $action.key}}
        else
          [$action.prerequisite_facts[] | select($state.facts[.] == "unverified")] as $unknown
          | [$action.prerequisite_facts[] | select($state.facts[.] == "unsatisfied")] as $blocked
          | if ($unknown | length) > 0 then
              {diagnostic: {code: "unverified_prerequisite", action_key: $action.key}}
            else
              {
                action: {
                  key: $action.key,
                  readiness: (if ($blocked | length) > 0 then "Blocked" else "Ready" end),
                  unsatisfied_prerequisites: $blocked
                }
              }
            end
        end;
    [.actions[] | project] as $projected
    | {
        facts: $state.facts,
        actions: [$projected[] | .action? | select(. != null)],
        diagnostics: [$projected[] | .diagnostic? | select(. != null)],
        retired: [$projected[] | .retired? | select(. != null)]
      }
  ' <<<"$state"
}
