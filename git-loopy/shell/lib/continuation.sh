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
  printf '{"ok":true,"capabilities":{"release_version":"%s","continuation_contract_versions":["1.0"],"record_formats":[1],"wrapper_contract_version":"%s","event_schema_version":"1.1","tracker_adapters":{"github":{"operations":["publish","reconcile","repair-index"]}},"operations":{"capabilities":true,"publish":true,"reconcile":true,"record-dispatch-result":false,"repair-index":true},"instruction_handlers":[],"instruction_modes":[],"evaluators":[],"effect_scopes":[],"optional_capabilities":{"immutable_producer_revisions":true,"terminal_rendering":true,"concurrent_dispatch":false},"continuation_modes":{"default":"off","off":true,"report":false,"execute-frontier":false}}}\n' \
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

_git_loopy_continuation_validate_observation() {
  local request="$1"
  local repository="$2"
  local result token_source expected_token
  result="$(
    jq -c '
      def digest: test("^[0-9a-f]{64}$");
      if (
        (.observation | type == "object")
        and ((.observation | keys | sort) == ["heads","token","validators"])
        and (.observation.heads | type == "array")
        and (.observation.validators | type == "array")
        and all(.observation.heads[];
          type == "object"
          and ((keys | sort) == [
            "carrier","producer","revision_id","workstream_anchor"
          ])
          and (.carrier | type == "number" and . > 0 and floor == .)
          and (.producer | type == "string" and length > 0)
          and (.revision_id | type == "string" and digest)
          and (.workstream_anchor | type == "object")
        )
        and all(.observation.validators[];
          type == "object"
          and ((keys | sort) == ["comment_id","sha256"])
          and (.comment_id | type == "number" and . > 0 and floor == .)
          and (.sha256 | type == "string" and digest)
        )
        and (
          [.observation.heads[].revision_id] as $ids
          | ($ids | unique | length) == ($ids | length)
        )
        and (.parents | type == "array")
        and (.parents == [.observation.heads[].revision_id])
      ) then
        {ok:true}
      else
        {ok:false}
      end
    ' <<<"$request"
  )"
  if [[ "$(jq -r '.ok' <<<"$result")" != "true" ]]; then
    GIT_LOOPY_CONTINUATION_VALIDATION_ERROR="$(
      if ! jq -e '.observation | type == "object"' <<<"$request" >/dev/null 2>&1; then
        printf '%s' "observation must be an object"
      elif ! jq -e '.parents == [.observation.heads[].revision_id]' \
        <<<"$request" >/dev/null 2>&1; then
        printf '%s' "parents must name the observed heads in order"
      else
        printf '%s' "observation is outside the supported immutable revision contract"
      fi
    )"
    return 1
  fi
  token_source="$(
    jq -cn \
      --arg repository "$repository" \
      --argjson heads "$(jq -c '.observation.heads' <<<"$request")" \
      --argjson validators "$(jq -c '.observation.validators' <<<"$request")" \
      '{repository:$repository,heads:$heads,validators:$validators}'
  )"
  expected_token="sha256:$(
    printf '%s' "$(jq -cS . <<<"$token_source")" |
      _git_loopy_continuation_sha256
  )"
  if [[ "$(jq -r '.observation.token' <<<"$request")" != "$expected_token" ]]; then
    GIT_LOOPY_CONTINUATION_VALIDATION_ERROR="observation token does not match its bound state"
    return 1
  fi
}

