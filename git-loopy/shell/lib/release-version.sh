#!/usr/bin/env bash

# The single semver authority for the shell distribution's Release version.
#
# The Orchestrator answers `--version` with it, stamps it onto `wrapper.run.start`,
# and refuses a clone-local TUI helper that
# disagrees with it. The installer stages that helper. Both read the same file
# through this one reader, so there is no second opinion about what Release a
# clone is (ADR-0016, Wrapper contract §16).

if ((BASH_VERSINFO[0] < 4)); then
  printf 'git-loopy Release metadata support requires Bash 4+ (found %s).\n' \
    "$BASH_VERSION" >&2
  if [[ "${BASH_SOURCE[0]}" != "$0" ]]; then
    return 1
  fi
  exit 1
fi

git_loopy_read_release_version() {
  local path="${1:?Release metadata path is required}"
  if [[ ! -f "$path" || ! -r "$path" ]]; then
    printf 'git-loopy: cannot read Release metadata %s\n' "$path" >&2
    return 1
  fi

  local release_version=""
  local extra_line=""
  local first_status=0
  local second_status=0
  exec 3<"$path" || {
    printf 'git-loopy: cannot read Release metadata %s\n' "$path" >&2
    return 1
  }
  IFS= read -r release_version <&3 || first_status=$?
  IFS= read -r extra_line <&3 || second_status=$?
  exec 3<&-

  if ((first_status > 1 || second_status == 0)) || [[ -n "$extra_line" ]]; then
    printf 'git-loopy: Release metadata %s must contain exactly one Semantic Versioning value\n' \
      "$path" >&2
    return 1
  fi
  [[ "$release_version" != *$'\r' ]] || release_version="${release_version%$'\r'}"

  local numeric_identifier='(0|[1-9][0-9]*)'
  local prerelease_identifier='(0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*)'
  local semver_pattern
  semver_pattern="^${numeric_identifier}\\.${numeric_identifier}\\.${numeric_identifier}"
  semver_pattern+="(-${prerelease_identifier}(\\.${prerelease_identifier})*)?"
  semver_pattern+='(\+[0-9A-Za-z-]+(\.[0-9A-Za-z-]+)*)?$'
  if [[ ! "$release_version" =~ $semver_pattern ]]; then
    printf 'git-loopy: Release metadata %s must contain exactly one Semantic Versioning value\n' \
      "$path" >&2
    return 1
  fi

  printf '%s\n' "$release_version"
}

if ! declare -p GIT_LOOPY_RELEASE_VERSION_PATHS >/dev/null 2>&1; then
  declare -ar GIT_LOOPY_RELEASE_VERSION_PATHS=(
    "VERSION"
    "git-loopy/python/git_loopy/__init__.py"
    "git-loopy/python/git_loopy/VERSION"
    "git-loopy/python/pyproject.toml"
    "git-loopy/python/uv.lock"
    "git-loopy/tui/Cargo.toml"
    "git-loopy/tui/Cargo.lock"
    "git-loopy/tui/README.md"
    "git-loopy/conformance/release-version.json"
  )
fi

: "${GIT_LOOPY_RELEASE_LINE_INITIALIZED:=false}"
: "${GIT_LOOPY_RELEASE_LAST_STABLE:=}"
: "${GIT_LOOPY_RELEASE_TARGET:=}"
: "${GIT_LOOPY_RELEASE_COUNTER:=0}"
: "${GIT_LOOPY_RELEASE_ADVANCE_JSON:=null}"
if ! declare -p GIT_LOOPY_RELEASE_NOTE_PATHS >/dev/null 2>&1; then
  declare -a GIT_LOOPY_RELEASE_NOTE_PATHS=()
  declare -a GIT_LOOPY_RELEASE_NOTE_COMMIT_PATHS=()
  declare -a GIT_LOOPY_RELEASE_NOTE_BACKUPS=()
  declare -a GIT_LOOPY_RELEASE_NOTE_EXISTED=()
fi
: "${GIT_LOOPY_RELEASE_NOTES_DIRECTORY:=docs/releases}"

