#!/usr/bin/env bash

GIT_LOOPY_CONTINUATION_CONTRACT_VERSION="1.0"
GIT_LOOPY_CONTINUATION_RECORD_FORMAT=1
GIT_LOOPY_CONTINUATION_WRAPPER_CONTRACT_VERSION="1.2"
GIT_LOOPY_CONTINUATION_EVENT_SCHEMA_VERSION="1.1"
GIT_LOOPY_CONTINUATION_INDEX_LABEL="git-loopy-continuation"
GIT_LOOPY_CONTINUATION_RECORD_MARKER="<!-- git-loopy-continuation:1 -->"
GIT_LOOPY_CONTINUATION_REQUEST=""

git_loopy_continuation_usage() {
  cat <<'EOF'
Usage: git-loopy.sh continuation <operation> [options]

Operations:
  capabilities
  publish [--input FILE]
  reconcile [--input FILE] [--terminal]
  record-dispatch-result [--input FILE]
  repair-index [--input FILE]
EOF
}

git_loopy_continuation_capabilities() {
  cat <<'EOF'
{"ok":true,"capabilities":{"continuation_contract_versions":["1.0"],"record_formats":[1],"wrapper_contract_version":"1.2","event_schema_version":"1.1","tracker_adapters":{"github":{"operations":["publish","reconcile"]}},"operations":{"capabilities":true,"publish":true,"reconcile":true,"record-dispatch-result":false,"repair-index":false},"instruction_handlers":[],"instruction_modes":[],"evaluators":[],"effect_scopes":[],"optional_capabilities":{"terminal_rendering":false,"concurrent_dispatch":false},"continuation_modes":{"default":"off","off":true,"report":false,"execute-frontier":false}}}
EOF
}

_git_loopy_continuation_error() {
  local operation="$1"
  local code="$2"
  local message="$3"
  jq -cn \
    --arg operation "$operation" \
    --arg code "$code" \
    --arg message "$message" \
    '{ok:false,operation:$operation,error:{code:$code,message:$message}}'
  printf 'git-loopy continuation: %s\n' "$message" >&2
  return 1
}

_git_loopy_continuation_github_error() {
  local operation="$1"
  local context="$2"
  _git_loopy_continuation_error \
    "$operation" \
    "github_error" \
    "GitHub operation failed while $context"
}

_git_loopy_continuation_read_request() {
  local operation="$1"
  local input_path="$2"
  local raw
  if [[ -n "$input_path" ]]; then
    if [[ ! -r "$input_path" ]]; then
      _git_loopy_continuation_error \
        "$operation" \
        "invalid_request" \
        "could not read request: $input_path"
      return 1
    fi
    raw="$(<"$input_path")"
  else
    raw="$(cat)"
  fi

  local parsed
  if ! parsed="$(jq -cse \
    'if length == 1 and (.[0] | type == "object")
     then .[0]
     else error("request must be one UTF-8 JSON object")
     end' <<<"$raw" 2>/dev/null)"; then
    _git_loopy_continuation_error \
      "$operation" \
      "invalid_request" \
      "request must be one UTF-8 JSON object"
    return 1
  fi
  GIT_LOOPY_CONTINUATION_REQUEST="$parsed"
}

_git_loopy_continuation_sha256() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum | awk '{print $1}'
  else
    shasum -a 256 | awk '{print $1}'
  fi
}