_git_loopy_continuation_validate_reattestation() {
  local request="$1"
  local producer="$2"
  if ! jq -e '
    (.reattestation | type == "object")
    and ((.reattestation | keys | sort) == [
      "affected_heads","authorized_by","mode"
    ])
    and (.reattestation.affected_heads | type == "array" and length > 0)
    and all(.reattestation.affected_heads[];
      type == "string" and test("^[0-9a-f]{64}$")
    )
    and (
      .reattestation.affected_heads as $heads
      | ($heads | unique | length) == ($heads | length)
    )
    and (.reattestation.authorized_by | type == "string" and length > 0)
    and (.reattestation.mode | IN("copy","replace","retire"))
    and ((.trusted_reattesters // []) | type == "array")
  ' <<<"$request" >/dev/null 2>&1; then
    GIT_LOOPY_CONTINUATION_VALIDATION_ERROR="reattestation is outside the supported immutable revision contract"
    return 1
  fi
  if [[ "$(jq -r '.reattestation.authorized_by' <<<"$request")" != "$producer" ]]; then
    GIT_LOOPY_CONTINUATION_VALIDATION_ERROR="reattestation.authorized_by must match the authenticated producer"
    return 1
  fi
  if ! jq -e --arg producer "$producer" \
    '(.trusted_reattesters // []) | index($producer) != null' \
    <<<"$request" >/dev/null; then
    GIT_LOOPY_CONTINUATION_VALIDATION_ERROR="reattestation actor is not separately authorized"
    return 1
  fi
  GIT_LOOPY_CONTINUATION_REATTESTATION="$(
    jq -c '.reattestation' <<<"$request"
  )"
}

_git_loopy_continuation_authorize_producer() {
  local request="$1"
  local repository="$2"
  local producer="$3"
  local actor permission
  if ! actor="$(gh api user)"; then
    _git_loopy_continuation_github_error \
      "publish" \
      "reading the authenticated GitHub actor"
    return 1
  fi
  if ! jq -e '
    type == "object"
    and (.login | type == "string")
    and (.type | type == "string")
  ' <<<"$actor" >/dev/null 2>&1; then
    _git_loopy_continuation_github_error \
      "publish" \
      "decoding the authenticated GitHub actor"
    return 1
  fi
  if [[ "$(jq -r '.login' <<<"$actor")" != "$producer" ]]; then
    GIT_LOOPY_CONTINUATION_VALIDATION_ERROR="authenticated actor does not match completion producer"
    return 2
  fi
  if [[ "$(jq -r '.type' <<<"$actor")" == "Bot" ||
    "$(jq -r '.type' <<<"$actor")" == "App" ]]; then
    if ! jq -e --arg producer "$producer" \
      '(.trusted_apps // []) | index($producer) != null' \
      <<<"$request" >/dev/null; then
      GIT_LOOPY_CONTINUATION_VALIDATION_ERROR="authenticated App producer is not allowlisted"
      return 2
    fi
    return 0
  fi
  if ! jq -e --arg producer "$producer" \
    '.trusted_producers | index($producer) != null' \
    <<<"$request" >/dev/null; then
    GIT_LOOPY_CONTINUATION_VALIDATION_ERROR="authenticated human producer is not trusted"
    return 2
  fi
  if ! permission="$(
    gh api "repos/$repository/collaborators/$producer/permission"
  )"; then
    _git_loopy_continuation_github_error \
      "publish" \
      "reading Producer repository permission"
    return 1
  fi
  if ! jq -e '.permission | type == "string"' <<<"$permission" >/dev/null 2>&1; then
    _git_loopy_continuation_github_error \
      "publish" \
      "decoding Producer repository permission"
    return 1
  fi
  case "$(jq -r '.permission | ascii_upcase' <<<"$permission")" in
    ADMIN | MAINTAIN | WRITE) return 0 ;;
    *)
      GIT_LOOPY_CONTINUATION_VALIDATION_ERROR="authenticated human producer lacks current write permission"
      return 2
      ;;
  esac
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
      def retirement($value; $name; $repository):
        object($value; $name)
        and fields(
          $value; $name;
          ["predecessor_revision_id", "action_key", "reason", "evidence"];
          ["replacement", "advisory_extensions"]
        )
        and string(
          $value.predecessor_revision_id;
          $name + ".predecessor_revision_id"
        )
        and (if ($value.predecessor_revision_id | test("^[0-9a-f]{64}$")) then true
               else fail(
                 $name
                 + ".predecessor_revision_id must be a sha256 revision identity"
               ) end)
        and string($value.action_key; $name + ".action_key")
        and string($value.reason; $name + ".reason")
        and (if [
          "completed", "lost-basis", "workstream-outcome", "supersession"
        ] | index($value.reason)
               then true
               else fail($name + ".reason is unsupported") end)
        and array($value.evidence; $name + ".evidence"; true)
        and all(
          $value.evidence[];
          durable(.; $name + ".evidence item"; $repository; [])
        )
        and (
          if ($value | has("replacement")) then
              if $value.reason != "supersession" then
                fail($name + ".replacement is valid only when reason is supersession")
              else
                object($value.replacement; $name + ".replacement")
                and fields(
                  $value.replacement; $name + ".replacement";
                  ["workstream_anchor", "kind", "target", "occurrence"];
                  ["advisory_extensions"]
                )
                and durable(
                  $value.replacement.workstream_anchor;
                  $name + ".replacement.workstream_anchor";
                  $repository; []
                )
                and string(
                  $value.replacement.kind;
                  $name + ".replacement.kind"
                )
                and (if [
                  "Address review findings", "Authorize operation",
                  "Chart workstream", "Close parent", "Decompose spec",
                  "Implement ticket", "Perform manual validation",
                  "Prototype evidence", "Provide information", "Publish head",
                  "Publish spec", "Research fact", "Resolve conflict",
                  "Resolve decision", "Review and merge PR", "Review head",
                  "Triage item"
                ] | index($value.replacement.kind)
                     then true
                     else fail($name + ".replacement.kind is unsupported") end)
                and durable(
                  $value.replacement.target;
                  $name + ".replacement.target";
                  $repository; []
                )
                and string(
                  $value.replacement.occurrence;
                  $name + ".replacement.occurrence"
                )
              end
          elif $value.reason == "supersession" then
              fail($name + " with reason supersession must declare a replacement")
          else true
          end
        );
      def validate_request($request):
        object($request; "request")
        and fields(
          $request; "request"; ["repository", "trusted_producers", "completion"];
          [
            "observation", "parents", "reattestation", "trusted_apps",
            "trusted_reattesters"
          ]
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
            "carrier", "actions", "outcome", "no_guidance", "retirements",
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
          $request.trusted_producers; "trusted_producers"; false
        )
        and all(
          $request.trusted_producers[];
          string(.; "trusted_producers item")
        )
        and (if ($request.trusted_producers | unique | length)
                  == ($request.trusted_producers | length)
             then true
             else fail("trusted_producers must not contain duplicates") end)
        and array($request.trusted_apps // []; "trusted_apps"; false)
        and all(
          ($request.trusted_apps // [])[];
          string(.; "trusted_apps item")
        )
        and (if (($request.trusted_apps // []) | unique | length)
                  == (($request.trusted_apps // []) | length)
             then true
             else fail("trusted_apps must not contain duplicates") end)
        and (if $request.completion.publication != "shared"
                  or (
                    ($request.trusted_producers + ($request.trusted_apps // []))
                    | length
                  ) > 0
             then true
             else fail("trusted Producers must not be empty") end)
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
                  or (($request.trusted_producers + ($request.trusted_apps // []))
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
             end)
        and (
          if ($request.completion | has("retirements")) then
            array($request.completion.retirements; "completion.retirements"; false)
            and all(
             range(0; ($request.completion.retirements | length));
             . as $index
             | retirement(
                 $request.completion.retirements[$index];
                 "completion.retirements[" + ($index | tostring) + "]";
                 $request.repository
               )
            )
            and (
             [
               $request.completion.retirements[]
               | [
                   .predecessor_revision_id,
                   (.action_key | tostring)
                 ]
             ] as $pairs
             | if ($pairs | unique | length) == ($pairs | length) then true
               else fail(
                 "completion.retirements contains duplicate predecessor_revision_id/action_key pair"
               )
               end
            )
          else true
          end
        );
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

  local repository completion producer publication protocol parents fingerprints
  local reattestation
  repository="$(jq -r '.repository' <<<"$request")"
  completion="$(jq -c '.completion' <<<"$request")"
  producer="$(jq -r '.producer.login' <<<"$completion")"
  publication="$(jq -r '.publication' <<<"$completion")"
  protocol=0
  parents="null"
  reattestation="null"
  if jq -e 'has("observation")' <<<"$request" >/dev/null; then
    protocol=1
  elif jq -e 'has("parents") or has("reattestation")' \
    <<<"$request" >/dev/null; then
    _git_loopy_continuation_error \
      "publish" \
      "invalid_request" \
      "observation is required when parents or reattestation is present"
    return 1
  fi
  if [[ "$publication" == "ephemeral" ]] && ((protocol)); then
    _git_loopy_continuation_error \
      "publish" \
      "invalid_request" \
      "immutable revision fields require shared publication"
    return 1
  fi

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

  if ((protocol)); then
    if ! _git_loopy_continuation_validate_observation "$request" "$repository"; then
      _git_loopy_continuation_error \
        "publish" \
        "invalid_request" \
        "$GIT_LOOPY_CONTINUATION_VALIDATION_ERROR"
      return 1
    fi
    local authorization_status
    if _git_loopy_continuation_authorize_producer \
      "$request" "$repository" "$producer"; then
      :
    else
      authorization_status=$?
      if ((authorization_status == 2)); then
        _git_loopy_continuation_error \
          "publish" \
          "invalid_request" \
          "$GIT_LOOPY_CONTINUATION_VALIDATION_ERROR"
      fi
      return 1
    fi
    if jq -e 'has("reattestation")' <<<"$request" >/dev/null; then
      if ! _git_loopy_continuation_validate_reattestation "$request" "$producer"; then
        _git_loopy_continuation_error \
          "publish" \
          "invalid_request" \
          "$GIT_LOOPY_CONTINUATION_VALIDATION_ERROR"
        return 1
      fi
      reattestation="$GIT_LOOPY_CONTINUATION_REATTESTATION"
    fi
    _git_loopy_continuation_load_all_carriers "$repository" || return 1
    parents="$(jq -c '.parents' <<<"$request")"
    local validator observed_comment actual_digest
    while IFS= read -r validator; do
      observed_comment="$(
        jq -c --argjson comment_id "$(jq '.comment_id' <<<"$validator")" '
          first(.[] | .comments[] | select(.id == $comment_id)) // null
        ' <<<"$GIT_LOOPY_CONTINUATION_CARRIERS"
      )"
      if [[ "$observed_comment" == "null" ]]; then
        _git_loopy_continuation_error \
          "publish" \
          "repair_required" \
          "observed Producer revision was deleted; repair required"
        return 1
      fi
      actual_digest="$(
        printf '%s' "$(jq -r '.body' <<<"$observed_comment")" |
          _git_loopy_continuation_sha256
      )"
      if [[ "$actual_digest" != "$(jq -r '.sha256' <<<"$validator")" ]]; then
        _git_loopy_continuation_error \
          "publish" \
          "repair_required" \
          "observed Producer revision was mutated; repair required"
        return 1
      fi
    done < <(jq -c '.observation.validators[]' <<<"$request")
    _git_loopy_continuation_tainted_heads \
      "$completion" \
      "$GIT_LOOPY_CONTINUATION_CARRIERS" \
      "$(jq -c '
        [(.trusted_producers + (.trusted_apps // []))[]] | unique | sort
      ' <<<"$request")"
    if [[ "$reattestation" == "null" ]] &&
      (($(jq 'length' <<<"$GIT_LOOPY_CONTINUATION_TAINTED_HEADS") > 0)); then
      _git_loopy_continuation_error \
        "publish" \
        "repair_required" \
        "tainted Producer lineage requires authorized re-attestation; repair required"
      return 1
    fi
    if (($(jq 'length' <<<"$GIT_LOOPY_CONTINUATION_TAINTED_HEADS") > 0)) &&
      [[ "$(jq -c 'sort' <<<"$GIT_LOOPY_CONTINUATION_TAINTED_HEADS")" != \
        "$(jq -c '.affected_heads | sort' <<<"$reattestation")" ]]; then
      _git_loopy_continuation_error \
        "publish" \
        "invalid_request" \
        "reattestation.affected_heads must name every tainted lineage head"
      return 1
    fi
  fi

  local revision_id record body identity_source
  identity_source="$canonical_completion"
  if ((protocol)); then
    if (($(jq 'length' <<<"$parents") > 0)) ||
      [[ "$reattestation" != "null" ]]; then
      identity_source="$(
        jq -cn \
          --argjson completion "$completion" \
          --argjson parents "$parents" \
          --argjson reattestation "$reattestation" \
          '{
            completion:$completion,
            parents:$parents
          } + (
            if $reattestation != null
            then {reattestation:$reattestation}
            else {}
            end
          )'
      )"
    fi
  fi
  revision_id="$(
    printf '%s' "$(jq -cS . <<<"$identity_source")" |
      _git_loopy_continuation_sha256
  )"
  local carrier carrier_number
  carrier="$(jq -c '.carrier' <<<"$completion")"
  carrier_number="$(jq -r '.number' <<<"$carrier")"
  record="$(
    jq -cS \
      --arg revision_id "$revision_id" \
      --argjson fingerprints "$fingerprints" \
      --argjson protocol "$protocol" \
      --argjson parents "$parents" \
      --argjson reattestation "$reattestation" \
      '. + {
        revision_id: $revision_id,
        semantic_fingerprints: $fingerprints
      }
      + (if $protocol == 1 then {parents:$parents} else {} end)
      + (
          if $reattestation != null
          then {reattestation:$reattestation}
          else {}
          end
        )' <<<"$completion"
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

  if ((protocol)); then
    local existing_comment
    while IFS= read -r existing_comment; do
      [[ "$(jq -r '.author' <<<"$existing_comment")" == "$producer" ]] || continue
      if [[ "$(jq -r '.body' <<<"$existing_comment")" == "$body" ]]; then
        if _git_loopy_continuation_parse_revision_record \
          "$existing_comment" "$repository" \
          "$(jq -c '
            [(.trusted_producers + (.trusted_apps // []))[]] | unique | sort
          ' <<<"$request")"; then
          if [[ "$(jq -r '.revision_id' \
            <<<"$GIT_LOOPY_CONTINUATION_RECORD")" == "$revision_id" ]]; then
            jq -cn \
              --arg revision_id "$revision_id" \
              --argjson carrier "$carrier" \
              --argjson comment_id "$(jq '.id' <<<"$existing_comment")" \
              --arg comment_url "$(jq -r '.url' <<<"$existing_comment")" \
              --arg index_label "$GIT_LOOPY_CONTINUATION_INDEX_LABEL" \
              --argjson fingerprints "$fingerprints" \
              --argjson parents "$parents" \
              --argjson reattestation "$reattestation" \
              '{
                ok:true,
                operation:"publish",
                receipt:{
                  status:"idempotent",
                  revision_id:$revision_id,
                  carrier:$carrier,
                  comment:{id:$comment_id,url:$comment_url},
                  index_label:$index_label,
                  semantic_fingerprints:$fingerprints,
                  parents:$parents
                }
              }
              | if $reattestation != null
                then .receipt.reattestation = $reattestation
                else .
                end'
            return 0
          fi
        fi
      fi
    done < <(
      jq -c --argjson carrier "$carrier_number" '
        .[]
        | select(.number == $carrier)
        | .comments[]
      ' <<<"$GIT_LOOPY_CONTINUATION_CARRIERS"
    )
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
        --input - 2>&1
  )"; then
    if ((protocol)); then
      _git_loopy_continuation_error \
        "publish" \
        "repair_required" \
        "publication failed after durable transition: $appended; repair required"
    else
      _git_loopy_continuation_github_error \
        "publish" \
        "appending the Producer revision"
    fi
    return 1
  fi
  if ! jq -e 'type == "object"' <<<"$appended" >/dev/null 2>&1; then
    _git_loopy_continuation_github_error \
      "publish" \
      "decoding the appended Producer revision"
    return 1
  fi
  if [[ "$(jq -r '.user.login' <<<"$appended")" != "$producer" ]]; then
    if ((protocol)); then
      _git_loopy_continuation_error \
        "publish" \
        "repair_required" \
        "published Producer revision author does not match completion producer; repair required"
    else
      _git_loopy_continuation_error \
        "publish" \
        "invalid_request" \
        "authenticated comment author does not match completion producer"
    fi
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
    if ((protocol)); then
      _git_loopy_continuation_error \
        "publish" \
        "repair_required" \
        "Producer revision reread did not match the append; repair required"
    else
      _git_loopy_continuation_error \
        "publish" \
        "invalid_request" \
        "Producer revision reread did not match the append"
    fi
    return 1
  fi

  local receipt_status conflicting_heads
  receipt_status="committed"
  conflicting_heads="[]"
  if ((protocol)); then
    local lineage_records existing_comment
    lineage_records="$(jq -cn --argjson record "$record" '[$record]')"
    while IFS= read -r existing_comment; do
      [[ "$(jq -r '.author' <<<"$existing_comment")" == "$producer" ]] || continue
      if _git_loopy_continuation_parse_revision_record \
        "$existing_comment" "$repository" \
        "$(jq -c '
          [(.trusted_producers + (.trusted_apps // []))[]] | unique | sort
        ' <<<"$request")"; then
        if [[ "$(jq -cS '.workstream.anchor' \
          <<<"$GIT_LOOPY_CONTINUATION_RECORD")" == \
          "$(jq -cS '.workstream.anchor' <<<"$record")" ]]; then
          lineage_records="$(
            jq -cn \
              --argjson current "$lineage_records" \
              --argjson record "$GIT_LOOPY_CONTINUATION_RECORD" \
              '$current + [$record]'
          )"
        fi
      fi
    done < <(
      jq -c --argjson carrier "$carrier_number" '
        .[]
        | select(.number == $carrier)
        | .comments[]
      ' <<<"$GIT_LOOPY_CONTINUATION_CARRIERS"
    )
    if [[ "$reattestation" != "null" ]]; then
      lineage_records="$(
        jq -c \
          --argjson affected "$(jq -c '.affected_heads' <<<"$reattestation")" \
          '[.[] | select(
            .revision_id as $id | $affected | index($id) == null
          )]' <<<"$lineage_records"
      )"
    fi
    local live_records semantics_count
    live_records="$(
      jq -c '
        [.[] | .parents[]?] as $referenced
        | [
            .[]
            | select(
                (.revision_id as $id | $referenced | index($id)) == null
              )
          ]
      ' <<<"$lineage_records"
    )"
    semantics_count="$(
      jq '
        [
          .[] | {
            disposition:.disposition,
            actions:(
              .semantic_fingerprints
              | to_entries
              | sort_by(.key)
              | map([.key,.value])
            ),
            outcome:(.outcome // null),
            no_guidance:(.no_guidance // null)
          }
        ] | unique | length
      ' <<<"$live_records"
    )"
    if ((semantics_count > 1)); then
      receipt_status="conflict"
      conflicting_heads="$(
        jq -c '[.[].revision_id] | sort' <<<"$live_records"
      )"
    fi
  fi

  jq -cn \
    --arg revision_id "$revision_id" \
    --argjson carrier "$carrier" \
    --argjson comment_id "$comment_id" \
    --arg comment_url "$(jq -r '.html_url' <<<"$committed")" \
    --arg index_label "$GIT_LOOPY_CONTINUATION_INDEX_LABEL" \
    --argjson fingerprints "$fingerprints" \
    --argjson protocol "$protocol" \
    --argjson parents "$parents" \
    --arg status "$receipt_status" \
    --argjson conflicting_heads "$conflicting_heads" \
    --argjson reattestation "$reattestation" \
    '{
      ok: true,
      operation: "publish",
      receipt: {
        status: $status,
        revision_id: $revision_id,
        carrier: $carrier,
        comment: {id: $comment_id, url: $comment_url},
        index_label: $index_label,
        semantic_fingerprints: $fingerprints
      }
    }
    | if $protocol == 1 then .receipt.parents = $parents else . end
    | if $reattestation != null
      then .receipt.reattestation = $reattestation
      else .
      end
    | if ($conflicting_heads | length) > 0
      then .receipt.conflicting_heads = $conflicting_heads
      else .
      end'
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

_git_loopy_continuation_comment_taint_identity() {
  local carrier="$1"
  local comment_id="$2"
  local source
  source="$(
    jq -cn \
      --argjson carrier "$carrier" \
      --argjson comment_id "$comment_id" \
      '{carrier:$carrier,comment_id:$comment_id,kind:"invalid-producer-comment"}'
  )"
  printf '%s' "$(jq -cS . <<<"$source")" |
    _git_loopy_continuation_sha256
}

_git_loopy_continuation_parse_revision_record() {
  local comment="$1"
  local repository="$2"
  local trusted="$3"
  local body prefix raw record completion parents identity_source expected fingerprints
  body="$(jq -r '.body' <<<"$comment")"
  prefix="$GIT_LOOPY_CONTINUATION_RECORD_MARKER"$'\n```json\n'
  if [[ "$body" != "$prefix"* || "$body" != *$'\n```' ]]; then
    return 2
  fi
  raw="${body#"$prefix"}"
  raw="${raw%$'\n```'}"
  record="$(jq -cS . <<<"$raw" 2>/dev/null)" || {
    GIT_LOOPY_CONTINUATION_VALIDATION_ERROR="Producer revision comment $(jq -r '.id' <<<"$comment") contains invalid JSON"
    return 1
  }
  completion="$(
    jq -cS 'del(
      .revision_id,
      .semantic_fingerprints,
      .parents,
      .reattestation
    )' <<<"$record"
  )"
  parents="$(jq -c '.parents // []' <<<"$record")"
  identity_source="$completion"
  if (($(jq 'length' <<<"$parents") > 0)) ||
    jq -e 'has("reattestation")' <<<"$record" >/dev/null; then
    identity_source="$(
      jq -cn \
        --argjson completion "$completion" \
        --argjson parents "$parents" \
        --argjson record "$record" \
        '{
          completion:$completion,
          parents:$parents
        } + (
          if ($record | has("reattestation"))
          then {reattestation:$record.reattestation}
          else {}
          end
        )'
    )"
  fi
  expected="$(
    printf '%s' "$(jq -cS . <<<"$identity_source")" |
      _git_loopy_continuation_sha256
  )"
  if [[ "$(jq -r '.revision_id // ""' <<<"$record")" != "$expected" ]]; then
    GIT_LOOPY_CONTINUATION_VALIDATION_ERROR="Producer revision has an invalid revision identity"
    return 1
  fi
  fingerprints="$(_git_loopy_continuation_fingerprints "$completion")"
  if [[ "$(jq -cS '.semantic_fingerprints' <<<"$record")" != \
    "$(jq -cS . <<<"$fingerprints")" ]]; then
    GIT_LOOPY_CONTINUATION_VALIDATION_ERROR="Producer revision has invalid semantic fingerprints"
    return 1
  fi
  local validation_request
  validation_request="$(
    jq -cn \
      --arg repository "$repository" \
      --argjson trusted_producers "$trusted" \
      --argjson completion "$completion" \
      '{
        repository:$repository,
        trusted_producers:$trusted_producers,
        completion:$completion
      }'
  )"
  if ! _git_loopy_continuation_validate_completion_request "$validation_request"; then
    return 1
  fi
  if ! jq -e '
    (.parents // [] | type == "array")
    and all((.parents // [])[];
      type == "string" and test("^[0-9a-f]{64}$")
    )
    and (
      (.parents // []) as $parents
      | ($parents | unique | length) == ($parents | length)
    )
  ' <<<"$record" >/dev/null 2>&1; then
    GIT_LOOPY_CONTINUATION_VALIDATION_ERROR="revision parents are malformed"
    return 1
  fi
  GIT_LOOPY_CONTINUATION_RECORD="$record"
  GIT_LOOPY_CONTINUATION_COMPLETION="$completion"
}

_git_loopy_continuation_tainted_heads() {
  local completion="$1"
  local carriers="$2"
  local trusted="$3"
  local carrier_number producer anchor records tainted comment parse_status
  carrier_number="$(jq -r '.carrier.number' <<<"$completion")"
  producer="$(jq -r '.producer.login' <<<"$completion")"
  anchor="$(jq -cS '.workstream.anchor' <<<"$completion")"
  records="[]"
  tainted="[]"
  while IFS= read -r comment; do
    [[ "$(jq -r '.author' <<<"$comment")" == "$producer" ]] || continue
    [[ "$(jq -r '.body' <<<"$comment")" == *"$GIT_LOOPY_CONTINUATION_RECORD_MARKER"* ]] ||
      continue
    if _git_loopy_continuation_parse_revision_record \
      "$comment" "$(jq -r '.carrier.repository' <<<"$completion")" "$trusted"; then
      parse_status=0
    else
      parse_status=$?
    fi
    if ((parse_status == 1)); then
      local taint_identity
      taint_identity="$(
        _git_loopy_continuation_comment_taint_identity \
          "$carrier_number" \
          "$(jq -r '.id' <<<"$comment")"
      )"
      tainted="$(
        jq -cn \
          --argjson current "$tainted" \
          --arg id "$taint_identity" \
          '($current + [$id]) | unique | sort'
      )"
      continue
    elif ((parse_status == 2)); then
      continue
    fi
    [[ "$(jq -cS '.workstream.anchor' \
      <<<"$GIT_LOOPY_CONTINUATION_RECORD")" == "$anchor" ]] || continue
    records="$(
      jq -cn \
        --argjson current "$records" \
        --argjson record "$GIT_LOOPY_CONTINUATION_RECORD" \
        '$current + [$record]'
    )"
    if [[ "$(jq -r '.created_at' <<<"$comment")" != \
      "$(jq -r '.updated_at' <<<"$comment")" ]]; then
      tainted="$(
        jq -cn \
          --argjson current "$tainted" \
          --arg id "$(jq -r '.revision_id' \
            <<<"$GIT_LOOPY_CONTINUATION_RECORD")" \
          '($current + [$id]) | unique | sort'
      )"
    fi
  done < <(
    jq -c --argjson carrier "$carrier_number" '
      .[]
      | select(.number == $carrier)
      | .comments[]
    ' <<<"$carriers"
  )
  tainted="$(
    jq -cn \
      --argjson records "$records" \
      --argjson initial "$tainted" '
      [$records[].revision_id] as $ids
      | (
          $initial + [
            $records[]
            | select(any(
                .parents[]?;
                . as $parent | $ids | index($parent) == null
              ))
            | .revision_id
          ]
          | unique
        ) as $direct
      | reduce range(0; ($records | length)) as $iteration (
          $direct;
          . as $tainted
          | (
              . + [
                $records[]
                | select(any(
                    .parents[]?;
                    . as $parent | $tainted | index($parent) != null
                  ))
                | .revision_id
              ]
              | unique
            )
        )
      | . as $all_tainted
      | [
          $records[]
          | select(
              .revision_id as $id
              | $all_tainted
              | index($id) != null
            )
          | .parents[]?
          | select(. as $parent | $all_tainted | index($parent) != null)
        ] as $referenced
      | [
          $all_tainted[]
          | select(. as $id | $referenced | index($id) == null)
        ]
      | unique
      | sort
    '
  )"
  GIT_LOOPY_CONTINUATION_TAINTED_HEADS="$tainted"
}

