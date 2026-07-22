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

# shellcheck disable=SC1091
source "$_git_loopy_orchestrator_dir/events.sh"
# shellcheck disable=SC1091
source "$_git_loopy_orchestrator_dir/continuation.sh"

declare -a GIT_LOOPY_DENY_TOOLS_RESOLVED=()
declare -a GIT_LOOPY_DENY_SKILLS_RESOLVED=()
GIT_LOOPY_MAX_ITERATIONS=0
# Public config variables remain untouched here because inherited values are
# inputs to CLI-over-environment precedence resolution.
GIT_LOOPY_REPO_ROOT=""
GIT_LOOPY_PROMPT_PATH=""
GIT_LOOPY_POOL_JSON='[]'

git_loopy_usage() {
  cat <<'EOF'
Usage: git-loopy.sh [<max-iterations>] [options]

Commands:
  continuation                    Native Continuation contract commands.

Options:
  --model ID
  --reasoning-effort none|minimal|low|medium|high|xhigh|max
  --issue-source github|prds
  --max-nmt-strikes N
  --deny-tool TOOL              Repeatable; unioned with GIT_LOOPY_DENY_TOOLS.
  --deny-skill SKILL            Repeatable; unioned with GIT_LOOPY_DENY_SKILLS.
  --send-timeout-seconds N
  -h, --help
EOF
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
  local -a cli_tools=()
  local -a cli_skills=()

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
      --send-timeout-seconds)
        _git_loopy_require_option_value "$@" || return 2
        send_timeout="$2"
        shift 2
        ;;
      --send-timeout-seconds=*)
        send_timeout="${1#*=}"
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
}