_git_loopy_continuation_semantic_fingerprint() {
  local action="$1"
  local semantics
  semantics="$(
    jq -cS '
      def without_advisory:
        if type == "object" then
          with_entries(
            select(.key != "advisory_extensions")
            | .value |= without_advisory
          )
        elif type == "array" then map(without_advisory)
        else .
        end;
      {
        instruction,
        prerequisites,
        interaction,
        completion_condition,
        effects: (.effects // []),
        requirements: (.requirements // []),
        triggers: (.triggers // [])
      }
      | without_advisory
    ' <<<"$action"
  )"
  printf '%s' "$semantics" | _git_loopy_continuation_sha256
}

_git_loopy_continuation_fingerprints() {
  local completion="$1"
  local fingerprints="{}"
  local action
  while IFS= read -r action; do
    local key fingerprint
    key="$(jq -r '.key' <<<"$action")"
    fingerprint="$(_git_loopy_continuation_semantic_fingerprint "$action")"
    fingerprints="$(
      jq -cn \
        --argjson fingerprints "$fingerprints" \
        --arg key "$key" \
        --arg fingerprint "$fingerprint" \
        '$fingerprints + {($key): $fingerprint}'
    )"
  done < <(jq -c '.actions[]?' <<<"$completion")
  printf '%s\n' "$fingerprints"
}

_git_loopy_continuation_validate_tracer_request() {
  local request="$1"
  jq -e '
    (.repository | type == "string" and test("^[^/]+/[^/]+$"))
    and (.trusted_producers | type == "array" and length > 0)
    and (all(.trusted_producers[]; type == "string" and length > 0))
    and ((.trusted_producers | unique | length) == (.trusted_producers | length))
    and (.completion | type == "object")
    and (.completion.continuation_contract_version == "1.0")
    and (.completion.record_format == 1)
    and (.completion.publication == "shared")
    and (.completion.disposition == "continue")
    and (.completion.workstream.anchor.kind == "issue")
    and (.completion.workstream.anchor.repository == .repository)
    and (.completion.workstream.anchor.number | type == "number")
    and (.completion.workstream.destination.kind == "issue-closed")
    and (.completion.workstream.destination.target.kind == "issue")
    and (.completion.workstream.destination.target.repository == .repository)
    and (.completion.workstream.destination.target.number | type == "number")
    and (.completion.transition.owner | type == "string" and length > 0)
    and (.completion.producer.role == "planning")
    and (.trusted_producers | index($request.completion.producer.login) != null)
    and (.completion.carrier.kind == "issue")
    and (.completion.carrier.repository == .repository)
    and (.completion.carrier.number | type == "number")
    and (.completion.transition.evidence | type == "array" and length > 0)
    and (all(
      .completion.transition.evidence[];
      .kind == "issue-comment"
      and .repository == $request.repository
      and (.comment_id | type == "number")
    ))
    and (.completion.actions | type == "array" and length == 1)
    and (all(
      .completion.actions[];
      (.key | type == "string" and length > 0)
      and (.summary | type == "string" and length > 0)
      and (.kind == "Publish spec")
      and (.occurrence | type == "string" and length > 0)
      and (.instruction.mode == "skill")
      and (.instruction.value | type == "string" and length > 0)
      and (.target.kind == "issue")
      and (.target.repository == $request.repository)
      and (.target.number | type == "number")
      and (.basis | type == "array" and length > 0)
      and (all(
        .basis[];
        .kind == "issue"
        and .repository == $request.repository
        and (.number | type == "number")
      ))
      and (.prerequisites == [])
      and (.interaction.classification == "AFK-safe")
      and (.interaction.evidence.kind == "transition-owner-attestation")
      and (.interaction.evidence.owner == $request.completion.transition.owner)
      and (.completion_condition.kind == "issue-closed")
      and (.completion_condition.target.kind == "issue")
      and (.completion_condition.target.repository == $request.repository)
      and (.completion_condition.target.number | type == "number")
    ))
  ' --argjson request "$request" <<<"$request" >/dev/null 2>&1
}

_git_loopy_continuation_publish() {
  local request="$1"
  if ! _git_loopy_continuation_validate_tracer_request "$request"; then
    _git_loopy_continuation_error \
      "publish" \
      "invalid_request" \
      "request is outside the supported trusted planning publication contract"
    return 1
  fi

  local repository completion carrier carrier_number producer
  repository="$(jq -r '.repository' <<<"$request")"
  completion="$(jq -c '.completion' <<<"$request")"
  carrier="$(jq -c '.carrier' <<<"$completion")"
  carrier_number="$(jq -r '.number' <<<"$carrier")"
  producer="$(jq -r '.producer.login' <<<"$completion")"

  local canonical_completion revision_id fingerprints record body
  canonical_completion="$(jq -cS . <<<"$completion")"
  revision_id="$(
    printf '%s' "$canonical_completion" | _git_loopy_continuation_sha256
  )"
  fingerprints="$(_git_loopy_continuation_fingerprints "$completion")"
  record="$(
    jq -cS \
      --arg revision_id "$revision_id" \
      --argjson fingerprints "$fingerprints" \
      '. + {
        revision_id: $revision_id,
        semantic_fingerprints: $fingerprints
      }' <<<"$completion"
  )"
  body="$GIT_LOOPY_CONTINUATION_RECORD_MARKER"$'\n```json\n'"$record"$'\n```'

  local evidence_id
  while IFS= read -r evidence_id; do
    if ! gh api "repos/$repository/issues/comments/$evidence_id" >/dev/null; then
      _git_loopy_continuation_github_error \
        "publish" \
        "reading transition evidence"
      return 1
    fi
  done < <(jq -r '.transition.evidence[].comment_id' <<<"$completion")
  if ! gh label create "$GIT_LOOPY_CONTINUATION_INDEX_LABEL" \
    --repo "$repository" \
    --color 5319E7 \
    --description "Repairable discovery index for git-loopy Continuation records" \
    --force >/dev/null; then
    _git_loopy_continuation_github_error \
      "publish" \
      "establishing the discovery label"
    return 1
  fi
  if ! gh issue edit "$carrier_number" \
    --repo "$repository" \
    --add-label "$GIT_LOOPY_CONTINUATION_INDEX_LABEL" >/dev/null; then
    _git_loopy_continuation_github_error \
      "publish" \
      "indexing the carrier"
    return 1
  fi

  local appended
  if ! appended="$(
    jq -cn --arg body "$body" '{body:$body}' |
      gh api \
        --method POST \
        "repos/$repository/issues/$carrier_number/comments" \
        --input -
  )"; then
    _git_loopy_continuation_github_error \
      "publish" \
      "appending the Producer revision"
    return 1
  fi
  if ! jq -e 'type == "object"' <<<"$appended" >/dev/null 2>&1; then
    _git_loopy_continuation_github_error \
      "publish" \
      "decoding the appended Producer revision"
    return 1
  fi
  if [[ "$(jq -r '.user.login' <<<"$appended")" != "$producer" ]]; then
    _git_loopy_continuation_error \
      "publish" \
      "invalid_request" \
      "authenticated comment author does not match completion producer"
    return 1
  fi

  local comment_id committed
  comment_id="$(jq -r '.id' <<<"$appended")"
  if ! committed="$(
    gh api "repos/$repository/issues/comments/$comment_id"
  )"; then
    _git_loopy_continuation_github_error \
      "publish" \
      "rereading the Producer revision"
    return 1
  fi
  if ! jq -e 'type == "object"' <<<"$committed" >/dev/null 2>&1; then
    _git_loopy_continuation_github_error \
      "publish" \
      "decoding the committed Producer revision"
    return 1
  fi
  if [[ "$(jq -r '.body' <<<"$committed")" != "$body" ]] ||
    [[ "$(jq -r '.user.login' <<<"$committed")" != "$producer" ]]; then
    _git_loopy_continuation_error \
      "publish" \
      "invalid_request" \
      "Producer revision reread did not match the append"
    return 1
  fi

  jq -cn \
    --arg revision_id "$revision_id" \
    --argjson carrier "$carrier" \
    --argjson comment_id "$comment_id" \
    --arg comment_url "$(jq -r '.html_url' <<<"$committed")" \
    --arg index_label "$GIT_LOOPY_CONTINUATION_INDEX_LABEL" \
    --argjson fingerprints "$fingerprints" \
    '{
      ok: true,
      operation: "publish",
      receipt: {
        status: "committed",
        revision_id: $revision_id,
        carrier: $carrier,
        comment: {id: $comment_id, url: $comment_url},
        index_label: $index_label,
        semantic_fingerprints: $fingerprints
      }
    }'
}