git_loopy_resolve_bump_class() {
  local labels_json="${1:?issue labels JSON is required}"
  jq -e 'type == "array" and all(.[]; type == "string")' \
    <<<"$labels_json" >/dev/null || {
    printf 'git-loopy: Release-line Bump class: invalid_labels\n' >&2
    return 1
  }

  local -a keys=()
  mapfile -t keys < <(
    jq -r '.[] | select(startswith("semver:")) | ltrimstr("semver:")' \
      <<<"$labels_json"
  )
  local key
  for key in "${keys[@]}"; do
    case "$key" in
      major | minor | patch | none) ;;
      *)
        printf 'git-loopy: Release-line Bump class: unknown_semver_key\n' >&2
        return 1
        ;;
    esac
  done
  if ((${#keys[@]} == 0)); then
    printf 'git-loopy: Release-line Bump class: unclassified_bump_class\n' >&2
    return 1
  fi
  if ((${#keys[@]} != 1)); then
    printf 'git-loopy: Release-line Bump class: conflicting_semver_labels\n' >&2
    return 1
  fi
  printf '%s\n' "${keys[0]}"
}

_git_loopy_release_target_parts() {
  local value="${1:?Release target is required}"
  local label="${2:?Release target label is required}"
  local numeric_identifier='(0|[1-9][0-9]*)'
  local pattern="^${numeric_identifier}\\.${numeric_identifier}\\.${numeric_identifier}$"
  if [[ ! "$value" =~ $pattern ]]; then
    printf 'git-loopy: %s must be a stable major.minor.patch Semantic Versioning value\n' \
      "$label" >&2
    return 1
  fi
  printf '%s %s %s\n' \
    "${BASH_REMATCH[1]}" "${BASH_REMATCH[2]}" "${BASH_REMATCH[3]}"
}

git_loopy_advance_release_line() {
  local last_stable="${1:?last stable Release version is required}"
  local current_target="${2:?current Release target is required}"
  local current_counter="${3:?current Release counter is required}"
  local bump_class="${4:?Bump class is required}"
  if [[ ! "$current_counter" =~ ^(0|[1-9][0-9]*)$ ]]; then
    printf 'git-loopy: Release-line counter must be a non-negative integer\n' >&2
    return 1
  fi

  local -a stable_parts current_parts
  mapfile -t stable_parts < <(
    _git_loopy_release_target_parts "$last_stable" "Last stable Release version"
  )
  ((${#stable_parts[@]} == 1)) || return 1
  read -r stable_major stable_minor stable_patch <<<"${stable_parts[0]}"
  mapfile -t current_parts < <(
    _git_loopy_release_target_parts "$current_target" "Release target"
  )
  ((${#current_parts[@]} == 1)) || return 1
  read -r current_major current_minor current_patch <<<"${current_parts[0]}"

  local candidate_major candidate_minor candidate_patch increments=1
  case "$bump_class" in
    major)
      candidate_major=$((10#$stable_major + 1))
      candidate_minor=0
      candidate_patch=0
      ;;
    minor)
      candidate_major=$((10#$stable_major))
      candidate_minor=$((10#$stable_minor + 1))
      candidate_patch=0
      ;;
    patch)
      candidate_major=$((10#$stable_major))
      candidate_minor=$((10#$stable_minor))
      candidate_patch=$((10#$stable_patch + 1))
      ;;
    none)
      candidate_major=$((10#$stable_major))
      candidate_minor=$((10#$stable_minor))
      candidate_patch=$((10#$stable_patch))
      increments=0
      ;;
    *)
      printf 'git-loopy: unknown Release-line Bump class %s\n' "$bump_class" >&2
      return 1
      ;;
  esac

  local current_major_number=$((10#$current_major))
  local current_minor_number=$((10#$current_minor))
  local current_patch_number=$((10#$current_patch))
  local target_major="$current_major_number"
  local target_minor="$current_minor_number"
  local target_patch="$current_patch_number"
  if ((candidate_major > current_major_number)) ||
    ((candidate_major == current_major_number && candidate_minor > current_minor_number)) ||
    ((candidate_major == current_major_number && candidate_minor == current_minor_number &&
      candidate_patch > current_patch_number)); then
    target_major="$candidate_major"
    target_minor="$candidate_minor"
    target_patch="$candidate_patch"
  fi

  local counter=$((10#$current_counter + increments))
  local target="${target_major}.${target_minor}.${target_patch}"
  local version="$target"
  ((counter == 0)) || version+="-dev.$counter"
  jq -cn \
    --arg target "$target" \
    --argjson counter "$counter" \
    --arg version "$version" \
    '{target: $target, counter: $counter, version: $version}'
}

git_loopy_promote_release_line() {
  local release_line="${1:?Release line is required}"
  local bump_class="${2:?Bump class is required}"
  local target
  # A `major` publishes on the label alone; every other Bump class stays on its
  # `dev.N` line until the `vX.Y.Z` milestone it promised closes (ADR-0052).
  # Callers ask this rather than testing the class themselves, so *which* class
  # is exempt is one decision rather than one per call site.
  case "$bump_class" in
    major) ;;
    minor | patch | none)
      printf '%s\n' "$release_line"
      return 0
      ;;
    *)
      printf 'git-loopy: unknown Release-line Bump class %s\n' "$bump_class" >&2
      return 1
      ;;
  esac
  target="$(jq -er '.target' <<<"$release_line")" || return 1
  jq -cn --arg target "$target" \
    '{target: $target, counter: 0, version: $target}'
}

git_loopy_release_line_commit_subject() {
  local version="${1:?Release version is required}"
  local verb="promote"
  # A Promotion and an advance are different events in the domain, so they read
  # differently in `git log`: only a stable value was cut from its line.
  [[ "${version%%+*}" != *-* ]] || verb="advance"
  printf 'chore(release): %s Release line to %s\n' "$verb" "$version"
}

git_loopy_promote_closed_milestone() {
  local current_version="${1:?current Release version is required}"
  local milestone_title="${2:?milestone title is required}"
  local milestone_state="${3:?milestone state is required}"
  local target
  if [[ "${milestone_state,,}" != "closed" ]] ||
    [[ ! "$current_version" =~ ^((0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*))-dev\.(0|[1-9][0-9]*)$ ]]; then
    jq -cn null
    return 0
  fi
  target="${BASH_REMATCH[1]}"
  if [[ "$milestone_title" != "v$target" ]]; then
    jq -cn null
    return 0
  fi
  jq -cn --arg version "$target" '$version'
}

_git_loopy_release_line_from_version() {
  local repository_root="${1:?repository root is required}"
  local version="${2:?Release version is required}"
  local stable_tag=""
  if [[ "$version" =~ ^((0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*))(-dev\.(0|[1-9][0-9]*))?$ ]]; then
    GIT_LOOPY_RELEASE_TARGET="${BASH_REMATCH[1]}"
    GIT_LOOPY_RELEASE_COUNTER="${BASH_REMATCH[6]:-0}"
  else
    printf 'git-loopy: Release line must be a stable or -dev.N Semantic Versioning value\n' >&2
    return 1
  fi

  if [[ "$GIT_LOOPY_RELEASE_COUNTER" == "0" ]]; then
    GIT_LOOPY_RELEASE_LAST_STABLE="$GIT_LOOPY_RELEASE_TARGET"
  else
    while IFS= read -r stable_tag; do
      if [[ "$stable_tag" =~ ^v((0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*))$ ]]; then
        GIT_LOOPY_RELEASE_LAST_STABLE="${BASH_REMATCH[1]}"
        break
      fi
    done < <(
      git -C "$repository_root" tag --merged HEAD --list 'v*' --sort=-version:refname
    )
    if [[ -z "$GIT_LOOPY_RELEASE_LAST_STABLE" ]]; then
      printf 'git-loopy: a prerelease Release line requires a reachable stable Release tag\n' >&2
      return 1
    fi
  fi
  GIT_LOOPY_RELEASE_LINE_INITIALIZED=true
}

_git_loopy_validate_release_line_version() {
  local version="${1:?Release version is required}"
  if [[ ! "$version" =~ ^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(-dev\.(0|[1-9][0-9]*))?$ ]]; then
    printf 'git-loopy: Release line must be a stable or -dev.N Semantic Versioning value\n' >&2
    return 1
  fi
}

_git_loopy_python_distribution_version() {
  local version="${1:?Release version is required}"
  if [[ "$version" =~ ^((0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*))(-dev\.(0|[1-9][0-9]*))?$ ]]; then
    if [[ -n "${BASH_REMATCH[6]}" ]]; then
      printf '%s.dev%s\n' "${BASH_REMATCH[1]}" "${BASH_REMATCH[6]}"
    else
      printf '%s\n' "${BASH_REMATCH[1]}"
    fi
    return 0
  fi
  printf 'git-loopy: Release version must be stable or a -dev.N prerelease\n' >&2
  return 1
}

_git_loopy_release_transform_exact_version() {
  local source="$1"
  local destination="$2"
  local expected="$3"
  local version="$4"
  local count
  count="$(grep -Fxc "$expected" "$source" || true)"
  [[ "$count" == "1" ]] || return 1
  printf '%s\n' "$version" >"$destination"
}

_git_loopy_release_transform_python_source() {
  local source="$1"
  local destination="$2"
  local expected="$3"
  local version="$4"
  awk -v expected="$expected" -v version="$version" '
    $0 == "__version__ = \"" expected "\"" {
      print "__version__ = \"" version "\""
      count++
      next
    }
    { print }
    END { exit count == 1 ? 0 : 1 }
  ' "$source" >"$destination"
}

_git_loopy_release_transform_project_metadata() {
  local source="$1"
  local destination="$2"
  local expected="$3"
  local version="$4"
  awk -v expected="$expected" -v version="$version" '
    /^\[project\]$/ { in_project = 1 }
    in_project && /^\[/ && $0 != "[project]" { in_project = 0 }
    in_project && $0 == "version = \"" expected "\"" {
      print "version = \"" version "\""
      count++
      next
    }
    { print }
    END { exit count == 1 ? 0 : 1 }
  ' "$source" >"$destination"
}

_git_loopy_release_transform_package_metadata() {
  local source="$1"
  local destination="$2"
  local expected="$3"
  local version="$4"
  local package_name="$5"
  awk -v expected="$expected" -v version="$version" -v package="$package_name" '
    /^\[\[package\]\]$/ { in_package = 0 }
    $0 == "name = \"" package "\"" { in_package = 1 }
    in_package && $0 == "version = \"" expected "\"" {
      print "version = \"" version "\""
      count++
      next
    }
    { print }
    END { exit count == 1 ? 0 : 1 }
  ' "$source" >"$destination"
}

_git_loopy_release_transform_tui_manifest() {
  local source="$1"
  local destination="$2"
  local expected="$3"
  local version="$4"
  awk -v expected="$expected" -v version="$version" '
    /^\[package\]$/ { in_package = 1 }
    in_package && /^\[/ && $0 != "[package]" { in_package = 0 }
    in_package && $0 == "version = \"" expected "\"" {
      print "version = \"" version "\""
      count++
      next
    }
    { print }
    END { exit count == 1 ? 0 : 1 }
  ' "$source" >"$destination"
}

_git_loopy_release_transform_tui_probe() {
  local source="$1"
  local destination="$2"
  local expected="$3"
  local version="$4"
  awk -v expected="$expected" -v version="$version" '
    $0 == "  \"version\": \"" expected "\"," {
      print "  \"version\": \"" version "\","
      count++
      next
    }
    { print }
    END { exit count == 1 ? 0 : 1 }
  ' "$source" >"$destination"
}

_git_loopy_release_transform_conformance_fixture() {
  local source="$1"
  local destination="$2"
  local expected_release_version="$3"
  local release_version="$4"
  local expected_python_distribution_version="$5"
  local python_distribution_version="$6"

  if ! jq -e --stream '
    reduce (., inputs) as $entry (
      {release_versions: [], python_distribution_versions: []};
      if ($entry | length) == 2
        and $entry[0] == ["expected_release_version"] then
        .release_versions += [$entry[1]]
      elif ($entry | length) == 2
        and $entry[0] == ["expected_python_distribution_version"] then
        .python_distribution_versions += [$entry[1]]
      else
        .
      end
    )
    | if (.release_versions | length) == 1
        and (.python_distribution_versions | length) == 1 then
        .
      else
        error("live Release version fields must each appear exactly once")
      end
  ' "$source" >/dev/null; then
    printf 'git-loopy: Release conformance fixture %s must contain each live Release version field exactly once\n' \
      "$source" >&2
    return 1
  fi

  jq -e \
    --arg expected_release_version "$expected_release_version" \
    --arg release_version "$release_version" \
    --arg expected_python_distribution_version "$expected_python_distribution_version" \
    --arg python_distribution_version "$python_distribution_version" '
      if type != "object" then
        error("fixture must be an object")
      elif (.expected_release_version | type) != "string"
        or (.expected_python_distribution_version | type) != "string" then
        error("live Release version fields must be strings")
      elif .expected_release_version != $expected_release_version
        or .expected_python_distribution_version != $expected_python_distribution_version then
        error("live Release version fields do not match repository metadata")
      else
        .expected_release_version = $release_version
        | .expected_python_distribution_version = $python_distribution_version
      end
    ' "$source" >"$destination" || {
    printf 'git-loopy: cannot replace live Release version fields in %s\n' "$source" >&2
    return 1
  }
}

git_loopy_write_repository_release_version() {
  local repository_root="${1:?repository root is required}"
  local version="${2:?Release version is required}"
  local authority python_authority python_version
  authority="$(git_loopy_read_release_version "$repository_root/VERSION")" || return 1
  _git_loopy_validate_release_line_version "$version" || return 1
  python_authority="$(_git_loopy_python_distribution_version "$authority")" || return 1
  python_version="$(_git_loopy_python_distribution_version "$version")" || return 1

  local -a paths=("${GIT_LOOPY_RELEASE_VERSION_PATHS[@]}")
  local -a destinations=() originals=()
  local index source destination original
  for path in "${paths[@]}"; do
    source="$repository_root/$path"
    [[ -f "$source" && -r "$source" ]] || {
      printf 'git-loopy: cannot read Release metadata %s\n' "$source" >&2
      return 1
    }
  done
  for ((index = 0; index < ${#paths[@]}; index++)); do
    source="$repository_root/${paths[index]}"
    destination="$(mktemp "$(dirname "$source")/.git-loopy-release.$(basename "$source").XXXXXX")" ||
      {
        rm -f "${destinations[@]}" "${originals[@]}"
        return 1
      }
    original="$(mktemp "$(dirname "$source")/.git-loopy-release-original.$(basename "$source").XXXXXX")" ||
      {
        rm -f "${destinations[@]}" "${originals[@]}" "$destination"
        return 1
      }
    destinations+=("$destination")
    originals+=("$original")
    cp -p "$source" "$destination" || {
      rm -f "${destinations[@]}" "${originals[@]}"
      return 1
    }
    cp -p "$source" "$original" || {
      rm -f "${destinations[@]}" "${originals[@]}"
      return 1
    }
    case "${paths[index]}" in
      VERSION | git-loopy/python/git_loopy/VERSION)
        _git_loopy_release_transform_exact_version \
          "$source" "$destination" "$authority" "$version"
        ;;
      git-loopy/python/git_loopy/__init__.py)
        _git_loopy_release_transform_python_source \
          "$source" "$destination" "$authority" "$version"
        ;;
      git-loopy/python/pyproject.toml)
        _git_loopy_release_transform_project_metadata \
          "$source" "$destination" "$authority" "$version"
        ;;
      git-loopy/python/uv.lock)
        _git_loopy_release_transform_package_metadata \
          "$source" "$destination" "$python_authority" "$python_version" "git-loopy"
        ;;
      git-loopy/tui/Cargo.toml)
        _git_loopy_release_transform_tui_manifest \
          "$source" "$destination" "$authority" "$version"
        ;;
      git-loopy/tui/Cargo.lock)
        _git_loopy_release_transform_package_metadata \
          "$source" "$destination" "$authority" "$version" "git-loopy-tui"
        ;;
      git-loopy/tui/README.md)
        _git_loopy_release_transform_tui_probe \
          "$source" "$destination" "$authority" "$version"
        ;;
      git-loopy/conformance/release-version.json)
        _git_loopy_release_transform_conformance_fixture \
          "$source" "$destination" "$authority" "$version" \
          "$python_authority" "$python_version"
        ;;
    esac || {
      printf 'git-loopy: cannot replace Release version in %s\n' "$source" >&2
      rm -f "${destinations[@]}" "${originals[@]}"
      return 1
    }
  done

  for ((index = 0; index < ${#paths[@]}; index++)); do
    mv "${destinations[index]}" "$repository_root/${paths[index]}" || {
      printf 'git-loopy: cannot replace Release metadata %s\n' \
        "$repository_root/${paths[index]}" >&2
      local restored
      for ((restored = 0; restored < index; restored++)); do
        mv "${originals[restored]}" "$repository_root/${paths[restored]}" ||
          printf 'git-loopy: cannot restore Release metadata %s\n' \
            "$repository_root/${paths[restored]}" >&2
      done
      rm -f "${destinations[@]}" "${originals[@]}"
      return 1
    }
  done
  rm -f "${originals[@]}"
}

_git_loopy_release_notes_relative_path() {
  local version="${1:?Release version is required}"
  _git_loopy_validate_release_line_version "$version" || return 1
  printf '%s/v%s.md\n' "$GIT_LOOPY_RELEASE_NOTES_DIRECTORY" "$version"
}

_git_loopy_release_note_fragment_content() {
  local version="${1:?Release version is required}"
  local target="${2:?Release target is required}"
  printf '# git-loopy %s\n\n' "$version"
  printf 'This development fragment advances the Release line to `%s` on the way to stable `%s`.\n' \
    "$version" "$target"
}

_git_loopy_compose_stable_release_notes() {
  local repository_root="${1:?repository root is required}"
  local stable_version="${2:?stable Release version is required}"
  local target="${3:?Release target is required}"
  local fragment_path="${4:?development fragment path is required}"
  local fragment_stage="${5:?staged development fragment is required}"
  local destination="${6:?stable Release-note destination is required}"
  local release_directory="$repository_root/$GIT_LOOPY_RELEASE_NOTES_DIRECTORY"
  local path name counter version body
  local -a fragments=()

  shopt -s nullglob
  for path in "$release_directory"/v"$target"-dev.*.md; do
    name="${path##*/}"
    if [[ "$name" =~ ^v[0-9]+\.[0-9]+\.[0-9]+-dev\.(0|[1-9][0-9]*)\.md$ ]]; then
      version="${name#v}"
      version="${version%.md}"
      fragments+=("${BASH_REMATCH[1]}"$'\t'"$version"$'\t'"$path")
    fi
  done
  shopt -u nullglob
  name="${fragment_path##*/}"
  [[ "$name" =~ ^v[0-9]+\.[0-9]+\.[0-9]+-dev\.(0|[1-9][0-9]*)\.md$ ]] || return 1
  if [[ ! -f "$fragment_path" ]]; then
    version="${name#v}"
    version="${version%.md}"
    fragments+=("${BASH_REMATCH[1]}"$'\t'"$version"$'\t'"$fragment_stage")
  fi

  {
    printf '# git-loopy %s\n\n' "$stable_version"
    printf 'git-loopy %s was promoted from the committed development fragments below.\n' \
      "$stable_version"
    if ((${#fragments[@]} == 0)); then
      printf '\nNo development fragments were available when this stable draft was composed.\n'
    else
      printf '\n## Development fragments\n'
      while IFS=$'\t' read -r counter version path; do
        printf '\n### %s\n\n' "$version"
        body="$(
          awk '
            NR == 1 && /^# / { next }
            !started && /^[[:space:]]*$/ { next }
            { started = 1; print }
          ' "$path"
        )"
        if [[ -n "$body" ]]; then
          printf '%s\n' "$body"
        else
          printf 'No additional notes were recorded in this fragment.\n'
        fi
      done < <(printf '%s\n' "${fragments[@]}" | sort -n -k1,1)
    fi
  } >"$destination"
}

git_loopy_write_repository_release_notes() {
  local repository_root="${1:?repository root is required}"
  local advanced_line="${2:?advanced Release line is required}"
  local release_line="${3:?Release line is required}"
  local advanced_version target stable_version stable_counter
  advanced_version="$(jq -er '.version' <<<"$advanced_line")" || return 1
  target="$(jq -er '.target' <<<"$advanced_line")" || return 1
  stable_version="$(jq -er '.version' <<<"$release_line")" || return 1
  stable_counter="$(jq -er '.counter' <<<"$release_line")" || return 1
  _git_loopy_validate_release_line_version "$advanced_version" || return 1
  _git_loopy_release_target_parts "$target" "Release target" >/dev/null || return 1

  GIT_LOOPY_RELEASE_NOTE_PATHS=()
  GIT_LOOPY_RELEASE_NOTE_COMMIT_PATHS=()
  GIT_LOOPY_RELEASE_NOTE_BACKUPS=()
  GIT_LOOPY_RELEASE_NOTE_EXISTED=()

  local fragment_relative fragment_path stable_relative stable_path release_directory
  fragment_relative="$(_git_loopy_release_notes_relative_path "$advanced_version")" || return 1
  fragment_path="$repository_root/$fragment_relative"
  release_directory="$(dirname "$fragment_path")"
  mkdir -p "$release_directory" || return 1

  local -a paths=() relatives=() commit_relatives=("$fragment_relative")
  local fragment_stage="$fragment_path"
  if [[ -e "$fragment_path" ]]; then
    [[ -f "$fragment_path" && -r "$fragment_path" ]] || return 1
  else
    paths+=("$fragment_path")
    relatives+=("$fragment_relative")
  fi
  local -a stages=() backups=() existed=()
  if [[ "$stable_counter" == "0" ]]; then
    stable_relative="$(_git_loopy_release_notes_relative_path "$stable_version")" || return 1
    stable_path="$repository_root/$stable_relative"
    commit_relatives+=("$stable_relative")
    [[ -e "$stable_path" ]] || {
      paths+=("$stable_path")
      relatives+=("$stable_relative")
    }
  fi

  local index path stage backup
  for ((index = 0; index < ${#paths[@]}; index++)); do
    path="${paths[index]}"
    mkdir -p "$(dirname "$path")" || break
    stage="$(mktemp "$(dirname "$path")/.git-loopy-release.$(basename "$path").XXXXXX")" ||
      break
    stages+=("$stage")
    if [[ -e "$path" ]]; then
      [[ -f "$path" && -r "$path" ]] || break
      backup="$(mktemp "$(dirname "$path")/.git-loopy-release-original.$(basename "$path").XXXXXX")" ||
        break
      cp -p "$path" "$backup" || break
      backups+=("$backup")
      existed+=("true")
    else
      backups+=("")
      existed+=("false")
    fi
    if [[ "$path" == "$fragment_path" ]]; then
      _git_loopy_release_note_fragment_content "$advanced_version" "$target" >"$stage" || break
      fragment_stage="$stage"
    else
      _git_loopy_compose_stable_release_notes \
        "$repository_root" "$stable_version" "$target" "$fragment_path" "$fragment_stage" "$stage" ||
        break
    fi
  done
  if ((index != ${#paths[@]})); then
    rm -f "${stages[@]}" "${backups[@]}"
    return 1
  fi

  for ((index = 0; index < ${#paths[@]}; index++)); do
    mv "${stages[index]}" "${paths[index]}" || {
      local restored
      for ((restored = 0; restored < index; restored++)); do
        if [[ "${existed[restored]}" == "true" ]]; then
          mv "${backups[restored]}" "${paths[restored]}" ||
            printf 'git-loopy: cannot restore Release note %s\n' "${paths[restored]}" >&2
        else
          rm -f "${paths[restored]}"
        fi
      done
      rm -f "${stages[@]}" "${backups[@]}"
      return 1
    }
  done

  GIT_LOOPY_RELEASE_NOTE_PATHS=("${relatives[@]}")
  GIT_LOOPY_RELEASE_NOTE_COMMIT_PATHS=("${commit_relatives[@]}")
  GIT_LOOPY_RELEASE_NOTE_BACKUPS=("${backups[@]}")
  GIT_LOOPY_RELEASE_NOTE_EXISTED=("${existed[@]}")
}

_git_loopy_discard_repository_release_note_backups() {
  rm -f "${GIT_LOOPY_RELEASE_NOTE_BACKUPS[@]}"
  GIT_LOOPY_RELEASE_NOTE_PATHS=()
  GIT_LOOPY_RELEASE_NOTE_COMMIT_PATHS=()
  GIT_LOOPY_RELEASE_NOTE_BACKUPS=()
  GIT_LOOPY_RELEASE_NOTE_EXISTED=()
}

_git_loopy_restore_repository_release_notes() {
  local repository_root="${1:?repository root is required}"
  local index path
  for ((index = 0; index < ${#GIT_LOOPY_RELEASE_NOTE_PATHS[@]}; index++)); do
    path="$repository_root/${GIT_LOOPY_RELEASE_NOTE_PATHS[index]}"
    if [[ "${GIT_LOOPY_RELEASE_NOTE_EXISTED[index]}" == "true" ]]; then
      mv "${GIT_LOOPY_RELEASE_NOTE_BACKUPS[index]}" "$path" ||
        printf 'git-loopy: cannot restore Release note %s\n' "$path" >&2
    else
      rm -f "$path"
    fi
  done
  _git_loopy_discard_repository_release_note_backups
}

git_loopy_advance_repository_release_line() {
  local repository_root="${1:?repository root is required}"
  local labels_json="${2:?issue labels JSON is required}"
  local bump_class
  bump_class="$(git_loopy_resolve_bump_class "$labels_json")" || return 1
  if [[ "$bump_class" == "none" ]]; then
    GIT_LOOPY_RELEASE_ADVANCE_JSON="null"
    printf '%s\n' "$GIT_LOOPY_RELEASE_ADVANCE_JSON"
    return 0
  fi

  if [[ "$GIT_LOOPY_RELEASE_LINE_INITIALIZED" != true ]]; then
    local current_version
    current_version="$(git_loopy_read_release_version "$repository_root/VERSION")" ||
      return 1
    _git_loopy_release_line_from_version "$repository_root" "$current_version" || return 1
  fi

  local advanced_line next_line
  advanced_line="$(
    git_loopy_advance_release_line \
      "$GIT_LOOPY_RELEASE_LAST_STABLE" \
      "$GIT_LOOPY_RELEASE_TARGET" \
      "$GIT_LOOPY_RELEASE_COUNTER" \
      "$bump_class"
  )" || return 1
  next_line="$(git_loopy_promote_release_line "$advanced_line" "$bump_class")" || return 1
  local next_version
  next_version="$(jq -r '.version' <<<"$next_line")" || return 1
  git_loopy_write_repository_release_version "$repository_root" "$next_version" || return 1
  if ! git_loopy_write_repository_release_notes \
    "$repository_root" "$advanced_line" "$next_line"; then
    local previous_version="$GIT_LOOPY_RELEASE_TARGET"
    ((GIT_LOOPY_RELEASE_COUNTER == 0)) ||
      previous_version+="-dev.$GIT_LOOPY_RELEASE_COUNTER"
    git_loopy_write_repository_release_version "$repository_root" "$previous_version" ||
      printf 'git-loopy: Release metadata could not be restored after a refused note write\n' >&2
    return 1
  fi
  local -a commit_paths=(
    "${GIT_LOOPY_RELEASE_VERSION_PATHS[@]}"
    "${GIT_LOOPY_RELEASE_NOTE_COMMIT_PATHS[@]}"
  )
  if ! git -C "$repository_root" add -- "${commit_paths[@]}" ||
    ! git -C "$repository_root" commit -m \
      "$(git_loopy_release_line_commit_subject "$next_version")" \
      -- "${commit_paths[@]}" >/dev/null; then
    git -C "$repository_root" reset -- "${commit_paths[@]}" ||
      printf 'git-loopy: Release metadata could not be unstaged after a refused commit\n' >&2
    local previous_version="$GIT_LOOPY_RELEASE_TARGET"
    ((GIT_LOOPY_RELEASE_COUNTER == 0)) ||
      previous_version+="-dev.$GIT_LOOPY_RELEASE_COUNTER"
    git_loopy_write_repository_release_version \
      "$repository_root" \
      "$previous_version" ||
      printf 'git-loopy: Release metadata could not be restored after a refused commit\n' >&2
    _git_loopy_restore_repository_release_notes "$repository_root"
    return 1
  fi
  _git_loopy_discard_repository_release_note_backups

  GIT_LOOPY_RELEASE_TARGET="$(jq -r '.target' <<<"$next_line")"
  GIT_LOOPY_RELEASE_COUNTER="$(jq -r '.counter' <<<"$next_line")"
  # A Promotion is the new stable base the next issue ratchets from, and its
  # `dev.N` counter has already restarted at zero.
  ((GIT_LOOPY_RELEASE_COUNTER != 0)) ||
    GIT_LOOPY_RELEASE_LAST_STABLE="$GIT_LOOPY_RELEASE_TARGET"
  GIT_LOOPY_RELEASE_ADVANCE_JSON="$(jq -cn \
    --argjson line "$next_line" --arg bump_class "$bump_class" \
    '$line + {bump_class: $bump_class}'
  )" || return 1
  printf '%s\n' "$GIT_LOOPY_RELEASE_ADVANCE_JSON"
}
