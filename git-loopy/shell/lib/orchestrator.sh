#!/usr/bin/env bash

if ((BASH_VERSINFO[0] < 4)); then
  printf 'git-loopy shell Orchestrator requires Bash 4+ (found %s).\n' \
    "$BASH_VERSION" >&2
  if [[ "${BASH_SOURCE[0]}" != "$0" ]]; then
    return 1
  fi
  exit 1
fi

_git_loopy_orchestrator_dir="$(
  cd "$(dirname "${BASH_SOURCE[0]}")" && pwd
)"
_GIT_LOOPY_RELEASE_VERSION_PATH="$_git_loopy_orchestrator_dir/../../../VERSION"

# shellcheck disable=SC1091
source "$_git_loopy_orchestrator_dir/release-version.sh"
# shellcheck disable=SC1091
source "$_git_loopy_orchestrator_dir/events.sh"
# shellcheck disable=SC1091
source "$_git_loopy_orchestrator_dir/continuation.sh"
# shellcheck disable=SC1091
source "$_git_loopy_orchestrator_dir/tui.sh"

declare -a GIT_LOOPY_DENY_TOOLS_RESOLVED=()
declare -a GIT_LOOPY_DENY_SKILLS_RESOLVED=()
# Closed-world Skill-policy overlay flags seen on this invocation (contract
# §17.6). The shell port recognises them so it can fail closed; it never applies
# them. Recording the flag *names* (not their values) is deliberate: the abort
# names the surface, and a Skill name is not the operator's problem here.
declare -a GIT_LOOPY_SKILL_POLICY_FLAGS_SEEN=()
GIT_LOOPY_MAX_ITERATIONS=0
# Public config variables remain untouched here because inherited values are
# inputs to CLI-over-environment precedence resolution.
GIT_LOOPY_REPO_ROOT=""
GIT_LOOPY_PROMPT_PATH=""
GIT_LOOPY_POOL_JSON='[]'
# Wrapper contract §3.1 — the ready-for-agent candidates this collection
# rejected, as `{issue, title, reason}` objects in source order. Rebuilt with
# the Pool on every Iteration, never carried across one.
GIT_LOOPY_POOL_EXCLUSIONS_JSON='[]'
# Wrapper contract §2 — whether this Iteration's candidate fetch reached the end
# of the backlog. `0` means the read hit its ceiling or failed outright, so the
# candidates it did return may not stand in for the whole Pool: they establish
# neither emptiness (ADR-0020 §2.13) nor the head of the order (§3.2). Rebuilt
# with the Pool on every Iteration.
GIT_LOOPY_POOL_COMPLETE=1
# The raw `gh issue list` array the paginating reader last produced, carried as
# a global because a command substitution's subshell would discard the
# completeness flag that has to travel with it.
GIT_LOOPY_ISSUE_LIST_JSON='[]'
# Tri-state interactive request: "on", "off", or empty for "no flag given".
GIT_LOOPY_INTERACTIVE_FLAG=""
# Wrapper contract §3.2 — the invocation-scoped **Pin** (`--issue N`, #396), or
# empty when unpinned. Deliberately *not* seeded from the environment, unlike
# every other knob below: an env var is inherited by every Run launched from
# that shell, which is the same globally-scoped hazard that rules out expressing
# the pin as a label (ADR-0032). A flag is the only surface whose lifetime
# matches the thing being expressed.
GIT_LOOPY_ISSUE_PIN=""
# Continuation authority is collected as three uncombined sources and resolved
# by the native Continuation module during preflight. An empty value preserves
# the pre-Continuation Pool lifecycle byte-for-byte.
GIT_LOOPY_CONTINUATION_SOURCES_JSON='[]'
GIT_LOOPY_CONTINUATION_AUTHORITY_JSON=''

git_loopy_usage() {
  cat <<'EOF'
Usage: git-loopy.sh [<max-iterations>] [options]

Commands:
  continuation                    Native Continuation contract commands.

Options:
  --model ID
  --reasoning-effort none|minimal|low|medium|high|xhigh|max
  --issue-source github|prds
  --issue N                     Pin issue N for this invocation (ADR-0032):
                                work N instead of the head of the selection
                                order. At most once. Bypasses order and nothing
                                else -- a pinned issue that is closed, missing,
                                unreadable, lacks ready-for-agent, or fails the
                                AFK-ready discriminator fails the invocation.
  --max-nmt-strikes N
  --deny-tool TOOL              Repeatable; unioned with GIT_LOOPY_DENY_TOOLS.
  --deny-skill SKILL            Repeatable; unioned with GIT_LOOPY_DENY_SKILLS.
  --enable-skill SKILL          Closed-world Skill policy; not yet supported by
                                the shell Orchestrator (fails closed).
  --disable-skill SKILL         Closed-world Skill policy; not yet supported by
                                the shell Orchestrator (fails closed).
  --send-timeout-seconds N
  --interactive                 Drive the shared git-loopy-tui helper when a
                                compatible one is discoverable.
  --no-interactive              Keep raw JSONL on stdout (CI-safe).
  --version
  -h, --help
EOF
}

git_loopy_print_release_version() {
  local release_version
  release_version="$(
    git_loopy_read_release_version "$_GIT_LOOPY_RELEASE_VERSION_PATH"
  )" || return 1
  printf 'git-loopy %s\n' "$release_version"
}

_git_loopy_config_error() {
  printf 'git-loopy: %s\n' "$*" >&2
  return 2
}

_git_loopy_require_option_value() {
  local option="$1"
  shift
  (($# >= 1)) &&
    [[ "$1" != -* ]] ||
    _git_loopy_config_error "$option requires a value"
}

# Accepts one `--issue N` and refuses a second. Exactly one issue may be pinned
# per invocation (#396): "the last one wins" is the silent substitution the pin
# exists to prevent, arrived at by an argument-parsing default. A malformed
# invocation is a *usage* error (exit 2), not the preflight failure an
# ineligible-but-well-formed pin produces.
#
# Writes `issue_pin` / `issue_pin_seen` in the caller's scope, which are
# `git_loopy_resolve_config`'s locals — the same convention the repeatable
# `--deny-tool` accumulators use.
_git_loopy_accept_issue_pin() {
  local raw="$1"
  ((issue_pin_seen == 0)) ||
    _git_loopy_config_error \
      "--issue may be given at most once; exactly one issue may be pinned per invocation" ||
    return 2
  [[ "$raw" =~ ^[0-9]+$ ]] ||
    _git_loopy_config_error "--issue must be an issue number, got '$raw'" ||
    return 2
  ((10#$raw >= 1)) ||
    _git_loopy_config_error "--issue must be a positive issue number, got '$raw'" ||
    return 2
  issue_pin="$((10#$raw))"
  issue_pin_seen=1
}

_git_loopy_trim() {
  local value="$1"
  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"
  printf '%s' "$value"
}

_git_loopy_array_contains() {
  local needle="$1"
  shift
  local value
  for value in "$@"; do
    [[ "$value" == "$needle" ]] && return 0
  done
  return 1
}

_git_loopy_string_array_json() {
  if (($# == 0)); then
    printf '[]\n'
    return
  fi
  printf '%s\n' "$@" | jq -Rsc 'split("\n") | map(select(length > 0))'
}

_git_loopy_json_object_array() {
  if (($# == 0)); then
    printf '[]\n'
    return
  fi
  printf '%s\n' "$@" | jq -sc '.'
}

_git_loopy_add_unique_tool() {
  local value
  value="$(_git_loopy_trim "$1")"
  [[ -n "$value" ]] || return
  if ! _git_loopy_array_contains "$value" \
    ${GIT_LOOPY_DENY_TOOLS_RESOLVED[@]+"${GIT_LOOPY_DENY_TOOLS_RESOLVED[@]}"}; then
    GIT_LOOPY_DENY_TOOLS_RESOLVED+=("$value")
  fi
}

_git_loopy_add_unique_skill() {
  local value
  value="$(_git_loopy_trim "$1")"
  [[ -n "$value" ]] || return
  if ! _git_loopy_array_contains "$value" \
    ${GIT_LOOPY_DENY_SKILLS_RESOLVED[@]+"${GIT_LOOPY_DENY_SKILLS_RESOLVED[@]}"}; then
    GIT_LOOPY_DENY_SKILLS_RESOLVED+=("$value")
  fi
}

_git_loopy_note_skill_policy_flag() {
  local flag="$1"
  if ! _git_loopy_array_contains "$flag" \
    ${GIT_LOOPY_SKILL_POLICY_FLAGS_SEEN[@]+"${GIT_LOOPY_SKILL_POLICY_FLAGS_SEEN[@]}"}; then
    GIT_LOOPY_SKILL_POLICY_FLAGS_SEEN+=("$flag")
  fi
}

# Resolve the global scope directory (`<config-home>/git-loopy`) the same way
# every scope-aware lookup does: $XDG_CONFIG_HOME, else $HOME/.config, else the
# expanded home directory. Prints nothing when no home is resolvable.
_git_loopy_config_home() {
  if [[ -n "${XDG_CONFIG_HOME:-}" ]] &&
    [[ -n "$(_git_loopy_trim "$XDG_CONFIG_HOME")" ]]; then
    printf '%s\n' "$XDG_CONFIG_HOME"
    return 0
  fi
  if [[ -n "${HOME:-}" ]]; then
    printf '%s\n' "$HOME/.config"
    return 0
  fi
  local fallback_home=""
  fallback_home="$(cd ~ 2>/dev/null && pwd)" || true
  [[ -n "$fallback_home" ]] || return 0
  printf '%s\n' "$fallback_home/.config"
}

# Decode one TOML quoted key's escape sequences.
#
# `printf '%b'` would be shorter, but its `\uXXXX` / `\UXXXXXXXX` support arrived
# in Bash 4.2 while this port supports Bash 4+ — and on an older Bash the escape
# would survive undecoded, which is precisely the miss that widens a Run. So the
# scan is explicit. Only the ASCII range is materialised: a canonical Skill-policy
# key is `[a-z_]`, so any wider codepoint cannot spell one and is dropped.
_git_loopy_decode_toml_key() {
  local raw="$1"
  local out="" index=0 length=${#raw} char hex width
  while ((index < length)); do
    char="${raw:index:1}"
    if [[ "$char" != "\\" ]] || ((index + 1 >= length)); then
      out+="$char"
      index=$((index + 1))
      continue
    fi
    case "${raw:index+1:1}" in
      u) width=4 ;;
      U) width=8 ;;
      *)
        out+="${raw:index+1:1}"
        index=$((index + 2))
        continue
        ;;
    esac
    hex="${raw:index+2:width}"
    index=$((index + 2 + width))
    # Two hex digits after leading zeros keeps the format literal safe to build.
    [[ "$hex" =~ ^0*([0-9a-fA-F]{2})$ ]] || continue
    out+="$(printf "\\x${BASH_REMATCH[1]}")"
  done
  printf '%s' "$out"
}

# Conservative detection of an `enabled_skills` key in one `config.toml`.
#
# The shell port has no TOML parser, and this decision only ever *widens the
# abort*: a false positive costs an operator one diagnostic, while a false
# negative runs an Iteration on a wider capability set than they configured
# (contract §17.6). So any assignment of the key counts, including one nested
# under a table that the Python resolver would ignore. Anchoring the match to the
# start of the trimmed line is what keeps a commented example — including the
# comment-only banner `write_config` generates — from reading as a policy.
#
# A TOML *quoted* key is decoded before comparison: `tomllib` resolves
# `"enabled\u005fskills"` to `enabled_skills`, so a Config the Python
# Orchestrator honours would otherwise slip through this port unnoticed.
git_loopy_config_declares_enabled_skills() {
  local path="$1"
  [[ -f "$path" && -r "$path" ]] || return 1
  local line trimmed key
  while IFS= read -r line || [[ -n "$line" ]]; do
    trimmed="$(_git_loopy_trim "$line")"
    if [[ "$trimmed" =~ ^enabled_skills[[:space:]]*= ]]; then
      return 0
    fi
    if [[ "$trimmed" =~ ^(\"([^\"]*)\"|\'([^\']*)\')[[:space:]]*= ]]; then
      # Exactly one alternative matched, so the other capture is empty.
      # Decoding a literal (single-quoted) key too is harmless: TOML gives it no
      # escapes, and the only possible effect is widening the abort.
      key="$(_git_loopy_decode_toml_key "${BASH_REMATCH[2]}${BASH_REMATCH[3]}")"
      [[ "$key" == "enabled_skills" ]] && return 0
    fi
  done <"$path"
  return 1
}

# Every closed-world Skill-policy surface this Run carries that the shell port
# cannot honour, one per line, in the canonical order of the family fixture's
# `native_transition.policy_surfaces`. Empty output means nothing unsupported was
# configured; legacy deny-only inputs are never a surface.
git_loopy_detect_skill_policy_surfaces() {
  local repo_root="${1:-}"

  # Presence, not content: an explicit empty replacement is a real policy.
  [[ -n "${GIT_LOOPY_ENABLED_SKILLS+x}" ]] &&
    printf '%s\n' 'GIT_LOOPY_ENABLED_SKILLS'

  local flag
  for flag in '--enable-skill' '--disable-skill'; do
    _git_loopy_array_contains "$flag" \
      ${GIT_LOOPY_SKILL_POLICY_FLAGS_SEEN[@]+"${GIT_LOOPY_SKILL_POLICY_FLAGS_SEEN[@]}"} &&
      printf '%s\n' "$flag"
  done

  local -a config_paths=()
  [[ -n "$repo_root" ]] && config_paths+=("$repo_root/git-loopy/config.toml")
  local config_home
  config_home="$(_git_loopy_config_home)"
  [[ -n "$config_home" ]] && config_paths+=("$config_home/git-loopy/config.toml")

  local path
  for path in ${config_paths[@]+"${config_paths[@]}"}; do
    if git_loopy_config_declares_enabled_skills "$path"; then
      printf '%s\n' 'enabled_skills'
      break
    fi
  done

  return 0
}

# Read a top-level Continuation key from the limited TOML surface this
# Orchestrator supports. Continuation settings are scalar strings or arrays of
# strings written by the shared config writer; tables are deliberately ignored.
_git_loopy_continuation_toml_value() {
  local path="$1" key="$2"
  [[ -f "$path" && -r "$path" ]] || return 1

  local line trimmed in_table=0
  while IFS= read -r line || [[ -n "$line" ]]; do
    trimmed="$(_git_loopy_trim "$line")"
    [[ -n "$trimmed" && "${trimmed:0:1}" != "#" ]] || continue
    if [[ "$trimmed" == \[* ]]; then
      in_table=1
      continue
    fi
    ((in_table == 0)) || continue
    if [[ "$trimmed" =~ ^$key[[:space:]]*=(.*)$ ]]; then
      _git_loopy_trim "${BASH_REMATCH[1]}"
      return 0
    fi
  done <"$path"
  return 1
}

_git_loopy_continuation_toml_string() {
  local raw
  raw="$(_git_loopy_trim "$1")"
  if [[ "$raw" =~ ^\".*\"$ ]]; then
    jq -er 'if type == "string" then . else empty end' <<<"$raw"
    return
  fi
  if [[ "$raw" =~ ^\'(.*)\'$ ]]; then
    printf '%s' "${BASH_REMATCH[1]}"
    return
  fi
  printf 'git-loopy: Continuation configuration must be a quoted string.\n' >&2
  return 1
}

_git_loopy_continuation_toml_string_array() {
  local raw
  raw="$(_git_loopy_trim "$1")"
  jq -ce '
    if type == "array" and all(.[]; type == "string") then . else empty end
  ' <<<"$raw" || {
    printf 'git-loopy: Continuation configuration must be an array of strings.\n' >&2
    return 1
  }
}

_git_loopy_continuation_csv_array() {
  local raw="${1:-}"
  local -a values=()
  local value
  IFS=',' read -r -a values <<<"$raw"
  local -a trimmed=()
  for value in "${values[@]}"; do
    value="$(_git_loopy_trim "$value")"
    [[ -n "$value" ]] && trimmed+=("$value")
  done
  _git_loopy_string_array_json "${trimmed[@]}"
}

_git_loopy_continuation_source_from_toml() {
  local source="$1" path="$2"
  local raw mode
  if ! raw="$(_git_loopy_continuation_toml_value "$path" continuation_mode)"; then
    printf ''
    return 0
  fi
  mode="$(_git_loopy_continuation_toml_string "$raw")" || return 1

  local actor_json='null'
  if raw="$(_git_loopy_continuation_toml_value "$path" continuation_actor)"; then
    local actor
    actor="$(_git_loopy_continuation_toml_string "$raw")" || return 1
    actor_json="$(jq -cn --arg actor "$actor" '$actor')" || return 1
  fi

  local field values
  local -a arrays=()
  for field in trusted_producers maintainers repositories targets \
    action_kinds instruction_modes effect_scopes; do
    if raw="$(_git_loopy_continuation_toml_value \
      "$path" "continuation_$field")"; then
      values="$(_git_loopy_continuation_toml_string_array "$raw")" || return 1
    else
      values='[]'
    fi
    arrays+=("$values")
  done

  jq -cn \
    --arg source "$source" \
    --arg mode "$mode" \
    --argjson actor "$actor_json" \
    --argjson trusted_producers "${arrays[0]}" \
    --argjson maintainers "${arrays[1]}" \
    --argjson repositories "${arrays[2]}" \
    --argjson targets "${arrays[3]}" \
    --argjson action_kinds "${arrays[4]}" \
    --argjson instruction_modes "${arrays[5]}" \
    --argjson effect_scopes "${arrays[6]}" \
    '{
      source: $source,
      mode: $mode,
      trusted_producers: $trusted_producers,
      ceilings: {
        repositories: $repositories,
        targets: $targets,
        action_kinds: $action_kinds,
        instruction_modes: $instruction_modes,
        effect_scopes: $effect_scopes
      }
    }
    + (if $actor == null then {} else {actor: $actor} end)
    + (if $maintainers == [] then {} else {maintainers: $maintainers} end)'
}

_git_loopy_continuation_source_from_environment() {
  local mode
  mode="$(_git_loopy_trim "${GIT_LOOPY_CONTINUATION_MODE:-}")"
  [[ -n "$mode" ]] || {
    printf ''
    return 0
  }

  local actor_json='null'
  local actor
  actor="$(_git_loopy_trim "${GIT_LOOPY_CONTINUATION_ACTOR:-}")"
  [[ -z "$actor" ]] ||
    actor_json="$(jq -cn --arg actor "$actor" '$actor')" || return 1

  local field env_name values
  local -a arrays=()
  for field in trusted_producers maintainers repositories targets \
    action_kinds instruction_modes effect_scopes; do
    env_name="GIT_LOOPY_CONTINUATION_${field^^}"
    values="$(_git_loopy_continuation_csv_array "${!env_name:-}")" || return 1
    arrays+=("$values")
  done

  jq -cn \
    --argjson actor "$actor_json" \
    --arg mode "$mode" \
    --argjson trusted_producers "${arrays[0]}" \
    --argjson maintainers "${arrays[1]}" \
    --argjson repositories "${arrays[2]}" \
    --argjson targets "${arrays[3]}" \
    --argjson action_kinds "${arrays[4]}" \
    --argjson instruction_modes "${arrays[5]}" \
    --argjson effect_scopes "${arrays[6]}" \
    '{
      source: "runtime",
      mode: $mode,
      trusted_producers: $trusted_producers,
      ceilings: {
        repositories: $repositories,
        targets: $targets,
        action_kinds: $action_kinds,
        instruction_modes: $instruction_modes,
        effect_scopes: $effect_scopes
      }
    }
    + (if $actor == null then {} else {actor: $actor} end)
    + (if $maintainers == [] then {} else {maintainers: $maintainers} end)'
}

git_loopy_collect_continuation_sources() {
  local repo_root="$1"
  local config_home global_path project_path source
  config_home="$(_git_loopy_config_home)"
  global_path="${config_home:+$config_home/git-loopy/config.toml}"
  project_path="$repo_root/git-loopy/config.toml"
  local -a sources=()

  if source="$(_git_loopy_continuation_source_from_toml global "$global_path")"; then
    [[ -z "$source" ]] || sources+=("$source")
  else
    return 1
  fi
  if source="$(_git_loopy_continuation_source_from_toml project "$project_path")"; then
    [[ -z "$source" ]] || sources+=("$source")
  else
    return 1
  fi
  if source="$(_git_loopy_continuation_source_from_environment)"; then
    [[ -z "$source" ]] || sources+=("$source")
  else
    return 1
  fi

  GIT_LOOPY_CONTINUATION_SOURCES_JSON="$(
    _git_loopy_json_object_array "${sources[@]}"
  )" || return 1
}

git_loopy_resolve_continuation_authority() {
  local repo_root="$1"
  GIT_LOOPY_CONTINUATION_AUTHORITY_JSON=''
  git_loopy_collect_continuation_sources "$repo_root" || return 1
  [[ "$(jq -r 'length' <<<"$GIT_LOOPY_CONTINUATION_SOURCES_JSON")" != "0" ]] ||
    return 0

  local request response
  request="$(jq -cn --argjson sources "$GIT_LOOPY_CONTINUATION_SOURCES_JSON" \
    '{sources: $sources}')" || return 1
  response="$(_git_loopy_continuation_resolve_authority "$request")" ||
    return 1
  GIT_LOOPY_CONTINUATION_AUTHORITY_JSON="$(
    jq -ce '.result' <<<"$response"
  )" || return 1
}

git_loopy_prepare_continuation_frontier() {
  [[ -n "$GIT_LOOPY_CONTINUATION_AUTHORITY_JSON" ]] || return 0
  local mode
  mode="$(jq -r '.mode' <<<"$GIT_LOOPY_CONTINUATION_AUTHORITY_JSON")" ||
    return 1
  [[ "$mode" == "execute-frontier" ]] || return 0

  [[ -n "$(jq -r '.actor // ""' <<<"$GIT_LOOPY_CONTINUATION_AUTHORITY_JSON")" ]] || {
    printf '%s\n' \
      'git-loopy: continuation mode execute-frontier requires a configured actor.' \
      >&2
    return 1
  }
  local axis
  for axis in action_kinds targets; do
    if [[ "$(jq -r --arg axis "$axis" '.ceilings[$axis] | length' \
      <<<"$GIT_LOOPY_CONTINUATION_AUTHORITY_JSON")" != "0" ]]; then
      printf 'git-loopy: continuation ceiling %s cannot be enforced by the shell Orchestrator.\n' \
        "$axis" >&2
      return 1
    fi
  done
  if ! jq -e '
    (.ceilings.instruction_modes | length == 0)
    or (.ceilings.instruction_modes | index("skill") != null)
  ' <<<"$GIT_LOOPY_CONTINUATION_AUTHORITY_JSON" >/dev/null; then
    printf '%s\n' \
      'git-loopy: continuation ceiling instruction_modes excludes every Instruction mode this distribution handles.' \
      >&2
    return 1
  fi
}

git_loopy_resolve_config() {
  local env_model="${GIT_LOOPY_MODEL:-}"
  local env_effort="${GIT_LOOPY_REASONING_EFFORT:-}"
  local env_source="${GIT_LOOPY_ISSUE_SOURCE:-}"
  local env_strikes="${GIT_LOOPY_MAX_NMT_STRIKES:-}"
  local env_tools="${GIT_LOOPY_DENY_TOOLS:-}"
  local env_skills="${GIT_LOOPY_DENY_SKILLS:-}"
  local env_timeout="${GIT_LOOPY_SEND_TIMEOUT_SECONDS:-}"

  local model="claude-opus-4.8"
  local effort=""
  local model_explicit=0
  local effort_explicit=0
  if [[ -n "$(_git_loopy_trim "$env_model")" ]]; then
    model="$env_model"
    model_explicit=1
  fi
  if [[ -n "$(_git_loopy_trim "$env_effort")" ]]; then
    effort="$env_effort"
    effort_explicit=1
  fi
  local issue_source="${env_source:-github}"
  local max_strikes="${env_strikes:-3}"
  local send_timeout="${env_timeout:-7200}"
  local max_iterations=0
  local positional_seen=0
  local issue_pin=""
  local issue_pin_seen=0
  local -a cli_tools=()
  local -a cli_skills=()
  # Tri-state, exactly like the Python reference: empty means "no flag", so the
  # environment and then TTY auto-detection still get their turn.
  local interactive_flag=""
  GIT_LOOPY_SKILL_POLICY_FLAGS_SEEN=()

  while (($# > 0)); do
    case "$1" in
      -h | --help)
        git_loopy_usage
        return 64
        ;;
      --model)
        _git_loopy_require_option_value "$@" || return 2
        model="$2"
        model_explicit=1
        shift 2
        ;;
      --model=*)
        model="${1#*=}"
        model_explicit=1
        shift
        ;;
      --reasoning-effort)
        _git_loopy_require_option_value "$@" || return 2
        effort="$2"
        effort_explicit=1
        shift 2
        ;;
      --reasoning-effort=*)
        effort="${1#*=}"
        effort_explicit=1
        shift
        ;;
      --issue)
        _git_loopy_require_option_value "$@" || return 2
        _git_loopy_accept_issue_pin "$2" || return 2
        shift 2
        ;;
      --issue=*)
        _git_loopy_accept_issue_pin "${1#*=}" || return 2
        shift
        ;;
      --issue-source)
        _git_loopy_require_option_value "$@" || return 2
        issue_source="$2"
        shift 2
        ;;
      --issue-source=*)
        issue_source="${1#*=}"
        shift
        ;;
      --max-nmt-strikes)
        _git_loopy_require_option_value "$@" || return 2
        max_strikes="$2"
        shift 2
        ;;
      --max-nmt-strikes=*)
        max_strikes="${1#*=}"
        shift
        ;;
      --deny-tool)
        _git_loopy_require_option_value "$@" || return 2
        cli_tools+=("$2")
        shift 2
        ;;
      --deny-tool=*)
        cli_tools+=("${1#*=}")
        shift
        ;;
      --deny-skill)
        _git_loopy_require_option_value "$@" || return 2
        cli_skills+=("$2")
        shift 2
        ;;
      --deny-skill=*)
        cli_skills+=("${1#*=}")
        shift
        ;;
      --enable-skill | --disable-skill)
        _git_loopy_require_option_value "$@" || return 2
        _git_loopy_note_skill_policy_flag "$1"
        shift 2
        ;;
      --enable-skill=* | --disable-skill=*)
        _git_loopy_note_skill_policy_flag "${1%%=*}"
        shift
        ;;
      --send-timeout-seconds)
        _git_loopy_require_option_value "$@" || return 2
        send_timeout="$2"
        shift 2
        ;;
      --send-timeout-seconds=*)
        send_timeout="${1#*=}"
        shift
        ;;
      --interactive)
        interactive_flag="on"
        shift
        ;;
      --no-interactive)
        interactive_flag="off"
        shift
        ;;
      --)
        shift
        while (($# > 0)); do
          ((positional_seen == 0)) ||
            {
              _git_loopy_config_error "only one iteration cap is accepted"
              return 2
            }
          max_iterations="$1"
          positional_seen=1
          shift
        done
        ;;
      -*)
        _git_loopy_config_error "unknown option: $1"
        return 2
        ;;
      *)
        ((positional_seen == 0)) ||
          {
            _git_loopy_config_error "only one iteration cap is accepted"
            return 2
          }
        max_iterations="$1"
        positional_seen=1
        shift
        ;;
    esac
  done

  model="$(_git_loopy_trim "$model")"
  local suffix_effort=""
  if [[ "$model" =~ ^(.+)-(none|minimal|low|medium|high|xhigh|max)$ ]]; then
    model="${BASH_REMATCH[1]}"
    suffix_effort="${BASH_REMATCH[2]}"
  fi
  if ((effort_explicit == 0)); then
    if [[ -n "$suffix_effort" ]]; then
      effort="$suffix_effort"
    elif ((model_explicit == 0)); then
      effort="max"
    else
      effort=""
    fi
  fi
  effort="${effort,,}"
  issue_source="${issue_source,,}"

  [[ -n "$model" ]] || {
    _git_loopy_config_error "model must not be empty"
    return 2
  }
  if ((effort_explicit != 0)) && [[ -z "$effort" ]]; then
    _git_loopy_config_error "reasoning effort must not be empty"
    return 2
  fi
  [[ -z "$effort" ||
    "$effort" =~ ^(none|minimal|low|medium|high|xhigh|max)$ ]] || {
    _git_loopy_config_error "invalid reasoning effort: $effort"
    return 2
  }
  [[ "$issue_source" == "github" || "$issue_source" == "prds" ]] || {
    _git_loopy_config_error "issue source must be github or prds"
    return 2
  }
  [[ "$max_iterations" =~ ^[0-9]+$ ]] || {
    _git_loopy_config_error "iteration cap must be a non-negative integer"
    return 2
  }
  [[ "$max_strikes" =~ ^[1-9][0-9]*$ ]] || {
    _git_loopy_config_error "max NMT strikes must be a positive integer"
    return 2
  }
  [[ "$send_timeout" =~ ^([0-9]+([.][0-9]*)?|[.][0-9]+)$ ]] &&
    [[ "$send_timeout" =~ [1-9] ]] || {
    _git_loopy_config_error "send timeout must be a positive number"
    return 2
  }

  GIT_LOOPY_DENY_TOOLS_RESOLVED=()
  GIT_LOOPY_DENY_SKILLS_RESOLVED=()
  local value
  for value in ${cli_tools[@]+"${cli_tools[@]}"}; do
    _git_loopy_add_unique_tool "$value"
  done
  local -a env_tool_values=()
  IFS=',' read -r -a env_tool_values <<<"$env_tools"
  for value in ${env_tool_values[@]+"${env_tool_values[@]}"}; do
    _git_loopy_add_unique_tool "$value"
  done
  for value in ${cli_skills[@]+"${cli_skills[@]}"}; do
    _git_loopy_add_unique_skill "$value"
  done
  local -a env_skill_values=()
  IFS=',' read -r -a env_skill_values <<<"$env_skills"
  for value in ${env_skill_values[@]+"${env_skill_values[@]}"}; do
    _git_loopy_add_unique_skill "$value"
  done

  GIT_LOOPY_MAX_ITERATIONS="$((10#$max_iterations))"
  GIT_LOOPY_MODEL="$model"
  GIT_LOOPY_REASONING_EFFORT="$effort"
  GIT_LOOPY_ISSUE_SOURCE="$issue_source"
  GIT_LOOPY_MAX_NMT_STRIKES="$((10#$max_strikes))"
  GIT_LOOPY_SEND_TIMEOUT_SECONDS="$send_timeout"
  GIT_LOOPY_INTERACTIVE_FLAG="$interactive_flag"
  GIT_LOOPY_ISSUE_PIN="$issue_pin"
}