_git_loopy_continuation_comment_id() {
  local comment="$1"
  jq -r '
    if (.databaseId | type) == "number" then .databaseId
    elif (.id | type) == "number" then .id
    else (.url | capture("#issuecomment-(?<id>[0-9]+)$").id | tonumber)
    end
  ' <<<"$comment"
}

_git_loopy_continuation_reconcile() {
  local request="$1"
  if ! jq -e '
    (.repository | type == "string" and test("^[^/]+/[^/]+$"))
    and (.trusted_producers | type == "array" and length > 0)
    and (all(.trusted_producers[]; type == "string" and length > 0))
  ' <<<"$request" >/dev/null 2>&1; then
    _git_loopy_continuation_error \
      "reconcile" \
      "invalid_request" \
      "request is outside the supported trusted Reconciliation contract"
    return 1
  fi

  local repository carriers actions revision_count
  repository="$(jq -r '.repository' <<<"$request")"
  if ! carriers="$(
    gh issue list \
      --repo "$repository" \
      --state all \
      --label "$GIT_LOOPY_CONTINUATION_INDEX_LABEL" \
      --limit 100 \
      --json number,state,url,comments
  )"; then
    _git_loopy_continuation_github_error \
      "reconcile" \
      "discovering indexed carriers"
    return 1
  fi
  if ! jq -e '
    type == "array"
    and all(.[];
      type == "object"
      and (.comments | type == "array")
    )
  ' <<<"$carriers" >/dev/null 2>&1; then
    _git_loopy_continuation_github_error \
      "reconcile" \
      "decoding indexed carriers"
    return 1
  fi
  actions="[]"
  revision_count=0

  local comment
  while IFS= read -r comment; do
    local author
    author="$(jq -r '.author.login' <<<"$comment")"
    if ! jq -e --arg author "$author" \
      '.trusted_producers | index($author) != null' \
      <<<"$request" >/dev/null; then
      continue
    fi

    local body prefix raw record completion expected_revision fingerprints
    body="$(jq -r '.body' <<<"$comment")"
    prefix="$GIT_LOOPY_CONTINUATION_RECORD_MARKER"$'\n```json\n'
    [[ "$body" == "$prefix"* && "$body" == *$'\n```' ]] || continue
    raw="${body#"$prefix"}"
    raw="${raw%$'\n```'}"
    record="$(jq -cS . <<<"$raw" 2>/dev/null)" || continue
    completion="$(jq -cS 'del(.revision_id, .semantic_fingerprints)' <<<"$record")"
    expected_revision="$(
      printf '%s' "$completion" | _git_loopy_continuation_sha256
    )"
    [[ "$(jq -r '.revision_id' <<<"$record")" == "$expected_revision" ]] ||
      continue
    fingerprints="$(_git_loopy_continuation_fingerprints "$completion")"
    local stored_fingerprints
    stored_fingerprints="$(jq -cS '.semantic_fingerprints' <<<"$record")"
    [[ "$stored_fingerprints" == "$(jq -cS . <<<"$fingerprints")" ]] ||
      continue
    [[ "$(jq -r '.producer.login' <<<"$record")" == "$author" ]] || continue
    local validation_request
    validation_request="$(
      jq -cn \
        --arg repository "$repository" \
        --argjson trusted_producers "$(jq -c '.trusted_producers' <<<"$request")" \
        --argjson completion "$completion" \
        '{
          repository: $repository,
          trusted_producers: $trusted_producers,
          completion: $completion
        }'
    )"
    _git_loopy_continuation_validate_tracer_request "$validation_request" ||
      continue
    revision_count=$((revision_count + 1))

    local action
    while IFS= read -r action; do
      if ! jq -e '
        .target.kind == "issue"
        and .prerequisites == []
        and .completion_condition.kind == "issue-closed"
        and .completion_condition.target.kind == "issue"
      ' <<<"$action" >/dev/null; then
        continue
      fi

      local target_number target
      target_number="$(jq -r '.target.number' <<<"$action")"
      if ! target="$(
        gh issue view "$target_number" \
          --repo "$repository" \
          --json number,state,url
      )"; then
        _git_loopy_continuation_github_error \
          "reconcile" \
          "reading an Action Target"
        return 1
      fi
      if ! jq -e 'type == "object"' <<<"$target" >/dev/null 2>&1; then
        _git_loopy_continuation_github_error \
          "reconcile" \
          "decoding an Action Target"
        return 1
      fi
      [[ "$(jq -r '.state' <<<"$target")" == "OPEN" ]] || continue

      local identity_source identity comment_id projection
      identity_source="$(
        jq -cS \
          --argjson action "$action" \
          '{
            anchor: .workstream.anchor,
            kind: $action.kind,
            target: $action.target,
            occurrence: $action.occurrence
          }' <<<"$record"
      )"
      identity="$(
        printf '%s' "$identity_source" | _git_loopy_continuation_sha256
      )"
      comment_id="$(_git_loopy_continuation_comment_id "$comment")"
      projection="$(
        jq -cn \
          --arg identity "$identity" \
          --argjson record "$record" \
          --argjson action "$action" \
          --argjson comment_id "$comment_id" \
          --arg comment_url "$(jq -r '.url' <<<"$comment")" \
          '{
            identity: $identity,
            semantic_fingerprint:
              $record.semantic_fingerprints[$action.key],
            workstream_anchor: $record.workstream.anchor,
            summary: $action.summary,
            kind: $action.kind,
            readiness: "Ready",
            instruction: $action.instruction,
            target: $action.target,
            basis: $action.basis,
            producer: (
              $record.producer + {
                carrier: $record.carrier,
                revision_id: $record.revision_id,
                comment_id: $comment_id,
                comment_url: $comment_url
              }
            ),
            prerequisites: $action.prerequisites,
            interaction: $action.interaction,
            completion_condition: $action.completion_condition
          }'
      )"
      actions="$(
        jq -cn \
          --argjson actions "$actions" \
          --argjson projection "$projection" \
          '$actions + [$projection]'
      )"
    done < <(jq -c '.actions[]?' <<<"$record")
  done < <(jq -c '.[] | .comments[]' <<<"$carriers")

  actions="$(jq -c 'sort_by(.identity)' <<<"$actions")"
  jq -cn \
    --arg repository "$repository" \
    --argjson indexed_carriers "$(jq 'length' <<<"$carriers")" \
    --argjson producer_revisions "$revision_count" \
    --argjson actions "$actions" \
    '{
      ok: true,
      operation: "reconcile",
      result: {
        status: (if ($actions | length) > 0 then "guidance" else "waiting" end),
        observed: {
          repository: $repository,
          indexed_carriers: $indexed_carriers,
          producer_revisions: $producer_revisions
        },
        actions: $actions,
        diagnostics: []
      }
    }'
}

