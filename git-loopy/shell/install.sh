#!/usr/bin/env bash

# Optional installer for git-loopy's shell Orchestrator (ADR-0013).
#
# Puts a single `git-loopy` command on your PATH by writing a small launcher
# shim that `exec`s this clone's git-loopy.sh by absolute path. It installs
# NOTHING else — no Python, no TUI helper (git-loopy-tui arrives in phase 2), and
# no package-manager distribution. Run-in-place from the clone stays the
# baseline; this is a convenience so you can type `git-loopy` from any repo.
#
# The shim points back into this clone so the shared git-loopy/PROMPT.md keeps
# resolving one directory above the launcher — the installer never copies the
# Orchestrator out of the tree.

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

usage() {
  cat <<'EOF'
Usage: install.sh [--bin-dir DIR]

Install a `git-loopy` launcher for the shell Orchestrator onto your PATH.

Options:
  --bin-dir DIR   Directory to install the launcher into
                  (default: $XDG_BIN_HOME, else ~/.local/bin).
  -h, --help      Show this help and exit.
EOF
}

bin_dir="${XDG_BIN_HOME:-$HOME/.local/bin}"

while (($# > 0)); do
  case "$1" in
    -h | --help)
      usage
      exit 0
      ;;
    --bin-dir)
      [[ $# -ge 2 && "$2" != -* ]] || {
        printf 'install.sh: --bin-dir requires a value\n' >&2
        exit 2
      }
      bin_dir="$2"
      shift 2
      ;;
    --bin-dir=*)
      bin_dir="${1#*=}"
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
