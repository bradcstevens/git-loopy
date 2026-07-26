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
source "$port_dir/lib/events.sh"
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

# --- Checksum verification --------------------------------------------------
#
# Both halves are load-bearing. A digest that matches proves nothing if it was
# published for a different file, so the manifest has to name *this* artifact
# too — otherwise a correct macOS arm64 checksum would happily bless the x64
# archive an installer downloaded by mistake.

checksum_dir="$(cd "$(mktemp -d "${TMPDIR:-/tmp}/git-loopy-checksum.XXXXXX")" && pwd)"
trap 'rm -rf "$checksum_dir"' EXIT

printf 'helper' >"$checksum_dir/git-loopy-tui-x86_64-apple-darwin.tar.xz"
# The SHA-256 of the six bytes `helper`, computed independently of this suite.
helper_digest="e81d3b0e9d82feaaf5f6e55bdff24731d7eee08632ffa63801e6397290c5d20a"
printf '%s  git-loopy-tui-x86_64-apple-darwin.tar.xz\n' "$helper_digest" \
  >"$checksum_dir/git-loopy-tui-x86_64-apple-darwin.tar.xz.sha256"

assert_equal "$helper_digest" \
  "$(
    git_loopy_tui_verify_checksum \
      "$checksum_dir/git-loopy-tui-x86_64-apple-darwin.tar.xz" \
      "$checksum_dir/git-loopy-tui-x86_64-apple-darwin.tar.xz.sha256"
  )" \
  "a published checksum proves the artifact it names"

printf 'tampered' >"$checksum_dir/tampered.tar.xz"
printf '%s  tampered.tar.xz\n' "$helper_digest" >"$checksum_dir/tampered.tar.xz.sha256"
if git_loopy_tui_verify_checksum \
  "$checksum_dir/tampered.tar.xz" "$checksum_dir/tampered.tar.xz.sha256" \
  >/dev/null 2>"$checksum_dir/tampered.err"; then
  fail "a tampered artifact passed its checksum"
fi
assert_contains "$(<"$checksum_dir/tampered.err")" "failed its SHA-256 checksum" \
  "a tampered artifact is refused by name"

printf '%s  some-other-artifact.tar.xz\n' "$helper_digest" \
  >"$checksum_dir/mismatched.sha256"
if git_loopy_tui_verify_checksum \
  "$checksum_dir/git-loopy-tui-x86_64-apple-darwin.tar.xz" \
  "$checksum_dir/mismatched.sha256" \
  >/dev/null 2>"$checksum_dir/mismatched.err"; then
  fail "a checksum published for a different artifact was accepted"
fi
assert_contains "$(<"$checksum_dir/mismatched.err")" "some-other-artifact.tar.xz" \
  "a checksum for another artifact names what it actually publishes"

printf 'not a checksum manifest\n' >"$checksum_dir/empty.sha256"
if git_loopy_tui_verify_checksum \
  "$checksum_dir/git-loopy-tui-x86_64-apple-darwin.tar.xz" \
  "$checksum_dir/empty.sha256" >/dev/null 2>&1; then
  fail "a manifest declaring no SHA-256 entry was accepted"
fi

if git_loopy_tui_verify_checksum \
  "$checksum_dir/git-loopy-tui-x86_64-apple-darwin.tar.xz" \
  "$checksum_dir/absent.sha256" >/dev/null 2>&1; then
  fail "a missing checksum manifest was accepted"
fi

# --- Extraction -------------------------------------------------------------

extract_dir="$(cd "$(mktemp -d "${TMPDIR:-/tmp}/git-loopy-extract.XXXXXX")" && pwd)"
trap 'rm -rf "$checksum_dir" "$extract_dir"' EXIT

mkdir -p "$extract_dir/payload"
printf '#!/bin/sh\necho staged\n' >"$extract_dir/payload/git-loopy-tui"
chmod +x "$extract_dir/payload/git-loopy-tui"
tar -cJf "$extract_dir/helper.tar.xz" -C "$extract_dir/payload" git-loopy-tui

staged="$(
  git_loopy_tui_extract "$extract_dir/helper.tar.xz" "$extract_dir/into" git-loopy-tui
)"
[[ -x "$staged" ]] || fail "the extracted helper is not executable"
assert_equal "staged" "$("$staged")" "the extracted helper is the archived one"