declare -A GIT_LOOPY_CONTINUATION_FACT_STATUS=()
declare -A GIT_LOOPY_CONTINUATION_FACT_VALUE=()
declare -A GIT_LOOPY_CONTINUATION_COMPLETION_STATUS=()
declare -a GIT_LOOPY_CONTINUATION_READ_COMMAND=()

_git_loopy_continuation_normalize_fact() {
  local source_kind="$1"
  local raw="$2"
  case "$source_kind" in
    issue | pull-request)
      jq -ce '
        select(
          type == "object"
          and (.number | type == "number")
          and (.state | type == "string")
          and (.url | type == "string")
        )
        | {number, state, url}
      ' <<<"$raw"
      ;;
    issue-labels)
      jq -ce '
        select(
          type == "object"
          and (.number | type == "number")
          and (.labels | type == "array")
        )
        | {
            number,
            labels: [.labels[] | select(.name | type == "string") | .name]
          }
      ' <<<"$raw"
      ;;
    issue-sub-issues)
      jq -ce '
        select(
          type == "object"
          and (.number | type == "number")
          and ((.subIssuesSummary // {}) | type == "object")
        )
        | {
            number,
            total: (.subIssuesSummary.total // 0),
            completed: (.subIssuesSummary.completed // 0)
          }
        | select((.total | type == "number") and (.completed | type == "number"))
      ' <<<"$raw"
      ;;
    commit)
      jq -ce 'select(type == "object" and (.sha | type == "string")) | {sha}' \
        <<<"$raw"
      ;;
    branch)
      jq -ce '
        select(type == "object" and (.object.sha | type == "string"))
        | {sha: .object.sha}
      ' <<<"$raw"
      ;;
    issue-comment)
      jq -ce '
        select(
          type == "object"
          and (
            (.databaseId | type == "number")
            or (.id | type == "number")
          )
          and (
            (.author.login | type == "string")
            or (.user.login | type == "string")
          )
        )
      ' <<<"$raw"
      ;;
    pull-request-review)
      jq -ce '
        select(
          type == "object"
          and (.id | type == "number")
          and (.state | type == "string")
        )
        | {id, state}
      ' <<<"$raw"
      ;;
    *)
      return 1
      ;;
  esac
}

_git_loopy_continuation_cache_fact() {
  local key="$1"
  local status="$2"
  local value="${3:-null}"
  GIT_LOOPY_CONTINUATION_FACT_STATUS["$key"]="$status"
  GIT_LOOPY_CONTINUATION_FACT_VALUE["$key"]="$value"
  GIT_LOOPY_CONTINUATION_FACT_STATUS_VALUE="$status"
  GIT_LOOPY_CONTINUATION_FACT_JSON="$value"
}

_git_loopy_continuation_stable_read() {
  local key="$1"
  local source_kind="$2"
  shift 2
  if [[ -n "${GIT_LOOPY_CONTINUATION_FACT_STATUS[$key]+x}" ]]; then
    GIT_LOOPY_CONTINUATION_FACT_STATUS_VALUE="$(
      printf '%s' "${GIT_LOOPY_CONTINUATION_FACT_STATUS["$key"]}"
    )"
    GIT_LOOPY_CONTINUATION_FACT_JSON="$(
      printf '%s' "${GIT_LOOPY_CONTINUATION_FACT_VALUE["$key"]}"
    )"
    return 0
  fi

  local stderr_path raw normalized message marker previous _attempt
  stderr_path="$(mktemp "${TMPDIR:-/tmp}/git-loopy-continuation-read.XXXXXX")"
  if raw="$(gh "$@" 2>"$stderr_path")" &&
    normalized="$(
      _git_loopy_continuation_normalize_fact "$source_kind" "$raw" 2>/dev/null
    )"; then
    rm -f "$stderr_path"
    _git_loopy_continuation_cache_fact "$key" "value" "$normalized"
    return 0
  fi
  message="$(<"$stderr_path")"
  if [[ "${message,,}" == *"404"* ||
    "${message,,}" == *"not found"* ||
    "${message,,}" == *"could not resolve"* ]]; then
    rm -f "$stderr_path"
    _git_loopy_continuation_cache_fact "$key" "absent"
    return 0
  fi
  previous="unavailable"

  for _attempt in 2 3; do
    : >"$stderr_path"
    if raw="$(gh "$@" 2>"$stderr_path")" &&
      normalized="$(
        _git_loopy_continuation_normalize_fact "$source_kind" "$raw" 2>/dev/null
      )"; then
      marker="value:$(jq -cS . <<<"$normalized")"
    else
      message="$(<"$stderr_path")"
      if [[ "${message,,}" == *"404"* ||
        "${message,,}" == *"not found"* ||
        "${message,,}" == *"could not resolve"* ]]; then
        marker="absent"
      else
        marker="unavailable"
      fi
    fi
    if [[ "$marker" == "$previous" ]]; then
      rm -f "$stderr_path"
      case "$marker" in
        value:*)
          _git_loopy_continuation_cache_fact \
            "$key" "value" "${marker#value:}"
          ;;
        absent)
          _git_loopy_continuation_cache_fact "$key" "absent"
          ;;
        *)
          _git_loopy_continuation_cache_fact "$key" "unverified"
          ;;
      esac
      return 0
    fi
    previous="$marker"
  done
  rm -f "$stderr_path"
  _git_loopy_continuation_cache_fact "$key" "unverified"
}

_git_loopy_continuation_plan_condition_read() {
  local condition="$1"
  local repository="$2"
  local kind target_kind number name sha pull_request review_id comment_id
  kind="$(jq -r '.kind' <<<"$condition")"
  target_kind="$(jq -r '.target.kind // ""' <<<"$condition")"
  if [[ "$kind" == "artifact-exists" ]]; then
    kind="$target_kind"
  fi

  case "$kind" in
    issue-open | issue-closed | dependency-satisfied | issue)
      number="$(jq -r '.target.number' <<<"$condition")"
      GIT_LOOPY_CONTINUATION_FACT_KEY="issue:$repository:$number"
      GIT_LOOPY_CONTINUATION_FACT_SOURCE="issue"
      GIT_LOOPY_CONTINUATION_READ_COMMAND=(
        issue view "$number" --repo "$repository" --json "number,state,url"
      )
      ;;
    pull-request-open | pull-request-closed | pull-request-merged | pull-request)
      number="$(jq -r '.target.number' <<<"$condition")"
      GIT_LOOPY_CONTINUATION_FACT_KEY="pull-request:$repository:$number"
      GIT_LOOPY_CONTINUATION_FACT_SOURCE="pull-request"
      GIT_LOOPY_CONTINUATION_READ_COMMAND=(
        pr view "$number" --repo "$repository" --json "number,state,url"
      )
      ;;
    issue-label-present)
      number="$(jq -r '.target.number' <<<"$condition")"
      GIT_LOOPY_CONTINUATION_FACT_KEY="issue-labels:$repository:$number"
      GIT_LOOPY_CONTINUATION_FACT_SOURCE="issue-labels"
      GIT_LOOPY_CONTINUATION_READ_COMMAND=(
        issue view "$number" --repo "$repository" --json "number,labels"
      )
      ;;
    sub-issues-complete)
      number="$(jq -r '.target.number' <<<"$condition")"
      GIT_LOOPY_CONTINUATION_FACT_KEY="issue-sub-issues:$repository:$number"
      GIT_LOOPY_CONTINUATION_FACT_SOURCE="issue-sub-issues"
      GIT_LOOPY_CONTINUATION_READ_COMMAND=(
        issue view "$number" --repo "$repository" --json "number,subIssuesSummary"
      )
      ;;
    commit-exists | commit)
      sha="$(jq -r '.target.sha' <<<"$condition")"
      GIT_LOOPY_CONTINUATION_FACT_KEY="commit:$repository:$sha"
      GIT_LOOPY_CONTINUATION_FACT_SOURCE="commit"
      GIT_LOOPY_CONTINUATION_READ_COMMAND=(
        api "repos/$repository/commits/$sha"
      )
      ;;
    branch-head-equals | branch)
      name="$(jq -r '.target.name' <<<"$condition")"
      GIT_LOOPY_CONTINUATION_FACT_KEY="branch:$repository:$name"
      GIT_LOOPY_CONTINUATION_FACT_SOURCE="branch"
      GIT_LOOPY_CONTINUATION_READ_COMMAND=(
        api "repos/$repository/git/ref/heads/$name"
      )
      ;;
    issue-comment)
      comment_id="$(jq -r '.target.comment_id' <<<"$condition")"
      GIT_LOOPY_CONTINUATION_FACT_KEY="issue-comment:$repository:$comment_id"
      GIT_LOOPY_CONTINUATION_FACT_SOURCE="issue-comment"
      GIT_LOOPY_CONTINUATION_READ_COMMAND=(
        api "repos/$repository/issues/comments/$comment_id"
      )
      ;;
    pull-request-review-state | pull-request-review)
      pull_request="$(jq -r '.target.pull_request' <<<"$condition")"
      review_id="$(jq -r '.target.review_id' <<<"$condition")"
      GIT_LOOPY_CONTINUATION_FACT_KEY="pull-request-review:$repository:$pull_request:$review_id"
      GIT_LOOPY_CONTINUATION_FACT_SOURCE="pull-request-review"
      GIT_LOOPY_CONTINUATION_READ_COMMAND=(
        api "repos/$repository/pulls/$pull_request/reviews/$review_id"
      )
      ;;
    *)
      return 1
      ;;
  esac
}

_git_loopy_continuation_evaluate_condition() {
  local condition="$1"
  local repository="$2"
  local kind expected actual
  kind="$(jq -r '.kind' <<<"$condition")"
  if [[ "$kind" == "action-completed" ]]; then
    GIT_LOOPY_CONTINUATION_CONDITION_STATUS="local"
    GIT_LOOPY_CONTINUATION_CONDITION_LOCAL_KEY="$(
      jq -r '.action_key' <<<"$condition"
    )"
    return 0
  fi
  _git_loopy_continuation_plan_condition_read "$condition" "$repository" ||
    return 1
  _git_loopy_continuation_stable_read \
    "$GIT_LOOPY_CONTINUATION_FACT_KEY" \
    "$GIT_LOOPY_CONTINUATION_FACT_SOURCE" \
    "${GIT_LOOPY_CONTINUATION_READ_COMMAND[@]}"
  if [[ "$GIT_LOOPY_CONTINUATION_FACT_STATUS_VALUE" == "unverified" ]]; then
    GIT_LOOPY_CONTINUATION_CONDITION_STATUS="unverified"
    return 0
  fi
  if [[ "$GIT_LOOPY_CONTINUATION_FACT_STATUS_VALUE" == "absent" ]]; then
    GIT_LOOPY_CONTINUATION_CONDITION_STATUS="unsatisfied"
    return 0
  fi

  case "$kind" in
    issue-open)
      expected="OPEN"
      actual="$(jq -r '.state' <<<"$GIT_LOOPY_CONTINUATION_FACT_JSON")"
      ;;
    issue-closed | dependency-satisfied)
      expected="CLOSED"
      actual="$(jq -r '.state' <<<"$GIT_LOOPY_CONTINUATION_FACT_JSON")"
      ;;
    pull-request-open)
      expected="OPEN"
      actual="$(jq -r '.state' <<<"$GIT_LOOPY_CONTINUATION_FACT_JSON")"
      ;;
    pull-request-closed)
      expected="closed"
      actual="$(
        jq -r 'if .state == "CLOSED" or .state == "MERGED"
          then "closed" else .state end' \
          <<<"$GIT_LOOPY_CONTINUATION_FACT_JSON"
      )"
      ;;
    pull-request-merged)
      expected="MERGED"
      actual="$(jq -r '.state' <<<"$GIT_LOOPY_CONTINUATION_FACT_JSON")"
      ;;
    issue-label-present)
      expected="present"
      actual="$(
        jq -r --arg label "$(jq -r '.label' <<<"$condition")" \
          'if .labels | index($label) != null then "present" else "absent" end' \
          <<<"$GIT_LOOPY_CONTINUATION_FACT_JSON"
      )"
      ;;
    sub-issues-complete)
      expected="complete"
      actual="$(
        jq -r 'if .completed >= .total then "complete" else "incomplete" end' \
          <<<"$GIT_LOOPY_CONTINUATION_FACT_JSON"
      )"
      ;;
    branch-head-equals)
      expected="$(jq -r '.target.sha' <<<"$condition")"
      actual="$(jq -r '.sha' <<<"$GIT_LOOPY_CONTINUATION_FACT_JSON")"
      ;;
    pull-request-review-state)
      expected="$(jq -r '.state' <<<"$condition")"
      actual="$(
        jq -r '
          if .state == "APPROVED" then "approved"
          elif .state == "CHANGES_REQUESTED" then "changes-requested"
          elif .state == "COMMENTED" then "commented"
          else .state
          end
        ' <<<"$GIT_LOOPY_CONTINUATION_FACT_JSON"
      )"
      ;;
    commit-exists | artifact-exists)
      expected="exists"
      actual="exists"
      ;;
  esac
  if [[ "$actual" == "$expected" ]]; then
    GIT_LOOPY_CONTINUATION_CONDITION_STATUS="satisfied"
  else
    GIT_LOOPY_CONTINUATION_CONDITION_STATUS="unsatisfied"
  fi
}

