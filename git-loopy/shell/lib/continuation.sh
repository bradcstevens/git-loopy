#!/usr/bin/env bash

GIT_LOOPY_CONTINUATION_CONTRACT_VERSION="1.0"
GIT_LOOPY_CONTINUATION_RECORD_FORMAT=1
GIT_LOOPY_CONTINUATION_WRAPPER_CONTRACT_VERSION="1.3"
GIT_LOOPY_CONTINUATION_EVENT_SCHEMA_VERSION="1.1"
GIT_LOOPY_CONTINUATION_INDEX_LABEL="git-loopy-continuation"
GIT_LOOPY_CONTINUATION_RECORD_MARKER="<!-- git-loopy-continuation:1 -->"
GIT_LOOPY_CONTINUATION_REQUEST=""
GIT_LOOPY_CONTINUATION_VALIDATION_ERROR=""

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
  local release_version
  release_version="$(
    git_loopy_read_release_version "$_GIT_LOOPY_RELEASE_VERSION_PATH"
  )" || return 1
  printf '{"ok":true,"capabilities":{"release_version":"%s","continuation_contract_versions":["1.0"],"record_formats":[1],"wrapper_contract_version":"%s","event_schema_version":"1.1","tracker_adapters":{"github":{"operations":["publish","reconcile"]}},"operations":{"capabilities":true,"publish":true,"reconcile":true,"record-dispatch-result":false,"repair-index":false},"instruction_handlers":[],"instruction_modes":[],"evaluators":[],"effect_scopes":[],"optional_capabilities":{"terminal_rendering":false,"concurrent_dispatch":false},"continuation_modes":{"default":"off","off":true,"report":false,"execute-frontier":false}}}\n' \
    "$release_version" \
    "$GIT_LOOPY_CONTINUATION_WRAPPER_CONTRACT_VERSION"
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

  if [[ "$raw" == $'\xEF\xBB\xBF'* ]]; then
    _git_loopy_continuation_error \
      "$operation" \
      "invalid_request" \
      "request must be UTF-8 without a BOM"
    return 1
  fi
  if ! _git_loopy_continuation_scan_raw_json "$raw"; then
    _git_loopy_continuation_error \
      "$operation" \
      "invalid_request" \
      "$GIT_LOOPY_CONTINUATION_VALIDATION_ERROR"
    return 1
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
  if ! _git_loopy_continuation_validate_portable_json "$parsed"; then
    _git_loopy_continuation_error \
      "$operation" \
      "invalid_request" \
      "$GIT_LOOPY_CONTINUATION_VALIDATION_ERROR"
    return 1
  fi
  GIT_LOOPY_CONTINUATION_REQUEST="$parsed"
}

