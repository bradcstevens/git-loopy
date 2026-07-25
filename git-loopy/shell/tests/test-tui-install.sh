#!/usr/bin/env bash

# The shell installer's helper-staging suite (PRD #173, issue #193).
#
# Every case here is network-free. The "remote" download path is exercised
# through a `file://` base URL, which is a real curl transfer against a real URL
# and needs nothing but a directory — so the download branch is proven rather
# than stubbed out.

set -euo pipefail

if ((BASH_VERSINFO[0] < 4)); then
  printf 'Bash 4+ is required (found %s).\n' "$BASH_VERSION" >&2
  exit 1
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
port_dir="$(cd "$script_dir/.." && pwd)"
conformance_dir="$(cd "$port_dir/../conformance" && pwd)"
artifact_metadata="$conformance_dir/tui-artifacts.json"

# shellcheck disable=SC1091
source "$port_dir/lib/tui-install.sh"

fail() {
  printf 'FAIL: %s\n' "$*" >&2
  exit 1
}

assert_equal() {
  local expected="$1"
  local actual="$2"
  local description="$3"
  [[ "$actual" == "$expected" ]] ||
    fail "$description"$'\n'"expected: $expected"$'\n'"actual:   $actual"
}

assert_contains() {
  local haystack="$1"
  local needle="$2"
  local description="$3"
  [[ "$haystack" == *"$needle"* ]] ||
    fail "$description"$'\n'"expected to contain: $needle"$'\n'"actual: $haystack"
}

# --- Target selection -------------------------------------------------------
#
# The shared fixture's `selection_cases` are the same oracle the Python consumer
# reads, so a host shape that resolves differently in the two installers is a
# fixture failure rather than a difference of opinion.

while IFS= read -r case_json; do
  case_id="$(jq -r '.id' <<<"$case_json")"
  system="$(jq -r '.system' <<<"$case_json")"
  machine="$(jq -r '.machine' <<<"$case_json")"
  libc="$(jq -r 'if .libc == null then "" else .libc end' <<<"$case_json")"
  expected_target="$(jq -r 'if .target == null then "" else .target end' <<<"$case_json")"
  expected_error="$(jq -r 'if .error == null then "" else .error end' <<<"$case_json")"

  actual_target=""
  actual_error=""
  if actual_target="$(
    git_loopy_tui_select_target "$artifact_metadata" "$system" "$machine" "$libc" 2>/tmp/tui-select-err
  )"; then
    actual_error=""
  else
    actual_target=""
    actual_error="$(</tmp/tui-select-err)"
  fi
  rm -f /tmp/tui-select-err

  assert_equal "$expected_target" "$actual_target" \
    "selection fixture target: $case_id"
  if [[ -n "$expected_error" ]]; then
    assert_contains "$actual_error" "$expected_error" \
      "selection fixture error: $case_id"
  fi
done < <(jq -c '.selection_cases[]' "$artifact_metadata")

# --- Artifact naming --------------------------------------------------------
#
# Expected names are written out rather than re-derived from the templates, so a
# template edit has to be a deliberate change to this file too.

assert_equal \
  "git-loopy-tui-aarch64-apple-darwin.tar.xz git-loopy-tui-aarch64-apple-darwin.tar.xz.sha256 git-loopy-tui" \
  "$(git_loopy_tui_artifact_names "$artifact_metadata" aarch64-apple-darwin)" \
  "artifact names for macOS arm64"

assert_equal \
  "git-loopy-tui-x86_64-pc-windows-msvc.zip git-loopy-tui-x86_64-pc-windows-msvc.zip.sha256 git-loopy-tui.exe" \
  "$(git_loopy_tui_artifact_names "$artifact_metadata" x86_64-pc-windows-msvc)" \
  "artifact names for Windows x64"

assert_equal \
  "git-loopy-tui-x86_64-unknown-linux-musl.tar.xz git-loopy-tui-x86_64-unknown-linux-musl.tar.xz.sha256 git-loopy-tui" \
  "$(git_loopy_tui_artifact_names "$artifact_metadata" x86_64-unknown-linux-musl)" \
  "artifact names for musl Linux x64"

if git_loopy_tui_artifact_names "$artifact_metadata" sparc-unknown-none >/dev/null 2>&1; then
  fail "artifact names accepted a triple the Release does not publish"
fi

# --- Download URL -----------------------------------------------------------

assert_equal \
  "https://github.com/bradcstevens/git-loopy/releases/download/v9.9.9/git-loopy-tui-aarch64-apple-darwin.tar.xz" \
  "$(
    git_loopy_tui_artifact_url "$artifact_metadata" 9.9.9 \
      git-loopy-tui-aarch64-apple-darwin.tar.xz
  )" \
  "artifact URL resolves against the published Release tag"

printf 'ok: shell TUI installer\n'