_git_loopy_continuation_resolve_completion() {
  local action_key="$1"
  local stack="$2"
  local repository="$3"
  if [[ -n "${GIT_LOOPY_CONTINUATION_COMPLETION_STATUS[$action_key]+x}" ]]; then
    GIT_LOOPY_CONTINUATION_RESOLVED_STATUS="$(
      printf '%s' "${GIT_LOOPY_CONTINUATION_COMPLETION_STATUS["$action_key"]}"
    )"
    return 0
  fi

  local cycle_start cycle cycle_key action condition referenced next_stack
  cycle_start="$(
    jq -r --arg key "$action_key" '
      to_entries
      | map(select(.value == $key))
      | if length > 0 then .[0].key else -1 end
    ' <<<"$stack"
  )"
  if ((cycle_start >= 0)); then
    cycle="$(
      jq -c --argjson start "$cycle_start" --arg key "$action_key" \
        '.[($start):] + [$key]' <<<"$stack"
    )"
    GIT_LOOPY_CONTINUATION_LOCAL_DIAGNOSTICS="$(
      jq -cn \
        --argjson current "$GIT_LOOPY_CONTINUATION_LOCAL_DIAGNOSTICS" \
        --arg revision_id "$GIT_LOOPY_CONTINUATION_LOCAL_REVISION_ID" \
        --argjson cycle "$cycle" \
        '$current + [{
          code:"prerequisite_cycle",
          revision_id:$revision_id,
          actions:$cycle
        }]'
    )"
    while IFS= read -r cycle_key; do
      GIT_LOOPY_CONTINUATION_COMPLETION_STATUS["$cycle_key"]="conflict"
    done < <(jq -r '.[]' <<<"$cycle")
    GIT_LOOPY_CONTINUATION_RESOLVED_STATUS="conflict"
    return 0
  fi

  action="$(
    jq -c --arg key "$action_key" \
      'first(.[] | select(.key == $key)) // null' \
      <<<"$GIT_LOOPY_CONTINUATION_LOCAL_ACTIONS"
  )"
  if [[ "$action" == "null" ]]; then
    GIT_LOOPY_CONTINUATION_COMPLETION_STATUS["$action_key"]="unverified"
    GIT_LOOPY_CONTINUATION_RESOLVED_STATUS="unverified"
    return 0
  fi
  condition="$(jq -c '.completion_condition' <<<"$action")"
  _git_loopy_continuation_evaluate_condition "$condition" "$repository" ||
    GIT_LOOPY_CONTINUATION_CONDITION_STATUS="unverified"
  if [[ "$GIT_LOOPY_CONTINUATION_CONDITION_STATUS" == "local" ]]; then
    referenced="$GIT_LOOPY_CONTINUATION_CONDITION_LOCAL_KEY"
    next_stack="$(jq -c --arg key "$action_key" '. + [$key]' <<<"$stack")"
    _git_loopy_continuation_resolve_completion \
      "$referenced" "$next_stack" "$repository"
  else
    GIT_LOOPY_CONTINUATION_RESOLVED_STATUS="$(
      printf '%s' "$GIT_LOOPY_CONTINUATION_CONDITION_STATUS"
    )"
  fi
  if [[ -n "${GIT_LOOPY_CONTINUATION_COMPLETION_STATUS[$action_key]+x}" ]]; then
    GIT_LOOPY_CONTINUATION_RESOLVED_STATUS="$(
      printf '%s' "${GIT_LOOPY_CONTINUATION_COMPLETION_STATUS["$action_key"]}"
    )"
  else
    GIT_LOOPY_CONTINUATION_COMPLETION_STATUS["$action_key"]="$(
      printf '%s' "$GIT_LOOPY_CONTINUATION_RESOLVED_STATUS"
    )"
  fi
}

_git_loopy_continuation_local_topological_layers() {
  local record="$1"
  jq -cn --argjson record "$record" '
    def actions_by_key:
      (($record.actions // []) | map({(.key): .}) | add) // {};
    def local_prerequisite_keys($action):
      [
        ($action.prerequisites // [])[]
        | select(.kind == "action-completed")
        | .action_key
      ];
    def layer($key; $stack):
      if ($stack | index($key)) != null then 0
      elif (actions_by_key[$key] // null) == null then 0
      else
        (local_prerequisite_keys(actions_by_key[$key])) as $prerequisites
        | if ($prerequisites | length) == 0 then 0
          else (
            [$prerequisites[] | layer(.; $stack + [$key])]
            | max
          ) + 1
          end
      end;
    reduce (($record.actions // [])[] | .key) as $key (
      {};
      .[$key] = layer($key; [])
    )'
}

_git_loopy_continuation_validate_previous_actions() {
  local value="$1"
  local name="$2"
  local validation
  validation="$(
    jq -cn --argjson value "$value" --arg name "$name" '
      def fail($message): error($message);
      def fields($value; $name; $required; $optional):
        ($required - ($value | keys) | sort) as $missing
        | (($value | keys) - ($required + $optional) | sort) as $unknown
        | if ($missing | length) > 0 then
            fail($name + " is missing required field: " + $missing[0])
          elif ($unknown | length) > 0 then
            fail($name + " contains unknown field: " + $unknown[0])
          else true end;
      def string($value; $name):
        if ($value | type) == "string" then $value
        else fail($name + " must be a string") end;
      if ($value | type) != "array" then
        fail($name + " must be an array")
      else
        [
          range(0; $value | length) as $index
          | ($name + "[" + ($index | tostring) + "]") as $item_name
          | ($value[$index]) as $entry
          | if ($entry | type) != "object" then
              fail($item_name + " must be an object")
            else
              fields($entry; $item_name; ["identity", "semantic_fingerprint"]; [])
              | {
                  identity: string($entry.identity; $item_name + ".identity"),
                  semantic_fingerprint: string(
                    $entry.semantic_fingerprint;
                    $item_name + ".semantic_fingerprint"
                  )
                }
            end
        ]
      end
    ' 2>/dev/null
  )" || {
    GIT_LOOPY_CONTINUATION_VALIDATION_ERROR="$(
      jq -nr --argjson value "$value" --arg name "$name" '
        def fail($message): error($message);
        def fields($value; $name; $required; $optional):
          ($required - ($value | keys) | sort) as $missing
          | (($value | keys) - ($required + $optional) | sort) as $unknown
          | if ($missing | length) > 0 then
              fail($name + " is missing required field: " + $missing[0])
            elif ($unknown | length) > 0 then
              fail($name + " contains unknown field: " + $unknown[0])
            else true end;
        def string($value; $name):
          if ($value | type) == "string" then $value
          else fail($name + " must be a string") end;
        try (
          if ($value | type) != "array" then
            fail($name + " must be an array")
          else
            [
              range(0; $value | length) as $index
              | ($name + "[" + ($index | tostring) + "]") as $item_name
              | ($value[$index]) as $entry
              | if ($entry | type) != "object" then
                  fail($item_name + " must be an object")
                else
                  fields($entry; $item_name; ["identity", "semantic_fingerprint"]; [])
                  | {
                      identity: string($entry.identity; $item_name + ".identity"),
                      semantic_fingerprint: string(
                        $entry.semantic_fingerprint;
                        $item_name + ".semantic_fingerprint"
                      )
                    }
                end
            ] | ""
          end
        ) catch .
      '
    )"
    return 1
  }
  GIT_LOOPY_CONTINUATION_PREVIOUS_ACTIONS="$validation"
}

_git_loopy_continuation_actions_delta() {
  local actions="$1"
  local previous_actions="$2"
  jq -cn \
    --argjson actions "$actions" \
    --argjson previous_actions "$previous_actions" '
      ($actions | map({key:.identity, value:.semantic_fingerprint}) | from_entries)
        as $current
      | (
          $previous_actions
          | map({key:.identity, value:.semantic_fingerprint})
          | from_entries
        ) as $previous
      | {
          added: (
            [
              $current
              | keys[] as $identity
              | select($previous | has($identity) | not)
              | $identity
            ]
            | sort
          ),
          retired: (
            [
              $previous
              | keys[] as $identity
              | select($current | has($identity) | not)
              | $identity
            ]
            | sort
          ),
          changed: (
            [
              $current
              | keys[] as $identity
              | select(
                  ($previous | has($identity))
                  and $current[$identity] != $previous[$identity]
                )
              | $identity
            ]
            | sort
          )
        }'
}

_git_loopy_continuation_validate_handoff() {
  local value="$1"
  local name="$2"
  local validation
  validation="$(
    jq -cn --argjson value "$value" --arg name "$name" '
      def fail($message): error($message);
      def fields($value; $name; $required; $optional):
        ($required - ($value | keys) | sort) as $missing
        | (($value | keys) - ($required + $optional) | sort) as $unknown
        | if ($missing | length) > 0 then
            fail($name + " is missing required field: " + $missing[0])
          elif ($unknown | length) > 0 then
            fail($name + " contains unknown field: " + $unknown[0])
          else true end;
      def string($value; $name):
        if ($value | type) == "string" then $value
        else fail($name + " must be a string") end;
      if ($value | type) != "object" then
        fail($name + " must be an object")
      else
        fields($value; $name; ["action_identity", "context_available"]; ["reference", "note"])
        | if ($value.context_available | type) != "boolean" then
            fail($name + ".context_available must be a boolean")
          else {
            action_identity: string($value.action_identity; $name + ".action_identity"),
            context_available: $value.context_available
          } + (
            if $value.context_available then
              {reference: string($value.reference; $name + ".reference")}
            elif ($value | has("reference")) then
              fail($name + ".reference requires available machine-local context")
            else {}
            end
          ) + (
            if ($value | has("note")) then
              {note: string($value.note; $name + ".note")}
            else {}
            end
          )
          end
      end
    ' 2>/dev/null
  )" || {
    GIT_LOOPY_CONTINUATION_VALIDATION_ERROR="$(
      jq -nr --argjson value "$value" --arg name "$name" '
        def fail($message): error($message);
        def fields($value; $name; $required; $optional):
          ($required - ($value | keys) | sort) as $missing
          | (($value | keys) - ($required + $optional) | sort) as $unknown
          | if ($missing | length) > 0 then
              fail($name + " is missing required field: " + $missing[0])
            elif ($unknown | length) > 0 then
              fail($name + " contains unknown field: " + $unknown[0])
            else true end;
        def string($value; $name):
          if ($value | type) == "string" then $value
          else fail($name + " must be a string") end;
        try (
          if ($value | type) != "object" then
            fail($name + " must be an object")
          else
            fields($value; $name; ["action_identity", "context_available"]; ["reference", "note"])
            | if ($value.context_available | type) != "boolean" then
                fail($name + ".context_available must be a boolean")
              else {
                action_identity: string($value.action_identity; $name + ".action_identity"),
                context_available: $value.context_available
              } + (
                if $value.context_available then
                  {reference: string($value.reference; $name + ".reference")}
                elif ($value | has("reference")) then
                  fail($name + ".reference requires available machine-local context")
                else {}
                end
              ) + (
                if ($value | has("note")) then
                  {note: string($value.note; $name + ".note")}
                else {}
                end
              ) | ""
              end
          end
        ) catch .
      '
    )"
    return 1
  }
  GIT_LOOPY_CONTINUATION_HANDOFF="$validation"
}

_git_loopy_continuation_apply_handoff() {
  local actions="$1"
  local handoff="$2"
  if [[ "$(jq -r '.context_available' <<<"$handoff")" != "true" ]]; then
    GIT_LOOPY_CONTINUATION_HANDOFF_ACTIONS="$actions"
    GIT_LOOPY_CONTINUATION_HANDOFF_DIAGNOSTICS="$(
      jq -cn \
        --arg action_identity "$(jq -r '.action_identity' <<<"$handoff")" \
        '[{
          code:"handoff_context_unavailable",
          action_identity:$action_identity
        }]'
    )"
    return 0
  fi
  if ! jq -e --arg identity "$(jq -r '.action_identity' <<<"$handoff")" '
    any(.[]; .identity == $identity)
  ' <<<"$actions" >/dev/null; then
    GIT_LOOPY_CONTINUATION_HANDOFF_ACTIONS="$actions"
    GIT_LOOPY_CONTINUATION_HANDOFF_DIAGNOSTICS="$(
      jq -cn \
        --arg action_identity "$(jq -r '.action_identity' <<<"$handoff")" \
        '[{
          code:"handoff_action_unavailable",
          action_identity:$action_identity
        }]'
    )"
    return 0
  fi
  GIT_LOOPY_CONTINUATION_HANDOFF_ACTIONS="$(
    jq -c \
      --arg identity "$(jq -r '.action_identity' <<<"$handoff")" \
      --arg reference "$(jq -r '.reference' <<<"$handoff")" \
      --arg note "$(jq -r '.note // empty' <<<"$handoff")" '
      map(
        if .identity == $identity then
          .handoff_reference = (
            {available:true, reference:$reference}
            + (if $note != "" then {note:$note} else {} end)
          )
        else .
        end
      )' <<<"$actions"
  )"
  GIT_LOOPY_CONTINUATION_HANDOFF_DIAGNOSTICS="[]"
}

_git_loopy_continuation_action_identity_from_parts() {
  local anchor="$1"
  local kind="$2"
  local target="$3"
  local occurrence="$4"
  local source
  source="$(
    jq -cn \
      --argjson anchor "$anchor" \
      --arg kind "$kind" \
      --argjson target "$target" \
      --arg occurrence "$occurrence" \
      '{
        anchor:$anchor,
        kind:$kind,
        target:$target,
        occurrence:$occurrence
      }'
  )"
  printf '%s' "$(jq -cS . <<<"$source")" | _git_loopy_continuation_sha256
}

