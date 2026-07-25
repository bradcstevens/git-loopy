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

# The SHA-256 of one file, from whichever of the two standard tools this host
# carries. Linux distributions ship `sha256sum`; macOS ships `shasum`. Neither is
# guaranteed, so a host with neither is told what is missing rather than being
# handed an unverified binary.
git_loopy_tui_digest() {
  local path="${1:?path is required}"

  local line
  if command -v sha256sum >/dev/null 2>&1; then
    line="$(sha256sum "$path" 2>/dev/null)" || return 1
  elif command -v shasum >/dev/null 2>&1; then
    line="$(shasum -a 256 "$path" 2>/dev/null)" || return 1
  else
    _git_loopy_tui_install_error \
      "neither sha256sum nor shasum is available to verify a downloaded helper"
    return 1
  fi
  printf '%s\n' "${line%% *}"
}

# Prove `archive` is the artifact its published checksum names, and echo the
# digest that proved it.
#
# Both halves are checked. A digest that matches proves nothing if it was
# published for a different file, so the manifest's filename has to be this
# artifact's too — otherwise a correct macOS arm64 checksum would happily bless
# the x64 archive an installer downloaded by mistake.
git_loopy_tui_verify_checksum() {
  local archive="${1:?artifact path is required}"
  local manifest="${2:?checksum manifest path is required}"

  if [[ ! -f "$manifest" || ! -r "$manifest" ]]; then
    _git_loopy_tui_install_error "cannot read checksum manifest $manifest"
    return 1
  fi

  local expected=""
  local published=()
  local digest name rest
  while read -r digest name rest || [[ -n "$digest" ]]; do
    [[ -n "$digest" && -n "$name" ]] || continue
    [[ "$digest" =~ ^[0-9a-fA-F]{64}$ ]] || continue
    # A `*` prefix is how the coreutils tools mark a binary-mode entry; it is
    # part of the mode, not of the filename.
    name="${name#\*}"
    published+=("$name")
    if [[ "$name" == "$(basename -- "$archive")" ]]; then
      expected="${digest,,}"
    fi
  done <"$manifest"

  if ((${#published[@]} == 0)); then
    _git_loopy_tui_install_error \
      "checksum manifest $manifest declares no SHA-256 entry"
    return 1
  fi
  if [[ -z "$expected" ]]; then
    _git_loopy_tui_install_error \
      "checksum manifest $manifest publishes ${published[*]} rather than $(basename -- "$archive")"
    return 1
  fi

  local actual
  actual="$(git_loopy_tui_digest "$archive")" || {
    _git_loopy_tui_install_error "cannot read release artifact $archive"
    return 1
  }
  if [[ "${actual,,}" != "$expected" ]]; then
    _git_loopy_tui_install_error \
      "release artifact $(basename -- "$archive") failed its SHA-256 checksum: expected $expected, computed $actual"
    return 1
  fi
  printf '%s\n' "$actual"
}

# Unpack `archive` into `destination` and echo the path of the one executable the
# Release published inside it.
#
# The executable is looked up *by its published name* rather than by taking
# whatever the archive happens to contain: an archive is attacker-shaped input
# until its checksum has been verified, and even a well-formed one that carries a
# differently named binary is not the artifact this Release meant to ship.
git_loopy_tui_extract() {
  local archive="${1:?archive path is required}"
  local destination="${2:?destination directory is required}"
  local executable_name="${3:?published executable name is required}"

  mkdir -p "$destination" || {
    _git_loopy_tui_install_error "cannot create $destination"
    return 1
  }

  case "$archive" in
    *.zip)
      if ! command -v unzip >/dev/null 2>&1; then
        _git_loopy_tui_install_error \
          "unzip is required to unpack $(basename -- "$archive")"
        return 1
      fi
      unzip -q -o "$archive" -d "$destination" >/dev/null 2>&1 || {
        _git_loopy_tui_install_error \
          "cannot unpack $(basename -- "$archive")"
        return 1
      }
      ;;
    *)
      # `-xf` rather than `-xJf`: GNU tar and libarchive both detect xz from the
      # stream, and letting them do it keeps one code path for any compression a
      # later Release picks.
      tar -xf "$archive" -C "$destination" >/dev/null 2>&1 || {
        _git_loopy_tui_install_error \
          "cannot unpack $(basename -- "$archive")"
        return 1
      }
      ;;
  esac

  # cargo-dist archives the executable at the root, but a Release that starts
  # wrapping it in a versioned directory should install rather than fail, so the
  # search is by name over the whole extraction.
  local found
  found="$(
    find "$destination" -type f -name "$executable_name" -print 2>/dev/null |
      LC_ALL=C sort | head -n 1
  )"
  if [[ -z "$found" ]]; then
    _git_loopy_tui_install_error \
      "$(basename -- "$archive") does not contain $executable_name"
    return 1
  fi

  chmod +x "$found" 2>/dev/null || {
    _git_loopy_tui_install_error "cannot make $found executable"
    return 1
  }
  printf '%s\n' "$found"
}

