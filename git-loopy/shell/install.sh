#!/usr/bin/env bash

# Installer for git-loopy's shell distribution (ADR-0013, PRD #173).
#
# One command installs the two halves of this clone's distribution: a `git-loopy`
# launcher on your PATH, and the `git-loopy-tui` helper this clone's Release
# pins, staged into `.git-loopy/bin/` where the Orchestrator looks for it first.
#
# The launcher is a small shim that `exec`s this clone's git-loopy.sh by absolute
# path, so the shared git-loopy/PROMPT.md keeps resolving one directory above the
# launcher — the installer never copies the Orchestrator out of the tree.
#
# The helper is the only thing this script downloads, it is downloaded exactly
# once here, and a Run never downloads anything at all. `--no-tui` skips it;
# `--tui-archive`/`--tui-checksum` install it from files an air-gapped host
# already has. Either way the artifact has to prove its published checksum, this
# clone's exact Release version, and Event-schema compatibility before it
# replaces anything — and a failure at any of those steps leaves a previously
# installed helper exactly as it was, with the Orchestrator still runnable in
# plain mode.

if [[ -z "${BASH_VERSION:-}" ]] || ((BASH_VERSINFO[0] < 4)); then
  printf '%s\n' \
    "git-loopy's installer requires Bash 4+ (found ${BASH_VERSION:-unknown})." \
    "macOS ships Bash 3.2; install a current Bash with \`brew install bash\` and rerun this script with it." \
    >&2
  exit 1
fi

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
launcher="$script_dir/git-loopy.sh"
repository_root="$(cd "$script_dir/../.." && pwd)"
artifact_metadata="$repository_root/git-loopy/conformance/tui-artifacts.json"

# shellcheck disable=SC1091
source "$script_dir/lib/release-version.sh"
# shellcheck disable=SC1091
source "$script_dir/lib/events.sh"
# shellcheck disable=SC1091
source "$script_dir/lib/tui-install.sh"

usage() {
  cat <<'EOF'
Usage: install.sh [--bin-dir DIR] [--no-tui]
                  [--tui-archive PATH --tui-checksum PATH] [--tui-base-url URL]

Install the `git-loopy` launcher for the shell Orchestrator onto your PATH and
stage the `git-loopy-tui` helper this clone's Release pins.

Options:
  --bin-dir DIR        Directory to install the launcher into
                       (default: $XDG_BIN_HOME, else ~/.local/bin).
  --no-tui             Install only the launcher. Runs stay in plain mode.
  --tui-archive PATH   Install the helper from a local release archive instead
                       of downloading it. Requires --tui-checksum.
  --tui-checksum PATH  The archive's published `.sha256` manifest.
  --tui-base-url URL   Fetch the helper from somewhere other than this Release's
                       published download location.
  -h, --help           Show this help and exit.
EOF
}

bin_dir="${XDG_BIN_HOME:-$HOME/.local/bin}"
install_tui=1
tui_archive=""
tui_checksum=""
tui_base_url=""

require_value() {
  local option="$1"
  local count="$2"
  local value="${3-}"
  ((count >= 2)) && [[ "$value" != -* ]] || {
    printf 'install.sh: %s requires a value\n' "$option" >&2
    exit 2
  }
}

while (($# > 0)); do
  case "$1" in
    -h | --help)
      usage
      exit 0
      ;;
    --bin-dir)
      require_value --bin-dir $# "${2-}"
      bin_dir="$2"
      shift 2
      ;;
    --bin-dir=*)
      bin_dir="${1#*=}"
      shift
      ;;
    --no-tui)
      install_tui=0
      shift
      ;;
    --tui-archive)
      require_value --tui-archive $# "${2-}"
      tui_archive="$2"
      shift 2
      ;;
    --tui-archive=*)
      tui_archive="${1#*=}"
      shift
      ;;
    --tui-checksum)
      require_value --tui-checksum $# "${2-}"
      tui_checksum="$2"
      shift 2
      ;;
    --tui-checksum=*)
      tui_checksum="${1#*=}"
      shift
      ;;
    --tui-base-url)
      require_value --tui-base-url $# "${2-}"
      tui_base_url="$2"
      shift 2
      ;;
    --tui-base-url=*)
      tui_base_url="${1#*=}"
      shift
      ;;
    *)
      printf 'install.sh: unknown argument: %s\n' "$1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

[[ -f "$launcher" ]] || {
  printf 'install.sh: launcher not found at %s\n' "$launcher" >&2
  exit 1
}

[[ -n "$bin_dir" ]] || {
  printf 'install.sh: install directory resolved empty; pass --bin-dir DIR\n' >&2
  exit 2
}

mkdir -p "$bin_dir"
bin_dir="$(cd "$bin_dir" && pwd)"
shim="$bin_dir/git-loopy"

# The shim exec's the launcher by absolute path, so git-loopy.sh still resolves
# the shared git-loopy/PROMPT.md one directory above itself in this clone.
cat >"$shim" <<EOF
#!/usr/bin/env bash
exec "$launcher" "\$@"
EOF
chmod +x "$shim"

printf 'Installed git-loopy launcher: %s\n' "$shim"
printf '  -> %s\n' "$launcher"

# The helper is installed after the launcher because the launcher is the part
# that has to work: a Run without a helper is a Run in plain mode, while a helper
# without a launcher is nothing at all.
if ((install_tui == 1)); then
  release_version="$(git_loopy_read_release_version "$repository_root/VERSION")" || {
    printf 'install.sh: cannot determine which Release this clone pins\n' >&2
    exit 1
  }

  if helper_path="$(
    git_loopy_tui_install \
      "$artifact_metadata" \
      "$repository_root" \
      "$release_version" \
      "$GIT_LOOPY_EVENT_SCHEMA_VERSION" \
      "$tui_base_url" \
      "$tui_archive" \
      "$tui_checksum"
  )"; then
    printf 'Installed git-loopy-tui %s: %s\n' "$release_version" "$helper_path"
  else
    printf '%s\n' \
      "install.sh: could not install the git-loopy-tui $release_version helper." \
      "  Nothing was replaced; git-loopy still runs, in plain mode, without it." \
      "  Re-run with --no-tui to install the launcher alone." \
      >&2
    exit 1
  fi
else
  printf 'Skipped git-loopy-tui (--no-tui). Runs stay in plain mode.\n'
fi

case ":$PATH:" in
  *":$bin_dir:"*)
    printf 'Run it from inside any git repository: git-loopy\n'
    ;;
  *)
    printf '\n%s is not on your PATH. Add it, then reopen your shell:\n' "$bin_dir"
    # shellcheck disable=SC2016  # literal $PATH is guidance the operator pastes
    printf '  export PATH="%s:$PATH"\n' "$bin_dir"
    printf 'Until then, run the launcher directly: %s\n' "$shim"
    ;;
esac