_git_loopy_continuation_action_identity() {
  local record="$1"
  local action="$2"
  _git_loopy_continuation_action_identity_from_parts \
    "$(jq -c '.workstream.anchor' <<<"$record")" \
    "$(jq -r '.kind' <<<"$action")" \
    "$(jq -c '.target' <<<"$action")" \
    "$(jq -r '.occurrence' <<<"$action")"
}

_git_loopy_continuation_record_has_parent() {
  local record="$1"
  local parent_id="$2"
  jq -e --arg parent "$parent_id" '
    (.parents // []) | index($parent) != null
  ' <<<"$record" >/dev/null
}

_git_loopy_continuation_live_revision_entries() {
  local entries="$1"
  jq -c '
    [.[] | .record.parents[]?] as $referenced
    | [
        .[]
        | select(
            (.record.revision_id as $id | $referenced | index($id)) == null
          )
      ]
  ' <<<"$entries"
}

_git_loopy_continuation_retirement_relationship() {
  local record="$1"
  local receipt="$2"
  local predecessor_record="$3"
  local action_key retired_action retired_identity current_identities action
  local reason replacement replacement_identity
  action_key="$(jq -r '.action_key' <<<"$receipt")"
  retired_action="$(
    jq -c --arg key "$action_key" '
      first((.actions // [])[] | select((.key | tostring) == $key)) // null
    ' <<<"$predecessor_record"
  )"
  [[ "$retired_action" != "null" ]] || {
    printf 'null'
    return 0
  }
  retired_identity="$(
    _git_loopy_continuation_action_identity "$predecessor_record" "$retired_action"
  )"
  current_identities="[]"
  while IFS= read -r action; do
    current_identities="$(
      jq -cn \
        --argjson current "$current_identities" \
        --arg identity "$(
          _git_loopy_continuation_action_identity "$record" "$action"
        )" \
        '$current + [$identity]'
    )"
  done < <(jq -c '.actions[]?' <<<"$record")
  if jq -e --arg identity "$retired_identity" '
    index($identity) != null
  ' <<<"$current_identities" >/dev/null; then
    printf 'null'
    return 0
  fi
  reason="$(jq -r '.reason' <<<"$receipt")"
  replacement_identity="null"
  if [[ "$reason" == "supersession" ]]; then
    replacement="$(jq -c '.replacement' <<<"$receipt")"
    replacement_identity="$(
      _git_loopy_continuation_action_identity_from_parts \
        "$(jq -c '.workstream_anchor' <<<"$replacement")" \
        "$(jq -r '.kind' <<<"$replacement")" \
        "$(jq -c '.target' <<<"$replacement")" \
        "$(jq -r '.occurrence' <<<"$replacement")"
    )"
    if [[ "$replacement_identity" == "$retired_identity" ]] ||
      ! jq -e --arg identity "$replacement_identity" '
        index($identity) != null
      ' <<<"$current_identities" >/dev/null; then
      printf 'null'
      return 0
    fi
  fi
  jq -cn \
    --arg retired_identity "$retired_identity" \
    --arg replacement_identity "$replacement_identity" \
    '{
      retired_identity:$retired_identity,
      replacement_identity:(
        if $replacement_identity == "null" then null else $replacement_identity end
      )
    }'
}

_git_loopy_continuation_missing_retirement_receipts() {
  local record="$1"
  local lineage_entries="$2"
  local current_identities declared missing predecessor_id predecessor_entry
  local predecessor_record action action_identity receipt relationship action_key
  current_identities="[]"
  while IFS= read -r action; do
    current_identities="$(
      jq -cn \
        --argjson current "$current_identities" \
        --arg identity "$(
          _git_loopy_continuation_action_identity "$record" "$action"
        )" \
        '$current + [$identity]'
    )"
  done < <(jq -c '.actions[]?' <<<"$record")
  declared="[]"
  while IFS= read -r receipt; do
    predecessor_id="$(jq -r '.predecessor_revision_id' <<<"$receipt")"
    predecessor_entry="$(
      jq -c --arg revision_id "$predecessor_id" '
        first(.[] | select(.record.revision_id == $revision_id)) // null
      ' <<<"$lineage_entries"
    )"
    [[ "$predecessor_entry" != "null" ]] || continue
    predecessor_record="$(jq -c '.record' <<<"$predecessor_entry")"
    _git_loopy_continuation_record_has_parent "$record" "$predecessor_id" || continue
    relationship="$(
      _git_loopy_continuation_retirement_relationship \
        "$record" "$receipt" "$predecessor_record"
    )"
    [[ "$relationship" != "null" ]] || continue
    action_key="$(jq -r '.action_key' <<<"$receipt")"
    declared="$(
      jq -cn \
        --argjson current "$declared" \
        --arg predecessor_revision_id "$predecessor_id" \
        --arg action_key "$action_key" \
        '$current + [{
          predecessor_revision_id:$predecessor_revision_id,
          action_key:$action_key
        }]'
    )"
  done < <(jq -c '.retirements[]?' <<<"$record")
  missing="[]"
  while IFS= read -r predecessor_id; do
    predecessor_entry="$(
      jq -c --arg revision_id "$predecessor_id" '
        first(.[] | select(.record.revision_id == $revision_id)) // null
      ' <<<"$lineage_entries"
    )"
    [[ "$predecessor_entry" != "null" ]] || continue
    predecessor_record="$(jq -c '.record' <<<"$predecessor_entry")"
    while IFS= read -r action; do
      action_key="$(jq -r '.key' <<<"$action")"
      action_identity="$(
        _git_loopy_continuation_action_identity "$predecessor_record" "$action"
      )"
      if jq -e --arg identity "$action_identity" '
        index($identity) != null
      ' <<<"$current_identities" >/dev/null; then
        continue
      fi
      if jq -e \
        --arg predecessor_revision_id "$predecessor_id" \
        --arg action_key "$action_key" '
        any(
          .[];
          .predecessor_revision_id == $predecessor_revision_id
          and .action_key == $action_key
        )
      ' <<<"$declared" >/dev/null; then
        continue
      fi
      missing="$(
        jq -cn \
          --argjson current "$missing" \
          --arg predecessor_revision_id "$predecessor_id" \
          --arg action_key "$action_key" \
          '$current + [{
            predecessor_revision_id:$predecessor_revision_id,
            action_key:$action_key
          }]'
      )"
    done < <(jq -c '.actions[]?' <<<"$predecessor_record")
  done < <(jq -r '.parents[]?' <<<"$record")
  jq -c '
    sort_by(.predecessor_revision_id, .action_key)
  ' <<<"$missing"
}

_git_loopy_continuation_apply_retirement_receipts() {
  local live_entries="$1"
  local lineage_entries="$2"
  local retirements diagnostics entry record comment receipt predecessor_id
  local predecessor_entry predecessor_record relationship action_key replacement_identity
  retirements="[]"
  diagnostics="[]"
  while IFS= read -r entry; do
    record="$(jq -c '.record' <<<"$entry")"
    comment="$(jq -c '.comment' <<<"$entry")"
    while IFS= read -r receipt; do
      predecessor_id="$(jq -r '.predecessor_revision_id' <<<"$receipt")"
      action_key="$(jq -r '.action_key' <<<"$receipt")"
      predecessor_entry="$(
        jq -c --arg revision_id "$predecessor_id" '
          first(.[] | select(.record.revision_id == $revision_id)) // null
        ' <<<"$lineage_entries"
      )"
      if [[ "$predecessor_entry" != "null" ]]; then
        predecessor_record="$(jq -c '.record' <<<"$predecessor_entry")"
        relationship="$(
          _git_loopy_continuation_retirement_relationship \
            "$record" "$receipt" "$predecessor_record"
        )"
      else
        relationship="null"
      fi
      if [[ "$relationship" == "null" ]] ||
        ! _git_loopy_continuation_record_has_parent "$record" "$predecessor_id"; then
        diagnostics="$(
          jq -cn \
            --argjson current "$diagnostics" \
            --argjson comment_id "$(jq '.id' <<<"$comment")" \
            --arg revision_id "$(jq -r '.revision_id' <<<"$record")" \
            --arg predecessor_revision_id "$predecessor_id" \
            --arg action_key "$action_key" \
            '$current + [{
              code:"invalid_retirement_receipt",
              comment_id:$comment_id,
              revision_id:$revision_id,
              predecessor_revision_id:$predecessor_revision_id,
              action_key:$action_key
            }]'
        )"
        continue
      fi
      retirements="$(
        jq -cn \
          --argjson current "$retirements" \
          --argjson workstream_anchor "$(jq -c '.workstream.anchor' <<<"$record")" \
          --arg action_identity "$(jq -r '.retired_identity' <<<"$relationship")" \
          --arg predecessor_revision_id "$predecessor_id" \
          --arg reason "$(jq -r '.reason' <<<"$receipt")" \
          --argjson evidence "$(jq -c '.evidence' <<<"$receipt")" \
          --arg replacement_identity "$(jq -r '.replacement_identity // empty' <<<"$relationship")" \
          '$current + [({
            workstream_anchor:$workstream_anchor,
            action_identity:$action_identity,
            predecessor_revision_id:$predecessor_revision_id,
            reason:$reason,
            evidence:$evidence
          } + (
            if $replacement_identity != ""
            then {replacement_identity:$replacement_identity}
            else {}
            end
          ))]'
      )"
    done < <(jq -c '.retirements[]?' <<<"$record")
  done < <(jq -c '.[]' <<<"$live_entries")
  jq -cn \
    --argjson retirements "$retirements" \
    --argjson diagnostics "$diagnostics" \
    '{retirements:$retirements, diagnostics:$diagnostics}'
}

_git_loopy_continuation_workstream_outcomes() {
  local guidance_entries="$1"
  jq -c '
    [
      .[]
      | .record as $record
      | select($record.disposition == "terminal")
      | ($record.outcome) as $outcome
      | {
          workstream_anchor: $record.workstream.anchor,
          kind: $outcome.kind,
          destination_satisfied: $outcome.destination_satisfied,
          effective_at: $outcome.effective_at,
          evidence: $outcome.evidence,
          summary: $outcome.summary
        }
      | if ($outcome | has("successor"))
        then .successor = $outcome.successor
        else .
        end
    ]
    | sort_by(.workstream_anchor | tojson)
  ' <<<"$guidance_entries"
}

_git_loopy_continuation_guidance_status() {
  local guidance_entries="$1"
  local actions="$2"
  local outcomes="$3"
  local closed_coverage="$4"
  jq -nr \
    --argjson guidance_entries "$guidance_entries" \
    --argjson actions "$actions" \
    --argjson outcomes "$outcomes" \
    --argjson closed_coverage "$closed_coverage" '
      if ($actions | length) > 0 then
        "guidance"
      else
        ($guidance_entries | length > 0 and all(
          $guidance_entries[];
          .record.disposition == "terminal"
        )) as $every_lineage_terminal
        | if $closed_coverage
            and $every_lineage_terminal
            and all(
              $outcomes[];
              .kind == "complete" and .destination_satisfied
            )
          then "complete"
          else "waiting"
          end
      end
    '
}

_git_loopy_continuation_render_locator() {
  local reference="$1"
  local kind repository
  kind="$(jq -r '.kind' <<<"$reference")"
  repository="$(jq -r '.repository' <<<"$reference")"
  case "$kind" in
    issue)
      printf 'https://github.com/%s/issues/%s' \
        "$repository" "$(jq -r '.number' <<<"$reference")"
      ;;
    pull-request)
      printf 'https://github.com/%s/pull/%s' \
        "$repository" "$(jq -r '.number' <<<"$reference")"
      ;;
    commit)
      printf 'https://github.com/%s/commit/%s' \
        "$repository" "$(jq -r '.sha' <<<"$reference")"
      ;;
    branch)
      printf 'https://github.com/%s/tree/%s' \
        "$repository" "$(jq -r '.sha' <<<"$reference")"
      ;;
    issue-comment)
      printf 'https://github.com/%s/issues/%s#issuecomment-%s' \
        "$repository" \
        "$(jq -r '.issue' <<<"$reference")" \
        "$(jq -r '.comment_id' <<<"$reference")"
      ;;
    pull-request-review)
      printf 'https://github.com/%s/pull/%s#pullrequestreview-%s' \
        "$repository" \
        "$(jq -r '.pull_request' <<<"$reference")" \
        "$(jq -r '.review_id' <<<"$reference")"
      ;;
    *)
      printf '%s' "$kind"
      ;;
  esac
}

