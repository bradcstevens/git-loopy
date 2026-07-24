#!/usr/bin/env bash

# PROTOTYPE: pure jq reducer for testing immutable Producer revision head rules.

git_loopy_revision_prototype_initial_state() {
  jq -cn '{
    next_id: 1,
    revisions: [],
    heads: [],
    tainted_heads: [],
    index_label: false,
    status: "waiting"
  }'
}

git_loopy_revision_prototype_apply() {
  local state="$1"
  local action="$2"
  jq -c --arg action "$action" '
    def refresh:
      .status = (
        if (.tainted_heads | length) > 0 then "unverified"
        elif (.heads | length) > 1 then "conflict"
        elif (.heads | length) == 1 then "guidance"
        else "waiting"
        end
      );
    def append($semantics; $parents; $reattests):
      ("r" + (.next_id | tostring)) as $id
      | .next_id += 1
      | .revisions += [{
          id: $id,
          semantics: $semantics,
          parents: $parents,
          reattests: $reattests
        }]
      | .heads = ((.heads - $parents) + [$id] | sort)
      | .tainted_heads -= $reattests;
    if $action == "root" then
      append("A"; []; []) | refresh
    elif $action == "fork" then
      append("B"; []; []) | refresh
    elif $action == "resolve" then
      append("resolved"; .heads; []) | refresh
    elif $action == "mutate" and (.heads | length) > 0 then
      .tainted_heads = [.[
        "heads"
      ][0]] | refresh
    elif $action == "reattest" and (.tainted_heads | length) > 0 then
      append("re-attested"; .tainted_heads; .tainted_heads) | refresh
    elif $action == "toggle-index" then
      .index_label = (.index_label | not) | refresh
    else
      refresh
    end
  ' <<<"$state"
}
