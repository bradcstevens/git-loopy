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
  )
fi

: "${GIT_LOOPY_RELEASE_LINE_INITIALIZED:=false}"
: "${GIT_LOOPY_RELEASE_LAST_STABLE:=}"
: "${GIT_LOOPY_RELEASE_TARGET:=}"
: "${GIT_LOOPY_RELEASE_COUNTER:=0}"
: "${GIT_LOOPY_RELEASE_ADVANCE_JSON:=null}"

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

  local next_line
  next_line="$(
    git_loopy_advance_release_line \
      "$GIT_LOOPY_RELEASE_LAST_STABLE" \
      "$GIT_LOOPY_RELEASE_TARGET" \
      "$GIT_LOOPY_RELEASE_COUNTER" \
      "$bump_class"
  )" || return 1
  local next_version
  next_version="$(jq -r '.version' <<<"$next_line")" || return 1
  git_loopy_write_repository_release_version "$repository_root" "$next_version" || return 1
  if ! git -C "$repository_root" add -- "${GIT_LOOPY_RELEASE_VERSION_PATHS[@]}" ||
    ! git -C "$repository_root" commit -m \
      "chore(release): advance Release line to $next_version" \
      -- "${GIT_LOOPY_RELEASE_VERSION_PATHS[@]}" >/dev/null; then
    git -C "$repository_root" reset -- "${GIT_LOOPY_RELEASE_VERSION_PATHS[@]}" ||
      printf 'git-loopy: Release metadata could not be unstaged after a refused commit\n' >&2
    local previous_version="$GIT_LOOPY_RELEASE_TARGET"
    ((GIT_LOOPY_RELEASE_COUNTER == 0)) ||
      previous_version+="-dev.$GIT_LOOPY_RELEASE_COUNTER"
    git_loopy_write_repository_release_version \
      "$repository_root" \
      "$previous_version" ||
      printf 'git-loopy: Release metadata could not be restored after a refused commit\n' >&2
    return 1
  fi

  GIT_LOOPY_RELEASE_TARGET="$(jq -r '.target' <<<"$next_line")"
  GIT_LOOPY_RELEASE_COUNTER="$(jq -r '.counter' <<<"$next_line")"
  GIT_LOOPY_RELEASE_ADVANCE_JSON="$(jq -cn \
    --argjson line "$next_line" --arg bump_class "$bump_class" \
    '$line + {bump_class: $bump_class}'
  )" || return 1
  printf '%s\n' "$GIT_LOOPY_RELEASE_ADVANCE_JSON"
}