_git_loopy_continuation_render_terminal() {
  local result="$1"
  local status_key status_title repository
  local primary ready_remainder blocked_remainder diagnostics outcomes retirements delta
  local output=""
  status_key="$(jq -r '.status' <<<"$result")"
  case "$status_key" in
    guidance) status_title="Guidance" ;;
    waiting) status_title="Waiting" ;;
    complete) status_title="Complete" ;;
    *) status_title="$status_key" ;;
  esac
  repository="$(jq -r '.observed.repository' <<<"$result")"
  output+="Continuation: $repository — $status_title"$'\n'$'\n'
  if [[ "$(jq '(.actions // []) | length' <<<"$result")" == "0" ]]; then
    output+="$status_title"$'\n'
  else
    primary="$(jq -c '.actions[0]' <<<"$result")"
    output+="Primary Action ($(jq -r '.readiness' <<<"$primary")): $(jq -r '.summary' <<<"$primary")"$'\n'
    output+="  Interaction: $(jq -r '.interaction.classification' <<<"$primary")"$'\n'
    output+="  Instruction ($(jq -r '.instruction.mode' <<<"$primary")):"$'\n'
    output+="$(jq -r '.instruction.value' <<<"$primary")"$'\n'
    output+="  Target: $(
      _git_loopy_continuation_render_locator "$(jq -c '.target' <<<"$primary")"
    )"$'\n'
    local basis_line=""
    while IFS= read -r basis_item; do
      local locator
      locator="$(_git_loopy_continuation_render_locator "$basis_item")"
      if [[ -z "$basis_line" ]]; then
        basis_line="$locator"
      else
        basis_line+=", $locator"
      fi
    done < <(jq -c '.basis[]' <<<"$primary")
    output+="  Basis: $basis_line"$'\n'
    if jq -e 'has("handoff_reference")' <<<"$primary" >/dev/null; then
      output+="  Handoff: $(jq -r '.handoff_reference.reference' <<<"$primary")"$'\n'
    fi
    ready_remainder="$(jq -c '[.actions[1:][]? | select(.readiness == "Ready")]' <<<"$result")"
    blocked_remainder="$(jq -c '[.actions[1:][]? | select(.readiness == "Blocked")]' <<<"$result")"
    if [[ "$(jq 'length' <<<"$ready_remainder")" != "0" ]]; then
      output+=$'\n'"Ready ($(jq -r 'length' <<<"$ready_remainder") more):"$'\n'
      while IFS= read -r action; do
        output+="  - $(jq -r '.summary' <<<"$action") [$(
          _git_loopy_continuation_render_locator "$(jq -c '.target' <<<"$action")"
        )]"$'\n'
      done < <(jq -c '.[]' <<<"$ready_remainder")
    fi
    if [[ "$(jq 'length' <<<"$blocked_remainder")" != "0" ]]; then
      output+=$'\n'"Blocked ($(jq -r 'length' <<<"$blocked_remainder") hidden):"$'\n'
      while IFS= read -r action; do
        output+="  - $(jq -r '.summary' <<<"$action") [$(
          _git_loopy_continuation_render_locator "$(jq -c '.target' <<<"$action")"
        )]"$'\n'
      done < <(jq -c '.[]' <<<"$blocked_remainder")
    fi
  fi
  diagnostics="$(jq -c '.diagnostics // []' <<<"$result")"
  if [[ "$(jq 'length' <<<"$diagnostics")" != "0" ]]; then
    output+=$'\n'"Needs attention ($(jq -r 'length' <<<"$diagnostics")):"$'\n'
    while IFS= read -r diagnostic; do
      output+="  - $(jq -r '.code' <<<"$diagnostic")"$'\n'
    done < <(jq -c '.[]' <<<"$diagnostics")
  fi
  outcomes="$(jq -c '.outcomes // []' <<<"$result")"
  if [[ "$(jq 'length' <<<"$outcomes")" != "0" ]]; then
    output+=$'\n'"Outcomes:"$'\n'
    while IFS= read -r outcome; do
      local satisfaction
      if [[ "$(jq -r '.destination_satisfied' <<<"$outcome")" == "true" ]]; then
        satisfaction="destination satisfied"
      else
        satisfaction="destination not satisfied"
      fi
      output+="  - $(
        _git_loopy_continuation_render_locator \
          "$(jq -c '.workstream_anchor' <<<"$outcome")"
      ): $(jq -r '.kind' <<<"$outcome") ($satisfaction)"$'\n'
    done < <(jq -c '.[]' <<<"$outcomes")
  fi
  retirements="$(jq -c '.retirements // []' <<<"$result")"
  if [[ "$(jq 'length' <<<"$retirements")" != "0" ]]; then
    output+=$'\n'"Retired this refresh ($(jq -r 'length' <<<"$retirements")):"$'\n'
    while IFS= read -r retirement; do
      output+="  - $(jq -r '.reason' <<<"$retirement") (predecessor $(
        jq -r '.predecessor_revision_id[:12]' <<<"$retirement"
      )…)"$'\n'
    done < <(jq -c '.[]' <<<"$retirements")
  fi
  delta="$(jq -c '.delta // null' <<<"$result")"
  if [[ "$delta" != "null" ]]; then
    output+=$'\n'"Refresh delta: +$(jq -r '.added | length' <<<"$delta") added, -$(jq -r '.retired | length' <<<"$delta") retired, ~$(jq -r '.changed | length' <<<"$delta") changed"$'\n'
  fi
  printf '%s' "$output"
}

_git_loopy_continuation_load_all_carriers() {
  local repository="$1"
  local page response item comment_page comments labels normalized
  GIT_LOOPY_CONTINUATION_CARRIERS="[]"
  page=1
  while :; do
    if ! response="$(
      gh api "repos/$repository/issues?state=all&per_page=100&page=$page"
    )"; then
      _git_loopy_continuation_github_error \
        "reconcile" \
        "discovering all Producer carriers"
      return 1
    fi
    if ! jq -e 'type == "array"' <<<"$response" >/dev/null 2>&1; then
      _git_loopy_continuation_github_error \
        "reconcile" \
        "decoding all Producer carriers"
      return 1
    fi
    while IFS= read -r item; do
      jq -e '
        (.number | type == "number")
        and (.state | type == "string")
        and (.html_url | type == "string")
        and (.labels | type == "array")
        and (.comments | type == "number")
      ' <<<"$item" >/dev/null 2>&1 || {
        _git_loopy_continuation_github_error \
          "reconcile" \
          "decoding all Producer carriers"
        return 1
      }
      [[ "$(jq -r 'has("pull_request")' <<<"$item")" == "false" ]] || continue

      comments="[]"
      if (($(jq -r '.comments' <<<"$item") > 0)); then
        comment_page=1
        while :; do
          local comment_response
          if ! comment_response="$(
            gh api \
              "repos/$repository/issues/$(jq -r '.number' <<<"$item")/comments?per_page=100&page=$comment_page"
          )"; then
            _git_loopy_continuation_github_error \
              "reconcile" \
              "reading Producer carrier comments"
            return 1
          fi
          if ! jq -e 'type == "array"' <<<"$comment_response" >/dev/null 2>&1; then
            _git_loopy_continuation_github_error \
              "reconcile" \
              "decoding Producer carrier comments"
            return 1
          fi
          comments="$(
            jq -cn \
              --argjson current "$comments" \
              --argjson page "$comment_response" \
              '$current + [
                $page[] | {
                  id: (.databaseId // .id),
                  url: (.url // .html_url),
                  body: .body,
                  author: (.user.login // .author.login),
                  author_type: (.user.type // .author.type // "User"),
                  created_at: (.createdAt // .created_at),
                  updated_at: (.updatedAt // .updated_at)
                }
              ]'
          )"
          (($(jq 'length' <<<"$comment_response") == 100)) || break
          comment_page=$((comment_page + 1))
        done
      fi
      labels="$(jq -c '[.labels[] | select(.name | type == "string") | .name]' <<<"$item")"
      normalized="$(
        jq -cn \
          --argjson number "$(jq '.number' <<<"$item")" \
          --arg state "$(jq -r '.state | ascii_upcase' <<<"$item")" \
          --arg url "$(jq -r '.html_url' <<<"$item")" \
          --argjson labels "$labels" \
          --argjson comments "$comments" \
          '{
            number:$number,
            state:$state,
            url:$url,
            labels:$labels,
            comments:$comments
          }'
      )"
      GIT_LOOPY_CONTINUATION_CARRIERS="$(
        jq -cn \
          --argjson current "$GIT_LOOPY_CONTINUATION_CARRIERS" \
          --argjson carrier "$normalized" \
          '$current + [$carrier]'
      )"
    done < <(jq -c '.[]' <<<"$response")
    (($(jq 'length' <<<"$response") == 100)) || break
    page=$((page + 1))
  done
}