tar -cJf "$extract_dir/wrong.tar.xz" -C "$extract_dir/payload" git-loopy-tui \
  --transform 's/git-loopy-tui/something-else/' 2>/dev/null ||
  {
    mkdir -p "$extract_dir/wrong-payload"
    cp "$extract_dir/payload/git-loopy-tui" "$extract_dir/wrong-payload/something-else"
    tar -cJf "$extract_dir/wrong.tar.xz" -C "$extract_dir/wrong-payload" something-else
  }
if git_loopy_tui_extract "$extract_dir/wrong.tar.xz" "$extract_dir/wrong-into" \
  git-loopy-tui >/dev/null 2>"$extract_dir/wrong.err"; then
  fail "an archive without the published executable was accepted"
fi
assert_contains "$(<"$extract_dir/wrong.err")" "git-loopy-tui" \
  "an archive without the published executable names what was missing"

# --- Version and capability probe -------------------------------------------
#
# `--version` is Release identity and `--schema-version` is compatibility; §16
# of the Wrapper contract keeps them separate, and a helper this distribution
# stages has to satisfy both before it is allowed to become the active one.

probe_dir="$(cd "$(mktemp -d "${TMPDIR:-/tmp}/git-loopy-probe.XXXXXX")" && pwd)"
trap 'rm -rf "$checksum_dir" "$extract_dir" "$probe_dir"' EXIT

write_fake_helper() {
  local path="$1"
  local reported_version="$2"
  local minimum="$3"
  local maximum="$4"
  local probed_version="${5:-$reported_version}"
  mkdir -p "$(dirname -- "$path")"
  cat >"$path" <<FAKE
#!/usr/bin/env bash
case "\${1-}" in
  --version) printf 'git-loopy-tui %s\n' "$reported_version" ;;
  --schema-version)
    printf '{"version": "%s", "min_event_schema_version": %s, "max_event_schema_version": %s}\n' \\
      "$probed_version" "$minimum" "$maximum"
    ;;
  *) cat >/dev/null ;;
esac
FAKE
  chmod +x "$path"
}

write_fake_helper "$probe_dir/good" 4.5.6 1 "$GIT_LOOPY_EVENT_SCHEMA_VERSION"
git_loopy_tui_verify_helper "$probe_dir/good" 4.5.6 \
  "$GIT_LOOPY_EVENT_SCHEMA_VERSION" ||
  fail "a helper of this Release and schema was refused"

write_fake_helper "$probe_dir/other-release" 9.9.9 1 "$GIT_LOOPY_EVENT_SCHEMA_VERSION"
if git_loopy_tui_verify_helper "$probe_dir/other-release" 4.5.6 \
  "$GIT_LOOPY_EVENT_SCHEMA_VERSION" >/dev/null 2>"$probe_dir/other.err"; then
  fail "a helper from another Release was staged"
fi
assert_contains "$(<"$probe_dir/other.err")" "9.9.9" \
  "a helper from another Release reports what it actually is"

write_fake_helper "$probe_dir/older-schema" 4.5.6 0 0
if git_loopy_tui_verify_helper "$probe_dir/older-schema" 4.5.6 \
  "$GIT_LOOPY_EVENT_SCHEMA_VERSION" >/dev/null 2>"$probe_dir/schema.err"; then
  fail "a helper that cannot decode this Event schema was staged"
fi
assert_contains "$(<"$probe_dir/schema.err")" "Event schema" \
  "an incompatible helper is refused by capability, not by version"

# Release equality is product identity and never the compatibility authority
# (§16), so a helper that claims this Release while probing as another one is
# refused too.
write_fake_helper "$probe_dir/lying" 4.5.6 1 "$GIT_LOOPY_EVENT_SCHEMA_VERSION" 9.9.9
if git_loopy_tui_verify_helper "$probe_dir/lying" 4.5.6 \
  "$GIT_LOOPY_EVENT_SCHEMA_VERSION" >/dev/null 2>&1; then
  fail "a helper whose probe disagrees with its --version was staged"
fi

printf '#!/usr/bin/env bash\nexit 3\n' >"$probe_dir/broken"
chmod +x "$probe_dir/broken"
if git_loopy_tui_verify_helper "$probe_dir/broken" 4.5.6 \
  "$GIT_LOOPY_EVENT_SCHEMA_VERSION" >/dev/null 2>&1; then
  fail "a helper that cannot answer --version was staged"
fi

# --- Atomic activation ------------------------------------------------------
#
# The workspace is deliberately a sibling of the destination, so the last step is
# a same-directory rename: the bytes that were verified are exactly the bytes
# that land, and there is no window in which a half-written file is discoverable
# as the clone-local helper.

