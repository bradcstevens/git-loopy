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