# The gate a staged helper passes before it becomes the active one.
#
# Two separate questions, asked in the order that makes a failure legible. Is it
# this Release — Wrapper contract §16 requires exact equality for a component
# selected as an artifact of *this* distribution, and fails closed on drift. And
# can it decode what this Orchestrator emits — asked as containment rather than
# equality, because a later helper may decode a range of Event schemas at once.
#
# Release equality never answers the second question. A helper that reports this
# Release and probes as another one is refused: identity is what `--version`
# says, and a helper that contradicts itself has not established either.
git_loopy_tui_verify_helper() {
  local helper="${1:?helper path is required}"
  local release_version="${2:?Release version is required}"
  local schema_version="${3:?Event schema version is required}"

  local reported
  if ! reported="$("$helper" --version 2>/dev/null)"; then
    _git_loopy_tui_install_error \
      "the staged helper could not answer --version"
    return 1
  fi
  local expected="git-loopy-tui $release_version"
  if [[ "${reported//[$'\r\n']/}" != "$expected" ]]; then
    _git_loopy_tui_install_error \
      "the staged helper reports '${reported//[$'\r\n']/}', not '$expected'"
    return 1
  fi

  local probe
  if ! probe="$("$helper" --schema-version 2>/dev/null)"; then
    _git_loopy_tui_install_error \
      "the staged helper could not answer --schema-version"
    return 1
  fi
  if ! jq -e \
    --argjson wanted "$schema_version" \
    --arg release "$release_version" \
    '
      type == "object"
      and (.min_event_schema_version | type) == "number"
      and (.max_event_schema_version | type) == "number"
      and .min_event_schema_version <= $wanted
      and $wanted <= .max_event_schema_version
      and ((.version | type) != "string" or .version == $release)
    ' <<<"$probe" >/dev/null 2>&1; then
    _git_loopy_tui_install_error \
      "the staged helper does not support Event schema $schema_version as Release $release_version"
    return 1
  fi
}

# A scratch directory beside the destination, and the reason the whole module
# can promise a failed installation costs nothing.
#
# It is a *sibling* rather than `$TMPDIR` so that activation is a same-directory
# rename. A rename across filesystems is a copy, and a copy can be interrupted
# half-written — which would leave a truncated file sitting in exactly the slot
# `lib/tui.sh` discovers first. The directory is hidden and prefixed so an
# abandoned one is recognisable as this installer's.
git_loopy_tui_workspace() {
  local destination="${1:?destination path is required}"

  local parent
  parent="$(dirname -- "$destination")"
  mkdir -p "$parent" || {
    _git_loopy_tui_install_error "cannot create $parent"
    return 1
  }
  mktemp -d "$parent/.git-loopy-tui-staging.XXXXXX" || {
    _git_loopy_tui_install_error "cannot stage a helper under $parent"
    return 1
  }
}

# The one operation that changes what a Run will discover. Everything before it
# is reversible by deleting a scratch directory; this is not, which is why it
# happens last and happens once.
git_loopy_tui_activate() {
  local verified="${1:?verified helper path is required}"
  local destination="${2:?destination path is required}"

  chmod +x "$verified" 2>/dev/null || {
    _git_loopy_tui_install_error "cannot make $verified executable"
    return 1
  }
  mv -f "$verified" "$destination" || {
    _git_loopy_tui_install_error "cannot install the verified helper to $destination"
    return 1
  }
}

# Which C library a Linux host links against — the one selection input `uname`
# cannot answer, and the reason two Linux artifacts exist per architecture.
#
# Parsed from `ldd --version` text rather than inferred from the distribution,
# because the answer that matters is what this binary will be asked to link
# against, not what the packaging says. An unrecognized answer is left empty on
# purpose: `git_loopy_tui_select_target` then takes the statically linked musl
# build, which runs either way, instead of guessing at a dynamic one.
git_loopy_tui_libc_from_ldd() {
  local report="${1-}"
  local lowered="${report,,}"
  case "$lowered" in
    *musl*) printf 'musl\n' ;;
    *glibc* | *"gnu libc"* | *"gnu c library"*) printf 'gnu\n' ;;
    *) printf '\n' ;;
  esac
}

git_loopy_tui_host_libc() {
  [[ "$(uname -s 2>/dev/null)" == "Linux" ]] || {
    printf '\n'
    return 0
  }
  local report
  report="$(ldd --version 2>&1)" || true
  git_loopy_tui_libc_from_ldd "$report"
}