activate_dir="$(cd "$(mktemp -d "${TMPDIR:-/tmp}/git-loopy-activate.XXXXXX")" && pwd)"
trap 'rm -rf "$checksum_dir" "$extract_dir" "$probe_dir" "$activate_dir"' EXIT

destination="$activate_dir/repo/.git-loopy/bin/git-loopy-tui"
workspace="$(git_loopy_tui_workspace "$destination")"
assert_equal "$(dirname -- "$destination")" "$(dirname -- "$workspace")" \
  "the staging workspace is a sibling of the destination"

printf '#!/bin/sh\necho fresh\n' >"$workspace/git-loopy-tui"
chmod +x "$workspace/git-loopy-tui"
git_loopy_tui_activate "$workspace/git-loopy-tui" "$destination" ||
  fail "a verified helper could not be activated"
assert_equal "fresh" "$("$destination")" "activation installs the verified helper"

printf '#!/bin/sh\necho upgraded\n' >"$workspace/git-loopy-tui"
chmod +x "$workspace/git-loopy-tui"
git_loopy_tui_activate "$workspace/git-loopy-tui" "$destination" ||
  fail "a verified helper could not replace an installed one"
assert_equal "upgraded" "$("$destination")" "activation replaces the installed helper"
rm -rf "$workspace"

# --- Host libc --------------------------------------------------------------
#
# Linux's alone, and the one selection input `uname` cannot answer. Parsed from
# `ldd --version` text rather than probed, so the cases a real host produces are
# pinned rather than described.

assert_equal "musl" \
  "$(git_loopy_tui_libc_from_ldd 'musl libc (x86_64)
Version 1.2.4')" \
  "musl identifies itself"
assert_equal "gnu" \
  "$(git_loopy_tui_libc_from_ldd 'ldd (Ubuntu GLIBC 2.35-0ubuntu3.6) 2.35')" \
  "glibc identifies itself"
assert_equal "" "$(git_loopy_tui_libc_from_ldd 'command not found')" \
  "an unrecognized C library is not guessed at"

# --- The installer, end to end ----------------------------------------------
#
# A fake clone with its own VERSION and a fake published Release served over
# `file://`, so the download branch is a real curl transfer and the suite still
# never touches the network.

cli_dir="$(cd "$(mktemp -d "${TMPDIR:-/tmp}/git-loopy-install.XXXXXX")" && pwd)"
trap 'rm -rf "$checksum_dir" "$extract_dir" "$probe_dir" "$activate_dir" "$cli_dir"' EXIT