_git_loopy_continuation_scan_raw_json() {
  local raw="$1"
  if ! printf '%s' "$raw" | perl -e '
    use strict;
    use warnings;
    binmode STDIN, ":raw";
    local $/;
    my $text = <STDIN>;
    my $depth = 0;
    my $in_string = 0;
    my $escaped = 0;
    for my $character (split //, $text) {
      if ($in_string) {
        if ($escaped) {
          $escaped = 0;
        } elsif ($character eq q{\\}) {
          $escaped = 1;
        } elsif ($character eq q{"}) {
          $in_string = 0;
        }
      } elsif ($character eq q{"}) {
        $in_string = 1;
      } elsif ($character eq "{" or $character eq "[") {
        exit 11 if ++$depth > 16;
      } elsif ($character eq "}" or $character eq "]") {
        $depth-- if $depth > 0;
      }
    }
    exit 0;
  '; then
    GIT_LOOPY_CONTINUATION_VALIDATION_ERROR="request exceeds maximum nesting depth 16"
    return 1
  fi

  local duplicate_key
  if duplicate_key="$(
    printf '%s' "$raw" | perl -MEncode=decode,encode,FB_CROAK -MJSON::PP -e '
      use strict;
      use warnings;
      binmode STDIN, ":raw";
      local $/;
      my $bytes = <STDIN>;
      my $text;
      eval { $text = decode("UTF-8", $bytes, FB_CROAK); 1 } or exit 0;
      my $length = length($text);
      my $index = 0;

      sub whitespace {
        $index++ while $index < $length
          && substr($text, $index, 1) =~ /[\x20\x09\x0a\x0d]/;
      }

      sub string_token {
        die "invalid" unless substr($text, $index, 1) eq q{"};
        my $start = $index++;
        while ($index < $length) {
          my $character = substr($text, $index++, 1);
          if ($character eq q{\\}) {
            die "invalid" if $index >= $length;
            my $escape = substr($text, $index++, 1);
            if ($escape eq "u") {
              die "invalid" if $index + 4 > $length;
              $index += 4;
            }
          } elsif ($character eq q{"}) {
            return substr($text, $start, $index - $start);
          }
        }
        die "invalid";
      }

      sub value {
        my ($depth) = @_;
        whitespace();
        die "invalid" if $index >= $length;
        my $character = substr($text, $index, 1);
        if ($character eq "{") {
          exit 11 if $depth + 1 > 16;
          $index++;
          whitespace();
          return if substr($text, $index, 1) eq "}" && ++$index;
          my %keys;
          while (1) {
            whitespace();
            my $raw_key = string_token();
            my $key = JSON::PP->new->utf8->allow_nonref->decode(
              encode("UTF-8", $raw_key)
            );
            if (exists $keys{$key}) {
              binmode STDOUT, ":encoding(UTF-8)";
              print $key;
              exit 10;
            }
            $keys{$key} = 1;
            whitespace();
            die "invalid" unless substr($text, $index++, 1) eq ":";
            value($depth + 1);
            whitespace();
            my $separator = substr($text, $index++, 1);
            last if $separator eq "}";
            die "invalid" unless $separator eq ",";
          }
        } elsif ($character eq "[") {
          exit 11 if $depth + 1 > 16;
          $index++;
          whitespace();
          return if substr($text, $index, 1) eq "]" && ++$index;
          while (1) {
            value($depth + 1);
            whitespace();
            my $separator = substr($text, $index++, 1);
            last if $separator eq "]";
            die "invalid" unless $separator eq ",";
          }
        } elsif ($character eq q{"}) {
          string_token();
        } else {
          while ($index < $length
            && substr($text, $index, 1) !~ /[\x20\x09\x0a\x0d,\]}]/) {
            $index++;
          }
        }
      }

      eval { value(0); 1 } or exit 0;
      exit 0;
    '
  )"; then
    return 0
  else
    local status=$?
    case "$status" in
      10)
        GIT_LOOPY_CONTINUATION_VALIDATION_ERROR="request contains duplicate object key: $duplicate_key"
        ;;
      11)
        GIT_LOOPY_CONTINUATION_VALIDATION_ERROR="request exceeds maximum nesting depth 16"
        ;;
      *)
        return 0
        ;;
    esac
    return 1
  fi
}

_git_loopy_continuation_validate_portable_json() {
  local request="$1"
  local validation
  validation="$(
    jq -cn --argjson request "$request" '
      def validate($name; $depth):
        if $depth > 16 then
          error($name + " exceeds maximum nesting depth 16")
        elif type == "object" then
          to_entries[]
          | (.key | validate($name; $depth + 1)),
            (.value | validate($name; $depth + 1))
        elif type == "array" then
          if length > 256 then
            error($name + " array exceeds maximum length 256")
          else
            .[] | validate($name; $depth + 1)
          end
        elif type == "string" then
          if utf8bytelength > 8192 then
            error($name + " string exceeds maximum UTF-8 length 8192")
          else
            empty
          end
        elif type == "number" then
          if floor != . then
            error($name + " must not contain floating-point values")
          elif . < -9007199254740991 or . > 9007199254740991 then
            error($name + " integer exceeds interoperable signed 53-bit range")
          else
            empty
          end
        else
          empty
        end;
      try (
        $request | validate("request"; 1),
        {ok: true}
      ) catch {ok: false, message: .}
    ' | tail -n 1
  )"
  if [[ "$(jq -r '.ok' <<<"$validation")" != "true" ]]; then
    GIT_LOOPY_CONTINUATION_VALIDATION_ERROR="$(
      jq -r '.message' <<<"$validation"
    )"
    return 1
  fi

  if ! printf '%s' "$request" | perl -MJSON::PP -MUnicode::Normalize=NFC -e '
    use strict;
    use warnings;
    binmode STDIN, ":raw";
    local $/;
    my $value = JSON::PP->new->utf8->decode(<STDIN>);
    sub normalized {
      my ($item) = @_;
      if (ref($item) eq "HASH") {
        for my $key (keys %{$item}) {
          return 0 if NFC($key) ne $key;
          return 0 unless normalized($item->{$key});
        }
      } elsif (ref($item) eq "ARRAY") {
        for my $entry (@{$item}) {
          return 0 unless normalized($entry);
        }
      } elsif (!ref($item)) {
        return 0 if NFC($item) ne $item;
      }
      return 1;
    }
    exit(normalized($value) ? 0 : 1);
  '; then
    GIT_LOOPY_CONTINUATION_VALIDATION_ERROR="request strings must be NFC-normalized"
    return 1
  fi
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