_git_loopy_continuation_reconcile_revision_protocol() {
  local request="$1"
  local repository carriers
  repository="$(jq -r '.repository' <<<"$request")"
  _git_loopy_continuation_load_all_carriers "$repository" || return 1
  carriers="$GIT_LOOPY_CONTINUATION_CARRIERS"

  local diagnostics indexed_carriers trusted_marker_carriers record_carriers entries
  local trusted comment
  diagnostics="[]"
  trusted_marker_carriers="[]"
  record_carriers="[]"
  entries="[]"
  trusted="$(jq -c '
    [(.trusted_producers + (.trusted_apps // []))[]] | unique | sort
  ' <<<"$request")"
  local -A producer_permissions=()
  indexed_carriers="$(
    jq --arg label "$GIT_LOOPY_CONTINUATION_INDEX_LABEL" \
      '[.[] | select(.labels | index($label) != null)] | length' <<<"$carriers"
  )"
  while IFS= read -r comment; do
    [[ "$(jq -r '.comment.body' <<<"$comment")" == *"$GIT_LOOPY_CONTINUATION_RECORD_MARKER"* ]] ||
      continue
    local author author_type carrier_number authorized rejection permission_response
    author="$(jq -r '.comment.author' <<<"$comment")"
    author_type="$(jq -r '.comment.author_type' <<<"$comment")"
    carrier_number="$(jq -r '.carrier' <<<"$comment")"
    authorized=0
    rejection="untrusted_marker_ignored"
    if [[ "$author_type" == "Bot" || "$author_type" == "App" ]]; then
      if jq -e --arg author "$author" \
        '(.trusted_apps // []) | index($author) != null' \
        <<<"$request" >/dev/null; then
        authorized=1
      fi
    elif jq -e --arg author "$author" \
      '.trusted_producers | index($author) != null' \
      <<<"$request" >/dev/null; then
      if [[ -z "${producer_permissions[$author]+x}" ]]; then
        if ! permission_response="$(
          gh api "repos/$repository/collaborators/$author/permission"
        )"; then
          _git_loopy_continuation_github_error \
            "reconcile" \
            "reading Producer repository permission"
          return 1
        fi
        if ! jq -e '.permission | type == "string"' \
          <<<"$permission_response" >/dev/null 2>&1; then
          _git_loopy_continuation_github_error \
            "reconcile" \
            "decoding Producer repository permission"
          return 1
        fi
        producer_permissions[$author]="$(jq -r '.permission | ascii_upcase' \
          <<<"$permission_response")"
      fi
      case "${producer_permissions[$author]}" in
        ADMIN | MAINTAIN | WRITE) authorized=1 ;;
        *) rejection="producer_permission_revoked" ;;
      esac
    fi

    if ((authorized)); then
      trusted_marker_carriers="$(
        jq -cn \
          --argjson current "$trusted_marker_carriers" \
          --argjson carrier "$carrier_number" \
          '($current + [$carrier]) | unique | sort'
      )"
      if [[ "$(jq -r '.comment.created_at' <<<"$comment")" != \
        "$(jq -r '.comment.updated_at' <<<"$comment")" ]]; then
        diagnostics="$(
          jq -cn \
            --argjson current "$diagnostics" \
            --argjson carrier "$carrier_number" \
            --argjson comment_id "$(jq '.comment.id' <<<"$comment")" \
            '$current + [{
              code:"mutated_revision",
              carrier:$carrier,
              comment_id:$comment_id
            }]'
        )"
        continue
      fi
      local parse_status
      if _git_loopy_continuation_parse_revision_record \
        "$(jq -c '.comment' <<<"$comment")" "$repository" "$trusted"; then
        if [[ "$(jq -r '.producer.login' \
          <<<"$GIT_LOOPY_CONTINUATION_RECORD")" != "$author" ]]; then
          GIT_LOOPY_CONTINUATION_VALIDATION_ERROR="embedded Producer does not match authenticated comment author"
          parse_status=1
        else
          parse_status=0
        fi
      else
        parse_status=$?
      fi
      if ((parse_status == 1)); then
        local affected_head
        affected_head="$(
          _git_loopy_continuation_comment_taint_identity \
            "$carrier_number" \
            "$(jq -r '.comment.id' <<<"$comment")"
        )"
        diagnostics="$(
          jq -cn \
            --argjson current "$diagnostics" \
            --argjson carrier "$carrier_number" \
            --argjson comment_id "$(jq '.comment.id' <<<"$comment")" \
            --arg affected_head "$affected_head" \
            --arg message "$GIT_LOOPY_CONTINUATION_VALIDATION_ERROR" \
            '$current + [{
              code:"invalid_revision",
              carrier:$carrier,
              comment_id:$comment_id,
              affected_head:$affected_head,
              message:$message
            }]'
        )"
      elif ((parse_status == 0)); then
        local entry
        entry="$(
          jq -cn \
            --argjson carrier "$carrier_number" \
            --argjson comment "$(jq -c '.comment' <<<"$comment")" \
            --argjson record "$GIT_LOOPY_CONTINUATION_RECORD" \
            '{
              carrier:$carrier,
              comment:$comment,
              record:$record,
              lineage: ([
                $carrier,
                $record.producer.login,
                $record.workstream.anchor
              ] | tojson),
              semantics: ({
                disposition:$record.disposition,
                actions:(
                  $record.semantic_fingerprints
                  | to_entries
                  | sort_by(.key)
                  | map([.key,.value])
                ),
                outcome:($record.outcome // null),
                no_guidance:($record.no_guidance // null)
              } | tojson)
            }'
        )"
        entries="$(
          jq -cn \
            --argjson current "$entries" \
            --argjson entry "$entry" \
            '$current + [$entry]'
        )"
        record_carriers="$(
          jq -cn \
            --argjson current "$record_carriers" \
            --argjson carrier "$carrier_number" \
            '($current + [$carrier]) | unique | sort'
        )"
      fi
    else
      diagnostics="$(
        jq -cn \
          --argjson current "$diagnostics" \
          --arg code "$rejection" \
          --argjson carrier "$carrier_number" \
          --argjson comment_id "$(jq '.comment.id' <<<"$comment")" \
          --arg author "$author" \
          '$current + [{
            code:$code,
            carrier:$carrier,
            comment_id:$comment_id,
            author:$author
          }]'
      )"
    fi
  done < <(jq -c '.[] as $carrier | $carrier.comments[] | {
    carrier:$carrier.number,
    comment:.
  }' <<<"$carriers")
  while IFS= read -r carrier_number; do
    if ! jq -e --argjson carrier "$carrier_number" \
      'index($carrier) != null' <<<"$(
        jq -c --arg label "$GIT_LOOPY_CONTINUATION_INDEX_LABEL" \
          '[.[] | select(.labels | index($label) != null) | .number]' \
          <<<"$carriers"
      )" >/dev/null; then
      diagnostics="$(
        jq -cn \
          --argjson current "$diagnostics" \
          --argjson carrier "$carrier_number" \
          '$current + [{code:"index_label_missing",carrier:$carrier}]'
      )"
    fi
  done < <(jq -r '.[]' <<<"$record_carriers")
  while IFS= read -r carrier_number; do
    if ! jq -e --argjson carrier "$carrier_number" \
      'index($carrier) != null' <<<"$trusted_marker_carriers" >/dev/null; then
      diagnostics="$(
        jq -cn \
          --argjson current "$diagnostics" \
          --argjson carrier "$carrier_number" \
          '$current + [{code:"index_label_stale",carrier:$carrier}]'
      )"
    fi
  done < <(
    jq -r --arg label "$GIT_LOOPY_CONTINUATION_INDEX_LABEL" \
      '.[] | select(.labels | index($label) != null) | .number' <<<"$carriers"
  )

  local live_entries guidance_entries observed_head_entries retirements
  local heads validators actions
  observed_head_entries="[]"
  guidance_entries="[]"
  retirements="[]"
  while IFS= read -r lineage_entries; do
    local tainted missing record comment revision_id changed predecessor_id
    local usable_entries semantics_count receipt_state
    tainted="[]"
    while IFS= read -r entry; do
      record="$(jq -c '.record' <<<"$entry")"
      comment="$(jq -c '.comment' <<<"$entry")"
      revision_id="$(jq -r '.revision_id' <<<"$record")"
      missing="$(
        jq -cn \
          --argjson lineage_entries "$lineage_entries" \
          --argjson record "$record" '
          [
            ($record.parents // [])[]
            | select(
                . as $parent
                | ($lineage_entries | any(.record.revision_id == $parent)) | not
              )
          ] | sort'
      )"
      if (($(jq 'length' <<<"$missing") > 0)); then
        tainted="$(
          jq -cn \
            --argjson current "$tainted" \
            --arg revision_id "$revision_id" \
            '($current + [$revision_id]) | unique'
        )"
        diagnostics="$(
          jq -cn \
            --argjson current "$diagnostics" \
            --argjson comment_id "$(jq '.id' <<<"$comment")" \
            --arg revision_id "$revision_id" \
            --argjson missing "$missing" \
            '$current + [{
              code:"missing_predecessor",
              comment_id:$comment_id,
              revision_id:$revision_id,
              missing:$missing
            }]'
        )"
        continue
      fi
      missing="$(_git_loopy_continuation_missing_retirement_receipts "$record" "$lineage_entries")"
      if (($(jq 'length' <<<"$missing") > 0)); then
        tainted="$(
          jq -cn \
            --argjson current "$tainted" \
            --arg revision_id "$revision_id" \
            '($current + [$revision_id]) | unique'
        )"
        while IFS= read -r receipt; do
          predecessor_id="$(jq -r '.predecessor_revision_id' <<<"$receipt")"
          local predecessor_entry predecessor_record relationship action_key
          predecessor_entry="$(
            jq -c --arg revision_id "$predecessor_id" '
              first(.[] | select(.record.revision_id == $revision_id)) // null
            ' <<<"$lineage_entries"
          )"
          if [[ "$predecessor_entry" != "null" ]]; then
            predecessor_record="$(jq -c '.record' <<<"$predecessor_entry")"
            relationship="$(
              _git_loopy_continuation_retirement_relationship \
                "$record" "$receipt" "$predecessor_record"
            )"
          else
            relationship="null"
          fi
          action_key="$(jq -r '.action_key' <<<"$receipt")"
          if [[ "$predecessor_entry" == "null" ]] ||
            ! _git_loopy_continuation_record_has_parent "$record" "$predecessor_id" ||
            [[ "$relationship" == "null" ]]; then
            diagnostics="$(
              jq -cn \
                --argjson current "$diagnostics" \
                --argjson comment_id "$(jq '.id' <<<"$comment")" \
                --arg revision_id "$revision_id" \
                --arg predecessor_revision_id "$predecessor_id" \
                --arg action_key "$action_key" \
                '$current + [{
                  code:"invalid_retirement_receipt",
                  comment_id:$comment_id,
                  revision_id:$revision_id,
                  predecessor_revision_id:$predecessor_revision_id,
                  action_key:$action_key
                }]'
            )"
          fi
        done < <(jq -c '.retirements[]?' <<<"$record")
        diagnostics="$(
          jq -cn \
            --argjson current "$diagnostics" \
            --argjson comment_id "$(jq '.id' <<<"$comment")" \
            --arg revision_id "$revision_id" \
            --argjson missing "$missing" \
            '$current + [{
              code:"missing_retirement_receipt",
              comment_id:$comment_id,
              revision_id:$revision_id,
              missing:$missing
            }]'
        )"
      fi
    done < <(jq -c '.[]' <<<"$lineage_entries")
    changed=1
    while ((changed)); do
      changed=0
      while IFS= read -r entry; do
        record="$(jq -c '.record' <<<"$entry")"
        revision_id="$(jq -r '.revision_id' <<<"$record")"
        if jq -e --arg revision_id "$revision_id" '
          index($revision_id) != null
        ' <<<"$tainted" >/dev/null; then
          continue
        fi
        if jq -e --argjson tainted "$tainted" '
          any((.parents // [])[]?; $tainted | index(.) != null)
        ' <<<"$record" >/dev/null; then
          tainted="$(
            jq -cn \
              --argjson current "$tainted" \
              --arg revision_id "$revision_id" \
              '($current + [$revision_id]) | unique'
          )"
          changed=1
        fi
      done < <(jq -c '.[]' <<<"$lineage_entries")
    done
    usable_entries="$(
      jq -c --argjson tainted "$tainted" '
        [
          .[]
          | select(
              (.record.revision_id as $id | $tainted | index($id)) == null
            )
        ]
      ' <<<"$lineage_entries"
    )"
    live_entries="$(_git_loopy_continuation_live_revision_entries "$usable_entries")"
    observed_head_entries="$(
      jq -cn \
        --argjson current "$observed_head_entries" \
        --argjson live_entries "$live_entries" \
        '$current + $live_entries'
    )"
    semantics_count="$(
      jq '
        [
          .[]
          | .record
          | {
              disposition,
              actions:(
                .semantic_fingerprints
                | to_entries
                | sort_by(.key)
                | map([.key,.value])
              ),
              outcome:(.outcome // null),
              no_guidance:(.no_guidance // null)
            }
        ] | unique | length
      ' <<<"$live_entries"
    )"
    if ((semantics_count > 1)); then
      diagnostics="$(
        jq -cn \
          --argjson current "$diagnostics" \
          --argjson carrier "$(jq '.[0].carrier' <<<"$live_entries")" \
          --argjson heads "$(
            jq -c '[.[].record.revision_id] | sort' <<<"$live_entries"
          )" \
          '$current + [{
            code:"revision_fork",
            carrier:$carrier,
            heads:$heads
          }]'
      )"
    elif (($(jq 'length' <<<"$live_entries") > 0)); then
      guidance_entries="$(
        jq -cn \
          --argjson current "$guidance_entries" \
          --argjson entry "$(jq -c 'min_by(.record.revision_id)' <<<"$live_entries")" \
          '$current + [$entry]'
      )"
    fi
    receipt_state="$(
      _git_loopy_continuation_apply_retirement_receipts \
        "$live_entries" \
        "$lineage_entries"
    )"
    retirements="$(
      jq -cn \
        --argjson current "$retirements" \
        --argjson added "$(jq -c '.retirements' <<<"$receipt_state")" \
        '$current + $added'
    )"
    diagnostics="$(
      jq -cn \
        --argjson current "$diagnostics" \
        --argjson added "$(jq -c '.diagnostics' <<<"$receipt_state")" \
        '$current + $added'
    )"
  done < <(jq -c 'sort_by(.lineage) | group_by(.lineage)[]' <<<"$entries")
  live_entries="$(
    jq -c 'sort_by(.carrier, .record.revision_id)' <<<"$observed_head_entries"
  )"
  guidance_entries="$(jq -c '.' <<<"$guidance_entries")"
  retirements="$(
    jq -c '
      sort_by(
        (.workstream_anchor | tojson),
        .predecessor_revision_id
      )
    ' <<<"$retirements"
  )"
  heads="$(
    jq -c '[.[] | {
      carrier:.carrier,
      producer:.record.producer.login,
      revision_id:.record.revision_id,
      workstream_anchor:.record.workstream.anchor
    }]' <<<"$live_entries"
  )"
  validators="$(
    while IFS= read -r entry; do
      jq -cn \
        --argjson comment_id "$(jq '.comment.id' <<<"$entry")" \
        --arg sha256 "$(
          printf '%s' "$(jq -r '.comment.body' <<<"$entry")" |
            _git_loopy_continuation_sha256
        )" \
        '{comment_id:$comment_id,sha256:$sha256}'
    done < <(jq -c 'sort_by(.comment.id)[]' <<<"$entries") |
      jq -sc .
  )"
  GIT_LOOPY_CONTINUATION_FACT_STATUS=()
  GIT_LOOPY_CONTINUATION_FACT_VALUE=()
  GIT_LOOPY_CONTINUATION_LOCAL_REVISION_ID=""
  GIT_LOOPY_CONTINUATION_LOCAL_DIAGNOSTICS="[]"
  actions="[]"
  local -A layer_cache=()
  while IFS= read -r candidate; do
    local action action_key revision_id completion_status
    local prerequisite prerequisite_status prerequisite_unverified conflicted
    local unsatisfied identity projection local_order_index layers
    action="$(jq -c '.action' <<<"$candidate")"
    action_key="$(jq -r '.key' <<<"$action")"
    revision_id="$(jq -r '.record.revision_id' <<<"$candidate")"
    if [[ "$revision_id" != "$GIT_LOOPY_CONTINUATION_LOCAL_REVISION_ID" ]]; then
      diagnostics="$(
        jq -cn \
          --argjson current "$diagnostics" \
          --argjson local_diagnostics \
            "$GIT_LOOPY_CONTINUATION_LOCAL_DIAGNOSTICS" \
          '$current + $local_diagnostics'
      )"
      GIT_LOOPY_CONTINUATION_LOCAL_REVISION_ID="$revision_id"
      GIT_LOOPY_CONTINUATION_LOCAL_ACTIONS="$(
        jq -c '.record.actions' <<<"$candidate"
      )"
      GIT_LOOPY_CONTINUATION_LOCAL_DIAGNOSTICS="[]"
      GIT_LOOPY_CONTINUATION_COMPLETION_STATUS=()
    fi
    _git_loopy_continuation_resolve_completion \
      "$action_key" "[]" "$repository"
    completion_status="$GIT_LOOPY_CONTINUATION_RESOLVED_STATUS"
    if [[ "$completion_status" == "satisfied" ||
      "$completion_status" == "conflict" ]]; then
      continue
    fi
    if [[ "$completion_status" == "unverified" ]]; then
      diagnostics="$(
        jq -cn \
          --argjson current "$diagnostics" \
          --arg revision_id "$revision_id" \
          --arg action_key "$action_key" \
          '$current + [{
            code:"unverified_completion",
            revision_id:$revision_id,
            action_key:$action_key
          }]'
      )"
      continue
    fi

    unsatisfied="[]"
    prerequisite_unverified=0
    conflicted=0
    while IFS= read -r prerequisite; do
      _git_loopy_continuation_evaluate_condition \
        "$prerequisite" "$repository" || {
        prerequisite_unverified=1
        continue
      }
      prerequisite_status="$GIT_LOOPY_CONTINUATION_CONDITION_STATUS"
      if [[ "$prerequisite_status" == "local" ]]; then
        _git_loopy_continuation_resolve_completion \
          "$GIT_LOOPY_CONTINUATION_CONDITION_LOCAL_KEY" \
          "$(jq -cn --arg key "$action_key" '[$key]')" \
          "$repository"
        prerequisite_status="$GIT_LOOPY_CONTINUATION_RESOLVED_STATUS"
      fi
      if [[ "$prerequisite_status" == "conflict" ]]; then
        conflicted=1
        break
      elif [[ "$prerequisite_status" == "unverified" ]]; then
        prerequisite_unverified=1
      elif [[ "$prerequisite_status" == "unsatisfied" ]]; then
        unsatisfied="$(
          jq -cn \
            --argjson current "$unsatisfied" \
            --argjson prerequisite "$prerequisite" \
            '$current + [$prerequisite]'
        )"
      fi
    done < <(jq -c '.prerequisites[]' <<<"$action")
    if ((conflicted)); then
      continue
    fi
    if ((prerequisite_unverified)); then
      diagnostics="$(
        jq -cn \
          --argjson current "$diagnostics" \
          --arg revision_id "$revision_id" \
          --arg action_key "$action_key" \
          '$current + [{
            code:"unverified_prerequisite",
            revision_id:$revision_id,
            action_key:$action_key
          }]'
      )"
      continue
    fi

    identity="$(
      _git_loopy_continuation_action_identity \
        "$(jq -c '.record' <<<"$candidate")" \
        "$action"
    )"
    if [[ -z "${layer_cache["$revision_id"]+x}" ]]; then
      layer_cache["$revision_id"]="$(
        _git_loopy_continuation_local_topological_layers \
          "$(jq -c '.record' <<<"$candidate")"
      )"
    fi
    layers="${layer_cache["$revision_id"]}"
    local_order_index="$(
      jq -r --arg key "$action_key" '
        [.actions[] | .key] | map(tostring) | index($key)
      ' <<<"$(jq -c '.record' <<<"$candidate")"
    )"
    projection="$(
      jq -cn \
        --arg identity "$identity" \
        --argjson candidate "$candidate" \
        --argjson unsatisfied "$unsatisfied" \
        --argjson layers "$layers" \
        --argjson local_order_index "$local_order_index" \
        '($candidate.record) as $record
        | ($candidate.action) as $action
        | {
            identity:$identity,
            semantic_fingerprint:
              $record.semantic_fingerprints[$action.key],
            workstream_anchor:$record.workstream.anchor,
            summary:$action.summary,
            kind:$action.kind,
            readiness:(
              if ($unsatisfied | length) > 0 then "Blocked" else "Ready" end
            ),
            instruction:$action.instruction,
            target:$action.target,
            basis:$action.basis,
            producer:(
              $record.producer + {
                carrier:$record.carrier,
                revision_id:$record.revision_id,
                comment_id:$candidate.comment.id,
                comment_url:$candidate.comment.url
              }
            ),
            prerequisites:$action.prerequisites,
            interaction:$action.interaction,
            completion_condition:$action.completion_condition,
            _topological_layer: ($layers[$action.key] // 0),
            _local_order_index: $local_order_index
          }
        | if ($unsatisfied | length) > 0
          then .unsatisfied_prerequisites = $unsatisfied
          else .
          end'
    )"
    actions="$(
      jq -cn \
        --argjson current "$actions" \
        --argjson projection "$projection" \
        '$current + [$projection]'
    )"
  done < <(jq -c '.[] as $entry | $entry.record.actions[]? | {
    record:$entry.record,
    comment:$entry.comment,
    action:.
  }' <<<"$guidance_entries")
  diagnostics="$(
    jq -cn \
      --argjson current "$diagnostics" \
      --argjson local_diagnostics "$GIT_LOOPY_CONTINUATION_LOCAL_DIAGNOSTICS" \
      '$current + $local_diagnostics'
  )"
  local action_conflicts
  action_conflicts="$(
    jq -c '
      sort_by(.identity)
      | group_by(.identity)
      | map(
          select([.[].semantic_fingerprint] | unique | length > 1)
          | {
              code:"action_conflict",
              identity:.[0].identity,
              revision_ids:([.[].producer.revision_id] | sort),
              semantic_fingerprints:([.[].semantic_fingerprint] | unique | sort)
            }
        )
    ' <<<"$actions"
  )"
  diagnostics="$(
    jq -cn \
      --argjson current "$diagnostics" \
      --argjson conflicts "$action_conflicts" \
      '$current + $conflicts'
  )"
  actions="$(
    jq -c '
      sort_by(.identity)
      | group_by(.identity)
      | map(
          select([.[].semantic_fingerprint] | unique | length == 1)
          | . as $claims
          | min_by(.producer.revision_id, .producer.comment_id)
          | .basis = (
              [$claims[].basis[]]
              | sort_by(tojson)
              | unique_by(tojson)
            )
          | if ($claims | length) > 1 then
              .provenance = (
                [
                  $claims[].producer
                  | {
                      login,
                      role,
                      carrier,
                      revision_id,
                      comment_id,
                      comment_url
                    }
                ]
                | sort_by(.carrier.number, .revision_id, .comment_id)
                | unique_by([.carrier.number, .revision_id, .comment_id])
              )
            else .
            end
        )
      | sort_by(
          (if .readiness == "Ready" then 0 else 1 end),
          ._topological_layer,
          (.workstream_anchor | tojson),
          ._local_order_index,
          .identity
        )
      | map(del(._topological_layer, ._local_order_index))
    ' <<<"$actions"
  )"

  local observation_source token outcomes closed_coverage status delta
  observation_source="$(
    jq -cn \
      --arg repository "$repository" \
      --argjson heads "$heads" \
      --argjson validators "$validators" \
      '{repository:$repository,heads:$heads,validators:$validators}'
  )"
  token="sha256:$(
    printf '%s' "$(jq -cS . <<<"$observation_source")" |
      _git_loopy_continuation_sha256
  )"
  outcomes="$(_git_loopy_continuation_workstream_outcomes "$guidance_entries")"
  closed_coverage="$(
    jq -cn --argjson diagnostics "$diagnostics" '
      [
        $diagnostics[]?
        | select(
            .code == "invalid_revision"
            or .code == "missing_predecessor"
            or .code == "missing_retirement_receipt"
            or .code == "mutated_revision"
            or .code == "revision_fork"
          )
      ] | length == 0
    '
  )"
  status="$(
    _git_loopy_continuation_guidance_status \
      "$guidance_entries" \
      "$actions" \
      "$outcomes" \
      "$closed_coverage"
  )"
  delta="null"
  if jq -e 'has("previous_actions")' <<<"$request" >/dev/null; then
    if ! _git_loopy_continuation_validate_previous_actions \
      "$(jq -c '.previous_actions' <<<"$request")" \
      "previous_actions"; then
      _git_loopy_continuation_error \
        "reconcile" \
        "invalid_request" \
        "$GIT_LOOPY_CONTINUATION_VALIDATION_ERROR"
      return 1
    fi
    delta="$(
      _git_loopy_continuation_actions_delta \
        "$actions" \
        "$GIT_LOOPY_CONTINUATION_PREVIOUS_ACTIONS"
    )"
  fi
  if jq -e 'has("handoff")' <<<"$request" >/dev/null; then
    if ! _git_loopy_continuation_validate_handoff \
      "$(jq -c '.handoff' <<<"$request")" \
      "handoff"; then
      _git_loopy_continuation_error \
        "reconcile" \
        "invalid_request" \
        "$GIT_LOOPY_CONTINUATION_VALIDATION_ERROR"
      return 1
    fi
    _git_loopy_continuation_apply_handoff "$actions" "$GIT_LOOPY_CONTINUATION_HANDOFF"
    actions="$GIT_LOOPY_CONTINUATION_HANDOFF_ACTIONS"
    diagnostics="$(
      jq -cn \
        --argjson current "$diagnostics" \
        --argjson handoff_diagnostics \
          "$GIT_LOOPY_CONTINUATION_HANDOFF_DIAGNOSTICS" \
        '$current + $handoff_diagnostics'
    )"
  fi
  jq -cn \
    --arg repository "$repository" \
    --argjson indexed_carriers "$indexed_carriers" \
    --argjson diagnostics "$diagnostics" \
    --argjson heads "$heads" \
    --arg token "$token" \
    --argjson validators "$validators" \
    --argjson producer_revisions "$(jq 'length' <<<"$entries")" \
    --argjson actions "$actions" \
    --argjson outcomes "$outcomes" \
    --argjson retirements "$retirements" \
    --argjson delta "$delta" \
    --arg status "$status" \
    '{
      ok: true,
      operation: "reconcile",
      result: {
        status: $status,
        observed: {
          repository: $repository,
          indexed_carriers: $indexed_carriers,
          producer_revisions: $producer_revisions
        },
        actions: $actions,
        outcomes: $outcomes,
        retirements: $retirements,
        delta: $delta,
        diagnostics: $diagnostics,
        observation: {
          heads: $heads,
          token: $token,
          validators: $validators
        }
      }
    }
    | if ($outcomes | length) > 0
      then .
      else del(.result.outcomes)
      end
    | if ($retirements | length) > 0
      then .
      else del(.result.retirements)
      end
    | if $delta != null
      then .
      else del(.result.delta)
      end'
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
  if ! jq -e '
    (.trusted_apps // [] | type == "array")
    and ((has("revision_protocol") | not) or (.revision_protocol | type == "boolean"))
  ' <<<"$request" >/dev/null 2>&1; then
    _git_loopy_continuation_error \
      "reconcile" \
      "invalid_request" \
      "request is outside the supported trusted Reconciliation contract"
    return 1
  fi
  if [[ "$(jq -r '.revision_protocol // false' <<<"$request")" == "true" ]]; then
    _git_loopy_continuation_reconcile_revision_protocol "$request"
    return $?
  fi

  local repository carriers actions revision_count delta diagnostics
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

      local identity comment_id projection
      identity="$(
        _git_loopy_continuation_action_identity "$record" "$action"
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
  delta="null"
  if jq -e 'has("previous_actions")' <<<"$request" >/dev/null; then
    if ! _git_loopy_continuation_validate_previous_actions \
      "$(jq -c '.previous_actions' <<<"$request")" \
      "previous_actions"; then
      _git_loopy_continuation_error \
        "reconcile" \
        "invalid_request" \
        "$GIT_LOOPY_CONTINUATION_VALIDATION_ERROR"
      return 1
    fi
    delta="$(
      _git_loopy_continuation_actions_delta \
        "$actions" \
        "$GIT_LOOPY_CONTINUATION_PREVIOUS_ACTIONS"
    )"
  fi
  if jq -e 'has("handoff")' <<<"$request" >/dev/null; then
    if ! _git_loopy_continuation_validate_handoff \
      "$(jq -c '.handoff' <<<"$request")" \
      "handoff"; then
      _git_loopy_continuation_error \
        "reconcile" \
        "invalid_request" \
        "$GIT_LOOPY_CONTINUATION_VALIDATION_ERROR"
      return 1
    fi
    _git_loopy_continuation_apply_handoff "$actions" "$GIT_LOOPY_CONTINUATION_HANDOFF"
    actions="$GIT_LOOPY_CONTINUATION_HANDOFF_ACTIONS"
    diagnostics="$GIT_LOOPY_CONTINUATION_HANDOFF_DIAGNOSTICS"
  else
    diagnostics="[]"
  fi
  jq -cn \
    --arg repository "$repository" \
    --argjson indexed_carriers "$(jq 'length' <<<"$carriers")" \
    --argjson producer_revisions "$revision_count" \
    --argjson actions "$actions" \
    --argjson delta "$delta" \
    --argjson diagnostics "$diagnostics" \
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
        delta: $delta,
        diagnostics: $diagnostics
      }
    }
    | if $delta != null
      then .
      else del(.result.delta)
      end'
}