make_fake_clone() {
  local root="$1"
  local version="$2"
  mkdir -p "$root/git-loopy/shell/lib" "$root/git-loopy/conformance"
  cp "$port_dir/install.sh" "$port_dir/git-loopy.sh" "$root/git-loopy/shell/"
  cp "$port_dir"/lib/*.sh "$root/git-loopy/shell/lib/"
  cp "$artifact_metadata" "$root/git-loopy/conformance/"
  printf '%s\n' "$version" >"$root/VERSION"
}

host_triple="$(git_loopy_tui_host_target "$artifact_metadata")" ||
  fail "this host has no published artifact, so the installer cannot be exercised"
read -r host_archive host_checksum host_executable \
  < <(git_loopy_tui_artifact_names "$artifact_metadata" "$host_triple")

# Publish one fake Release: the archive this host would select, its checksum
# manifest, and a helper inside that answers `--version` and `--schema-version`.
publish_fake_release() {
  local into="$1"
  local reported_version="$2"
  local maximum="${3:-$GIT_LOOPY_EVENT_SCHEMA_VERSION}"

  rm -rf "$into"
  mkdir -p "$into/payload"
  write_fake_helper "$into/payload/$host_executable" "$reported_version" 1 "$maximum"
  if [[ "$host_archive" == *.zip ]]; then
    (cd "$into/payload" && zip -q "$into/$host_archive" "$host_executable")
  else
    tar -cJf "$into/$host_archive" -C "$into/payload" "$host_executable"
  fi
  printf '%s  %s\n' \
    "$(git_loopy_tui_digest "$into/$host_archive")" "$host_archive" \
    >"$into/$host_checksum"
}

# 1. The default installation stages both halves of the distribution.
clone="$cli_dir/clone"
make_fake_clone "$clone" 4.5.6
release="$cli_dir/release"
publish_fake_release "$release" 4.5.6

install_out="$(
  "$BASH" "$clone/git-loopy/shell/install.sh" \
    --bin-dir "$cli_dir/bin" --tui-base-url "file://$release" 2>&1
)" || fail "the default installation failed: $install_out"

[[ -x "$cli_dir/bin/git-loopy" ]] || fail "the launcher shim was not installed"
[[ -x "$clone/.git-loopy/bin/git-loopy-tui" ]] ||
  fail "the pinned helper was not staged into the clone"
assert_equal "git-loopy-tui 4.5.6" \
  "$("$clone/.git-loopy/bin/git-loopy-tui" --version)" \
  "the staged helper is the Release this clone pins"
assert_contains "$install_out" "$clone/.git-loopy/bin/git-loopy-tui" \
  "the installation reports where the helper landed"

# 2. The opt-out keeps the Phase 1 launcher-only behaviour.
opt_out_clone="$cli_dir/opt-out"
make_fake_clone "$opt_out_clone" 4.5.6
"$BASH" "$opt_out_clone/git-loopy/shell/install.sh" \
  --bin-dir "$cli_dir/opt-out-bin" --no-tui >/dev/null 2>&1 ||
  fail "--no-tui failed"
[[ -x "$cli_dir/opt-out-bin/git-loopy" ]] || fail "--no-tui skipped the launcher"
[[ ! -e "$opt_out_clone/.git-loopy/bin/git-loopy-tui" ]] ||
  fail "--no-tui staged a helper anyway"

# 3. An air-gapped host installs from local files and never reaches for a URL.
airgap_clone="$cli_dir/airgap"
make_fake_clone "$airgap_clone" 4.5.6
"$BASH" "$airgap_clone/git-loopy/shell/install.sh" \
  --bin-dir "$cli_dir/airgap-bin" \
  --tui-archive "$release/$host_archive" \
  --tui-checksum "$release/$host_checksum" \
  --tui-base-url "file:///nonexistent" >/dev/null 2>&1 ||
  fail "the air-gapped installation failed"
assert_equal "git-loopy-tui 4.5.6" \
  "$("$airgap_clone/.git-loopy/bin/git-loopy-tui" --version)" \
  "a local artifact installs when its published checksum matches"

# 4. A local artifact without its matching checksum manifest is refused.
if "$BASH" "$airgap_clone/git-loopy/shell/install.sh" \
  --bin-dir "$cli_dir/airgap-bin" \
  --tui-archive "$release/$host_archive" >/dev/null 2>&1; then
  fail "a local artifact was accepted with no checksum manifest"
fi

# 5. Checksum drift is refused, and the installation that already succeeded is
#    left exactly as it was.
tampered="$cli_dir/tampered"
publish_fake_release "$tampered" 4.5.6
printf 'tampered' >>"$tampered/$host_archive"

set +e
drift_out="$(
  "$BASH" "$clone/git-loopy/shell/install.sh" \
    --bin-dir "$cli_dir/bin" --tui-base-url "file://$tampered" 2>&1
)"
drift_status=$?
set -e
((drift_status != 0)) || fail "a tampered artifact installed successfully"
assert_contains "$drift_out" "SHA-256 checksum" \
  "a tampered artifact is refused by checksum"
assert_contains "$drift_out" "plain" \
  "a refused helper says the Orchestrator still runs in plain mode"
assert_equal "git-loopy-tui 4.5.6" \
  "$("$clone/.git-loopy/bin/git-loopy-tui" --version)" \
  "a failed installation leaves the previously verified helper untouched"
[[ -z "$(find "$clone/.git-loopy/bin" -maxdepth 1 -name '.git-loopy-tui-staging.*' -print -quit)" ]] ||
  fail "a failed installation left staging debris beside the helper"

# 6. A helper from another Release is refused before it is activated.
foreign="$cli_dir/foreign"
publish_fake_release "$foreign" 9.9.9
set +e
foreign_out="$(
  "$BASH" "$clone/git-loopy/shell/install.sh" \
    --bin-dir "$cli_dir/bin" --tui-base-url "file://$foreign" 2>&1
)"
foreign_status=$?
set -e
((foreign_status != 0)) || fail "a helper from another Release installed"
assert_contains "$foreign_out" "9.9.9" "the refused Release version is named"
assert_equal "git-loopy-tui 4.5.6" \
  "$("$clone/.git-loopy/bin/git-loopy-tui" --version)" \
  "a refused Release leaves the installed helper untouched"

# 7. A helper that cannot decode this Event schema is refused too.
incapable="$cli_dir/incapable"
publish_fake_release "$incapable" 4.5.6 0
set +e
incapable_out="$(
  "$BASH" "$clone/git-loopy/shell/install.sh" \
    --bin-dir "$cli_dir/bin" --tui-base-url "file://$incapable" 2>&1
)"
incapable_status=$?
set -e
((incapable_status != 0)) || fail "an incapable helper installed"
assert_contains "$incapable_out" "Event schema" \
  "an incapable helper is refused by capability"

# 8. A download that cannot be fetched fails loudly and changes nothing.
set +e
missing_out="$(
  "$BASH" "$clone/git-loopy/shell/install.sh" \
    --bin-dir "$cli_dir/bin" --tui-base-url "file://$cli_dir/nowhere" 2>&1
)"
missing_status=$?
set -e
((missing_status != 0)) || fail "an unreachable Release installed something"
assert_contains "$missing_out" "cannot download" \
  "an unreachable Release is refused by download, not by checksum"
assert_equal "git-loopy-tui 4.5.6" \
  "$("$clone/.git-loopy/bin/git-loopy-tui" --version)" \
  "an unreachable Release leaves the installed helper untouched"

# 9. PATH guidance is printed exactly when the shim is not discoverable.
guidance_clone="$cli_dir/guidance"
make_fake_clone "$guidance_clone" 4.5.6
guidance_out="$(
  "$BASH" "$guidance_clone/git-loopy/shell/install.sh" \
    --bin-dir "$cli_dir/guidance-bin" --no-tui 2>&1
)"
assert_contains "$guidance_out" "is not on your PATH" \
  "an undiscoverable shim earns PATH guidance"

on_path_out="$(
  PATH="$cli_dir/guidance-bin:$PATH" "$BASH" \
    "$guidance_clone/git-loopy/shell/install.sh" \
    --bin-dir "$cli_dir/guidance-bin" --no-tui 2>&1
)"
assert_contains "$on_path_out" "Run it from inside any git repository" \
  "a discoverable shim is reported as ready to run"

# 10. Setup verifies the one native distribution it is installing (#257).
#
# The distribution being verified is the clone this installer belongs to, so the
# report names the profile and this clone's Release rather than an executable path:
# nothing host-specific is stated and nothing about the choice is written down.

assert_contains "$guidance_out" "Continuation capabilities" \
  "the installation reports the Continuation capabilities it verified"
assert_contains "$guidance_out" "foundation profile" \
  "the report names the capability profile that was satisfied"
assert_contains "$guidance_out" "prospective_projection" \
  "the report names the optional capabilities this distribution does not support"

# 11. A distribution that misses a required capability installs nothing at all.
#
# Verification runs before the launcher is written for the same reason preflight
# exists: an operator who is told at install time never learns it from a Run that
# fails further from the cause.
unverifiable_clone="$cli_dir/unverifiable"
make_fake_clone "$unverifiable_clone" 4.5.6
# Drop one required native operation from the manifest this clone advertises.
"$BASH" -c 'sed -e "s/\"repair-index\":true/\"repair-index\":false/" "$1" >"$1.patched" \
  && mv "$1.patched" "$1"' _ "$unverifiable_clone/git-loopy/shell/lib/continuation.sh"

set +e
unverifiable_out="$(
  "$BASH" "$unverifiable_clone/git-loopy/shell/install.sh" \
    --bin-dir "$cli_dir/unverifiable-bin" --no-tui 2>&1
)"
unverifiable_status=$?
set -e
((unverifiable_status != 0)) ||
  fail "an unverifiable distribution installed anyway"
assert_contains "$unverifiable_out" "native-operations" \
  "the refusal names the requirement the distribution does not satisfy"
[[ ! -e "$cli_dir/unverifiable-bin/git-loopy" ]] ||
  fail "an unverifiable distribution installed its launcher"

# --- A Run never installs software ------------------------------------------
#
# The other half of the promise. Installation is an explicit act by the operator
# and happens exactly once, here; the Orchestrator only ever *discovers* a helper
# somebody already staged. Nothing on the Run path may reach for a network or for
# this module, so that is asserted rather than merely documented.

for run_path_module in continuation.sh events.sh orchestrator.sh tui.sh; do
  if grep -Eq '(^|[^[:alnum:]_-])(curl|wget)([^[:alnum:]_-]|$)|tui-install\.sh' \
    "$port_dir/lib/$run_path_module"; then
    fail "lib/$run_path_module reaches for a download on the Run path"
  fi
done

printf 'ok: shell TUI installer\n'