git_loopy_is_afk_ready() {
  local body="$1"
  [[ "$body" =~ (^|$'\n')##\ What\ to\ build ]] &&
    [[ "$body" =~ (^|$'\n')##\ Acceptance\ criteria ]]
}

# Wrapper contract §3.1 — name *why* a candidate leaves the Pool. Prints one
# reason from the closed exclusion vocabulary, or nothing when the body is
# AFK-ready. `git_loopy_is_afk_ready` stays the boolean oracle so membership and
# the reported reason are decided by the same two matches.
git_loopy_afk_ready_exclusion() {
  local body="$1"
  local has_what=0 has_ac=0
  [[ "$body" =~ (^|$'\n')##\ What\ to\ build ]] && has_what=1
  [[ "$body" =~ (^|$'\n')##\ Acceptance\ criteria ]] && has_ac=1
  if ((has_what && has_ac)); then
    return 0
  fi
  if ((has_what)); then
    printf 'missing_acceptance_criteria\n'
  elif ((has_ac)); then
    printf 'missing_what_to_build\n'
  else
    printf 'missing_both_sections\n'
  fi
}

# Wrapper contract §3.2 — the total order over eligible issues (#391, ADR-0032).
#
# The order was previously `gh issue list`'s undeclared `sort=created&
# direction=desc`, so an issue filed in January waited behind one filed today,
# indefinitely. These functions are this port's half of the shared decision:
# `(priority rank, created_at, issue number)` ascending, pinned against Python
# and PowerShell by `conformance/issue-ordering.json`.
#
# The civil-date arithmetic below is spelled out rather than delegated to
# `date(1)`: `date -d` is GNU-only and `date -j` is BSD-only, so a port reaching
# for either would order Pools differently on Linux and macOS — and would inherit
# that implementation's tolerances instead of the contract's grammar.

# The label that carries **Priority**. A human assertion, read at selection and
# never inferred. Provisioning it is #395's job; ordering only needs its name.
GIT_LOOPY_PRIORITY_LABEL="priority"
# The accepted `created_at` year range (§3.2). The floor is what keeps every
# division in `_git_loopy_days_from_civil` on a non-negative operand, so Bash's
# truncating `/` cannot diverge from Python's flooring `//`.
GIT_LOOPY_MIN_ACCEPTED_YEAR=1970
GIT_LOOPY_MAX_ACCEPTED_YEAR=9999

_git_loopy_is_leap_year() {
  local year="$1"
  ((year % 4 == 0 && (year % 100 != 0 || year % 400 == 0)))
}

_git_loopy_days_in_month() {
  local year="$1" month="$2"
  local -a lengths=(31 28 31 30 31 30 31 31 30 31 30 31)
  if ((month == 2)) && _git_loopy_is_leap_year "$year"; then
    printf '29\n'
    return 0
  fi
  printf '%d\n' "${lengths[month - 1]}"
}

# Days from 1970-01-01 to this civil date (Howard Hinnant's `days_from_civil`).
_git_loopy_days_from_civil() {
  local year="$1" month="$2" day="$3"
  local shifted era year_of_era day_of_year day_of_era
  shifted=$((month <= 2 ? year - 1 : year))
  era=$((shifted / 400))
  year_of_era=$((shifted - era * 400))
  day_of_year=$(((153 * (month + (month > 2 ? -3 : 9)) + 2) / 5 + day - 1))
  day_of_era=$((year_of_era * 365 + year_of_era / 4 - year_of_era / 100 +
    day_of_year))
  printf '%d\n' $((era * 146097 + day_of_era - 719468))
}

# Prints `<UTC seconds> <nanoseconds>` for a usable timestamp; returns 1 when the
# value is outside the contract's grammar. Offsets are normalized here rather
# than compared as text: `2026-03-01T01:00:00+01:00` is fifteen minutes *before*
# `2026-03-01T00:45:00Z`, and string order says the opposite.
_git_loopy_issue_instant() {
  local created_at="$1"
  local pattern='^([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})(\.([0-9]{1,9}))?([Zz]|([+-])([0-9]{2}):([0-9]{2}))$'
  [[ "$created_at" =~ $pattern ]] || return 1

  local year month day hour minute second fraction sign
  year=$((10#${BASH_REMATCH[1]}))
  month=$((10#${BASH_REMATCH[2]}))
  day=$((10#${BASH_REMATCH[3]}))
  hour=$((10#${BASH_REMATCH[4]}))
  minute=$((10#${BASH_REMATCH[5]}))
  second=$((10#${BASH_REMATCH[6]}))
  fraction="${BASH_REMATCH[8]}"
  sign="${BASH_REMATCH[10]}"

  ((year >= GIT_LOOPY_MIN_ACCEPTED_YEAR &&
    year <= GIT_LOOPY_MAX_ACCEPTED_YEAR)) || return 1
  ((month >= 1 && month <= 12)) || return 1
  local month_length
  month_length="$(_git_loopy_days_in_month "$year" "$month")"
  ((day >= 1 && day <= month_length)) || return 1
  ((hour <= 23 && minute <= 59 && second <= 59)) || return 1

  local offset_seconds=0
  if [[ -n "$sign" ]]; then
    local offset_hour offset_minute
    offset_hour=$((10#${BASH_REMATCH[11]}))
    offset_minute=$((10#${BASH_REMATCH[12]}))
    ((offset_hour <= 23 && offset_minute <= 59)) || return 1
    offset_seconds=$((offset_hour * 3600 + offset_minute * 60))
    if [[ "$sign" == "-" ]]; then
      offset_seconds=$((-offset_seconds))
    fi
  fi

  local nanoseconds=0
  if [[ -n "$fraction" ]]; then
    local padded="${fraction}000000000"
    nanoseconds=$((10#${padded:0:9}))
  fi

  local days
  days="$(_git_loopy_days_from_civil "$year" "$month" "$day")"
  printf '%d %d\n' \
    $((days * 86400 + hour * 3600 + minute * 60 + second - offset_seconds)) \
    "$nanoseconds"
}

# Prints `0` when these labels carry **Priority**, `1` otherwise. Matching is
# exact: `priority:high` is a different label and a vocabulary nobody decided.
git_loopy_priority_rank() {
  local labels_json="$1"
  if jq -e --arg label "$GIT_LOOPY_PRIORITY_LABEL" \
    'any(.[]?; . == $label)' <<<"$labels_json" >/dev/null 2>&1; then
    printf '0\n'
  else
    printf '1\n'
  fi
}

# Prints one timestamp defect from the closed §3.2 vocabulary, or nothing when
# the value is usable. `_git_loopy_issue_instant` stays the oracle so the order
# and the reported defect are decided by the same parse.
git_loopy_timestamp_defect() {
  local created_at="$1"
  if [[ -z "$created_at" ]]; then
    printf 'absent\n'
    return 0
  fi
  if _git_loopy_issue_instant "$created_at" >/dev/null; then
    return 0
  fi
  printf 'malformed\n'
}

# Orders a JSON array of `{number, created_at, labels}` candidates and prints
# `{"order": [...], "undated": [{"issue": N, "defect": "..."}]}`.
#
# The sort key is a fixed-width string so `LC_ALL=C sort` compares it as the
# tuple it stands for: rank, then whether the issue is dated at all, then the
# instant, then the issue number. The dated bit sits *between* the rank and the
# instant, which is exactly what "sorts last within its own priority rank"
# means. The number makes the order total, so no two distinct issues tie.
#
# The optional second argument is the invocation's **Pin** (`--issue N`, #396).
# It is applied to the *finished* order rather than folded into the sort key,
# because §3.2 requires the key to be a pure function of the fetched issue
# fields and a pin is one operator's instruction, not a property of any issue.
# The promotion is stable — everything else keeps its §3.2 sequence behind the
# pin — and a pin naming an issue that is not here is a no-op; refusing that
# invocation is `_git_loopy_preflight_pin`'s job, and it has already run.
git_loopy_order_issues() {
  local issues_json="$1"
  local pin="${2:-}"
  local -a keyed=()
  local candidate

  # One jq pass for the whole array rather than three per candidate: a backlog
  # that paginates to 1600 would otherwise pay ~4800 process spawns to be
  # sorted. `@tsv` escapes any tab or newline *inside* a value, so splitting on
  # real tabs is unambiguous — and it is split by hand because `read`'s IFS
  # treats a tab as whitespace and would collapse the empty `created_at` that
  # the `absent` defect is made of.
  local line rest
  while IFS= read -r line; do
    local number created_at labels_json
    local rank dated seconds nanoseconds defect instant
    number="${line%%$'\t'*}"
    rest="${line#*$'\t'}"
    created_at="${rest%%$'\t'*}"
    labels_json="${rest#*$'\t'}"
    rank="$(git_loopy_priority_rank "$labels_json")"
    dated=1
    seconds=0
    nanoseconds=0
    if [[ -n "$created_at" ]] && instant="$(
      _git_loopy_issue_instant "$created_at"
    )"; then
      dated=0
      seconds="${instant%% *}"
      nanoseconds="${instant##* }"
    fi
    defect="$(git_loopy_timestamp_defect "$created_at")"
    printf -v candidate '%d%d%014d%09d%010d\t%s\t%s' \
      "$rank" "$dated" "$seconds" "$nanoseconds" "$number" \
      "$number" "$defect"
    keyed+=("$candidate")
  done < <(jq -r '
    .[]?
    | [(.number | tostring), (.created_at // ""), ((.labels // []) | tojson)]
    | @tsv
  ' <<<"$issues_json")

  local -a ordered=() undated=()
  if ((${#keyed[@]} > 0)); then
    local _key number defect
    while IFS=$'\t' read -r _key number defect; do
      ordered+=("$number")
      if [[ -n "$defect" ]]; then
        undated+=("$number"$'\t'"$defect")
      fi
    done < <(printf '%s\n' "${keyed[@]}" | LC_ALL=C sort)
  fi

  local order_json='[]' undated_json='[]'
  if ((${#ordered[@]} > 0)); then
    order_json="$(printf '%s\n' "${ordered[@]}" | jq -cs 'map(tonumber)')" ||
      return 1
    if [[ -n "$pin" ]]; then
      order_json="$(jq -c --argjson pin "$pin" \
        'map(select(. == $pin)) + map(select(. != $pin))' \
        <<<"$order_json")" || return 1
    fi
  fi
  if ((${#undated[@]} > 0)); then
    undated_json="$(printf '%s\n' "${undated[@]}" |
      jq -cRs 'split("\n")
        | map(select(length > 0))
        | map(split("\t"))
        | map({issue: (.[0] | tonumber), defect: .[1]})')" || return 1
  fi

  jq -cn --argjson order "$order_json" --argjson undated "$undated_json" \
    '{order: $order, undated: $undated}'
}

git_loopy_exit_code_for() {
  case "$1" in
    empty_pool | iteration_cap)
      printf '0\n'
      ;;
    stuck | preflight_failed)
      printf '1\n'
      ;;
    usage_error)
      printf '2\n'
      ;;
    *)
      printf 'unknown Run exit reason: %s\n' "$1" >&2
      return 1
      ;;
  esac
}

# GitHub closing-keyword regex — kept byte-identical to the Conformance suite's
# reference_regex and the Python reference CLOSE_KEYWORD_RE so the whole Runner
# family shares one close-keyword oracle. jq (Oniguruma) honours the embedded
# `(?i)` and `\s`/`\d` the same way Python's `re` does.
GIT_LOOPY_CLOSE_KEYWORD_RE='(?i)(close[sd]?|fix(?:es|ed)?|resolve[sd]?)\s+#(\d+)'

# Runner Checkpoint message contract (ADR-0004), kept in lockstep with the
# Python reference `checkpoint_message` / `CHECKPOINT_TRAILER_KEY`. The trailer
# key tags a runner-authored Checkpoint so it is distinguishable from an agent
# commit and excluded from Strike progress; its value is the active issue ref
# (or `unattributed`) — deliberately NOT `#N`, so a Checkpoint never opens a
# GitHub cross-reference. The body is byte-identical to the reference so the
# whole family authors the same close-keyword-free message.
GIT_LOOPY_CHECKPOINT_TRAILER_KEY="GitLoopy-Checkpoint"
_GIT_LOOPY_CHECKPOINT_BODY="Runner-authored Checkpoint (ADR-0004): staged the worktree the agent left
uncommitted so the next iteration starts on a clean tree and the work can
reach the remote. Not an agent commit; excluded from Strike progress."

git_loopy_extract_close_refs() {
  # Extract deduplicated issue numbers referenced via GitHub closing keywords,
  # in first-encounter order. Matching is line-by-line — split on `\n` only, so
  # a newline is a hard boundary while `\r` and Unicode line separators stay
  # inline whitespace, mirroring the Python reference `extract_close_refs`.
  # Prints a compact JSON array.
  local messages="$1"
  jq -cn \
    --arg messages "$messages" \
    --arg re "$GIT_LOOPY_CLOSE_KEYWORD_RE" '
    [ ($messages | split("\n"))[]
      | [ match($re; "g") | .captures[1].string | tonumber ]
    ]
    | add // []
    | reduce .[] as $n ([]; if any(.[]; . == $n) then . else . + [$n] end)
  '
}

git_loopy_actionable_close_refs() {
  # First-seen close refs restricted to *issues* in the current Pool. Pull
  # requests and non-integer refs are excluded, preserving the Wrapper
  # contract's issues-only closure boundary. `$1` is the concatenated commit
  # messages; `$2` is a JSON array of `{ref, kind}` Pool descriptors. Prints a
  # compact JSON array in first-encounter order.
  local messages="$1"
  local pool_json="$2"
  local refs
  refs="$(git_loopy_extract_close_refs "$messages")" || return 1
  jq -cn \
    --argjson refs "$refs" \
    --argjson pool "$pool_json" '
    ($pool
      | map(select(.kind == "issue" and (.ref | type) == "number") | .ref)
    ) as $issues
    | [ $refs[] | select(. as $n | $issues | any(. == $n)) ]
  '
}

git_loopy_did_iteration_make_progress() {
  # Return success (progress) iff an agent commit landed, an issue was
  # auto-closed, or a PR head advanced. Runner Checkpoints and the legacy
  # no-more-tasks sentinel are informational and never progress. Positional
  # signals mirror the Conformance fixture order.
  local commits="$1"
  local auto_closures="$2"
  local checkpoints="$3"
  local pr_advances="$4"
  local saw_nmt="$5"
  : "$checkpoints" "$saw_nmt"
  ((commits > 0 || auto_closures > 0 || pr_advances > 0))
}

git_loopy_strike_tick() {
  # Advance the NMT Strike state machine by one Iteration and print
  # "<strikes> <outcome>". Progress resets strikes to zero; a no-progress
  # Iteration adds one and, on reaching the threshold, flips the outcome to
  # `aborted` and freezes there. `$1` max strikes, `$2` current strikes, `$3`
  # current outcome, then the five progress signals.
  local max="$1"
  local strikes="$2"
  local outcome="$3"
  shift 3
  if [[ "$outcome" == "aborted" ]]; then
    printf '%s %s\n' "$strikes" "$outcome"
    return
  fi
  if git_loopy_did_iteration_make_progress "$@"; then
    printf '0 %s\n' "$outcome"
    return
  fi
  strikes=$((strikes + 1))
  if ((strikes >= max)); then
    outcome="aborted"
  fi
  printf '%s %s\n' "$strikes" "$outcome"
}

git_loopy_is_checkpoint_message() {
  # Return success if `$1` carries the runner Checkpoint trailer
  # (`GitLoopy-Checkpoint:`), tolerant of surrounding whitespace and case so a
  # Checkpoint is excluded from Strike progress even before this port authors
  # one. Mirrors the Python reference `is_checkpoint_message`.
  local message="$1"
  local prefix="${GIT_LOOPY_CHECKPOINT_TRAILER_KEY,,}:"
  local line trimmed
  while IFS= read -r line || [[ -n "$line" ]]; do
    trimmed="$(_git_loopy_trim "$line")"
    [[ "${trimmed,,}" == "$prefix"* ]] && return 0
  done <<<"$message"
  return 1
}

git_loopy_checkpoint_message() {
  # Build a runner Checkpoint commit message (ADR-0004) attributed to the active
  # ref `$1` — an issue number, a PRDs/PR string ref, or empty for an
  # unattributed Checkpoint. The message is guaranteed close-keyword-free (its
  # subject/body never match `GIT_LOOPY_CLOSE_KEYWORD_RE`) and carries the
  # `GitLoopy-Checkpoint:` trailer, mirroring the Python reference
  # `checkpoint_message` byte-for-byte.
  local active_ref="${1-}"
  local subject attribution
  if [[ -z "$active_ref" ]]; then
    subject="Checkpoint: capture uncommitted work-in-progress"
    attribution="unattributed"
  elif [[ "$active_ref" =~ ^[0-9]+$ ]]; then
    subject="Checkpoint: capture work-in-progress for issue $active_ref"
    attribution="$active_ref"
  else
    subject="Checkpoint: capture work-in-progress for $active_ref"
    attribution="$active_ref"
  fi
  printf '%s\n\n%s\n\n%s: %s' \
    "$subject" "$_GIT_LOOPY_CHECKPOINT_BODY" \
    "$GIT_LOOPY_CHECKPOINT_TRAILER_KEY" "$attribution"
}

git_loopy_resolve_prompt() {
  local repo_root="$1"
  local packaged_prompt="$2"
  local project_lower="$repo_root/git-loopy/prompt.md"
  local project_upper="$repo_root/git-loopy/PROMPT.md"
  local config_home
  config_home="$(_git_loopy_config_home)"

  local -a candidates=("$project_lower" "$project_upper")
  [[ -n "$config_home" ]] &&
    candidates+=("$config_home/git-loopy/PROMPT.md")
  candidates+=("$packaged_prompt")

  local candidate
  for candidate in "${candidates[@]}"; do
    if [[ -f "$candidate" && -r "$candidate" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

# Fail closed on any closed-world Skill-policy surface this port cannot honour
# (contract §17.6). This runs before the issue tracker, dependency, and GitHub
# checks — and therefore before source collection and before Copilot exists — so
# a configured policy can never be silently widened into an Iteration.
git_loopy_assert_skill_policy_supported() {
  local repo_root="$1"
  local -a surfaces=()
  local surface
  while IFS= read -r surface; do
    [[ -n "$surface" ]] && surfaces+=("$surface")
  done < <(git_loopy_detect_skill_policy_surfaces "$repo_root")

  ((${#surfaces[@]} > 0)) || return 0

  printf '%s\n' \
    'git-loopy: the shell Orchestrator does not yet support the closed-world Skill policy.' \
    >&2
  for surface in "${surfaces[@]}"; do
    printf 'git-loopy: unsupported Skill-policy surface: %s\n' "$surface" >&2
  done
  printf '%s\n' \
    'git-loopy: run the Python Orchestrator, or wait for a shell release with native config parity.' \
    'git-loopy: legacy --deny-skill / GIT_LOOPY_DENY_SKILLS invocations continue to run unchanged.' \
    >&2
  return 1
}

# Refuse a **Lane cap** this distribution cannot honour, instead of accepting it
# and running serially. A silently ignored cap is indistinguishable from a Run
# whose tracker carries no `parallel-safe` issue, so the operator learns nothing
# from either. The manifest in `lib/events.sh` is the single source of the
# answer, so flipping a capability there flips this refusal with it.
git_loopy_assert_parallel_supported() {
  local requested="${GIT_LOOPY_MAX_PARALLEL:-}"
  [[ -n "$requested" ]] || return 0
  if [[ ! "$requested" =~ ^[0-9]+$ ]]; then
    printf '%s\n' \
      "git-loopy: GIT_LOOPY_MAX_PARALLEL must be a non-negative integer (got '$requested')." \
      >&2
    return 1
  fi
  # Compare as a normalized decimal string rather than with `(( ))`: bash reads
  # a leading-zero literal as octal and silently wraps a value wider than 64
  # bits, and either failure would leave an arithmetic error accepting a cap
  # this port cannot honour.
  local normalized="${requested#"${requested%%[!0]*}"}"
  [[ -n "$normalized" ]] || normalized="0"
  if ((${#normalized} == 1)) && [[ "$normalized" == [01] ]]; then
    return 0
  fi
  local supported
  supported="$(
    jq -r '.parallel_mode' <<<"$GIT_LOOPY_PARALLEL_CAPABILITIES_JSON"
  )"
  [[ "$supported" != "true" ]] || return 0
  printf '%s\n' \
    "git-loopy: a Lane cap of $normalized was requested, but the shell Orchestrator declares parallel_mode unsupported." \
    'git-loopy: this distribution has no Rolling dispatch scheduler, so it cannot fill a second Lane.' \
    'git-loopy: unset GIT_LOOPY_MAX_PARALLEL or set it to 1 to run serially, or use a distribution whose parallel_capabilities.parallel_mode is true.' \
    >&2
  return 1
}

git_loopy_preflight() {
  local packaged_prompt="$1"

  command -v git >/dev/null 2>&1 || {
    printf 'git-loopy: git is required on PATH.\n' >&2
    return 1
  }

  local repo_root
  repo_root="$(git rev-parse --show-toplevel 2>/dev/null)" || {
    printf 'git-loopy: run from inside a git repository.\n' >&2
    return 1
  }
  [[ -n "$repo_root" ]] || {
    printf 'git-loopy: git returned an empty repository root.\n' >&2
    return 1
  }

  git_loopy_assert_skill_policy_supported "$repo_root" || return 1

  if [[ ! -f "$repo_root/docs/agents/issue-tracker.md" ]]; then
    printf '%s\n' \
      'git-loopy: issue tracking is not configured. Run /setup-agent-skills interactively, then retry.' \
      >&2
    return 1
  fi

  command -v jq >/dev/null 2>&1 || {
    printf 'git-loopy: jq is required by the shell Orchestrator.\n' >&2
    return 1
  }
  git_loopy_resolve_continuation_authority "$repo_root" || return 1
  git_loopy_prepare_continuation_frontier || return 1
  git_loopy_assert_parallel_supported || return 1
  command -v copilot >/dev/null 2>&1 || {
    printf 'git-loopy: copilot is required on PATH.\n' >&2
    return 1
  }

  if [[ "$GIT_LOOPY_ISSUE_SOURCE" == "github" ]]; then
    command -v gh >/dev/null 2>&1 || {
      printf 'git-loopy: gh is required for the GitHub issue source.\n' >&2
      return 1
    }
    gh auth status >/dev/null 2>&1 || {
      printf '%s\n' \
        "git-loopy: gh is not authenticated. Run 'gh auth login', then retry." \
        >&2
      return 1
    }
    gh repo view --json owner,name,defaultBranchRef >/dev/null 2>&1 || {
      printf '%s\n' \
        'git-loopy: gh could not resolve this GitHub repository.' >&2
      return 1
    }
    _git_loopy_preflight_pin || return 1
  fi

  local prompt_path
  prompt_path="$(git_loopy_resolve_prompt "$repo_root" "$packaged_prompt")" || {
    printf '%s\n' \
      'git-loopy: PROMPT.md was not found in project, global, or packaged scope.' \
      >&2
    return 1
  }

  GIT_LOOPY_REPO_ROOT="$repo_root"
  GIT_LOOPY_PROMPT_PATH="$prompt_path"
}

# Wrapper contract §3.2 — refuse the invocation when `--issue N` names an issue
# this Run cannot work (#396, ADR-0032).
#
# The pin bypasses order and *nothing else*, so every rule §3.1 already applies
# to a candidate applies to a pin — the difference is what a failure means. §3.3
# makes a candidate the runner cannot take a **skip**, because a serial Run
# merely walked past it and ending the Run over one mislabelled issue would be
# worse than moving on. A pin is an operator naming an issue, and there is no
# next candidate that honours what they asked for: silently working a different
# issue than the one named is worse than stopping.
#
# It runs *here*, before the Pool, for two reasons. An ineligible pin costs one
# `issue view` rather than a whole collection that would then be
# indistinguishable from a backlog which simply did not contain the issue. And
# it runs once per invocation rather than once per Iteration, because the pin
# names an invocation's intent — re-asking each Iteration would kill a healthy
# Run the moment it legitimately closed the issue it was pinned to.
#
# The shell Orchestrator is serial, so `parallel-safe` is deliberately not part
# of this: §14's Parallel mode is future work for this port, and a rule with
# nothing to enforce it against would be a rule that drifts unnoticed.
_git_loopy_preflight_pin() {
  [[ -n "$GIT_LOOPY_ISSUE_PIN" ]] || return 0

  local raw normalized state body exclusion
  raw="$(gh issue view "$GIT_LOOPY_ISSUE_PIN" \
    --json "$GIT_LOOPY_SHALLOW_ISSUE_FIELDS,comments" 2>/dev/null)" || {
    printf '%s\n' \
      "git-loopy: --issue $GIT_LOOPY_ISSUE_PIN: #$GIT_LOOPY_ISSUE_PIN could not be read from the tracker; it may not exist or may not be visible to this gh login." \
      >&2
    return 1
  }
  normalized="$(_git_loopy_normalize_issue <<<"$raw" 2>/dev/null)" || {
    printf '%s\n' \
      "git-loopy: --issue $GIT_LOOPY_ISSUE_PIN: #$GIT_LOOPY_ISSUE_PIN could not be read from the tracker; it may not exist or may not be visible to this gh login." \
      >&2
    return 1
  }

  state="$(jq -r '.state' <<<"$normalized")" || return 1
  if [[ "${state^^}" != "OPEN" ]]; then
    printf '%s\n' \
      "git-loopy: --issue $GIT_LOOPY_ISSUE_PIN: #$GIT_LOOPY_ISSUE_PIN is closed." >&2
    return 1
  fi

  if ! jq -e --arg label "$GIT_LOOPY_READY_LABEL" \
    'any(.labels[]?; . == $label)' <<<"$normalized" >/dev/null 2>&1; then
    printf '%s\n' \
      "git-loopy: --issue $GIT_LOOPY_ISSUE_PIN: #$GIT_LOOPY_ISSUE_PIN does not carry the $GIT_LOOPY_READY_LABEL label." \
      >&2
    return 1
  fi

  body="$(jq -r '.body' <<<"$normalized")" || return 1
  exclusion="$(git_loopy_afk_ready_exclusion "$body")"
  if [[ -n "$exclusion" ]]; then
    printf '%s\n' \
      "git-loopy: --issue $GIT_LOOPY_ISSUE_PIN: #$GIT_LOOPY_ISSUE_PIN is not AFK-ready; its body is missing $(_git_loopy_missing_sections "$exclusion")." \
      >&2
    return 1
  fi
}

# Names the section(s) an AFK-ready exclusion reason says are absent. #396 asks
# for the *specific* missing section, because the operator's next action is to
# edit the issue and a message that sends them back to the discriminator to work
# out which heading is missing has failed at the only job it had.
_git_loopy_missing_sections() {
  case "$1" in
    missing_what_to_build) printf '`## What to build`\n' ;;
    missing_acceptance_criteria) printf '`## Acceptance criteria`\n' ;;
    *) printf '`## What to build` and `## Acceptance criteria`\n' ;;
  esac
}

_git_loopy_normalize_issue() {
  jq -ce '{
    number: .number,
    title: (.title // ""),
    body: (.body // ""),
    labels: [
      (.labels // [])[]
      | if type == "object" then (.name // "") else tostring end
    ],
    state: (.state // "OPEN"),
    url: (.url // ""),
    created_at: (.createdAt // .created_at // ""),
    comments: [
      (.comments // [])[]
      | {
          author: (
            if (.author | type) == "object"
            then (.author.login // "")
            else (.author // "")
            end
          ),
          body: (.body // ""),
          created_at: (.createdAt // .created_at // "")
        }
    ]
  }'
}

# Wrapper contract §2.1 — the read schedule the whole family walks. The first
# ask is `GIT_LOOPY_LIST_PAGE_LIMIT`; each ambiguous full page doubles it until a
# short page proves completeness or `GIT_LOOPY_LIST_MAX_LIMIT` is reached, at
# which point the read is reported incomplete rather than silently truncated.
#
# Shared rather than this port's own, and pinned by `issue-ordering.json`'s
# `read_schedule`: under §3.2's oldest-first order a page limit stops hiding the
# *oldest* candidates and starts hiding the *newest*, so a **Priority** issue
# filed today would fall outside the first hundred exactly when it matters most.
# Two members walking different limits read different backlogs, and a backlog is
# what §3.2 orders — so a divergent ceiling is a divergent head of the order,
# reached without either member sorting anything differently.
GIT_LOOPY_LIST_PAGE_LIMIT=100
GIT_LOOPY_LIST_MAX_LIMIT=1600

# What one shallow page, asked for at `$1` and answering with `$2` rows, forces
# on the reader walking a backlog. The pure half of
# `_git_loopy_gh_issue_list_to_completion`'s loop, split out so the Conformance
# adapter drives the *decision* without a fake `gh` — the same seam split §3.2's
# comparison already has, and for the same reason: an adapter that reproduced
# the walk would agree with itself while the Orchestrator read a different
# backlog.
#
# Emits JSON, like `git_loopy_order_issues`, so the adapter reads named fields
# rather than positional ones. `authoritative` is reported rather than derived
# because §2.1's "establishes neither that the Pool is empty nor which issue is
# the head of the order" is the seam's claim to make, not its caller's.
git_loopy_next_read_step() {
  local limit="$1" rows="$2"
  local outcome next_limit authoritative

  if ((rows < limit)); then
    outcome="complete"
    next_limit="null"
  elif ((limit >= GIT_LOOPY_LIST_MAX_LIMIT)); then
    outcome="incomplete"
    next_limit="null"
  else
    outcome="continue"
    next_limit="$((limit * 2))"
  fi

  authoritative="false"
  [[ "$outcome" == "complete" ]] && authoritative="true"

  printf '{"outcome":"%s","authoritative":%s,"next_limit":%s}\n' \
    "$outcome" "$authoritative" "$next_limit"
}

# The `--json` field set every shallow issue read asks for, named once so this
# port cannot drift from the Python reference's `_SHALLOW_ISSUE_FIELDS`.
GIT_LOOPY_SHALLOW_ISSUE_FIELDS="number,title,body,labels,state,url,createdAt"
# The label the Pool query filters on, named once so the pin's eligibility check
# (`_git_loopy_preflight_pin`) cannot drift from the query it must agree with.
GIT_LOOPY_READY_LABEL="ready-for-agent"

# Fetches every `ready-for-agent` candidate into `GIT_LOOPY_ISSUE_LIST_JSON` and
# reports completeness in `GIT_LOOPY_POOL_COMPLETE` (1 = provably exhaustive,
# 0 = hit the ceiling). Both are globals rather than stdout because a command
# substitution runs in a subshell, and a completeness flag set there would be
# discarded at exactly the moment the caller needs it. Returns 0 on a usable
# read, 1 when `gh` failed, 2 when it returned something that is not an array.
_git_loopy_gh_issue_list_to_completion() {
  local limit="$GIT_LOOPY_LIST_PAGE_LIMIT"
  local page count step next_limit
  GIT_LOOPY_ISSUE_LIST_JSON='[]'
  GIT_LOOPY_POOL_COMPLETE=1
  while true; do
    page="$(
      gh issue list \
        --state open \
        --label "$GIT_LOOPY_READY_LABEL" \
        --limit "$limit" \
        --json "$GIT_LOOPY_SHALLOW_ISSUE_FIELDS"
    )" || return 1
    jq -e 'type == "array"' <<<"$page" >/dev/null 2>&1 || return 2
    count="$(jq -r 'length' <<<"$page")" || return 2
    GIT_LOOPY_ISSUE_LIST_JSON="$page"
    # `git_loopy_next_read_step` is the Conformance seam; this loop is only the
    # I/O around it, so the schedule cannot drift from the fixture that pins it.
    step="$(git_loopy_next_read_step "$limit" "$count")"
    next_limit="$(jq -r '.next_limit // empty' <<<"$step")"
    if [[ -z "$next_limit" ]]; then
      jq -e '.authoritative' <<<"$step" >/dev/null || GIT_LOOPY_POOL_COMPLETE=0
      return 0
    fi
    limit="$next_limit"
  done
}

# Reorders a shallow candidate array into Wrapper contract §3.2 order and
# reports every timestamp the order could not use, once per `(issue, defect)`
# per Run. The ordering itself is `git_loopy_order_issues` — the Conformance
# seam — so this only maps its answer back onto the records, and a candidate
# the order somehow did not name is appended rather than dropped.
_GIT_LOOPY_ORDERED_CANDIDATES_JSON='[]'
declare -A _GIT_LOOPY_REPORTED_UNDATED=()

_git_loopy_order_candidates() {
  local candidates="$1"
  local normalized ordering order_json undated_json entry issue defect
  # `git_loopy_order_issues` is the Conformance seam and reads the normalized
  # `{number, created_at, labels}` the fixture feeds it. A raw `gh issue list`
  # row is not that shape — it carries `createdAt` and label *objects* — so the
  # projection happens here, once, rather than by widening the seam to accept
  # both and letting the two ports disagree about which spellings count.
  normalized="$(
    jq -c '[
      .[]?
      | {
          number: .number,
          created_at: ((.createdAt // .created_at // "") | tostring),
          labels: [
            (.labels // [])[]
            | if type == "object" then (.name // "") else tostring end
          ]
        }
    ]' <<<"$candidates"
  )" || return 1
  ordering="$(git_loopy_order_issues "$normalized" "$GIT_LOOPY_ISSUE_PIN")" ||
    return 1
  order_json="$(jq -c '.order' <<<"$ordering")" || return 1
  undated_json="$(jq -c '.undated' <<<"$ordering")" || return 1

  _GIT_LOOPY_ORDERED_CANDIDATES_JSON="$(
    jq -c --argjson order "$order_json" '
      . as $rows
      | ($rows | map({key: (.number | tostring), value: .}) | from_entries)
        as $by
      | ([$order[] | $by[(. | tostring)] // empty])
        as $ordered
      | ($ordered | map(.number)) as $seen
      | $ordered
        + ($rows | map(select(.number as $n | ($seen | index($n)) == null)))
    ' <<<"$candidates"
  )" || return 1

  while IFS= read -r entry; do
    [[ -n "$entry" ]] || continue
    issue="$(jq -r '.issue' <<<"$entry")" || return 1
    defect="$(jq -r '.defect' <<<"$entry")" || return 1
    [[ -z "${_GIT_LOOPY_REPORTED_UNDATED["$issue/$defect"]+present}" ]] ||
      continue
    _GIT_LOOPY_REPORTED_UNDATED["$issue/$defect"]=1
    printf 'git-loopy: issue #%s has an unusable created_at (%s); it sorts last within its priority rank.\n' \
      "$issue" "$defect" >&2
  done < <(jq -c '.[]?' <<<"$undated_json")
}

git_loopy_collect_github_pool() {
  local candidates status=0
  GIT_LOOPY_POOL_COMPLETE=1
  _git_loopy_gh_issue_list_to_completion || status=$?
  if ((status != 0)); then
    if ((status == 2)); then
      printf 'git-loopy: gh issue list returned malformed JSON.\n' >&2
    else
      printf 'git-loopy: gh issue list failed; treating this Pool as empty.\n' >&2
    fi
    GIT_LOOPY_POOL_JSON='[]'
    GIT_LOOPY_POOL_EXCLUSIONS_JSON='[]'
    GIT_LOOPY_POOL_COMPLETE=0
    return 0
  fi
  candidates="$GIT_LOOPY_ISSUE_LIST_JSON"
  if ((GIT_LOOPY_POOL_COMPLETE == 0)); then
    printf 'git-loopy: gh issue list did not paginate to completion; this Pool is partial and may not be treated as the whole backlog.\n' >&2
  fi
  # Wrapper contract §3.2 — the order is decided at the read, before the
  # per-issue view loop, so a Pool truncated by a failing view is a *prefix* of
  # the order rather than an arbitrary subset of it. No consumer sorts: the
  # serial Pickup below takes element 0 and trusts it.
  _git_loopy_order_candidates "$candidates" || return 1
  candidates="$_GIT_LOOPY_ORDERED_CANDIDATES_JSON"

  local -a pool_items=()
  local -a exclusion_items=()
  local candidate
  while IFS= read -r candidate; do
    local body number title reason
    body="$(jq -r '.body // ""' <<<"$candidate")"
    number="$(jq -r '.number' <<<"$candidate")"
    title="$(jq -r '.title // ""' <<<"$candidate")"
    [[ "$number" =~ ^[1-9][0-9]*$ ]] || {
      printf 'git-loopy: skipping issue with malformed number %s.\n' \
        "$number" >&2
      continue
    }
    # Wrapper contract §3.1: a rejected candidate is reported, not dropped
    # silently. The reason comes from the same body the membership decision
    # was made on, and no extra round-trip is paid for it.
    reason="$(git_loopy_afk_ready_exclusion "$body")"
    if [[ -n "$reason" ]]; then
      exclusion_items+=("$(
        jq -cn \
          --argjson issue "$number" \
          --arg title "$title" \
          --arg reason "$reason" \
          '{issue: $issue, title: $title, reason: $reason}'
      )") || return 1
      continue
    fi

    local full
    if ! full="$(
      gh issue view "$number" \
        --json "$GIT_LOOPY_SHALLOW_ISSUE_FIELDS,comments"
    )"; then
      printf 'git-loopy: gh issue view #%s failed; skipping this Iteration.\n' \
        "$number" >&2
      continue
    fi
    body="$(jq -r '.body // ""' <<<"$full" 2>/dev/null)" || {
      printf 'git-loopy: gh issue view #%s returned malformed JSON; skipping.\n' \
        "$number" >&2
      continue
    }
    reason="$(git_loopy_afk_ready_exclusion "$body")"
    if [[ -n "$reason" ]]; then
      title="$(jq -r '.title // ""' <<<"$full" 2>/dev/null)" || title=""
      exclusion_items+=("$(
        jq -cn \
          --argjson issue "$number" \
          --arg title "$title" \
          --arg reason "$reason" \
          '{issue: $issue, title: $title, reason: $reason}'
      )") || return 1
      continue
    fi

    local normalized
    normalized="$(_git_loopy_normalize_issue <<<"$full")" || {
      printf 'git-loopy: gh issue view #%s returned malformed fields; skipping.\n' \
        "$number" >&2
      continue
    }
    pool_items+=("$normalized")
  done < <(jq -c '.[]' <<<"$candidates")

  GIT_LOOPY_POOL_JSON="$(
    _git_loopy_json_object_array \
      ${pool_items[@]+"${pool_items[@]}"}
  )" || return 1
  GIT_LOOPY_POOL_EXCLUSIONS_JSON="$(
    _git_loopy_json_object_array \
      ${exclusion_items[@]+"${exclusion_items[@]}"}
  )" || return 1
}

git_loopy_collect_prds_pool() {
  local repo_root="$1"
  local -a pool_items=()
  local -a exclusion_items=()
  local prds_dir="$repo_root/prds"
  # A directory walk has no page limit and no ceiling: the local-markdown Pool
  # is exhaustive by construction, including when the tree holds nothing.
  GIT_LOOPY_POOL_COMPLETE=1
  if [[ ! -d "$prds_dir" ]]; then
    GIT_LOOPY_POOL_JSON='[]'
    GIT_LOOPY_POOL_EXCLUSIONS_JSON='[]'
    return 0
  fi
  if [[ -L "$prds_dir" ]]; then
    printf 'git-loopy: linked prds root is not allowed: %s\n' \
      "$prds_dir" >&2
    GIT_LOOPY_POOL_JSON='[]'
    GIT_LOOPY_POOL_EXCLUSIONS_JSON='[]'
    return 0
  fi

  local LC_ALL=C
  local feature_dir
  shopt -s nullglob
  for feature_dir in "$prds_dir"/*; do
    [[ -d "$feature_dir" && ! -L "$feature_dir" ]] || continue
    [[ "$(basename "$feature_dir")" != "done" ]] || continue

    local path
    for path in "$feature_dir"/[0-9]*-*.md; do
      [[ -f "$path" && ! -L "$path" ]] || continue
      local name
      name="$(basename "$path")"
      [[ "$name" =~ ^[0-9]+-.*\.md$ ]] || continue

      local body
      if ! body="$(<"$path")"; then
        printf 'git-loopy: could not read %s; skipping.\n' "$path" >&2
        continue
      fi
      local ref="${path#"$repo_root"/}"
      # Wrapper contract §3.1 — the local-markdown backend shares the
      # discriminator, so it shares the exclusion vocabulary too.
      local reason
      reason="$(git_loopy_afk_ready_exclusion "$body")"
      if [[ -n "$reason" ]]; then
        exclusion_items+=("$(
          jq -cn \
            --arg issue "$ref" \
            --arg title "$ref" \
            --arg reason "$reason" \
            '{issue: $issue, title: $title, reason: $reason}'
        )") || return 1
        continue
      fi

      local item
      item="$(
        jq -cn \
          --arg ref "$ref" \
          --rawfile body "$path" \
          '{ref: $ref, title: $ref, body: $body}'
      )" || return 1
      pool_items+=("$item")
    done
  done
  shopt -u nullglob

  GIT_LOOPY_POOL_JSON="$(
    _git_loopy_json_object_array \
      ${pool_items[@]+"${pool_items[@]}"} |
      jq -c 'sort_by(.ref)'
  )" || return 1
  GIT_LOOPY_POOL_EXCLUSIONS_JSON="$(
    _git_loopy_json_object_array \
      ${exclusion_items[@]+"${exclusion_items[@]}"} |
      jq -c 'sort_by(.issue)'
  )" || return 1
}

git_loopy_collect_pool() {
  case "$GIT_LOOPY_ISSUE_SOURCE" in
    github)
      git_loopy_collect_github_pool
      ;;
    prds)
      git_loopy_collect_prds_pool "$GIT_LOOPY_REPO_ROOT"
      ;;
  esac
}

git_loopy_head_sha() {
  local repo_root="$1"
  git -C "$repo_root" rev-parse HEAD 2>/dev/null
}

_git_loopy_log_z_to_json() {
  # Reads NUL-delimited `git log -z --format=%H%n%s%n%ad%n%b` records on stdin
  # and prints a compact JSON array of {sha, subject, date, body}, newest first
  # (git's default log order). Mirrors the Python reference `_parse_log_z`.
  local -a objs=()
  local record
  while IFS= read -r -d '' record || [[ -n "$record" ]]; do
    record="${record#$'\n'}"
    [[ -n "$record" ]] || continue

    local sha subject date body rest
    sha="${record%%$'\n'*}"
    rest="${record#*$'\n'}"
    subject="${rest%%$'\n'*}"
    rest="${rest#*$'\n'}"
    date="${rest%%$'\n'*}"
    if [[ "$rest" == *$'\n'* ]]; then
      body="${rest#*$'\n'}"
    else
      body=""
    fi
    while [[ "$body" == *$'\n' ]]; do
      body="${body%$'\n'}"
    done

    objs+=("$(
      jq -cn \
        --arg sha "$sha" \
        --arg subject "$subject" \
        --arg date "$date" \
        --arg body "$body" \
        '{sha: $sha, subject: $subject, date: $date, body: $body}'
    )") || return 1
  done

  _git_loopy_json_object_array ${objs[@]+"${objs[@]}"}
}

git_loopy_commits_between() {
  local repo_root="$1"
  local pre="$2"
  local head="$3"
  if [[ "$pre" == "$head" ]]; then
    printf '[]\n'
    return 0
  fi
  git -C "$repo_root" log \
    --format=%H%n%s%n%ad%n%b --date=short -z "${pre}..${head}" 2>/dev/null |
    _git_loopy_log_z_to_json
}

git_loopy_worktree_dirty() {
  # Return success if the worktree carries any uncommitted tracked change OR any
  # untracked, non-ignored file — the ADR-0004 Checkpoint trigger. A single
  # `git status --porcelain` reports both (modified/staged tracked entries plus
  # `??` untracked ones) while honouring `.gitignore`, so it is the shell
  # equivalent of the Python reference's `is_dirty` OR `has_untracked`. A git
  # failure (e.g. not a repository) reports "not dirty" so the caller skips the
  # Checkpoint rather than aborting.
  local repo_root="$1"
  local status_output
  status_output="$(git -C "$repo_root" status --porcelain 2>/dev/null)" ||
    return 1
  [[ -n "$status_output" ]]
}

git_loopy_stage_all() {
  # Stage every change (`git add -A`, honouring `.gitignore`); the user's git
  # config stays the single source of truth (no `--force`, no excludes override).
  local repo_root="$1"
  git -C "$repo_root" add -A >/dev/null 2>&1
}

git_loopy_commit() {
  # Commit the staged index with `$2` and print the new HEAD SHA. A plain
  # `git commit -m` keeps the user's identity/hooks/signing config authoritative.
  # An empty index (nothing staged) exits non-zero, which the caller treats as a
  # skipped Checkpoint rather than an abort.
  local repo_root="$1"
  local message="$2"
  git -C "$repo_root" commit -m "$message" >/dev/null 2>&1 || return 1
  git_loopy_head_sha "$repo_root"
}

git_loopy_push() {
  # Push the current branch to its configured upstream. A bare `git push` (no
  # ref args, no `--force`) keeps `push.default`, the branch's upstream tracking
  # ref, and credential helpers authoritative. The exit status is the contract:
  # 0 pushed; non-zero for no upstream, an unreachable/missing remote, an auth
  # failure, or a non-fast-forward rejection — all non-fatal to the caller.
  local repo_root="$1"
  git -C "$repo_root" push >/dev/null 2>&1
}

git_loopy_recent_commits_block() {
  local repo_root="$1"
  local commits_json
  commits_json="$(
    git -C "$repo_root" log \
      -n5 --format=%H%n%s%n%ad%n%b --date=short -z 2>/dev/null |
      _git_loopy_log_z_to_json
  )" || commits_json='[]'

  jq -r '
    if length == 0
    then "No commits found"
    else
      [ .[]
        | .sha + "\n" + .date + "\n"
          + (if .body == "" then .subject else .subject + "\n" + .body end)
          + "---"
      ]
      | join("\n")
    end
  ' <<<"$commits_json"
}

git_loopy_render_pool_blocks() {
  # Renders the blocks the prompt shows. The argument is the *rendered* Pool,
  # which since #394 is not the collected Pool: a serial Iteration renders the
  # single issue its Pickup bound. It defaults to the whole Pool so every
  # non-prompt caller is unchanged.
  local pool_json="${1:-$GIT_LOOPY_POOL_JSON}"
  jq -r '
    def render_issue:
      "=== Issue #\(.number): \(.title) [labels: \((.labels // []) | join(", "))] ==="
        as $header
      | (.body // "") as $body
      | ([(.comments // [])[]] | sort_by(.created_at) | reverse | .[0:5]) as $recent
      | if ($recent | length) == 0
        then "\($header)\n\($body)"
        else "\($header)\n\($body)\n\n--- Recent comments (newest first, up to 5) ---\n"
          + ([$recent[] | "[\(.created_at) @\(.author)] \(.body)"] | join("\n\n"))
        end;
    def render_prds:
      "=== \(.ref) ===\n\(.body // "")";
    [ .[] | if has("number") then render_issue else render_prds end ]
    | join("\n\n")
  ' <<<"$pool_json"
}

git_loopy_build_prompt() {
  local pool_json="${1:-$GIT_LOOPY_POOL_JSON}"
  local commits_block issues_block prompt_text
  commits_block="$(git_loopy_recent_commits_block "$GIT_LOOPY_REPO_ROOT")" || return 1
  issues_block="$(git_loopy_render_pool_blocks "$pool_json")" || return 1
  prompt_text="$(<"$GIT_LOOPY_PROMPT_PATH")" || return 1
  printf 'Previous commits: %s Issues: %s %s' \
    "$commits_block" "$issues_block" "$prompt_text"
}

_git_loopy_pool_contains_ref() {
  local ref="$1"
  jq -e --argjson ref "$ref" '
    any(.[];
      (if has("number") then .number else .ref end) == $ref
    )
  ' <<<"$GIT_LOOPY_POOL_JSON" >/dev/null
}

_git_loopy_publish_active_binding() {
  local iteration="$1"
  local ref="$2"
  local source="$3"
  local observed_at="$4"
  [[ -z "$_GIT_LOOPY_ACTIVE_REF" ]] || return 1
  _GIT_LOOPY_ACTIVE_REF="$ref"
  _git_loopy_record_active_binding "$ref" "$source" "$observed_at"

  local issue_arg payload
  if [[ "$ref" =~ ^[0-9]+$ ]]; then
    issue_arg="$ref"
  else
    issue_arg="$(jq -cn --arg ref "$ref" '$ref')" || return 1
  fi
  payload="$(
    jq -cn \
      --arg activated_at "$observed_at" \
      --arg binding_source "$source" \
      --argjson issue "$issue_arg" \
      '{
        activated_at: $activated_at,
        binding_source: $binding_source,
        issue: $issue
      }'
  )" || return 1
  git_loopy_emit_event \
    "${GIT_LOOPY_EVENT_TYPES[WRAPPER_ISSUE_ACTIVATED]}" \
    "$iteration" \
    "$payload" \
    "$observed_at"
}

# The serial **Pickup** (ADR-0032, #394). Binds the head of the ordered Pool as
# the Active issue *before* the agent turn and publishes it, so the prompt below
# carries one issue and the Working marker confirms a binding it no longer
# creates. Sets `GIT_LOOPY_PICKUP_JSON` (the single-member Pool the prompt
# renders) and `_GIT_LOOPY_PICKUP_REF`; both are empty when the Pool is empty,
# which the caller has already turned into `empty_pool`.
#
# **Selecting and publishing are two steps, and only the first decides what the
# agent sees.** A sink that refuses the activation leaves the Iteration
# *unbound* — degraded, and the marker and close-keyword fallbacks' territory —
# but the prompt still carries the one issue that was selected. Putting the whole
# Pool back would restore the menu ADR-0032 removed, on the path least able to
# cope with it: the runner's own bookkeeping already named a head, so the agent
# would be choosing from a list the runner had privately picked out of.
#
# It does not sort. Order is decided at the read (§3.2), and re-deciding it here
# would be a second implementation of the one decision
# `conformance/issue-ordering.json` exists to keep single.
GIT_LOOPY_PICKUP_JSON='[]'
_GIT_LOOPY_PICKUP_REF=""
_GIT_LOOPY_PICKUP_AT=""

git_loopy_pick_serial() {
  local iteration="$1"
  local head ref observed_at
  GIT_LOOPY_PICKUP_JSON='[]'
  _GIT_LOOPY_PICKUP_REF=""
  _GIT_LOOPY_PICKUP_AT=""

  head="$(jq -c '.[0] // empty' <<<"$GIT_LOOPY_POOL_JSON")" || return 1
  [[ -n "$head" ]] || return 1
  ref="$(
    jq -r 'if has("number") then .number else .ref end' <<<"$head"
  )" || return 1
  [[ -n "$ref" ]] || return 1

  GIT_LOOPY_PICKUP_JSON="$(jq -c '[.]' <<<"$head")" || return 1
  observed_at="$(git_loopy_iso_timestamp)" || return 1

  local label="$ref"
  [[ "$ref" =~ ^[0-9]+$ ]] && label="#$ref"
  if ! _git_loopy_publish_active_binding \
    "$iteration" "$ref" "serial_pickup" "$observed_at"; then
    # The selection stands; the binding does not. Leaving the ref set would seed
    # the turn with a binding no Event ever announced, so the Iteration goes in
    # honestly unbound and `_git_loopy_bind_active_issue` may still bind it from
    # the agent's own Working marker.
    printf 'git-loopy: serial Pickup selected %s but could not publish its binding; the Iteration works that issue unbound.\n' \
      "$label" >&2
    return 1
  fi
  _GIT_LOOPY_PICKUP_REF="$ref"
  _GIT_LOOPY_PICKUP_AT="$observed_at"
  local considered
  considered="$(jq -r 'length' <<<"$GIT_LOOPY_POOL_JSON")" || return 1
  _git_loopy_emit_pickup_bound "$iteration" "$ref" "$head" "$considered" \
    "$observed_at" || return 1
  printf 'git-loopy: serial Pickup bound %s (position 1 of %s)\n' \
    "$label" "$considered" >&2
}

# One **Pickup** binding as an Event (#397): which issue, why it was chosen, and
# where it sat in the order. Emitted after the binding is published, because a
# Pickup record for a binding no `wrapper.issue.activated` announced would
# describe a decision the rest of the stream does not contain.
_git_loopy_emit_pickup_bound() {
  local iteration="$1"
  local ref="$2"
  local head="$3"
  local considered="$4"
  local observed_at="$5"
  local issue_arg reason payload

  if [[ "$ref" =~ ^[0-9]+$ ]]; then
    issue_arg="$ref"
  else
    issue_arg="$(jq -cn --arg ref "$ref" '$ref')" || return 1
  fi
  # `pin` outranks `priority`, which outranks `order`. A pinned issue reached
  # the head because an operator named it (#396) whatever its labels said, so
  # crediting the label would make "did my Priority label do anything?"
  # unanswerable on exactly the Runs where someone overrode it. `priority` in
  # turn is a human assertion read off the issue, never inferred; every other
  # head is the head because the order put it there.
  reason="$(
    jq -r --arg pin "${GIT_LOOPY_ISSUE_PIN:-}" '
      if ($pin != "" and ((.number // .ref) | tostring) == $pin)
      then "pin"
      elif ((.labels // []) | map(if type == "object" then .name else . end)
           | index("priority"))
      then "priority" else "order" end
    ' <<<"$head"
  )" || return 1
  payload="$(
    jq -cn \
      --argjson issue "$issue_arg" \
      --arg reason "$reason" \
      --argjson position 1 \
      --argjson considered "$considered" \
      '{
        issue: $issue,
        reason: $reason,
        position: $position,
        considered: $considered
      }'
  )" || return 1
  git_loopy_emit_event \
    "${GIT_LOOPY_EVENT_TYPES[WRAPPER_PICKUP_BOUND]}" \
    "$iteration" \
    "$payload" \
    "$observed_at"
}

_git_loopy_bind_active_issue() {
  local iteration="$1"
  local ref="$2"
  local source="$3"
  local observed_at="$4"
  local state_dir="$5"
  local active_path="$state_dir/active-ref"
  local warned_path="$state_dir/warned-marker-refs"
  local active_ref=""
  [[ -f "$active_path" ]] && active_ref="$(<"$active_path")"

  if [[ -n "$active_ref" ]]; then
    if [[ "$source" == "working_marker" && "$active_ref" != "$ref" ]] &&
      ! grep -Fxq "$ref" "$warned_path" 2>/dev/null; then
      printf '%s\n' "$ref" >>"$warned_path"
      printf 'git-loopy: Working marker disagreement: the agent named #%s but this Iteration is bound to #%s; the binding stands and the marker is recorded, not obeyed\n' \
        "$ref" "$active_ref" >&2
    fi
    return 1
  fi
  if [[ "$source" == "working_marker" ]] &&
    ! _git_loopy_pool_contains_ref "$ref"; then
    if ! grep -Fxq "$ref" "$warned_path" 2>/dev/null; then
      printf '%s\n' "$ref" >>"$warned_path"
      printf 'git-loopy: Active-issue marker for #%s ignored; issue is not in the current Pool\n' \
        "$ref" >&2
    fi
    return 1
  fi

  printf '%s' "$ref" >"$active_path"
  printf '%s' "$observed_at" >"$state_dir/active-at"
  git_loopy_monotonic_seconds >"$state_dir/active-monotonic"
  _git_loopy_publish_active_binding \
    "$iteration" "$ref" "$source" "$observed_at"
}

_git_loopy_stream_agent_output() {
  local iteration="$1"
  local state_dir="$2"
  local marker_pattern='<[[:space:]]*working[[:space:]]+issue[[:space:]]*=[[:space:]]*"?#?[0-9]+"?[[:space:]]*>'
  local marker_ref_pattern='([0-9]+)'
  local line observed_at remaining marker marker_ref payload
  shopt -s nocasematch

  while IFS= read -r line || [[ -n "$line" ]]; do
    observed_at="$(git_loopy_iso_timestamp)" || return 1
    printf '%s\n' "$line" >&2

    remaining="$line"
    while [[ "$remaining" =~ $marker_pattern ]]; do
      marker="${BASH_REMATCH[0]}"
      [[ "$marker" =~ $marker_ref_pattern ]] || break
      marker_ref="${BASH_REMATCH[1]}"
      _git_loopy_bind_active_issue \
        "$iteration" "$marker_ref" "working_marker" "$observed_at" "$state_dir" ||
        true
      remaining="${remaining#*"$marker"}"
    done

    payload="$(
      jq -cn --arg text "$line" '{kind: "unclassified", text: $text}'
    )" || return 1
    git_loopy_emit_event \
      "${GIT_LOOPY_EVENT_TYPES[AGENT_OUTPUT]}" \
      "$iteration" \
      "$payload" \
      "$observed_at" || return 1
  done
}

git_loopy_run_bounded_turn() {
  # Run one already-assembled agent turn ("$@") with stdout converted into
  # unclassified Events while the same text remains visible on stderr, bounded by a
  # wall-clock send timeout. The bound is enforced by a built-in background
  # watchdog rather than timeout(1)/gtimeout, so the shell port needs no extra
  # dependency and runs unchanged on Linux, macOS, and WSL. Returns the turn's
  # real exit status; a turn that overruns the bound is terminated and reported
  # as exit 124 (GNU timeout's convention) — a failed, non-progress turn that
  # lands no agent commit, so §6 Strike accounting counts it accordingly.
  local timeout_seconds="$1"
  local iteration=""
  if [[ "${2:-}" =~ ^[1-9][0-9]*$ ]]; then
    iteration="$2"
    shift 2
  else
    shift
  fi

  # Whole-second poll budget: the integer part (forced to base 10 so a
  # zero-padded value like "08" is never mis-parsed as octal) plus one second
  # only when the fractional part carries a non-zero digit. Rounding up never
  # bounds a turn shorter than configured; whole-second polling keeps this
  # Bash 4-compatible (no `wait -n`) and free of float arithmetic.
  local int_part="${timeout_seconds%%.*}"
  local frac_part=""
  [[ "$timeout_seconds" == *.* ]] && frac_part="${timeout_seconds#*.}"
  [[ "$int_part" =~ ^[0-9]+$ ]] || int_part=0
  local budget=$((10#$int_part))
  [[ "$frac_part" == *[1-9]* ]] && budget=$((budget + 1))
  ((budget > 0)) || budget=1

  local flag_dir
  flag_dir="$(mktemp -d)" || return 1
  local timed_out_flag="$flag_dir/timed_out"
  local output_pid=""
  if [[ -n "$iteration" && -n "$_GIT_LOOPY_PICKUP_REF" ]]; then
    # Seed the turn's binding state from the serial **Pickup** (#394). The
    # marker scanner below runs in a subshell over this directory, so this is
    # what makes a Working marker naming another issue land on the
    # already-bound branch — a disagreement to record rather than a rebind —
    # and what makes the parent's read-back after the turn find the Pickup's
    # instant rather than nothing.
    printf '%s' "$_GIT_LOOPY_PICKUP_REF" >"$flag_dir/active-ref" || return 1
    printf '%s' "$_GIT_LOOPY_PICKUP_AT" >"$flag_dir/active-at" || return 1
    printf '%s' "$_GIT_LOOPY_ACTIVE_STARTED_MONOTONIC" \
      >"$flag_dir/active-monotonic" || return 1
  fi
  if [[ -n "$iteration" ]]; then
    local output_fifo="$flag_dir/agent-output"
    mkfifo "$output_fifo" || {
      rm -rf "$flag_dir"
      return 1
    }
    _git_loopy_stream_agent_output "$iteration" "$flag_dir" <"$output_fifo" &
    output_pid=$!
    "$@" >"$output_fifo" &
  else
    "$@" 1>&2 &
  fi
  local turn_pid=$!

  # Watchdog: poll the turn's liveness once a second until the budget is spent.
  # If the turn is still running then, mark the timeout and escalate SIGTERM ->
  # SIGKILL so even an agent that ignores SIGTERM is reclaimed and the parent's
  # `wait` below can never hang the Iteration. The parent only signals this
  # watchdog after its `wait` returns (the turn is already gone by then), so the
  # escalation always runs to completion.
  local grace_seconds=5
  (
    remaining="$budget"
    while ((remaining > 0)) && kill -0 "$turn_pid" 2>/dev/null; do
      sleep 1
      remaining=$((remaining - 1))
    done
    if kill -0 "$turn_pid" 2>/dev/null; then
      : >"$timed_out_flag"
      kill -TERM "$turn_pid" 2>/dev/null || true
      grace="$grace_seconds"
      while ((grace > 0)) && kill -0 "$turn_pid" 2>/dev/null; do
        sleep 1
        grace=$((grace - 1))
      done
      kill -KILL "$turn_pid" 2>/dev/null || true
    fi
  ) &
  local watchdog_pid=$!

  local status=0
  wait "$turn_pid" 2>/dev/null || status=$?
  local output_status=0
  if [[ -n "$output_pid" ]]; then
    wait "$output_pid" 2>/dev/null || output_status=$?
  fi

  # The turn is gone (on its own, or via the watchdog's SIGTERM/SIGKILL). Retire
  # the watchdog so it never lingers into the next turn, then reap it.
  kill -TERM "$watchdog_pid" 2>/dev/null || true
  wait "$watchdog_pid" 2>/dev/null || true

  local result="$status"
  if [[ -e "$timed_out_flag" ]]; then
    printf 'git-loopy: copilot turn exceeded the %ss send timeout; terminated.\n' \
      "$timeout_seconds" >&2
    result=124
  elif ((output_status != 0)); then
    printf 'git-loopy: could not record the complete copilot output stream.\n' >&2
    result="$output_status"
  fi
  if [[ -n "$iteration" ]]; then
    _GIT_LOOPY_ACTIVE_REF=""
    [[ -f "$flag_dir/active-ref" ]] &&
      _GIT_LOOPY_ACTIVE_REF="$(<"$flag_dir/active-ref")"
    if [[ -n "$_GIT_LOOPY_ACTIVE_REF" ]]; then
      _GIT_LOOPY_ACTIVE_STARTED_AT="$(<"$flag_dir/active-at")"
      _GIT_LOOPY_ACTIVE_STARTED_MONOTONIC="$(<"$flag_dir/active-monotonic")"
      _git_loopy_remember_issue_start
    fi
  fi
  rm -rf "$flag_dir"
  return "$result"
}

git_loopy_run_agent_turn() {
  local iteration="$1"
  local prompt="$2"
  local -a argv=(copilot --yolo -p "$prompt" --model "$GIT_LOOPY_MODEL" --no-color)
  if [[ -n "$GIT_LOOPY_REASONING_EFFORT" ]]; then
    argv+=(--reasoning-effort "$GIT_LOOPY_REASONING_EFFORT")
  fi
  local tool
  for tool in ${GIT_LOOPY_DENY_TOOLS_RESOLVED[@]+"${GIT_LOOPY_DENY_TOOLS_RESOLVED[@]}"}; do
    argv+=(--deny-tool "$tool")
  done
  local skill
  for skill in ${GIT_LOOPY_DENY_SKILLS_RESOLVED[@]+"${GIT_LOOPY_DENY_SKILLS_RESOLVED[@]}"}; do
    argv+=(--deny-tool "skill($skill)")
  done
  # Stream the agent's own output to stderr so stdout stays the JSONL Event
  # stream, and bound the turn by the resolved send timeout. The helper preserves
  # Copilot's real exit status (contract §4), or terminates and fails a turn that
  # overruns the bound so a hung agent never hangs the Iteration.
  git_loopy_run_bounded_turn \
    "$GIT_LOOPY_SEND_TIMEOUT_SECONDS" "$iteration" "${argv[@]}"
}

_GIT_LOOPY_AUTO_CLOSURES=0
_GIT_LOOPY_ACTIVE_REF=""
_GIT_LOOPY_ITERATION_STARTED_AT=""
_GIT_LOOPY_ITERATION_STARTED_MONOTONIC=0
_GIT_LOOPY_ACTIVE_STARTED_AT=""
_GIT_LOOPY_ACTIVE_STARTED_MONOTONIC=0
_GIT_LOOPY_ACTIVE_CLOSED_AT=""
_GIT_LOOPY_ACTIVE_CLOSED_MONOTONIC=0
GIT_LOOPY_ITERATION_ROLLUP_JSON=""
_GIT_LOOPY_MONOTONIC_CLOCK_DIR=""
_GIT_LOOPY_MONOTONIC_CLOCK_PID=""
declare -A _GIT_LOOPY_ISSUE_FIRST_STARTED_AT=()
declare -A _GIT_LOOPY_ISSUE_FIRST_STARTED_MONOTONIC=()
declare -A _GIT_LOOPY_ISSUE_CUMULATIVE_ACTIVE=()
# The first Pool issue this Iteration actually closed (OPEN -> closed), in
# encounter order. It is the strongest Checkpoint-attribution signal — the
# equivalent of the Python reference's `completions[0].ref` — so `infer_active_ref`
# consults it first. Empty when nothing closed this Iteration.
_GIT_LOOPY_FIRST_CLOSED_REF=""

git_loopy_monotonic_seconds() {
  local ticks
  [[ -n "$_GIT_LOOPY_MONOTONIC_CLOCK_DIR" ]] || return 1
  [[ -n "$_GIT_LOOPY_MONOTONIC_CLOCK_PID" ]] &&
    kill -0 "$_GIT_LOOPY_MONOTONIC_CLOCK_PID" 2>/dev/null || return 1
  ticks="$(<"$_GIT_LOOPY_MONOTONIC_CLOCK_DIR/ticks")" || return 1
  [[ "$ticks" =~ ^[0-9]+$ ]] || return 1
  printf '%s\n' "$ticks"
}

git_loopy_monotonic_clock_start() {
  _GIT_LOOPY_MONOTONIC_CLOCK_DIR="$(mktemp -d)" || return 1
  printf '0\n' >"$_GIT_LOOPY_MONOTONIC_CLOCK_DIR/ticks" || {
    rm -rf "$_GIT_LOOPY_MONOTONIC_CLOCK_DIR"
    _GIT_LOOPY_MONOTONIC_CLOCK_DIR=""
    return 1
  }
  # Relative sleep is independent of wall-clock correction, giving Bash 4 a
  # native monotonic elapsed clock without adding a Python/runtime dependency.
  (
    trap 'exit 0' TERM INT
    local ticks=0
    while sleep 1; do
      ticks=$((ticks + 1))
      printf '%s\n' "$ticks" >"$_GIT_LOOPY_MONOTONIC_CLOCK_DIR/ticks.next" ||
        exit 1
      mv -f \
        "$_GIT_LOOPY_MONOTONIC_CLOCK_DIR/ticks.next" \
        "$_GIT_LOOPY_MONOTONIC_CLOCK_DIR/ticks" || exit 1
    done
  ) &
  _GIT_LOOPY_MONOTONIC_CLOCK_PID=$!
}

git_loopy_monotonic_clock_stop() {
  if [[ -n "$_GIT_LOOPY_MONOTONIC_CLOCK_PID" ]]; then
    kill -TERM "$_GIT_LOOPY_MONOTONIC_CLOCK_PID" 2>/dev/null || true
    wait "$_GIT_LOOPY_MONOTONIC_CLOCK_PID" 2>/dev/null || true
  fi
  [[ -z "$_GIT_LOOPY_MONOTONIC_CLOCK_DIR" ]] ||
    rm -rf "$_GIT_LOOPY_MONOTONIC_CLOCK_DIR"
  _GIT_LOOPY_MONOTONIC_CLOCK_PID=""
  _GIT_LOOPY_MONOTONIC_CLOCK_DIR=""
}

_git_loopy_remember_issue_start() {
  local ref="$_GIT_LOOPY_ACTIVE_REF"
  [[ -n "$ref" ]] || return 0
  if [[ -z "${_GIT_LOOPY_ISSUE_FIRST_STARTED_AT[$ref]+present}" ]]; then
    _GIT_LOOPY_ISSUE_FIRST_STARTED_AT["$ref"]="$_GIT_LOOPY_ACTIVE_STARTED_AT"
    _GIT_LOOPY_ISSUE_FIRST_STARTED_MONOTONIC["$ref"]="$(
      printf '%s' "$_GIT_LOOPY_ACTIVE_STARTED_MONOTONIC"
    )"
  fi
}

_git_loopy_record_active_binding() {
  local ref="$1"
  local source="$2"
  local observed_at="$3"
  local activated_monotonic
  # The closed retroactive set the family shares
  # (`git_loopy.interactive.state.RETROACTIVE_BINDING_SOURCES`, and
  # `is_retroactive_binding` in the Rust reader). Stated as the set rather than
  # as its complement so a binding source added later is *prospective* by
  # default: a Pickup or a marker names the issue as work on it begins, and only
  # an after-the-fact fallback owes the pre-binding work back to the issue.
  case "$source" in
  closure | commit | single_member_pool)
    activated_monotonic="$_GIT_LOOPY_ITERATION_STARTED_MONOTONIC"
    ;;
  *)
    activated_monotonic="$(git_loopy_monotonic_seconds)" || return 1
    ;;
  esac
  _GIT_LOOPY_ACTIVE_STARTED_AT="$observed_at"
  _GIT_LOOPY_ACTIVE_STARTED_MONOTONIC="$activated_monotonic"
  _git_loopy_remember_issue_start
}

git_loopy_build_iteration_rollup() {
  local commits="$1"
  local auto_closures="$2"
  local pr_advances="$3"
  local strikes="$4"
  local terminal_outcome="${5:-}"
  local finished_monotonic duration issues outcome="no_progress"
  finished_monotonic="$(git_loopy_monotonic_seconds)" || return 1
  duration=$((finished_monotonic - _GIT_LOOPY_ITERATION_STARTED_MONOTONIC))
  ((duration >= 0)) || duration=0

  issues='[]'
  if [[ -n "$_GIT_LOOPY_ACTIVE_REF" ]]; then
    local ended_monotonic active_seconds cumulative_active status
    ended_monotonic="$finished_monotonic"
    status="no-progress"
    if [[ -n "$_GIT_LOOPY_ACTIVE_CLOSED_AT" ]]; then
      ended_monotonic="$_GIT_LOOPY_ACTIVE_CLOSED_MONOTONIC"
      status="closed"
    elif [[ "$terminal_outcome" == "aborted" || "$terminal_outcome" == "gone" ]]; then
      status="$terminal_outcome"
    elif ((commits > 0 || pr_advances > 0)); then
      status="advanced"
    fi
    active_seconds=$((ended_monotonic - _GIT_LOOPY_ACTIVE_STARTED_MONOTONIC))
    ((active_seconds >= 0)) || active_seconds=0
    cumulative_active="$(
      printf '%s' "${_GIT_LOOPY_ISSUE_CUMULATIVE_ACTIVE[$_GIT_LOOPY_ACTIVE_REF]:-0}"
    )"
    cumulative_active=$((cumulative_active + active_seconds))
    _GIT_LOOPY_ISSUE_CUMULATIVE_ACTIVE["$_GIT_LOOPY_ACTIVE_REF"]="$cumulative_active"
    outcome="$status"

    local issue_arg issue_elapsed='null' closed_at='null'
    if [[ "$_GIT_LOOPY_ACTIVE_REF" =~ ^[0-9]+$ ]]; then
      issue_arg="$_GIT_LOOPY_ACTIVE_REF"
    else
      issue_arg="$(
        jq -cn --arg issue "$_GIT_LOOPY_ACTIVE_REF" '$issue'
      )" || return 1
    fi
    if [[ -n "$_GIT_LOOPY_ACTIVE_CLOSED_AT" ]]; then
      closed_at="$(
        jq -cn --arg closed_at "$_GIT_LOOPY_ACTIVE_CLOSED_AT" '$closed_at'
      )" || return 1
      issue_elapsed=$((
        _GIT_LOOPY_ACTIVE_CLOSED_MONOTONIC -
          _GIT_LOOPY_ISSUE_FIRST_STARTED_MONOTONIC[$_GIT_LOOPY_ACTIVE_REF]
      ))
      ((issue_elapsed >= 0)) || issue_elapsed=0
    fi
    issues="$(
      jq -cn \
        --argjson issue "$issue_arg" \
        --arg status "$status" \
        --arg first_started_at \
        "${_GIT_LOOPY_ISSUE_FIRST_STARTED_AT[$_GIT_LOOPY_ACTIVE_REF]}" \
        --argjson closed_at "$closed_at" \
        --argjson issue_elapsed_seconds "$issue_elapsed" \
        --argjson active_seconds "$active_seconds" \
        --argjson cumulative_active_seconds "$cumulative_active" \
        '[{
          issue: $issue,
          status: $status,
          first_started_at: $first_started_at,
          closed_at: $closed_at,
          issue_elapsed_seconds: $issue_elapsed_seconds,
          active_seconds: $active_seconds,
          cumulative_active_seconds: $cumulative_active_seconds,
          consumption: {model: null, tokens_in: null, tokens_out: null},
          cost_usd: null,
          peak_context_window: null
        }]'
    )" || return 1
  fi

  GIT_LOOPY_ITERATION_ROLLUP_JSON="$(
    jq -cn \
    --argjson duration_seconds "$duration" \
    --argjson commits "$commits" \
    --argjson auto_closures "$auto_closures" \
    --argjson pr_advances "$pr_advances" \
    --argjson strikes "$strikes" \
    --arg outcome "$outcome" \
    --argjson issues "$issues" \
    '{
      outcome: (
        if $outcome == "no-progress" then "no_progress" else $outcome end
      ),
      duration_seconds: $duration_seconds,
      summary: {
        model: null,
        tokens_in: null,
        tokens_out: null,
        observed_tokens: null,
        cost_usd: null,
        tool_count: null,
        skill_call_count: null,
        skills_consulted: null,
        commits: $commits,
        auto_closures: $auto_closures,
        pr_advances: $pr_advances,
        strikes: $strikes,
        peak_context_window: null
      },
      issues: $issues
    }'
  )" || return 1
}

git_loopy_close_one_issue() {
  # Re-verify one Pool issue is still OPEN and close it via `gh issue close`,
  # attributing every new commit that referenced it. Emits one
  # `wrapper.auto_close` on success and bumps `_GIT_LOOPY_AUTO_CLOSURES`. A
  # `gh` failure or an already-CLOSED issue warns/skips without aborting.
  local iteration="$1"
  local issue="$2"
  local commits_json="$3"

  local -a ref_shas=()
  local count sha msg refs commit_index
  count="$(jq -r 'length' <<<"$commits_json")" || return 1
  for ((commit_index = 0; commit_index < count; commit_index++)); do
    msg="$(
      jq -r --argjson i "$commit_index" \
        '.[$i] | if .body == "" then .subject else .subject + "\n" + .body end' \
        <<<"$commits_json"
    )" || return 1
    refs="$(git_loopy_extract_close_refs "$msg")" || return 1
    if jq -e --argjson issue "$issue" 'any(.[]; . == $issue)' \
      <<<"$refs" >/dev/null; then
      sha="$(jq -r --argjson i "$commit_index" '.[$i].sha' <<<"$commits_json")" ||
        return 1
      ref_shas+=("$sha")
    fi
  done
  # Defence-in-depth: `actionable` came from the same parser, so this should
  # always find at least one SHA. Skipping is safer than misattributing.
  ((${#ref_shas[@]} > 0)) || return 0

  local view state
  view="$(gh issue view "$issue" --json number,state,url 2>/dev/null)" || {
    printf 'git-loopy: gh issue view #%s during auto-close failed; issue remains open.\n' \
      "$issue" >&2
    return 0
  }
  state="$(jq -r '.state // ""' <<<"$view" 2>/dev/null)" || state=""
  [[ "$state" == "OPEN" ]] || return 0

  local shas_str comment
  shas_str="${ref_shas[*]}"
  # The backticks below are literal Markdown in the closure comment, so single
  # quotes (no expansion) are exactly right.
  # shellcheck disable=SC2016
  comment="$(
    printf 'Implemented in %s.\n\n' "$shas_str"
    printf 'Closed by the git-loopy loop because the agent did not run '
    printf '`gh issue close` itself this iteration (commit messages did '
    printf 'reference `Closes #%s`).\n\n' "$issue"
    printf 'If this closure looks wrong, reopen with `gh issue reopen %s` — ' \
      "$issue"
    printf 'the loop will not re-close it without a new commit that references it.'
  )"
  gh issue close "$issue" --comment "$comment" >/dev/null 2>&1 || {
    printf 'git-loopy: gh issue close #%s failed; issue remains open.\n' \
      "$issue" >&2
    return 0
  }
  [[ -n "$_GIT_LOOPY_FIRST_CLOSED_REF" ]] ||
    _GIT_LOOPY_FIRST_CLOSED_REF="$issue"
  _git_loopy_publish_active_binding \
    "$iteration" "$issue" "closure" "$_GIT_LOOPY_ITERATION_STARTED_AT" ||
    true

  local closed_at closed_monotonic shas_json payload
  closed_at="$(git_loopy_iso_timestamp)" || return 1
  closed_monotonic="$(git_loopy_monotonic_seconds)" || return 1
  if [[ "$_GIT_LOOPY_ACTIVE_REF" == "$issue" ]]; then
    _GIT_LOOPY_ACTIVE_CLOSED_AT="$closed_at"
    _GIT_LOOPY_ACTIVE_CLOSED_MONOTONIC="$closed_monotonic"
  fi
  shas_json="$(_git_loopy_string_array_json "${ref_shas[@]}")" || return 1
  payload="$(
    jq -cn \
      --argjson issue "$issue" \
      --arg sha "${ref_shas[0]}" \
      --argjson shas "$shas_json" \
      '{issue: $issue, sha: $sha, shas: $shas}'
  )" || return 1
  git_loopy_emit_event \
    "${GIT_LOOPY_EVENT_TYPES[WRAPPER_AUTO_CLOSE]}" \
    "$iteration" \
    "$payload" \
    "$closed_at" || return 1
  _GIT_LOOPY_AUTO_CLOSURES=$((_GIT_LOOPY_AUTO_CLOSURES + 1))
}

git_loopy_pool_actionable_close_refs() {
  # Assemble the actionable Pool-*issue* close-refs named in this Iteration's new
  # commits: the `{ref, kind: "issue"}` Pool descriptors crossed with the closing
  # keywords in the concatenated commit subjects/bodies. Shared by the auto-close
  # backstop (§5) and the Checkpoint active-ref inference (§7) so both derive the
  # identical first-encounter-ordered close-ref set from one assembly (the two
  # paths must never disagree about which Pool issues this Iteration referenced).
  # `$1` is the new-commit JSON array; prints the compact JSON array returned by
  # `git_loopy_actionable_close_refs`.
  local commits_json="$1"
  local pool_descriptors concatenated
  pool_descriptors="$(
    jq -c '[.[] | select(has("number")) | {ref: .number, kind: "issue"}]' \
      <<<"$GIT_LOOPY_POOL_JSON"
  )" || return 1
  concatenated="$(
    jq -r '
      [ .[]
        | if .body == "" then .subject else .subject + "\n" + .body end
      ] | join("\n")
    ' <<<"$commits_json"
  )" || return 1
  git_loopy_actionable_close_refs "$concatenated" "$pool_descriptors"
}

git_loopy_auto_close_pool_issues() {
  # Close finished Pool *issues* referenced by closing keywords in this
  # Iteration's new commits. Only the GitHub source auto-closes (the PRDs agent
  # owns its own `git mv ... done/`). Repeated references collapse to at most
  # one closure via the first-encounter dedup in `actionable_close_refs`. Sets
  # `_GIT_LOOPY_AUTO_CLOSURES` to the number of issues closed.
  local iteration="$1"
  local commits_json="$2"
  _GIT_LOOPY_AUTO_CLOSURES=0
  _GIT_LOOPY_FIRST_CLOSED_REF=""
  [[ "$GIT_LOOPY_ISSUE_SOURCE" == "github" ]] || return 0

  local actionable
  actionable="$(git_loopy_pool_actionable_close_refs "$commits_json")" || return 1

  local ref
  while IFS= read -r ref; do
    [[ -n "$ref" ]] || continue
    git_loopy_close_one_issue "$iteration" "$ref" "$commits_json" || return 1
  done < <(jq -r '.[]' <<<"$actionable")
}

git_loopy_infer_active_ref() {
  # Best-effort attribution of the Iteration's Active issue for a Checkpoint,
  # mirroring the Python reference `_infer_active_ref`. In priority order: the
  # first Pool issue this Iteration actually auto-closed (the strongest signal of
  # what was worked, `completions[0].ref` in the reference); then an actionable
  # Pool-issue close-ref named in this Iteration's agent commits (the agent named
  # the issue it worked, even if the closure did not fire); then a single-member
  # Pool (the only candidate); else nothing (unattributed). `$1` is the
  # new-commit JSON; prints the ref (an issue number or a PRDs path) or nothing.
  local commits_json="$1"
  if [[ -n "$_GIT_LOOPY_ACTIVE_REF" ]]; then
    printf '%s' "$_GIT_LOOPY_ACTIVE_REF"
    return 0
  fi
  if [[ -n "$_GIT_LOOPY_FIRST_CLOSED_REF" ]]; then
    printf '%s' "$_GIT_LOOPY_FIRST_CLOSED_REF"
    return 0
  fi
  local actionable first
  actionable="$(git_loopy_pool_actionable_close_refs "$commits_json")" || return 1
  first="$(jq -r '.[0] // empty' <<<"$actionable")" || return 1
  if [[ -n "$first" ]]; then
    printf '%s' "$first"
    return 0
  fi

  local pool_length
  pool_length="$(jq -r 'length' <<<"$GIT_LOOPY_POOL_JSON")" || return 1
  if [[ "$pool_length" == "1" ]]; then
    jq -r '.[0] | if has("number") then .number else .ref end' \
      <<<"$GIT_LOOPY_POOL_JSON" || return 1
    return 0
  fi
  printf ''
}

git_loopy_infer_active_binding() {
  local commits_json="$1"
  if [[ -n "$_GIT_LOOPY_FIRST_CLOSED_REF" ]]; then
    printf '%s\nclosure\n' "$_GIT_LOOPY_FIRST_CLOSED_REF"
    return 0
  fi

  local actionable first
  actionable="$(git_loopy_pool_actionable_close_refs "$commits_json")" || return 1
  first="$(jq -r '.[0] // empty' <<<"$actionable")" || return 1
  if [[ -n "$first" ]]; then
    printf '%s\ncommit\n' "$first"
    return 0
  fi

  local pool_length
  pool_length="$(jq -r 'length' <<<"$GIT_LOOPY_POOL_JSON")" || return 1
  if [[ "$pool_length" == "1" ]]; then
    jq -r '.[0] | if has("number") then .number else .ref end' \
      <<<"$GIT_LOOPY_POOL_JSON" || return 1
    printf 'single_member_pool\n'
  fi
}

_GIT_LOOPY_CHECKPOINT_SHA=""

git_loopy_maybe_checkpoint() {
  # ADR-0004 durability net, first half. If the worktree carries any uncommitted
  # or untracked change, stage it all and capture it in exactly one
  # close-keyword-free Checkpoint attributed to the Active issue, then emit
  # `wrapper.checkpoint.recorded` ({issue, sha}). Runs AFTER the agent-commit
  # accounting and BEFORE the Strike decision, so the Checkpoint is structurally
  # excluded from both the commit tally (it is never a `wrapper.commit.recorded`)
  # and Strike progress. Sets `_GIT_LOOPY_CHECKPOINT_SHA` to the new SHA, or
  # empty when the tree was clean or the Checkpoint could not be made. Every
  # failure warns and continues, so a clean tree, a non-repo, and a local-only
  # repo all complete normally.
  local iteration="$1"
  local commits_json="$2"
  _GIT_LOOPY_CHECKPOINT_SHA=""
  git_loopy_worktree_dirty "$GIT_LOOPY_REPO_ROOT" || return 0

  local active_ref message sha
  active_ref="$(git_loopy_infer_active_ref "$commits_json")" || return 1
  message="$(git_loopy_checkpoint_message "$active_ref")" || return 1
  if ! git_loopy_stage_all "$GIT_LOOPY_REPO_ROOT"; then
    printf 'git-loopy: checkpoint staging failed; continuing without it.\n' >&2
    return 0
  fi
  if ! sha="$(git_loopy_commit "$GIT_LOOPY_REPO_ROOT" "$message")"; then
    printf 'git-loopy: checkpoint commit failed; continuing without it.\n' >&2
    return 0
  fi

  local issue_arg payload
  if [[ -z "$active_ref" ]]; then
    issue_arg='null'
  elif [[ "$active_ref" =~ ^[0-9]+$ ]]; then
    issue_arg="$active_ref"
  else
    issue_arg="$(jq -cn --arg ref "$active_ref" '$ref')" || return 1
  fi
  payload="$(
    jq -cn --arg sha "$sha" --argjson issue "$issue_arg" \
      '{issue: $issue, sha: $sha}'
  )" || return 1
  git_loopy_emit_event \
    "${GIT_LOOPY_EVENT_TYPES[WRAPPER_CHECKPOINT_RECORDED]}" \
    "$iteration" \
    "$payload" || return 1
  _GIT_LOOPY_CHECKPOINT_SHA="$sha"
}

git_loopy_maybe_push() {
  # ADR-0004 durability net, second half. Whenever this Iteration produced any
  # new local commit — an agent commit and/or the Checkpoint just authored —
  # push the current branch to its configured upstream and emit
  # `wrapper.push.recorded` on success. A missing upstream, an
  # unreachable/missing remote, an auth failure, or a non-fast-forward rejection
  # warns but never aborts (a local-only repo completes normally) and — like a
  # failed Checkpoint — emits no event, so replay records only pushes that
  # actually landed. An Iteration with no new local commit skips the push.
  # `$2` is this Iteration's total new-commit count; `$3` the Checkpoint SHA.
  local iteration="$1"
  local new_commit_count="$2"
  local checkpoint_sha="$3"
  if ((new_commit_count == 0)) && [[ -z "$checkpoint_sha" ]]; then
    return 0
  fi
  if ! git_loopy_push "$GIT_LOOPY_REPO_ROOT"; then
    printf 'git-loopy: auto-push failed; continuing (work stays local).\n' >&2
    return 0
  fi
  git_loopy_emit_event \
    "${GIT_LOOPY_EVENT_TYPES[WRAPPER_PUSH_RECORDED]}" \
    "$iteration" || return 1
}

git_loopy_ensure_gitignore_entry() {
  # Idempotently keep `.git-loopy/` in the repo's `.gitignore` so the runner's
  # own replay/summary artefacts never trip the Checkpoint dirty-check or get
  # swept into a Checkpoint by `git add -A`. Mirrors the Python reference
  # `ensure_gitignore_entry`: a no-op when `.gitignore` is absent (downstream
  # projects own their conventions — we never create it) or already carries a
  # `.git-loopy/` / `.git-loopy` line; otherwise appends one line, adding a
  # leading newline when the file does not already end in one.
  local repo_root="$1"
  local gitignore="$repo_root/.gitignore"
  [[ -f "$gitignore" ]] || return 0

  local line trimmed
  while IFS= read -r line || [[ -n "$line" ]]; do
    trimmed="$(_git_loopy_trim "$line")"
    [[ "$trimmed" == ".git-loopy/" || "$trimmed" == ".git-loopy" ]] && return 0
  done <"$gitignore"

  [[ -s "$gitignore" && -z "$(tail -c1 "$gitignore")" ]] || printf '\n' >>"$gitignore"
  printf '.git-loopy/\n' >>"$gitignore"
}

# ---------------------------------------------------------------------------
# Serial execute-frontier Continuation lifecycle
#
# The native Continuation module remains the authority for Reconciliation and
# Automation projection. This layer freezes its first read, binds one returned
# authorization to one noninteractive Copilot process, and asks the native
# module again before deciding whether anything else may run.
# ---------------------------------------------------------------------------

_git_loopy_frontier_reconcile() {
  local request="$1" response
  response="$(_git_loopy_continuation_reconcile "$request")" || return 1
  jq -ce '.result' <<<"$response"
}

_git_loopy_frontier_in_coverage_actions() {
  local actions="$1" repositories="$2"
  jq -ce --argjson repositories "$repositories" '
    [
      .[]
      | select(
          (
            [
              (.workstream_anchor.repository // empty),
              (.target.repository // empty)
            ] - $repositories
            | length
          ) == 0
        )
    ]
  ' <<<"$actions"
}

_git_loopy_frontier_observe() {
  local repository="$1"
  local trusted_producers request
  trusted_producers="$(
    jq -c '.trusted_producers' <<<"$GIT_LOOPY_CONTINUATION_AUTHORITY_JSON"
  )" || return 1
  request="$(
    jq -cn \
      --arg repository "$repository" \
      --argjson trusted_producers "$trusted_producers" \
      '{repository: $repository, trusted_producers: $trusted_producers}'
  )" || return 1
  # Deliberately `automation`-free: the grants a Run dispatches under are derived
  # from what this read-only observation already contains, so asking for an
  # authorization here would ask a question whose answer has not been computed.
  _git_loopy_frontier_reconcile "$request"
}

_git_loopy_frontier_freeze() {
  local result="$1" repositories="$2"
  local actions covered
  actions="$(jq -ce '.actions' <<<"$result")" || return 1
  covered="$(_git_loopy_frontier_in_coverage_actions "$actions" "$repositories")" ||
    return 1
  jq -ce '[.[] | {identity, semantic_fingerprint}]' <<<"$covered"
}

_git_loopy_frontier_grants() {
  local result="$1" repositories="$2" effect_kinds="$3"
  local actions covered
  actions="$(jq -ce '.actions' <<<"$result")" || return 1
  covered="$(_git_loopy_frontier_in_coverage_actions "$actions" "$repositories")" ||
    return 1
  jq -ce --argjson effect_kinds "$effect_kinds" '
    [
      .[]
      | (.safety_case.effects // [])[]
      | select(.kind as $kind | $effect_kinds | index($kind) != null)
      | {kind, scope}
    ]
    | unique_by([.kind, .scope])
    | sort_by(.kind, .scope)
  ' <<<"$covered"
}

_git_loopy_frontier_satisfied_requirements() {
  local result="$1" repositories="$2" effect_kinds="$3"
  local actions covered
  actions="$(jq -ce '.actions' <<<"$result")" || return 1
  covered="$(_git_loopy_frontier_in_coverage_actions "$actions" "$repositories")" ||
    return 1
  local denied_skills
  denied_skills="$(
    _git_loopy_string_array_json \
      ${GIT_LOOPY_DENY_SKILLS_RESOLVED[@]+"${GIT_LOOPY_DENY_SKILLS_RESOLVED[@]}"}
  )" || return 1
  jq -ce \
    --argjson denied_skills "$denied_skills" \
    --argjson effect_kinds "$effect_kinds" '
    (
      [$effect_kinds[] | {kind: "access", name: .}]
      + [
          .[]
          | (.safety_case.requirements // [])[]
          | select(.kind == "skill")
          | select(.name as $name | $denied_skills | index($name) == null)
          | {kind, name}
        ]
    )
    | unique_by([.kind, .name])
    | sort_by(.kind, .name)
  ' <<<"$covered"
}

_git_loopy_frontier_request() {
  local repository="$1" frontier="$2" grants="$3" dispatched="$4" prior="$5"
  local satisfied="$6"
  local authority="$GIT_LOOPY_CONTINUATION_AUTHORITY_JSON"
  jq -cn \
    --arg repository "$repository" \
    --arg performer "$(jq -r '.actor' <<<"$authority")" \
    --argjson repositories "$(jq -c '.ceilings.repositories' <<<"$authority")" \
    --argjson trusted_producers "$(jq -c '.trusted_producers' <<<"$authority")" \
    --argjson frontier "$frontier" \
    --argjson grants "$grants" \
    --argjson dispatched "$dispatched" \
    --argjson prior "$prior" \
    --argjson satisfied "$satisfied" \
    '{
      repository: $repository,
      trusted_producers: $trusted_producers,
      automation: {
        performer: {
          id: $performer,
          posture: {
            noninteractive: true,
            satisfied_requirements: $satisfied,
            instruction_modes: ["skill"]
          }
        },
        scope: {
          ceilings: [{
            source: "project",
            coverage: {repositories: $repositories},
            grants: $grants,
            denials: []
          }],
          revocations: []
        },
        frontier: {actions: $frontier},
        dispatched: $dispatched
      }
    }
    | if $prior == null then .
      else .automation.scope.prior = {
        coverage: $prior.coverage,
        grants: $prior.grants,
        denials: $prior.denials
      }
      end'
}

_git_loopy_frontier_bind_dispatch() {
  local result="$1" repository="$2"
  jq -ce --arg repository "$repository" '
    .automation.authorization as $authorization
    | (.actions
       | map(select(.identity == $authorization.action_identity))
       | first) as $action
    | if $action == null then
        error("authorized Action is absent from the Reconciliation")
      else
        {
          repository: $repository,
          action_identity: $authorization.action_identity,
          semantic_fingerprint: $authorization.semantic_fingerprint,
          performer: $authorization.performer,
          kind: ($action.kind // ""),
          instruction: $action.instruction,
          workstream_anchor: $authorization.workstream_anchor,
          target: $authorization.target,
          carrier: ($action.producer.carrier // $authorization.workstream_anchor),
          safety_case_version: $authorization.safety_case_version,
          completion_condition: $authorization.completion_condition,
          effects: $authorization.effects,
          requirements: $authorization.requirements,
          retry: $authorization.retry,
          triggers: $authorization.triggers
        }
      end
  ' <<<"$result"
}

_git_loopy_frontier_emit_dispatch_started() {
  local dispatch="$1" index="$2" payload
  payload="$(
    jq -cn --argjson dispatch "$dispatch" --argjson index "$index" '
      {
        mode: "execute-frontier",
        repository: $dispatch.repository,
        performer: $dispatch.performer,
        dispatch_index: $index,
        action_identity: $dispatch.action_identity,
        semantic_fingerprint: $dispatch.semantic_fingerprint,
        kind: $dispatch.kind,
        instruction_mode: ($dispatch.instruction.mode // ""),
        safety_case_version: $dispatch.safety_case_version,
        retry: ($dispatch.retry.kind // ""),
        effects: $dispatch.effects,
        requirements: $dispatch.requirements,
        triggers: [$dispatch.triggers[] | .kind],
        target: $dispatch.target,
        workstream_anchor: $dispatch.workstream_anchor,
        noninteractive: true
      }
    '
  )" || return 1
  git_loopy_emit_event \
    "${GIT_LOOPY_EVENT_TYPES[WRAPPER_CONTINUATION_DISPATCH_STARTED]}" \
    "null" "$payload"
}

_git_loopy_frontier_emit_dispatch_ended() {
  local dispatch="$1" index="$2" outcome="$3" duration_ms="$4" payload
  payload="$(
    jq -cn \
      --argjson dispatch "$dispatch" \
      --argjson index "$index" \
      --arg outcome "$outcome" \
      --argjson duration_ms "$duration_ms" \
      '{
        mode: "execute-frontier",
        repository: $dispatch.repository,
        performer: $dispatch.performer,
        dispatch_index: $index,
        action_identity: $dispatch.action_identity,
        semantic_fingerprint: $dispatch.semantic_fingerprint,
        outcome: $outcome,
        boundary: false,
        evidence_recorded: false,
        duration_ms: $duration_ms
      }'
  )" || return 1
  git_loopy_emit_event \
    "${GIT_LOOPY_EVENT_TYPES[WRAPPER_CONTINUATION_DISPATCH_ENDED]}" \
    "null" "$payload"
}

_git_loopy_frontier_emit_stopped() {
  local repository="$1" stop="$2" dispatched="$3"
  local performer payload
  performer="$(jq -r '.actor' <<<"$GIT_LOOPY_CONTINUATION_AUTHORITY_JSON")" ||
    return 1
  payload="$(
    jq -cn \
      --arg repository "$repository" \
      --arg performer "$performer" \
      --argjson stop "$stop" \
      --argjson dispatched "$dispatched" \
      '{
        mode: "execute-frontier",
        repository: $repository,
        performer: $performer,
        disposition: ($stop.disposition // ""),
        reason: ($stop.reason // ""),
        terminal: (($stop.reason // "") == "workstreams-terminal"),
        nonterminal_status: ($stop.nonterminal_status // ""),
        secondary_barriers: [
          ($stop.secondary_barriers // [])[]
          | {
              identity: (.identity // ""),
              reasons: (.reasons // [])
            }
        ],
        report_only_successors: [
          ($stop.report_only_successors // [])[]
          | {
              identity: (.identity // ""),
              semantic_fingerprint: (.semantic_fingerprint // ""),
              reason: (.reason // "")
            }
        ],
        evidence: ($stop.evidence // []),
        dispatched: $dispatched,
        successor_executed: false,
        statement: ($stop.statement // "")
      }
      + (
          if ($stop.next | type) == "object" then
            {
              next: (
                {
                  identity: ($stop.next.identity // ""),
                  summary: ($stop.next.summary // ""),
                  readiness: ($stop.next.readiness // "")
                }
                + (
                    if ($stop.next.condition | type) == "object"
                    then {condition: ($stop.next.condition.kind // "")}
                    else {}
                    end
                  )
              )
            }
          else {}
          end
        )'
  )" || return 1
  git_loopy_emit_event \
    "${GIT_LOOPY_EVENT_TYPES[WRAPPER_CONTINUATION_STOPPED]}" \
    "null" "$payload"
}

_git_loopy_frontier_render_stop() {
  local repository="$1" stop="$2"
  local line reason
  reason="$(jq -r '.reason // "unknown"' <<<"$stop")" || return 1
  line="git-loopy continuation (execute-frontier, $repository): $(jq -r '.disposition // "unknown"' <<<"$stop"); $reason"
  if [[ "$reason" != "workstreams-terminal" ]]; then
    line+="; status $(jq -r '.nonterminal_status // "unknown" | if . == "" then "unknown" else . end' <<<"$stop")"
    local next_summary barriers successors
    next_summary="$(jq -r '.next.summary // .next.identity // ""' <<<"$stop")" ||
      return 1
    [[ -z "$next_summary" ]] || line+="; next $next_summary"
    barriers="$(jq -r '(.secondary_barriers // []) | length' <<<"$stop")" ||
      return 1
    successors="$(jq -r '(.report_only_successors // []) | length' <<<"$stop")" ||
      return 1
    line+="; $barriers secondary barrier(s); $successors report-only successor(s)"
  fi
  printf '%s. No successor Action was executed.\n' "$line" >&2
}

_git_loopy_frontier_run_dispatch() {
  local dispatch="$1"
  local condition prompt
  condition="$(jq -cS '.completion_condition' <<<"$dispatch")" || return 1
  prompt="$(
    printf '%s\n' \
      'You are running one authorized Continuation Action, noninteractively.'
    printf 'Action: %s\n' "$(jq -r '.kind // ""' <<<"$dispatch")"
    printf 'Completion condition: %s\n' "$condition"
    printf '%s\n\n' \
      'Run this Instruction and stop. Do not start follow-up work, do not ask questions, and do not wait for approval: nobody is watching this session. If the Instruction cannot be completed without a human decision, stop and say so.'
    jq -r '.instruction.value' <<<"$dispatch"
  )" || return 1

  local -a argv=(copilot --yolo -p "$prompt" --model "$GIT_LOOPY_MODEL" --no-color)
  [[ -z "$GIT_LOOPY_REASONING_EFFORT" ]] ||
    argv+=(--reasoning-effort "$GIT_LOOPY_REASONING_EFFORT")
  local tool skill
  for tool in ${GIT_LOOPY_DENY_TOOLS_RESOLVED[@]+"${GIT_LOOPY_DENY_TOOLS_RESOLVED[@]}"}; do
    argv+=(--deny-tool "$tool")
  done
  for skill in ${GIT_LOOPY_DENY_SKILLS_RESOLVED[@]+"${GIT_LOOPY_DENY_SKILLS_RESOLVED[@]}"}; do
    argv+=(--deny-tool "skill($skill)")
  done

  _GIT_LOOPY_FRONTIER_DISPATCH_OUTCOME="complete"
  local started_seconds="$SECONDS" status=0
  git_loopy_run_bounded_turn "$GIT_LOOPY_SEND_TIMEOUT_SECONDS" "${argv[@]}" ||
    status=$?
  _GIT_LOOPY_FRONTIER_DISPATCH_DURATION_MS=$(( (SECONDS - started_seconds) * 1000 ))
  if ((status != 0)); then
    _GIT_LOOPY_FRONTIER_DISPATCH_OUTCOME="failed"
    printf 'git-loopy: continuation dispatch %s failed with status %s.\n' \
      "$(jq -r '.action_identity' <<<"$dispatch")" "$status" >&2
  fi
}

git_loopy_run_continuation_frontier_repository() {
  local repository="$1"
  local frontier="$2" grants="$3" satisfied="$4"

  local dispatched='[]' prior='null' refreshed=0 index=0
  while true; do
    local request result automation
    request="$(_git_loopy_frontier_request \
      "$repository" "$frontier" "$grants" "$dispatched" "$prior" "$satisfied")" ||
      return 1
    result="$(_git_loopy_frontier_reconcile "$request")" || return 1
    automation="$(jq -ce '.automation' <<<"$result")" || return 1
    prior="$(jq -ce '.scope' <<<"$automation")" || return 1

    if jq -e 'has("authorization")' <<<"$automation" >/dev/null; then
      local dispatch
      dispatch="$(_git_loopy_frontier_bind_dispatch "$result" "$repository")" ||
        return 1
      index=$((index + 1))
      _git_loopy_frontier_emit_dispatch_started "$dispatch" "$index" || return 1
      _git_loopy_frontier_run_dispatch "$dispatch" || return 1
      _git_loopy_frontier_emit_dispatch_ended \
        "$dispatch" "$index" "$_GIT_LOOPY_FRONTIER_DISPATCH_OUTCOME" \
        "$_GIT_LOOPY_FRONTIER_DISPATCH_DURATION_MS" || return 1
      dispatched="$(
        jq -cn --argjson dispatched "$dispatched" \
          --arg identity "$(jq -r '.action_identity' <<<"$dispatch")" \
          '$dispatched + [$identity]'
      )" || return 1
      refreshed=0
      if [[ "$_GIT_LOOPY_FRONTIER_DISPATCH_OUTCOME" == "failed" ]]; then
        _GIT_LOOPY_FRONTIER_EXECUTION_FAILED=1
        return 0
      fi
      continue
    fi

    local stop reason
    stop="$(jq -ce '.stop' <<<"$automation")" || return 1
    reason="$(jq -r '.reason' <<<"$stop")" || return 1
    if [[ "$reason" == "awaiting-prerequisites" ]] && ((refreshed == 0)); then
      refreshed=1
      continue
    fi
    _git_loopy_frontier_emit_stopped "$repository" "$stop" "$dispatched" ||
      return 1
    _git_loopy_frontier_render_stop "$repository" "$stop" || return 1
    if [[ "$(jq -r '.disposition' <<<"$stop")" == "attention-required" ]]; then
      _GIT_LOOPY_FRONTIER_ATTENTION_REQUIRED=1
    fi
    return 0
  done
}

git_loopy_run_continuation_frontier() {
  local authority="$GIT_LOOPY_CONTINUATION_AUTHORITY_JSON"
  local repository
  _GIT_LOOPY_FRONTIER_EXECUTION_FAILED=0
  _GIT_LOOPY_FRONTIER_ATTENTION_REQUIRED=0
  local failed=0

  # The freeze belongs to the Run, not to each repository's turn. Every covered
  # repository is observed before the first Dispatch, because a Run that froze
  # the second one only once the first had finished would let work published
  # during the first repository's Dispatches authorize itself in the second.
  local -a repositories=() frozen_frontiers=() frozen_grants=() frozen_satisfied=()
  while IFS= read -r repository; do
    [[ -n "$repository" ]] || continue
    repositories+=("$repository")
  done < <(jq -r '.ceilings.repositories[]' <<<"$authority")

  local covered effect_kinds
  covered="$(jq -c '.ceilings.repositories' <<<"$authority")" || return 1
  effect_kinds="$(jq -c '.ceilings.effect_scopes' <<<"$authority")" || return 1
  for repository in ${repositories[@]+"${repositories[@]}"}; do
    local observation frontier grants satisfied
    # Every failure here leaves the Run through the same door an execution
    # failure does, so a Run that could not freeze still closes its envelope
    # rather than disappearing without a `wrapper.run.end`.
    observation="$(_git_loopy_frontier_observe "$repository")" &&
      frontier="$(_git_loopy_frontier_freeze "$observation" "$covered")" &&
      grants="$(_git_loopy_frontier_grants \
        "$observation" "$covered" "$effect_kinds")" &&
      satisfied="$(_git_loopy_frontier_satisfied_requirements \
        "$observation" "$covered" "$effect_kinds")" || {
      failed=1
      break
    }
    frozen_frontiers+=("$frontier")
    frozen_grants+=("$grants")
    frozen_satisfied+=("$satisfied")
  done

  local index=0
  if ((failed == 0)); then
    for repository in ${repositories[@]+"${repositories[@]}"}; do
      git_loopy_run_continuation_frontier_repository \
        "$repository" \
        "${frozen_frontiers[$index]}" \
        "${frozen_grants[$index]}" \
        "${frozen_satisfied[$index]}" || {
        failed=1
        break
      }
      index=$((index + 1))
    done
  fi

  local outcome="empty_pool" exit_code=0
  if ((failed || _GIT_LOOPY_FRONTIER_EXECUTION_FAILED ||
    _GIT_LOOPY_FRONTIER_ATTENTION_REQUIRED)); then
    outcome="stuck"
    exit_code=1
  fi
  local payload
  payload="$(jq -cn --arg outcome "$outcome" \
    '{outcome: $outcome, iterations_run: 0}')" || return 1
  git_loopy_emit_event \
    "${GIT_LOOPY_EVENT_TYPES[WRAPPER_RUN_END]}" \
    "null" "$payload" || return 1
  return "$exit_code"
}

git_loopy_run_discovery() {
  local release_version
  release_version="$(
    git_loopy_read_release_version "$_GIT_LOOPY_RELEASE_VERSION_PATH"
  )" || return 1

  git_loopy_events_init "$GIT_LOOPY_REPO_ROOT" || return 1
  git_loopy_ensure_gitignore_entry "$GIT_LOOPY_REPO_ROOT" || return 1
  # Earn the live interface before the first Event exists, so the very first
  # `wrapper.run.start` already goes to its final destination and the helper
  # never has to be handed a partially replayed Run.
  git_loopy_tui_begin \
    "$GIT_LOOPY_REPO_ROOT" \
    "$GIT_LOOPY_INTERACTIVE_FLAG" \
    "${GIT_LOOPY_INTERACTIVE:-}" \
    "$release_version"
  _GIT_LOOPY_ISSUE_FIRST_STARTED_AT=()
  _GIT_LOOPY_ISSUE_FIRST_STARTED_MONOTONIC=()
  _GIT_LOOPY_ISSUE_CUMULATIVE_ACTIVE=()

  local deny_tools_json deny_skills_json
  deny_tools_json="$(
    _git_loopy_string_array_json \
      ${GIT_LOOPY_DENY_TOOLS_RESOLVED[@]+"${GIT_LOOPY_DENY_TOOLS_RESOLVED[@]}"}
  )" || return 1
  deny_skills_json="$(
    _git_loopy_string_array_json \
      ${GIT_LOOPY_DENY_SKILLS_RESOLVED[@]+"${GIT_LOOPY_DENY_SKILLS_RESOLVED[@]}"}
  )" || return 1
  local run_start_payload
  run_start_payload="$(
    jq -cn \
      --arg issue_source "$GIT_LOOPY_ISSUE_SOURCE" \
      --arg model "$GIT_LOOPY_MODEL" \
      --arg reasoning_effort "$GIT_LOOPY_REASONING_EFFORT" \
      --arg release_version "$release_version" \
      --arg prompt_path "$GIT_LOOPY_PROMPT_PATH" \
      --arg send_timeout "$GIT_LOOPY_SEND_TIMEOUT_SECONDS" \
      --argjson deny_skills "$deny_skills_json" \
      --argjson deny_tools "$deny_tools_json" \
      --argjson insight_capabilities "$(git_loopy_run_insight_capabilities_json)" \
      --argjson max_iterations "$GIT_LOOPY_MAX_ITERATIONS" \
      --argjson max_nmt_strikes "$GIT_LOOPY_MAX_NMT_STRIKES" \
      --argjson parallel_capabilities "$GIT_LOOPY_PARALLEL_CAPABILITIES_JSON" \
      --argjson rate_card "$GIT_LOOPY_RATE_CARD_JSON" \
      --argjson schema_version "$GIT_LOOPY_EVENT_SCHEMA_VERSION" \
      '{
        deny_skills: $deny_skills,
        deny_tools: $deny_tools,
        insight_capabilities: $insight_capabilities,
        issue_source: $issue_source,
        max_iterations: $max_iterations,
        max_nmt_strikes: $max_nmt_strikes,
        model: $model,
        parallel_capabilities: $parallel_capabilities,
        prompt_path: $prompt_path,
        rate_card: $rate_card,
        release_version: $release_version,
        reasoning_effort: (
          if $reasoning_effort == ""
          then null
          else $reasoning_effort
          end
        ),
        schema_version: $schema_version,
        send_timeout_seconds: ($send_timeout | tonumber)
      }'
  )" || return 1
  git_loopy_emit_event \
    "${GIT_LOOPY_EVENT_TYPES[WRAPPER_RUN_START]}" \
    "null" \
    "$run_start_payload" || return 1

  if [[ -n "$GIT_LOOPY_CONTINUATION_AUTHORITY_JSON" ]] &&
    [[ "$(jq -r '.mode' <<<"$GIT_LOOPY_CONTINUATION_AUTHORITY_JSON")" == "execute-frontier" ]]; then
    git_loopy_run_continuation_frontier
    return $?
  fi

  local iteration=0
  local iterations_run=0
  local outcome="iteration_cap"
  local strikes=0
  local strike_outcome="running"
  while true; do
    local next_iteration=$((iteration + 1))
    if ((GIT_LOOPY_MAX_ITERATIONS != 0)) &&
      ((next_iteration > GIT_LOOPY_MAX_ITERATIONS)); then
      outcome="iteration_cap"
      break
    fi
    iteration="$next_iteration"

    _GIT_LOOPY_ITERATION_STARTED_AT="$(git_loopy_iso_timestamp)" || return 1
    _GIT_LOOPY_ITERATION_STARTED_MONOTONIC="$(
      git_loopy_monotonic_seconds
    )" || return 1
    _GIT_LOOPY_ACTIVE_REF=""
    _GIT_LOOPY_ACTIVE_STARTED_AT=""
    _GIT_LOOPY_ACTIVE_STARTED_MONOTONIC=0
    _GIT_LOOPY_ACTIVE_CLOSED_AT=""
    _GIT_LOOPY_ACTIVE_CLOSED_MONOTONIC=0
    git_loopy_emit_event \
      "${GIT_LOOPY_EVENT_TYPES[WRAPPER_ITERATION_START]}" \
      "$iteration" \
      '{}' \
      "$_GIT_LOOPY_ITERATION_STARTED_AT" || return 1

    git_loopy_collect_pool || return 1
    local refs
    refs="$(
      jq -c '[
        .[]
        | if has("number") then .number else .ref end
      ]' <<<"$GIT_LOOPY_POOL_JSON"
    )" || return 1
    # Wrapper contract §3.1 — report what the discriminator rejected BEFORE
    # the collection it explains, and on the operator's own output as well as
    # the replay log: only a human can fix the issue's sections.
    local excluded_count exclusion
    excluded_count="$(jq -r 'length' <<<"$GIT_LOOPY_POOL_EXCLUSIONS_JSON")" ||
      return 1
    while IFS= read -r exclusion; do
      [[ -n "$exclusion" ]] || continue
      git_loopy_emit_event \
        "${GIT_LOOPY_EVENT_TYPES[WRAPPER_POOL_EXCLUDED]}" \
        "$iteration" \
        "$exclusion" || return 1
      printf 'git-loopy: excluded %s — %s\n' \
        "$(jq -r '.issue' <<<"$exclusion")" \
        "$(jq -r '.reason | gsub("_"; " ")' <<<"$exclusion")" >&2
    done < <(jq -c '.[]' <<<"$GIT_LOOPY_POOL_EXCLUSIONS_JSON")

    local collected_payload
    collected_payload="$(
      jq -cn --argjson issues "$refs" --argjson excluded "$excluded_count" \
        '{issues: $issues, excluded: $excluded}'
    )" || return 1
    git_loopy_emit_event \
      "${GIT_LOOPY_EVENT_TYPES[WRAPPER_AFK_READY_COLLECTED]}" \
      "$iteration" \
      "$collected_payload" || return 1

    local pool_length
    pool_length="$(jq -r 'length' <<<"$GIT_LOOPY_POOL_JSON")" || return 1
    if [[ "$pool_length" == "0" ]]; then
      local iteration_end_payload
      git_loopy_build_iteration_rollup 0 0 0 "$strikes" || return 1
      iteration_end_payload="$GIT_LOOPY_ITERATION_ROLLUP_JSON"
      git_loopy_emit_event \
        "${GIT_LOOPY_EVENT_TYPES[WRAPPER_ITERATION_END]}" \
        "$iteration" \
        "$iteration_end_payload" || return 1
      iterations_run="$iteration"
      outcome="empty_pool"
      break
    fi

    # ADR-0032 §**Pickup**: the runner binds the Active issue *before* the
    # session starts, takes the head of the §3.2 order, and hands the agent
    # exactly that issue. The prompt is one issue, not a menu.
    git_loopy_pick_serial "$iteration" || {
      printf 'git-loopy: serial Pickup did not bind an Active issue; the Iteration runs unbound.\n' >&2
      # The prompt keeps whatever the Pickup selected, which is one issue
      # whenever it got as far as reading a head. Restoring the whole Pool here
      # would put back the menu ADR-0032 removed, so it is reserved for the one
      # case that leaves nothing selected at all — a Pool whose head has no
      # usable ref, which the empty-Pool branch above cannot catch.
      [[ "$(jq -r 'length' <<<"$GIT_LOOPY_PICKUP_JSON")" != "0" ]] ||
        GIT_LOOPY_PICKUP_JSON="$GIT_LOOPY_POOL_JSON"
    }

    # Assemble the same minimum context as the Python reference (last-5
    # commits + the bound issue's block + the resolved shared prompt) and
    # run exactly one streamed Copilot turn. The agent's own output goes to
    # stderr so stdout stays the JSONL Event stream; the turn's real exit
    # status is preserved and a non-zero turn warns without failing the Run.
    local prompt
    prompt="$(git_loopy_build_prompt "$GIT_LOOPY_PICKUP_JSON")" || return 1

    local pre_sha
    pre_sha="$(git_loopy_head_sha "$GIT_LOOPY_REPO_ROOT")" || return 1

    local agent_status=0
    git_loopy_run_agent_turn "$iteration" "$prompt" || agent_status=$?
    if ((agent_status != 0)); then
      printf 'git-loopy: copilot turn exited with status %s; continuing.\n' \
        "$agent_status" >&2
    fi

    local head_sha
    head_sha="$(git_loopy_head_sha "$GIT_LOOPY_REPO_ROOT")" || head_sha="$pre_sha"
    local commits_json
    commits_json="$(
      git_loopy_commits_between "$GIT_LOOPY_REPO_ROOT" "$pre_sha" "$head_sha"
    )" || commits_json='[]'

    # Split the boundary commits into agent commits and recognized runner
    # Checkpoints. Only agent commits are recorded as contract commit events
    # (newest-first) and count toward Strike progress; a Checkpoint is excluded
    # even before this port authors one.
    local commit_count agent_commits=0 checkpoint_commits=0
    commit_count="$(jq -r 'length' <<<"$commits_json")" || commit_count=0
    local commit_index commit_message
    for ((commit_index = 0; commit_index < commit_count; commit_index++)); do
      commit_message="$(
        jq -r --argjson i "$commit_index" \
          '.[$i] | if .body == "" then .subject else .subject + "\n" + .body end' \
          <<<"$commits_json"
      )" || return 1
      if git_loopy_is_checkpoint_message "$commit_message"; then
        checkpoint_commits=$((checkpoint_commits + 1))
        continue
      fi
      agent_commits=$((agent_commits + 1))
      local commit_payload
      commit_payload="$(
        jq -c --argjson i "$commit_index" '.[$i] | {date, sha, subject}' \
          <<<"$commits_json"
      )" || return 1
      git_loopy_emit_event \
        "${GIT_LOOPY_EVENT_TYPES[WRAPPER_COMMIT_RECORDED]}" \
        "$iteration" \
        "$commit_payload" || return 1
    done

    # Auto-close finished Pool issues from the new commit messages, then decide
    # progress and advance the Strike machine. Progress (an agent commit or a
    # wrapper closure) resets the Strike count; consecutive no-progress
    # Iterations accumulate Strikes and the threshold ends the Run as stuck.
    git_loopy_auto_close_pool_issues "$iteration" "$commits_json" || return 1
    local auto_closures="$_GIT_LOOPY_AUTO_CLOSURES"
    if [[ -z "$_GIT_LOOPY_ACTIVE_REF" ]]; then
      local -a inferred_binding=()
      mapfile -t inferred_binding < <(
        git_loopy_infer_active_binding "$commits_json"
      )
      if ((${#inferred_binding[@]} == 2)); then
        _git_loopy_publish_active_binding \
          "$iteration" \
          "${inferred_binding[0]}" \
          "${inferred_binding[1]}" \
          "$_GIT_LOOPY_ITERATION_STARTED_AT" || return 1
      fi
    fi

    # Runner Checkpoint + auto-push (ADR-0004). Capture any dirty / untracked
    # work-in-progress in one close-keyword-free Checkpoint attributed to the
    # Active issue, then push the branch whenever this Iteration produced any new
    # local commit (an agent commit and/or the Checkpoint just made). Both run
    # AFTER the agent-commit accounting and BEFORE the Strike decision, so the
    # Checkpoint is excluded from the commit tally and Strike progress; both are
    # non-fatal so a local-only repo still completes.
    git_loopy_maybe_checkpoint "$iteration" "$commits_json" || return 1
    local checkpoint_sha="$_GIT_LOOPY_CHECKPOINT_SHA"
    git_loopy_maybe_push "$iteration" "$commit_count" "$checkpoint_sha" ||
      return 1

    local progress="false"
    if git_loopy_did_iteration_make_progress \
      "$agent_commits" "$auto_closures" "$checkpoint_commits" 0 false; then
      progress="true"
    fi
    local tick_result
    tick_result="$(
      git_loopy_strike_tick \
        "$GIT_LOOPY_MAX_NMT_STRIKES" "$strikes" "$strike_outcome" \
        "$agent_commits" "$auto_closures" "$checkpoint_commits" 0 false
    )" || return 1
    strikes="${tick_result%% *}"
    strike_outcome="${tick_result##* }"
    if [[ "$strike_outcome" == "aborted" || "$progress" == "false" ]]; then
      local strike_event_outcome="warn"
      [[ "$strike_outcome" == "aborted" ]] && strike_event_outcome="abort"
      local strike_payload
      strike_payload="$(
        jq -cn \
          --argjson strikes "$strikes" \
          --argjson max_strikes "$GIT_LOOPY_MAX_NMT_STRIKES" \
          --arg outcome "$strike_event_outcome" \
          '{strikes: $strikes, max_strikes: $max_strikes, outcome: $outcome}'
      )" || return 1
      git_loopy_emit_event \
        "${GIT_LOOPY_EVENT_TYPES[WRAPPER_STRIKE]}" \
        "$iteration" \
        "$strike_payload" || return 1
    fi

    local iteration_end_payload
    local terminal_outcome=""
    [[ "$strike_outcome" == "aborted" ]] && terminal_outcome="aborted"
    git_loopy_build_iteration_rollup \
      "$agent_commits" "$auto_closures" 0 "$strikes" "$terminal_outcome" ||
      return 1
    iteration_end_payload="$GIT_LOOPY_ITERATION_ROLLUP_JSON"
    git_loopy_emit_event \
      "${GIT_LOOPY_EVENT_TYPES[WRAPPER_ITERATION_END]}" \
      "$iteration" \
      "$iteration_end_payload" || return 1
    iterations_run="$iteration"
    if [[ "$strike_outcome" == "aborted" ]]; then
      outcome="stuck"
      break
    fi
  done

  local run_end_payload
  run_end_payload="$(
    jq -cn \
      --arg outcome "$outcome" \
      --argjson iterations_run "$iterations_run" \
      '{outcome: $outcome, iterations_run: $iterations_run}'
  )" || return 1
  git_loopy_emit_event \
    "${GIT_LOOPY_EVENT_TYPES[WRAPPER_RUN_END]}" \
    "null" \
    "$run_end_payload" || return 1

  local exit_code
  case "$outcome" in
    empty_pool)
      exit_code="$(git_loopy_exit_code_for "empty_pool")"
      ;;
    iteration_cap)
      exit_code="$(git_loopy_exit_code_for "iteration_cap")"
      ;;
    stuck)
      exit_code="$(git_loopy_exit_code_for "stuck")"
      ;;
  esac
  return "$exit_code"
}

git_loopy_main() {
  local packaged_prompt="$1"
  shift

  if [[ "${1:-}" == "--version" ]]; then
    (($# == 1)) || {
      git_loopy_usage >&2
      return 2
    }
    git_loopy_print_release_version
    return $?
  fi

  if [[ "${1:-}" == "continuation" ]]; then
    shift
    git_loopy_continuation_main "$@"
    return $?
  fi

  local config_status=0
  git_loopy_resolve_config "$@" || config_status=$?
  if ((config_status == 64)); then
    return 0
  fi
  if ((config_status != 0)); then
    git_loopy_usage >&2
    return 2
  fi

  git_loopy_preflight "$packaged_prompt" || return 1

  git_loopy_monotonic_clock_start || return 1
  local run_status=0
  git_loopy_run_discovery || run_status=$?
  # Teardown lives here, not inside the Run, so every exit path — clean, stuck,
  # or an early `return 1` — reaps the child exactly once. It deliberately cannot
  # change `run_status`: a presentation failure is not a Run failure.
  git_loopy_tui_finish
  git_loopy_monotonic_clock_stop
  return "$run_status"
}
