#!/usr/bin/env bash

# Staging the shared TUI helper for the shell distribution (PRD #173, issue #193).
#
# `lib/tui.sh` is the *supervision* seam: it finds a helper it is allowed to
# trust and drives it. This module is the *installation* seam that puts one
# there. They meet at exactly one path — `.git-loopy/bin/git-loopy-tui`, the
# clone-local rank-1 discovery slot — and at exactly one rule: a helper the
# distribution stages must be this Release's, proven before it is activated.
#
# Nothing here ever runs during a Run. A Run never downloads or updates
# software; installation is a separate, explicit act by the operator.
#
# The whole module keeps one promise: a helper only becomes visible once it has
# proven itself, so an interrupted or failed installation can never be the
# reason a Run loses a working live interface. Every check happens against a
# scratch copy, and the destination is touched exactly once, by a rename.

if ((BASH_VERSINFO[0] < 4)); then
  printf 'git-loopy helper installation requires Bash 4+ (found %s).\n' \
    "$BASH_VERSION" >&2
  if [[ "${BASH_SOURCE[0]}" != "$0" ]]; then
    return 1
  fi
  exit 1
fi

# The clone-local slot `lib/tui.sh` discovers first. Stated once, here and there,
# because the installer writing anywhere else would install a helper nothing
# looks for.
declare -r GIT_LOOPY_TUI_INSTALL_RELATIVE_PATH=".git-loopy/bin/git-loopy-tui"

_git_loopy_tui_install_error() {
  printf 'git-loopy: %s\n' "$*" >&2
  return 1
}

# Resolve the one artifact a host with this shape should install.
#
# `system` and `machine` are whatever the host calls itself — `uname -s`,
# `uname -m` — so the alias tables live in the shared artifact metadata and
# every family member normalizes identically. `libc` is Linux's alone; a host
# that cannot say which one it has takes the statically linked musl build,
# because that one runs either way.
#
# A deferred platform is named by its own recorded reason rather than by the
# same "nothing published" sentence a typo would produce.
git_loopy_tui_select_target() {
  local metadata="${1:?artifact metadata path is required}"
  local system="${2:?host system is required}"
  local machine="${3:?host machine is required}"
  local libc="${4-}"

  local resolved
  if ! resolved="$(
    jq -r \
      --arg system "$system" \
      --arg machine "$machine" \
      --arg libc "$libc" \
      '
        ($system | ascii_downcase | gsub("^\\s+|\\s+$"; "")) as $system_key
        | ($machine | ascii_downcase | gsub("^\\s+|\\s+$"; "")) as $machine_key
        | (.host_aliases.systems[$system_key] // null) as $os
        | (.host_aliases.machines[$machine_key] // null) as $arch
        | ([.deferred_targets[] | select(.os == $os and .arch == $arch)] | first) as $deferred
        | if $deferred != null then
            "deferred\t" + $deferred.reason
          else
            [.targets[] | select(.os == $os and .arch == $arch)] as $candidates
            | if ($candidates | length) == 0 then
                "unpublished\t"
              elif ($candidates | length) == 1 then
                "target\t" + $candidates[0].triple
              else
                (($libc | ascii_downcase | gsub("^\\s+|\\s+$"; ""))
                  | if . == "" then "musl" else . end) as $wanted
                | ([$candidates[] | select(.libc == $wanted)] | first) as $match
                | if $match == null then "nolibc\t" else "target\t" + $match.triple end
              end
          end
      ' "$metadata" 2>/dev/null
  )"; then
    _git_loopy_tui_install_error "cannot read helper artifact metadata $metadata"
    return 1
  fi

  local command_name
  command_name="$(jq -r '.command_name' "$metadata" 2>/dev/null)" || command_name="git-loopy-tui"

  local kind="${resolved%%$'\t'*}"
  local detail="${resolved#*$'\t'}"
  case "$kind" in
    target)
      printf '%s\n' "$detail"
      ;;
    deferred)
      _git_loopy_tui_install_error \
        "no $command_name artifact for $system $machine: $detail"
      ;;
    nolibc)
      _git_loopy_tui_install_error \
        "no $command_name artifact is published for $system $machine against $libc"
      ;;
    *)
      _git_loopy_tui_install_error \
        "no $command_name artifact is published for $system $machine"
      ;;
  esac
}

# The archive, its checksum manifest, and the executable inside it, as one
# space-separated triple. Every name comes from the shared templates rather than
# from concatenation here, so a Release that renames an artifact moves all of its
# consumers at once instead of leaving this one guessing.
git_loopy_tui_artifact_names() {
  local metadata="${1:?artifact metadata path is required}"
  local triple="${2:?target triple is required}"

  local names
  if ! names="$(
    jq -r \
      --arg triple "$triple" \
      '
        . as $meta
        | ([$meta.targets[] | select(.triple == $triple)] | first) as $target
        | if $target == null then empty
          else
            ($meta.archive_formats[$target.os] // null) as $format
            | if $format == null then empty
              else
                ($meta.archive_name_template
                  | gsub("\\{command\\}"; $meta.command_name)
                  | gsub("\\{target\\}"; $target.triple)
                  | gsub("\\{extension\\}"; $format.extension)) as $archive
                | [
                    $archive,
                    ($meta.checksum_name_template
                      | gsub("\\{archive\\}"; $archive)),
                    ($meta.command_name + $format.executable_suffix)
                  ]
                | join(" ")
              end
          end
      ' "$metadata" 2>/dev/null
  )"; then
    _git_loopy_tui_install_error "cannot read helper artifact metadata $metadata"
    return 1
  fi
  if [[ -z "$names" ]]; then
    _git_loopy_tui_install_error \
      "helper artifact metadata $metadata publishes no artifact for $triple"
    return 1
  fi
  printf '%s\n' "$names"
}

# Where one Release publishes one artifact. The template is shared so the
# installers, the Homebrew formula, and the winget/Scoop manifests all resolve
# the same URL for the same Release rather than each hard-coding a guess.
git_loopy_tui_artifact_url() {
  local metadata="${1:?artifact metadata path is required}"
  local release_version="${2:?Release version is required}"
  local artifact="${3:?artifact name is required}"

  local url
  if ! url="$(
    jq -r \
      --arg version "$release_version" \
      --arg artifact "$artifact" \
      '
        .release_download_url_template
        | gsub("\\{version\\}"; $version)
        | gsub("\\{artifact\\}"; $artifact)
      ' "$metadata" 2>/dev/null
  )" || [[ -z "$url" || "$url" == "null" ]]; then
    _git_loopy_tui_install_error \
      "helper artifact metadata $metadata declares no release download URL"
    return 1
  fi
  printf '%s\n' "$url"
}