_git_loopy_continuation_validate_completion_request() {
  local request="$1"
  local validation
  validation="$(
    jq -cn --argjson request "$request" '
      def fail($message): error($message);
      def object($value; $name):
        if ($value | type) == "object" then true
        else fail($name + " must be an object")
        end;
      def string($value; $name):
        if ($value | type) == "string" and ($value | gsub("\\s"; "") | length) > 0
        then true
        else fail($name + " must be a non-empty string")
        end;
      def positive_integer($value; $name):
        if ($value | type) == "number" and $value > 0 then true
        else fail($name + " must be a positive integer")
        end;
      def array($value; $name; $nonempty):
        if ($value | type) == "array" and (($nonempty | not) or ($value | length) > 0)
        then true
        else fail(
          $name + " must be a " + (if $nonempty then "non-empty " else "" end) + "array"
        )
        end;
      def fields($value; $name; $required; $optional):
        ($required - ($value | keys) | sort) as $missing
        | (($value | keys) - ($required + $optional) | sort) as $unknown
        | if ($missing | length) > 0 then
            fail($name + " is missing required field: " + $missing[0])
          elif ($unknown | length) > 0 then
            fail($name + " contains unknown field: " + $unknown[0])
          elif ($value | has("advisory_extensions")) then
            object($value.advisory_extensions; $name + ".advisory_extensions")
          else true
          end;
      def durable($value; $name; $repository; $allowed):
        {
          "issue": ["repository", "number"],
          "pull-request": ["repository", "number"],
          "issue-comment": ["repository", "issue", "comment_id"],
          "pull-request-review": ["repository", "pull_request", "review_id"],
          "commit": ["repository", "sha"],
          "branch": ["repository", "name", "sha"]
        } as $schemas
        | object($value; $name)
          and string($value.kind; $name + ".kind")
          and (if ($schemas | has($value.kind)) then true
               else fail($name + ".kind is unsupported") end)
          and (if ($allowed | length) == 0 or ($allowed | index($value.kind)) != null
               then true
               else fail(
                 $name + ".kind must be one of: " + ($allowed | sort | join(", "))
               ) end)
          and fields($value; $name; ["kind"] + $schemas[$value.kind]; [])
          and (if $value.repository == $repository then true
               else fail($name + ".repository must match repository") end)
          and all(
            ["number", "issue", "comment_id", "pull_request", "review_id"][]
            | select(. as $field | $value | has($field));
            . as $field
            | positive_integer($value[$field]; $name + "." + $field)
          )
          and (if ($value.kind == "commit" or $value.kind == "branch") then
                 string($value.sha; $name + ".sha")
                 and (if ($value.sha | test("^[0-9a-f]{40}$")) then true
                      else fail($name + ".sha must be a lowercase 40-character SHA") end)
               else true end)
          and (if $value.kind == "branch"
               then string($value.name; $name + ".name")
               else true end);
      def condition($value; $name; $repository; $allow_local):
        {
          "action-completed": {
            required: ["action_key", "kind"], strings: ["action_key"],
            local: "action_key", targets: [], enums: {}
          },
          "artifact-exists": {
            required: ["kind", "target"], strings: [], local: null,
            targets: ["branch", "commit", "issue", "issue-comment", "pull-request", "pull-request-review"],
            enums: {}
          },
          "branch-head-equals": {
            required: ["kind", "target"], strings: [], local: null,
            targets: ["branch"], enums: {}
          },
          "commit-exists": {
            required: ["kind", "target"], strings: [], local: null,
            targets: ["commit"], enums: {}
          },
          "dependency-satisfied": {
            required: ["kind", "target"], strings: [], local: null,
            targets: ["issue"], enums: {}
          },
          "issue-closed": {
            required: ["kind", "target"], strings: [], local: null,
            targets: ["issue"], enums: {}
          },
          "issue-label-present": {
            required: ["kind", "label", "target"], strings: ["label"], local: null,
            targets: ["issue"], enums: {}
          },
          "issue-open": {
            required: ["kind", "target"], strings: [], local: null,
            targets: ["issue"], enums: {}
          },
          "pull-request-closed": {
            required: ["kind", "target"], strings: [], local: null,
            targets: ["pull-request"], enums: {}
          },
          "pull-request-merged": {
            required: ["kind", "target"], strings: [], local: null,
            targets: ["pull-request"], enums: {}
          },
          "pull-request-open": {
            required: ["kind", "target"], strings: [], local: null,
            targets: ["pull-request"], enums: {}
          },
          "pull-request-review-state": {
            required: ["kind", "state", "target"], strings: [], local: null,
            targets: ["pull-request-review"],
            enums: {state: ["approved", "changes-requested", "commented"]}
          },
          "sub-issues-complete": {
            required: ["kind", "target"], strings: [], local: null,
            targets: ["issue"], enums: {}
          }
        } as $schemas
        | object($value; $name)
          and string($value.kind; $name + ".kind")
          and (if ($schemas | has($value.kind)) then true
               else fail($name + ".kind is unsupported") end)
          and fields(
            $value; $name; $schemas[$value.kind].required; ["advisory_extensions"]
          )
          and all(
            $schemas[$value.kind].strings[];
            . as $field | string($value[$field]; $name + "." + $field)
          )
          and all(
            ($schemas[$value.kind].enums | to_entries)[];
            . as $enum
            | if ($enum.value | index($value[$enum.key])) != null then true
              else fail($name + "." + $enum.key + " is unsupported")
              end
          )
          and (if $schemas[$value.kind].local != null then
                 if $allow_local then true
                 else fail($name + ".kind requires a durable subject")
                 end
               else durable(
                 $value.target; $name + ".target"; $repository;
                 $schemas[$value.kind].targets
               )
               end);
      def interaction($value; $repository; $owner):
        "completion.actions item.interaction" as $name
        | object($value; $name)
          and fields(
            $value; $name; ["classification", "evidence"]; ["advisory_extensions"]
          )
          and string($value.classification; $name + ".classification")
          and (if ["AFK-safe", "HITL-required"] | index($value.classification)
               then true
               else fail($name + ".classification is unsupported") end)
          and object($value.evidence; $name + ".evidence")
          and (if ($value.evidence | has("kind")) then true
               else fail($name + ".evidence is missing required field: kind") end)
          and string($value.evidence.kind; $name + ".evidence.kind")
          and (if $value.evidence.kind == "transition-owner-attestation" then
                 fields(
                   $value.evidence; $name + ".evidence";
                  ["kind", "noninteractive", "owner"]; ["advisory_extensions"]
                 )
                 and (if $value.classification == "AFK-safe" then true
                      else fail(
                        $name + ".evidence.kind is incompatible with "
                        + $value.classification
                      ) end)
                 and string($value.evidence.owner; $name + ".evidence.owner")
                 and (if $value.evidence.noninteractive == true then true
                      else fail(
                        $name + ".evidence.noninteractive must be true"
                      ) end)
                 and (if $value.evidence.owner == $owner then true
                      else fail(
                        $name
                        + ".evidence.owner must match completion.transition.owner"
                      ) end)
               elif $value.evidence.kind == "human-boundary" then
                 fields(
                   $value.evidence; $name + ".evidence";
                   ["kind", "reason", "resolution_condition"];
                   ["advisory_extensions"]
                 )
                 and (if $value.classification == "HITL-required" then true
                      else fail(
                        $name + ".evidence.kind is incompatible with "
                        + $value.classification
                      ) end)
                 and (if [
                   "consent-required", "credential-required", "human-decision",
                   "physical-interaction", "privilege-expansion",
                   "scope-ambiguity", "subjective-validation"
                 ] | index($value.evidence.reason)
                 then true
                 else fail($name + ".evidence.reason is unsupported") end)
                 and condition(
                   $value.evidence.resolution_condition;
                   $name + ".evidence.resolution_condition";
                   $repository; false
                 )
               else fail($name + ".evidence.kind is unsupported")
               end);
      def typed_semantics($value; $name; $kinds; $second):
        array($value; $name; false)
        and all(
          range(0; $value | length);
          . as $index
          | ($name + "[" + ($index | tostring) + "]") as $item_name
          | object($value[$index]; $item_name)
            and fields(
              $value[$index]; $item_name; ["kind", $second]; ["advisory_extensions"]
            )
            and string($value[$index].kind; $item_name + ".kind")
            and (if $kinds | index($value[$index].kind) then true
                 else fail($item_name + ".kind is unsupported") end)
            and string($value[$index][$second]; $item_name + "." + $second)
        );
      def action($value; $repository; $owner):
        "completion.actions item" as $name
        | {
          "Address review findings": ["AFK-safe", "HITL-required"],
          "Authorize operation": ["HITL-required"],
          "Chart workstream": ["HITL-required"],
          "Close parent": ["AFK-safe", "HITL-required"],
          "Decompose spec": ["AFK-safe", "HITL-required"],
          "Implement ticket": ["AFK-safe", "HITL-required"],
          "Perform manual validation": ["HITL-required"],
          "Prototype evidence": ["AFK-safe", "HITL-required"],
          "Provide information": ["HITL-required"],
          "Publish head": ["AFK-safe", "HITL-required"],
          "Publish spec": ["AFK-safe", "HITL-required"],
          "Research fact": ["AFK-safe", "HITL-required"],
          "Resolve conflict": ["AFK-safe", "HITL-required"],
          "Resolve decision": ["HITL-required"],
          "Review and merge PR": ["HITL-required"],
          "Review head": ["AFK-safe", "HITL-required"],
          "Triage item": ["AFK-safe", "HITL-required"]
        } as $kinds
        | object($value; $name)
          and fields(
            $value; $name;
            [
              "key", "summary", "kind", "occurrence", "instruction", "target",
              "basis", "prerequisites", "interaction", "completion_condition"
            ];
            [
              "context_references", "effects", "requirements", "triggers",
              "advisory_extensions"
            ]
          )
          and all(
            ["key", "summary", "occurrence"][];
            . as $field | string($value[$field]; $name + "." + $field)
          )
          and string($value.kind; $name + ".kind")
          and (if $kinds | has($value.kind) then true
               else fail($name + ".kind is unsupported") end)
          and object($value.instruction; $name + ".instruction")
          and fields(
            $value.instruction; $name + ".instruction"; ["mode", "value"];
            ["behavior_version", "variant", "advisory_extensions"]
          )
          and (if ["skill", "command", "manual"] | index($value.instruction.mode)
               then true
               else fail($name + ".instruction.mode is unsupported") end)
          and string($value.instruction.value; $name + ".instruction.value")
          and (if ($value.instruction.value | test("[\\r\\n]")) then
                 fail($name + ".instruction.value must be one line")
               else true end)
          and (if $value.instruction.mode == "skill"
                  and ($value.instruction.value | startswith("/") | not)
               then fail($name + ".instruction.value must name a canonical Skill")
               else true end)
          and all(
            ["behavior_version", "variant"][]
            | select(. as $field | $value.instruction | has($field));
            . as $field
            |
            string($value.instruction[$field]; $name + ".instruction." + $field)
          )
          and durable($value.target; $name + ".target"; $repository; [])
          and array($value.basis; $name + ".basis"; true)
          and all(
            $value.basis[];
            durable(.; $name + ".basis item"; $repository; [])
          )
          and array($value.prerequisites; $name + ".prerequisites"; false)
          and all(
            $value.prerequisites[];
            condition(.; $name + ".prerequisites item"; $repository; true)
          )
          and interaction($value.interaction; $repository; $owner)
          and (if $value.instruction.mode == "manual"
                  and $value.interaction.classification != "HITL-required"
               then fail("manual Instructions must be HITL-required")
               else true end)
          and (if $kinds[$value.kind] | index($value.interaction.classification)
               then true
               else fail($value.kind + " Actions must be HITL-required") end)
          and condition(
            $value.completion_condition; $name + ".completion_condition";
            $repository; true
          )
          and array(
            ($value.context_references // []); $name + ".context_references"; false
          )
          and all(
            ($value.context_references // [])[];
            durable(.; $name + ".context_references item"; $repository; [])
          )
          and typed_semantics(
            ($value.effects // []); $name + ".effects";
            [
              "external-write", "git-read", "git-write", "network-read",
              "repository-read", "repository-write", "tracker-read", "tracker-write"
            ]; "scope"
          )
          and typed_semantics(
            ($value.requirements // []); $name + ".requirements";
            ["access", "capability", "command", "evaluator", "policy", "skill"];
            "name"
          )
          and array(($value.triggers // []); $name + ".triggers"; false)
          and all(
            range(0; ($value.triggers // []) | length);
            . as $index
            | ($name + ".triggers[" + ($index | tostring) + "]") as $trigger_name
            | ($value.triggers[$index]) as $trigger
            | object($trigger; $trigger_name)
              and fields(
                $trigger; $trigger_name; ["kind", "condition"];
                ["advisory_extensions"]
              )
              and (if [
                "consent-required", "credential-required", "human-decision",
                "physical-interaction", "privilege-expansion",
                "scope-ambiguity", "subjective-validation"
              ] | index($trigger.kind)
              then true
              else fail($trigger_name + ".kind is unsupported") end)
              and condition(
                $trigger.condition; $trigger_name + ".condition";
                $repository; true
              )
          );
      def validate_request($request):
        object($request; "request")
        and fields(
          $request; "request"; ["repository", "trusted_producers", "completion"]; []
        )
        and string($request.repository; "repository")
        and (if ($request.repository | test("^[^/]+/[^/]+$")) then true
             else fail("repository must use owner/name form") end)
        and object($request.completion; "completion")
        and fields(
          $request.completion; "completion";
          [
            "continuation_contract_version", "record_format", "publication",
            "disposition", "workstream", "transition", "producer"
          ];
          [
            "carrier", "actions", "outcome", "no_guidance",
            "advisory_extensions"
          ]
        )
        and (if $request.completion.continuation_contract_version == "1.0"
             then true
             else fail("unsupported Continuation contract version") end)
        and (if $request.completion.record_format == 1 then true
             else fail("unsupported Continuation record format") end)
        and (if ["ephemeral", "shared"] | index($request.completion.publication)
             then true
             else fail("completion.publication is unsupported") end)
        and (if ["continue", "no-guidance", "terminal"]
                  | index($request.completion.disposition)
             then true
             else fail("completion.disposition is unsupported") end)
        and array(
          $request.trusted_producers; "trusted_producers";
          $request.completion.publication == "shared"
        )
        and all(
          $request.trusted_producers[];
          string(.; "trusted_producers item")
        )
        and (if ($request.trusted_producers | unique | length)
                  == ($request.trusted_producers | length)
             then true
             else fail("trusted_producers must not contain duplicates") end)
        and object($request.completion.workstream; "completion.workstream")
        and fields(
          $request.completion.workstream; "completion.workstream";
          ["destination"] + (
            if $request.completion.publication == "shared" then ["anchor"] else [] end
          );
          ["advisory_extensions"] + (
            if $request.completion.publication == "shared" then [] else ["anchor"] end
          )
        )
        and (if ($request.completion.workstream | has("anchor"))
             then durable(
               $request.completion.workstream.anchor;
               "completion.workstream.anchor"; $request.repository; []
             )
             else true end)
        and condition(
          $request.completion.workstream.destination;
          "completion.workstream.destination"; $request.repository; false
        )
        and object($request.completion.transition; "completion.transition")
        and fields(
          $request.completion.transition; "completion.transition";
          ["owner", "evidence"]; ["advisory_extensions"]
        )
        and string(
          $request.completion.transition.owner; "completion.transition.owner"
        )
        and array(
          $request.completion.transition.evidence;
          "completion.transition.evidence";
          $request.completion.publication == "shared"
        )
        and all(
          $request.completion.transition.evidence[];
          durable(
            .; "completion.transition.evidence item"; $request.repository;
            ["issue-comment"]
          )
        )
        and object($request.completion.producer; "completion.producer")
        and fields(
          $request.completion.producer; "completion.producer";
          ["login", "role"]; ["advisory_extensions"]
        )
        and string(
          $request.completion.producer.login; "completion.producer.login"
        )
        and (if $request.completion.producer.role == "planning" then true
             else fail("completion.producer.role must be planning") end)
        and (if $request.completion.publication != "shared"
                  or ($request.trusted_producers
                      | index($request.completion.producer.login))
             then true
             else fail("completion producer is not trusted") end)
        and (if $request.completion.publication == "shared"
             then durable(
               $request.completion.carrier; "completion.carrier";
               $request.repository; ["issue"]
             )
             elif ($request.completion | has("carrier")) then
               fail("ephemeral completion must not contain a carrier")
             else true end)
        and (
          {
            "continue": "actions",
            "terminal": "outcome",
            "no-guidance": "no_guidance"
          } as $branches
          | [
              $branches[] as $field
              | select($request.completion | has($field))
              | $field
            ] as $present
          | if $present == [$branches[$request.completion.disposition]]
            then true
            else fail(
              "completion must contain exactly one content branch matching disposition"
            )
            end
        )
        and (if $request.completion.disposition == "continue" then
               array($request.completion.actions; "completion.actions"; true)
               and all(
                 $request.completion.actions[];
                 action(
                   .; $request.repository; $request.completion.transition.owner
                 )
               )
               and (
                 [$request.completion.actions[].key] as $keys
                 | if ($keys | unique | length) == ($keys | length) then true
                   else fail(
                     "completion.actions contains duplicate local key: "
                     + (
                       $keys
                       | group_by(.)
                       | map(select(length > 1))[0][0]
                     )
                   )
                   end
               )
               and all(
                 (
                   $request.completion.actions[] as $action
                   | (
                     [
                       $action.prerequisites[]?,
                       $action.completion_condition,
                       $action.triggers[]?.condition
                     ]
                     | map(select(.kind == "action-completed").action_key)
                   )[] as $reference
                   | {action: $action, reference: $reference}
                 );
                 . as $local
                 | if (
                   [$request.completion.actions[].key] | index($local.reference)
                 ) == null
                 then fail(
                   "completion.actions contains broken local reference: "
                   + $local.reference
                 )
                 elif $local.reference == $local.action.key then fail(
                   "completion.actions contains self-reference: " + $local.reference
                 )
                 else true
                 end
               )
             elif $request.completion.disposition == "terminal" then
               if $request.completion.publication != "shared" then
                 fail("terminal completion must be shared")
               else
                 object($request.completion.outcome; "completion.outcome")
                 and fields(
                   $request.completion.outcome; "completion.outcome";
                   [
                     "kind", "destination_satisfied", "effective_at",
                     "evidence", "summary"
                   ]; ["successor", "advisory_extensions"]
                 )
                 and string(
                   $request.completion.outcome.kind; "completion.outcome.kind"
                 )
                 and (if ["complete", "rejected", "abandoned", "superseded"]
                           | index($request.completion.outcome.kind)
                      then true
                      else fail("completion.outcome.kind is unsupported") end)
                 and (if ($request.completion.outcome.destination_satisfied | type)
                           == "boolean"
                      then true
                      else fail(
                        "completion.outcome.destination_satisfied must be a boolean"
                      ) end)
                 and (if $request.completion.outcome.destination_satisfied
                           == ($request.completion.outcome.kind == "complete")
                      then true
                      else fail(
                        "completion.outcome contradicts destination satisfaction"
                      ) end)
                 and string(
                   $request.completion.outcome.effective_at;
                   "completion.outcome.effective_at"
                 )
                 and (if ($request.completion.outcome.effective_at
                           | test(
                             "^[0-9]{4}-[0-9]{2}-[0-9]{2}T"
                             + "[0-9]{2}:[0-9]{2}:[0-9]{2}(\\.[0-9]+)?Z$"
                           ))
                      then true
                      else fail(
                        "completion.outcome.effective_at must be an RFC3339 UTC timestamp"
                      ) end)
                 and string(
                   $request.completion.outcome.summary;
                   "completion.outcome.summary"
                 )
                 and array(
                   $request.completion.outcome.evidence;
                   "completion.outcome.evidence"; true
                 )
                 and all(
                   $request.completion.outcome.evidence[];
                   durable(
                     .; "completion.outcome.evidence item";
                     $request.repository; []
                   )
                 )
                 and (if $request.completion.outcome.kind == "superseded"
                      then durable(
                        $request.completion.outcome.successor;
                        "completion.outcome.successor"; $request.repository; []
                      )
                      elif ($request.completion.outcome | has("successor")) then
                        fail(
                          "completion.outcome.successor is valid only for superseded"
                        )
                      else true end)
               end
             else
               object($request.completion.no_guidance; "completion.no_guidance")
               and fields(
                 $request.completion.no_guidance; "completion.no_guidance";
                 ["reason", "summary", "references"]; ["advisory_extensions"]
               )
               and string(
                 $request.completion.no_guidance.reason;
                 "completion.no_guidance.reason"
               )
               and (if ["no-successor-created", "ephemeral-only"]
                         | index($request.completion.no_guidance.reason)
                    then true
                    else fail("completion.no_guidance.reason is unsupported") end)
               and ([
                    $request.completion.publication,
                    $request.completion.no_guidance.reason
                  ] as $publication_reason
                  | if [
                      ["shared", "no-successor-created"],
                      ["ephemeral", "ephemeral-only"]
                    ] | any(. == $publication_reason)
                    then true
                    else fail(
                      "completion publication contradicts no-guidance reason"
                    ) end)
               and string(
                 $request.completion.no_guidance.summary;
                 "completion.no_guidance.summary"
               )
               and array(
                 $request.completion.no_guidance.references;
                 "completion.no_guidance.references"; true
               )
               and all(
                 $request.completion.no_guidance.references[];
                 durable(
                   .; "completion.no_guidance.references item";
                   $request.repository; []
                 )
               )
             end);
      try (
        validate_request($request),
        {ok: true}
      ) catch {ok: false, message: .}
    ' | tail -n 1
  )"
  if [[ "$(jq -r '.ok' <<<"$validation")" == "true" ]]; then
    return 0
  fi
  GIT_LOOPY_CONTINUATION_VALIDATION_ERROR="$(
    jq -r '.message' <<<"$validation"
  )"
  return 1
}

_git_loopy_continuation_publish() {
  local request="$1"
  if ! _git_loopy_continuation_validate_completion_request "$request"; then
    _git_loopy_continuation_error \
      "publish" \
      "invalid_request" \
      "$GIT_LOOPY_CONTINUATION_VALIDATION_ERROR"
    return 1
  fi

  local canonical_completion completion_length
  canonical_completion="$(jq -cS '.completion' <<<"$request")"
  completion_length="$(
    printf '%s' "$canonical_completion" | LC_ALL=C wc -c | tr -d ' '
  )"
  if ((completion_length > 49152)); then
    _git_loopy_continuation_error \
      "publish" \
      "invalid_request" \
      "completion canonical JSON exceeds maximum record length 49152"
    return 1
  fi

  local repository completion producer publication
  repository="$(jq -r '.repository' <<<"$request")"
  completion="$(jq -c '.completion' <<<"$request")"
  producer="$(jq -r '.producer.login' <<<"$completion")"
  publication="$(jq -r '.publication' <<<"$completion")"

  local revision_id fingerprints record body
  revision_id="$(
    printf '%s' "$canonical_completion" | _git_loopy_continuation_sha256
  )"
  fingerprints="$(_git_loopy_continuation_fingerprints "$completion")"
  if [[ "$publication" == "ephemeral" ]]; then
    jq -cn \
      --arg disposition "$(jq -r '.disposition' <<<"$completion")" \
      --argjson fingerprints "$fingerprints" \
      '{
        ok: true,
        operation: "publish",
        receipt: {
          status: "unpublished",
          publication: "ephemeral",
          disposition: $disposition,
          semantic_fingerprints: $fingerprints
        }
      }'
    return 0
  fi

  local carrier carrier_number
  carrier="$(jq -c '.carrier' <<<"$completion")"
  carrier_number="$(jq -r '.number' <<<"$carrier")"
  record="$(
    jq -cS \
      --arg revision_id "$revision_id" \
      --argjson fingerprints "$fingerprints" \
      '. + {
        revision_id: $revision_id,
        semantic_fingerprints: $fingerprints
      }' <<<"$completion"
  )"
  local record_length
  record_length="$(printf '%s' "$record" | LC_ALL=C wc -c | tr -d ' ')"
  if ((record_length > 49152)); then
    _git_loopy_continuation_error \
      "publish" \
      "invalid_request" \
      "Producer revision exceeds maximum record length 49152"
    return 1
  fi
  body="$GIT_LOOPY_CONTINUATION_RECORD_MARKER"$'\n```json\n'"$record"$'\n```'
  local body_length
  body_length="$(printf '%s' "$body" | LC_ALL=C wc -c | tr -d ' ')"
  if ((body_length > 65536)); then
    _git_loopy_continuation_error \
      "publish" \
      "invalid_request" \
      "Producer revision exceeds live carrier body limit"
    return 1
  fi

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
    _git_loopy_continuation_validate_completion_request "$validation_request" ||
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
    return $?
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