_git_loopy_continuation_repair_index() {
    local request="$1"
    if ! jq -e '
      type == "object"
      and ((keys | sort) == ["repository","trusted_apps","trusted_producers"])
      and (.repository | type == "string" and test("^[^/]+/[^/]+$"))
      and (.trusted_producers | type == "array" and length > 0)
      and all(.trusted_producers[]; type == "string" and length > 0)
      and ((.trusted_apps // []) | type == "array")
    ' <<<"$request" >/dev/null 2>&1; then
      _git_loopy_continuation_error \
        "repair-index" \
        "invalid_request" \
        "request is outside the supported index-repair contract"
      return 1
    fi

    local repository actor login actor_type permission carriers trusted
    repository="$(jq -r '.repository' <<<"$request")"
    if ! actor="$(gh api user)"; then
      _git_loopy_continuation_github_error \
        "repair-index" \
        "reading the authenticated GitHub actor"
      return 1
    fi
    login="$(jq -r '.login // ""' <<<"$actor")"
    actor_type="$(jq -r '.type // ""' <<<"$actor")"
    if [[ -z "$login" || -z "$actor_type" ]]; then
      _git_loopy_continuation_github_error \
        "repair-index" \
        "decoding the authenticated GitHub actor"
      return 1
    fi
    if [[ "$actor_type" == "Bot" || "$actor_type" == "App" ]]; then
      if ! jq -e --arg login "$login" \
        '(.trusted_apps // []) | index($login) != null' \
        <<<"$request" >/dev/null; then
        _git_loopy_continuation_error \
          "repair-index" \
          "invalid_request" \
          "authenticated App actor is not allowlisted"
        return 1
      fi
    else
      if ! jq -e --arg login "$login" \
        '.trusted_producers | index($login) != null' \
        <<<"$request" >/dev/null; then
        _git_loopy_continuation_error \
          "repair-index" \
          "invalid_request" \
          "authenticated human actor is not trusted"
        return 1
      fi
      if ! permission="$(
        gh api "repos/$repository/collaborators/$login/permission"
      )"; then
        _git_loopy_continuation_github_error \
          "repair-index" \
          "reading Producer repository permission"
        return 1
      fi
      case "$(jq -r '.permission | ascii_upcase' <<<"$permission")" in
        ADMIN | MAINTAIN | WRITE) ;;
        *)
          _git_loopy_continuation_error \
            "repair-index" \
            "invalid_request" \
            "authenticated human actor lacks current write permission"
          return 1
          ;;
      esac
    fi

    _git_loopy_continuation_load_all_carriers "$repository" || return 1
    carriers="$GIT_LOOPY_CONTINUATION_CARRIERS"
    trusted="$(jq -c '
      [(.trusted_producers + (.trusted_apps // []))[]] | unique | sort
    ' <<<"$request")"
    local added removed carrier
    added="[]"
    removed="[]"
    while IFS= read -r carrier; do
      local has_record has_trusted_marker comment
      has_record=0
      has_trusted_marker=0
      while IFS= read -r comment; do
        local author author_type authorized comment_permission
        author="$(jq -r '.author' <<<"$comment")"
        author_type="$(jq -r '.author_type' <<<"$comment")"
        authorized=0
        if [[ "$author_type" == "Bot" || "$author_type" == "App" ]]; then
          jq -e --arg author "$author" \
            '(.trusted_apps // []) | index($author) != null' \
            <<<"$request" >/dev/null && authorized=1
        elif jq -e --arg author "$author" \
          '.trusted_producers | index($author) != null' \
          <<<"$request" >/dev/null; then
          if ! comment_permission="$(
            gh api "repos/$repository/collaborators/$author/permission"
          )"; then
            _git_loopy_continuation_github_error \
              "repair-index" \
              "reading Producer repository permission"
            return 1
          fi
          case "$(jq -r '.permission | ascii_upcase' <<<"$comment_permission")" in
            ADMIN | MAINTAIN | WRITE) authorized=1 ;;
          esac
        fi
        ((authorized)) || continue
        [[ "$(jq -r '.body' <<<"$comment")" != \
          *"$GIT_LOOPY_CONTINUATION_RECORD_MARKER"* ]] ||
          has_trusted_marker=1
        if _git_loopy_continuation_parse_revision_record \
          "$comment" "$repository" "$trusted"; then
          if [[ "$(jq -r '.producer.login' \
            <<<"$GIT_LOOPY_CONTINUATION_RECORD")" == "$author" ]]; then
            has_record=1
          fi
        fi
      done < <(jq -c '.comments[]' <<<"$carrier")

      local number indexed
      number="$(jq -r '.number' <<<"$carrier")"
      indexed=0
      jq -e --arg label "$GIT_LOOPY_CONTINUATION_INDEX_LABEL" \
        '.labels | index($label) != null' <<<"$carrier" >/dev/null &&
        indexed=1
      if ((has_record && !indexed)); then
        if ! gh label create "$GIT_LOOPY_CONTINUATION_INDEX_LABEL" \
          --repo "$repository" \
          --color 5319E7 \
          --description "Repairable discovery index for git-loopy Continuation records" \
          --force >/dev/null; then
          _git_loopy_continuation_github_error \
            "repair-index" \
            "establishing the discovery label"
          return 1
        fi
        if ! gh issue edit "$number" \
          --repo "$repository" \
          --add-label "$GIT_LOOPY_CONTINUATION_INDEX_LABEL" >/dev/null; then
          _git_loopy_continuation_github_error \
            "repair-index" \
            "adding the discovery label"
          return 1
        fi
        added="$(jq -cn --argjson current "$added" --argjson number "$number" \
          '($current + [$number]) | sort')"
      elif ((indexed && !has_trusted_marker)); then
        if ! gh issue edit "$number" \
          --repo "$repository" \
          --remove-label "$GIT_LOOPY_CONTINUATION_INDEX_LABEL" >/dev/null; then
          _git_loopy_continuation_github_error \
            "repair-index" \
            "removing the stale discovery label"
          return 1
        fi
        removed="$(jq -cn --argjson current "$removed" --argjson number "$number" \
          '($current + [$number]) | sort')"
      fi
    done < <(jq -c '.[]' <<<"$carriers")

    jq -cn \
      --arg index_label "$GIT_LOOPY_CONTINUATION_INDEX_LABEL" \
      --argjson added "$added" \
      --argjson removed "$removed" \
      '{
        ok:true,
        operation:"repair-index",
        result:{
          status:"repaired",
          index_label:$index_label,
          added:$added,
          removed:$removed
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
        local rendered
        if ! rendered="$(_git_loopy_continuation_reconcile "$request")"; then
          printf '%s\n' "$rendered"
          return 1
        fi
        _git_loopy_continuation_render_terminal "$(jq -c '.result' <<<"$rendered")"
      else
        _git_loopy_continuation_reconcile "$request"
      fi
      ;;
    repair-index)
      _git_loopy_continuation_repair_index "$request"
      ;;
    *)
      _git_loopy_continuation_error \
        "$operation" \
        "unsupported_operation" \
        "$operation is not supported by this distribution"
      ;;
  esac
}