git_loopy_continuation_main() {
  local operation="${1:-}"
  [[ -n "$operation" ]] || {
    git_loopy_continuation_usage >&2
    return 2
  }
  shift

  if [[ "$operation" == "capabilities" ]]; then
    (($# == 0)) || {
      git_loopy_continuation_usage >&2
      return 2
    }
    git_loopy_continuation_capabilities
    return 0
  fi

  case "$operation" in
    publish | reconcile | record-dispatch-result | repair-index) ;;
    *)
      git_loopy_continuation_usage >&2
      return 2
      ;;
  esac

  local input_path=""
  local terminal=0
  while (($# > 0)); do
    case "$1" in
      --input)
        (($# >= 2)) && [[ "$2" != -* ]] || {
          git_loopy_continuation_usage >&2
          return 2
        }
        [[ -z "$input_path" ]] || {
          git_loopy_continuation_usage >&2
          return 2
        }
        input_path="$2"
        shift 2
        ;;
      --input=*)
        [[ -z "$input_path" && -n "${1#*=}" ]] || {
          git_loopy_continuation_usage >&2
          return 2
        }
        input_path="${1#*=}"
        shift
        ;;
      --terminal)
        [[ "$operation" == "reconcile" && "$terminal" == 0 ]] || {
          git_loopy_continuation_usage >&2
          return 2
        }
        terminal=1
        shift
        ;;
      *)
        git_loopy_continuation_usage >&2
        return 2
        ;;
    esac
  done

  local request
  _git_loopy_continuation_read_request "$operation" "$input_path" || return 1
  request="$GIT_LOOPY_CONTINUATION_REQUEST"
  case "$operation" in
    publish)
      _git_loopy_continuation_publish "$request"
      ;;
    reconcile)
      if ((terminal)); then
        _git_loopy_continuation_error \
          "$operation" \
          "unsupported_operation" \
          "terminal rendering is not supported by this distribution"
      else
        _git_loopy_continuation_reconcile "$request"
      fi
      ;;
    *)
      _git_loopy_continuation_error \
        "$operation" \
        "unsupported_operation" \
        "$operation is not supported by this distribution"
      ;;
  esac
}