git_loopy_is_afk_ready() {
  local body="$1"
  [[ "$body" =~ (^|$'\n')##\ What\ to\ build ]] &&
    [[ "$body" =~ (^|$'\n')##\ Acceptance\ criteria ]]
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

  if [[ -n "${XDG_CONFIG_HOME:-}" ]] &&
    [[ -n "$(_git_loopy_trim "$XDG_CONFIG_HOME")" ]]; then
    config_home="$XDG_CONFIG_HOME"
  elif [[ -n "${HOME:-}" ]]; then
    config_home="$HOME/.config"
  else
    local fallback_home=""
    fallback_home="$(cd ~ 2>/dev/null && pwd)" || true
    if [[ -n "$fallback_home" ]]; then
      config_home="$fallback_home/.config"
    else
      config_home=""
    fi
  fi

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

git_loopy_collect_github_pool() {
  local candidates
  if ! candidates="$(
    gh issue list \
      --state open \
      --label ready-for-agent \
      --limit 100 \
      --json number,title,body,labels,state,url
  )"; then
    printf 'git-loopy: gh issue list failed; treating this Pool as empty.\n' >&2
    GIT_LOOPY_POOL_JSON='[]'
    return 0
  fi
  jq -e 'type == "array"' <<<"$candidates" >/dev/null 2>&1 || {
    printf 'git-loopy: gh issue list returned malformed JSON.\n' >&2
    GIT_LOOPY_POOL_JSON='[]'
    return 0
  }

  local -a pool_items=()
  local candidate
  while IFS= read -r candidate; do
    local body
    body="$(jq -r '.body // ""' <<<"$candidate")"
    git_loopy_is_afk_ready "$body" || continue

    local number
    number="$(jq -r '.number' <<<"$candidate")"
    [[ "$number" =~ ^[1-9][0-9]*$ ]] || {
      printf 'git-loopy: skipping issue with malformed number %s.\n' \
        "$number" >&2
      continue
    }

    local full
    if ! full="$(
      gh issue view "$number" \
        --json number,title,body,labels,state,url,comments
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
    git_loopy_is_afk_ready "$body" || continue

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
}

git_loopy_collect_prds_pool() {
  local repo_root="$1"
  local -a pool_items=()
  local prds_dir="$repo_root/prds"
  if [[ ! -d "$prds_dir" ]]; then
    GIT_LOOPY_POOL_JSON='[]'
    return 0
  fi
  if [[ -L "$prds_dir" ]]; then
    printf 'git-loopy: linked prds root is not allowed: %s\n' \
      "$prds_dir" >&2
    GIT_LOOPY_POOL_JSON='[]'
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
      git_loopy_is_afk_ready "$body" || continue

      local ref="${path#"$repo_root"/}"
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
  ' <<<"$GIT_LOOPY_POOL_JSON"
}

git_loopy_build_prompt() {
  local commits_block issues_block prompt_text
  commits_block="$(git_loopy_recent_commits_block "$GIT_LOOPY_REPO_ROOT")" || return 1
  issues_block="$(git_loopy_render_pool_blocks)" || return 1
  prompt_text="$(<"$GIT_LOOPY_PROMPT_PATH")" || return 1
  printf 'Previous commits: %s Issues: %s %s' \
    "$commits_block" "$issues_block" "$prompt_text"
}

git_loopy_run_bounded_turn() {
  # Run one already-assembled agent turn ("$@") with its own stdout folded to
  # stderr — so stdout stays the JSONL Event stream (contract §4) — bounded by a
  # wall-clock send timeout. The bound is enforced by a built-in background
  # watchdog rather than timeout(1)/gtimeout, so the shell port needs no extra
  # dependency and runs unchanged on Linux, macOS, and WSL. Returns the turn's
  # real exit status; a turn that overruns the bound is terminated and reported
  # as exit 124 (GNU timeout's convention) — a failed, non-progress turn that
  # lands no agent commit, so §6 Strike accounting counts it accordingly.
  local timeout_seconds="$1"
  shift

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

  "$@" 1>&2 &
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

  # The turn is gone (on its own, or via the watchdog's SIGTERM/SIGKILL). Retire
  # the watchdog so it never lingers into the next turn, then reap it.
  kill -TERM "$watchdog_pid" 2>/dev/null || true
  wait "$watchdog_pid" 2>/dev/null || true

  local result="$status"
  if [[ -e "$timed_out_flag" ]]; then
    printf 'git-loopy: copilot turn exceeded the %ss send timeout; terminated.\n' \
      "$timeout_seconds" >&2
    result=124
  fi
  rm -rf "$flag_dir"
  return "$result"
}

git_loopy_run_agent_turn() {
  local prompt="$1"
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
  git_loopy_run_bounded_turn "$GIT_LOOPY_SEND_TIMEOUT_SECONDS" "${argv[@]}"
}

_GIT_LOOPY_AUTO_CLOSURES=0
# The first Pool issue this Iteration actually closed (OPEN -> closed), in
# encounter order. It is the strongest Checkpoint-attribution signal — the
# equivalent of the Python reference's `completions[0].ref` — so `infer_active_ref`
# consults it first. Empty when nothing closed this Iteration.
_GIT_LOOPY_FIRST_CLOSED_REF=""

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

  local shas_json payload
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
    "$payload" || return 1
  _GIT_LOOPY_AUTO_CLOSURES=$((_GIT_LOOPY_AUTO_CLOSURES + 1))
  [[ -n "$_GIT_LOOPY_FIRST_CLOSED_REF" ]] || _GIT_LOOPY_FIRST_CLOSED_REF="$issue"
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

git_loopy_run_discovery() {
  git_loopy_events_init "$GIT_LOOPY_REPO_ROOT" || return 1
  git_loopy_ensure_gitignore_entry "$GIT_LOOPY_REPO_ROOT" || return 1

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
      --arg prompt_path "$GIT_LOOPY_PROMPT_PATH" \
      --arg send_timeout "$GIT_LOOPY_SEND_TIMEOUT_SECONDS" \
      --argjson deny_skills "$deny_skills_json" \
      --argjson deny_tools "$deny_tools_json" \
      --argjson max_iterations "$GIT_LOOPY_MAX_ITERATIONS" \
      --argjson max_nmt_strikes "$GIT_LOOPY_MAX_NMT_STRIKES" \
      '{
        deny_skills: $deny_skills,
        deny_tools: $deny_tools,
        issue_source: $issue_source,
        max_iterations: $max_iterations,
        max_nmt_strikes: $max_nmt_strikes,
        model: $model,
        prompt_path: $prompt_path,
        reasoning_effort: (
          if $reasoning_effort == ""
          then null
          else $reasoning_effort
          end
        ),
        send_timeout_seconds: ($send_timeout | tonumber)
      }'
  )" || return 1
  git_loopy_emit_event \
    "${GIT_LOOPY_EVENT_TYPES[WRAPPER_RUN_START]}" \
    "null" \
    "$run_start_payload" || return 1

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

    git_loopy_emit_event \
      "${GIT_LOOPY_EVENT_TYPES[WRAPPER_ITERATION_START]}" \
      "$iteration" || return 1

    git_loopy_collect_pool || return 1
    local refs
    refs="$(
      jq -c '[
        .[]
        | if has("number") then .number else .ref end
      ]' <<<"$GIT_LOOPY_POOL_JSON"
    )" || return 1
    local collected_payload
    collected_payload="$(
      jq -cn --argjson issues "$refs" '{issues: $issues}'
    )" || return 1
    git_loopy_emit_event \
      "${GIT_LOOPY_EVENT_TYPES[WRAPPER_AFK_READY_COLLECTED]}" \
      "$iteration" \
      "$collected_payload" || return 1

    local pool_length
    pool_length="$(jq -r 'length' <<<"$GIT_LOOPY_POOL_JSON")" || return 1
    if [[ "$pool_length" == "0" ]]; then
      git_loopy_emit_event \
        "${GIT_LOOPY_EVENT_TYPES[WRAPPER_ITERATION_END]}" \
        "$iteration" || return 1
      iterations_run="$iteration"
      outcome="empty_pool"
      break
    fi

    # Assemble the same minimum context as the Python reference (last-5
    # commits + the AFK-ready Pool blocks + the resolved shared prompt) and
    # run exactly one streamed Copilot turn. The agent's own output goes to
    # stderr so stdout stays the JSONL Event stream; the turn's real exit
    # status is preserved and a non-zero turn warns without failing the Run.
    local prompt
    prompt="$(git_loopy_build_prompt)" || return 1

    local pre_sha
    pre_sha="$(git_loopy_head_sha "$GIT_LOOPY_REPO_ROOT")" || return 1

    local agent_status=0
    git_loopy_run_agent_turn "$prompt" || agent_status=$?
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

    git_loopy_emit_event \
      "${GIT_LOOPY_EVENT_TYPES[WRAPPER_ITERATION_END]}" \
      "$iteration" || return 1
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

  git_loopy_run_discovery
}