# The triple this host should install, from what the host says about itself.
git_loopy_tui_host_target() {
  local metadata="${1:?artifact metadata path is required}"
  local system machine libc
  system="$(uname -s 2>/dev/null)" || system=""
  machine="$(uname -m 2>/dev/null)" || machine=""
  libc="$(git_loopy_tui_host_libc)"
  git_loopy_tui_select_target "$metadata" "$system" "$machine" "$libc"
}

# Fetch one published file. `--fail` is what turns an HTTP error page into a
# failed download rather than into an "archive" that later fails to unpack for a
# reason that says nothing about what went wrong.
git_loopy_tui_download() {
  local url="${1:?url is required}"
  local destination="${2:?destination is required}"

  if ! command -v curl >/dev/null 2>&1; then
    _git_loopy_tui_install_error \
      "curl is required to download a helper; pass --tui-archive and --tui-checksum to install from local files instead"
    return 1
  fi
  curl --fail --silent --show-error --location --output "$destination" "$url" \
    2>/dev/null || {
    _git_loopy_tui_install_error "cannot download $url"
    return 1
  }
}

# Install one verified helper into `<repository_root>/.git-loopy/bin/`.
#
# The order is the whole contract, and it is the order a failure is cheapest in:
# pick the artifact this host publishes, obtain it and its published checksum,
# prove the checksum over both filename and digest, unpack it, prove it reports
# this clone's Release and decodes this Orchestrator's Event schema — and only
# then rename it into the slot a Run discovers. Everything before the rename
# happens inside a scratch directory that is removed either way, so a prior
# verified helper survives every failure above untouched.
#
# `archive_override` / `checksum_override` are the air-gapped path: a host with
# no network hands over files it already has, and they face exactly the same
# proofs as a download.
git_loopy_tui_install() {
  local metadata="${1:?artifact metadata path is required}"
  local repository_root="${2:?repository root is required}"
  local release_version="${3:?Release version is required}"
  local schema_version="${4:?Event schema version is required}"
  local base_url="${5-}"
  local archive_override="${6-}"
  local checksum_override="${7-}"

  if ! command -v jq >/dev/null 2>&1; then
    _git_loopy_tui_install_error \
      "jq is required to install the $(basename -- "$GIT_LOOPY_TUI_INSTALL_RELATIVE_PATH") helper"
    return 1
  fi

  local triple
  triple="$(git_loopy_tui_host_target "$metadata")" || return 1

  local names archive_name checksum_name executable_name
  names="$(git_loopy_tui_artifact_names "$metadata" "$triple")" || return 1
  read -r archive_name checksum_name executable_name <<<"$names"

  local destination="$repository_root/$GIT_LOOPY_TUI_INSTALL_RELATIVE_PATH"
  local workspace
  workspace="$(git_loopy_tui_workspace "$destination")" || return 1
  # The workspace is a sibling of the destination, so it would otherwise be
  # discoverable debris in the directory a Run searches.
  trap 'rm -rf "$workspace"' RETURN

  local archive="$workspace/$archive_name"
  local checksum="$workspace/$checksum_name"
  if [[ -n "$archive_override" ]]; then
    if [[ -z "$checksum_override" ]]; then
      _git_loopy_tui_install_error \
        "--tui-archive needs the artifact's published checksum manifest; pass --tui-checksum too"
      return 1
    fi
    cp "$archive_override" "$archive" 2>/dev/null || {
      _git_loopy_tui_install_error "cannot read $archive_override"
      return 1
    }
    cp "$checksum_override" "$checksum" 2>/dev/null || {
      _git_loopy_tui_install_error "cannot read $checksum_override"
      return 1
    }
  else
    local archive_url checksum_url
    if [[ -n "$base_url" ]]; then
      archive_url="${base_url%/}/$archive_name"
      checksum_url="${base_url%/}/$checksum_name"
    else
      archive_url="$(
        git_loopy_tui_artifact_url "$metadata" "$release_version" "$archive_name"
      )" || return 1
      checksum_url="$(
        git_loopy_tui_artifact_url "$metadata" "$release_version" "$checksum_name"
      )" || return 1
    fi
    git_loopy_tui_download "$archive_url" "$archive" || return 1
    git_loopy_tui_download "$checksum_url" "$checksum" || return 1
  fi

  git_loopy_tui_verify_checksum "$archive" "$checksum" >/dev/null || return 1

  local staged
  staged="$(
    git_loopy_tui_extract "$archive" "$workspace/unpacked" "$executable_name"
  )" || return 1
  git_loopy_tui_verify_helper "$staged" "$release_version" "$schema_version" ||
    return 1
  git_loopy_tui_activate "$staged" "$destination" || return 1

  printf '%s\n' "$destination"
}
