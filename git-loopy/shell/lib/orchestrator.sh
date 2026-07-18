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
  # stream. No pipe, so $? is Copilot's real exit status (contract §4).
  "${argv[@]}" 1>&2
}

git_loopy_run_discovery() {
  git_loopy_events_init "$GIT_LOOPY_REPO_ROOT" || return 1

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

    # Emit one wrapper.commit.recorded per new commit in git's log order
    # (newest first), carrying only the sha/subject/date the contract names.
    local commit_count
    commit_count="$(jq -r 'length' <<<"$commits_json")" || commit_count=0
    local commit_index
    for ((commit_index = 0; commit_index < commit_count; commit_index++)); do
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

    git_loopy_emit_event \
      "${GIT_LOOPY_EVENT_TYPES[WRAPPER_ITERATION_END]}" \
      "$iteration" || return 1
    iterations_run="$iteration"
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
  esac
  return "$exit_code"
}

git_loopy_main() {
  local packaged_prompt="$1"
  shift

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
